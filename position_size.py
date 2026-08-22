import numpy as np
import pandas as pd

from logger import log_event


# --------------------------------------------------
# Parámetros validados — experimento 10 (2020-2025)
# Confirmados en experimento 16 B1 (2015-2025)
# --------------------------------------------------

RISK_PERCENT     = 0.0085     # Riesgo por operación: 0.85% del capital  # Ver también config.py — RISK_PER_TRADE
MAX_POSITION_PCT = 0.25       # Tamaño máximo de una posición: 25% del capital

# --------------------------------------------------
# Stop loss dinámico B1 — experimento 16
# Multiplicador ATR interpolado según percentil de volatilidad histórica.
# Percentil alto (activo muy volátil ahora) → multiplicador bajo → stop ajustado
# Percentil bajo (activo poco volátil ahora) → multiplicador alto → stop holgado
# --------------------------------------------------

B1_MULT_MIN = 2.2             # Multiplicador mínimo (volatilidad en máximos históricos)  # Ver también config.py — B1_MULT_MIN
B1_MULT_MAX = 4.0             # Multiplicador máximo (volatilidad en mínimos históricos)  # Ver también config.py — B1_MULT_MAX
ATR_MULTIPLIER_BASE = 3.1     # Multiplicador de respaldo si ATR_PERCENTIL no está disponible
TRAILING_FACTOR = 0.75        # Aprobado en Experimento 40-ter (stress test 3/3 crisis)  # Ver también config.py — TRAILING_FACTOR

# --------------------------------------------------
# Guardias anti-outlier del trailing stop en calcular_trailing_stop()
# — RETIRADAS 22/08/2026, historial completo abajo
# --------------------------------------------------
# ORIGEN (incidente ANET 05/08/2026): el stop de ANET quedó a 0,91 del
# cierre (196,40 vs 197,31). CAUSA REAL (verificada 06/08/2026 con datos
# oficiales de IBKR, get_price_history): el "bad tick" NO se confirmo --
# el 05/08 ANET tuvo un dia real y extraordinariamente volatil (apertura
# 210,19, high oficial 214,89, cierre 197,31, volumen 2-3x lo normal,
# probable reaccion a resultados). El high de 214,89 era un dato REAL, no
# corrupto. calcular_trailing_stop() confiaba en df["high"].iloc[-1] sin
# validar que fuera coherente con la volatilidad reciente. Se añadieron
# dos guardias (ATR_SALTO y STOP_DEMASIADO_CERCA) el 05-07/08/2026, en
# reaccion directa al incidente y SIN pasar por el backtest de 21 años
# antes de ir a produccion: si el ratio de ATR entre ciclos salia de
# [ATR_RATIO_MIN, ATR_RATIO_MAX], o si el stop resultante quedaba a menos
# de 1×ATR del cierre, el ciclo se descartaba (se conservaba el stop GTC
# vigente y se alertaba) en vez de calcular el nuevo stop.
#
# RETIRADA (22/08/2026) — Experimento 47, backtest de 21 años completo
# (backtest_exp47_guardias_trailing.py, resultados en backtest_results/
# *_exp47_*.csv), comparando el calculo CON las guardias (Variante B, tal
# como estaba en produccion) contra SIN ellas (Variante A, la que ya
# validan los 21 años del backtest canonico):
#   Variante A (sin guardias) : PF 2,6071 | capital 8.888.418€ | DD 10,4%
#   Variante B (con guardias) : PF 2,4342 | capital 6.984.283€ (-21,42%
#                               vs A) | DD 12,7% (peor drawdown)
# La variante A gana en las 4 metricas. Una tercera variante amortiguada
# (C, que capea el ATR en vez de descartar el ciclo, disenada el mismo
# 22/08/2026 para intentar salvar la idea) dio resultados IDENTICOS a B
# porque ATR_SALTO no se activo ni una sola vez en los 21 años de
# backtest -- su mecanismo diferenciador nunca tuvo efecto real.
# Adicionalmente: STOP_DEMASIADO_CERCA se disparo en el 73% de las
# operaciones del backtest (3.364 activaciones sobre 2.256 trades) -- no
# es una guardia de eventos raros, interviene en la mayoria de
# operaciones normales, rechazando el trailing recalculado (mas ajustado,
# protegiendo mas ganancia) y manteniendo el stop anterior mas flojo.
# Caso real MRNA (20/08/2026): ATR_SALTO bloqueo la actualizacion del
# trailing la noche antes de una reversion intradia del -23,6%. Verificado
# con la formula real del sistema que, sin el bloqueo, el calculo habria
# dado un stop entre 154-157$ -- MEJOR que los 150$ que Javier puso
# manualmente esa noche. La guardia no protegio nada; impidio que el
# sistema hiciera, solo, lo que habria hecho bien.
# Hasta la fecha de la retirada, ninguna de las dos guardias tiene un
# caso real documentado donde demostrablemente evitara una perdida que
# sin ella si hubiera ocurrido -- ATR_SALTO ni siquiera existia cuando
# paso el incidente original de ANET que las motivo (se añadio DESPUES,
# como reaccion), y ANET nunca las puso a prueba.
# calcular_trailing_stop() vuelve al calculo directo de la Variante A:
# mult -> nuevo_stop, sin ninguna comprobacion de salto de ATR ni de
# distancia minima. Ver 00_LIBERTAD2045_CONTEXT.txt para más contexto.
#
# NO SE TOCA verificar_margen_stop_vivo() (mas abajo en este modulo): es
# un mecanismo distinto (guardia de precio vivo justo antes de transmitir
# la orden, no del calculo por cierre), no medido por este backtest, y
# sigue en produccion sin cambios. Por eso MIN_STOP_DISTANCE_ATR y
# _alertar_anomalia_trailing() se conservan: verificar_margen_stop_vivo()
# los sigue usando. ATR_RATIO_MIN y ATR_RATIO_MAX si se eliminan: solo
# los usaba la guardia ATR_SALTO ya retirada (Articulo III).
# --------------------------------------------------

MIN_STOP_DISTANCE_ATR  = 1.0  # El stop nunca puede quedar a menos de 1×ATR del cierre de hoy — usado por verificar_margen_stop_vivo()


def _alertar_anomalia_trailing(codigo, mensaje, symbol=None):
    """
    Registra y alerta una anomalía detectada por las guardias del trailing stop.
    Nunca debe interrumpir el cálculo — cualquier fallo aquí se ignora.
    """
    try:
        log_event("CRITICAL", f"[{codigo}] Trailing stop descartado — {mensaje}",
                   symbol=symbol or "")
    except Exception:
        pass
    try:
        from telegram import send_telegram_critical
        etiqueta = f" ({symbol})" if symbol else ""
        send_telegram_critical(
            f"LIBERTAD_2045 - Trailing stop{etiqueta} descartado por dato anómalo [{codigo}]: "
            f"{mensaje}. Se mantiene el stop GTC vigente. Revisar manualmente."
        )
    except Exception:
        pass


def _obtener_multiplicador(df):
    """
    Calcula el multiplicador ATR dinámico según el percentil de volatilidad.

    Lee ATR_PERCENTIL del DataFrame generado por data_loader.
    Si no está disponible, usa ATR_MULTIPLIER_BASE como fallback.
    """

    try:
        percentil = df["ATR_PERCENTIL"].iloc[-1]

        if pd.isna(percentil):
            return ATR_MULTIPLIER_BASE

        # Interpolación lineal: percentil alto → multiplicador bajo
        mult = B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil
        return round(mult, 2)

    except (KeyError, IndexError):
        return ATR_MULTIPLIER_BASE


def calcular_posicion(df, capital):
    """
    Calcula el tamaño de posición basado en riesgo real.

    Parámetros:
        df      : DataFrame con columnas ATR, ATR_PERCENTIL y close
                  calculadas por data_loader
        capital : capital real de la cuenta, leído de IBKR en cada ciclo

    Retorna:
        shares        : número de acciones a comprar (0 si no se debe operar)
        stop_distance : distancia en precio hasta el stop-loss
        atr           : valor ATR usado en el cálculo
    """

    # --------------------------------------------------
    # Validaciones previas
    # --------------------------------------------------

    if df is None or len(df) < 20:
        return 0, None, None

    if capital <= 0:
        return 0, None, None

    # Leer ATR calculado por data_loader (sin recalcular)
    atr = df["ATR"].iloc[-1]

    if pd.isna(atr) or atr <= 0:
        return 0, None, None

    last_price = df["close"].iloc[-1]

    if pd.isna(last_price) or last_price <= 1:
        return 0, None, None

    # --------------------------------------------------
    # Multiplicador dinámico B1
    # --------------------------------------------------

    multiplicador = _obtener_multiplicador(df)
    stop_distance = atr * multiplicador

    if stop_distance <= 0:
        return 0, None, None

    # --------------------------------------------------
    # Cálculo del tamaño de posición
    # --------------------------------------------------

    # Shares por riesgo: cuántas acciones puedo comprar
    # para no perder más de RISK_PERCENT del capital si toca el stop
    risk_amount   = capital * RISK_PERCENT
    shares_risk   = int(risk_amount / stop_distance)

    # Shares por capital: límite máximo de exposición por posición
    max_position_value = capital * MAX_POSITION_PCT
    shares_capital     = int(max_position_value / last_price)

    # El tamaño final es el mínimo de ambos límites
    shares = min(shares_risk, shares_capital)

    # Si el cálculo da 0, no se opera — nunca forzar una posición
    if shares <= 0:
        return 0, None, None

    return shares, stop_distance, atr


def calcular_trailing_stop(df, trailing_factor=TRAILING_FACTOR, symbol=None):
    """
    Calcula el nivel de trailing stop B1 para una posición abierta.
    Fuente única de la lógica B1 — usada por portfolio_manager y rebalance.

    Parámetros:
        df             : DataFrame de data_loader con columnas ATR, ATR_PERCENTIL, high, close
        trailing_factor: multiplicador de compresión del stop (por defecto TRAILING_FACTOR)
        symbol         : ticker, opcional — no usado por este cálculo; se mantiene en la
                         firma por compatibilidad con las llamadas existentes (portfolio_manager,
                         rebalance). Lo usaban las guardias anti-outlier retiradas el 22/08/2026
                         (ver comentario de cabecera del módulo) para identificar el activo en
                         logs/alertas.

    Cálculo directo B1 — sin guardias anti-outlier (retiradas 22/08/2026, ver
    comentario de cabecera del módulo: el backtest de 21 años del Experimento 47
    mostró que empeoraban las 4 métricas frente a este cálculo directo).

    Retorna (nuevo_stop, mult) o (None, None) si los datos son insuficientes.
    """
    try:
        atr_val       = df["ATR"].iloc[-1]
        percentil_val = df["ATR_PERCENTIL"].iloc[-1]
        high_hoy      = df["high"].iloc[-1]

        if pd.isna(atr_val) or pd.isna(percentil_val) or atr_val <= 0:
            return None, None

        mult       = round((B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * float(percentil_val)) * trailing_factor, 2)
        nuevo_stop = round(float(high_hoy) - float(atr_val) * mult, 2)

        return nuevo_stop, mult

    except Exception:
        return None, None


# --------------------------------------------------
# Guardia de frescura temporal — desfase cálculo/transmisión
# Incidente ANET 05/08/2026 (segunda causa, tras las guardias anti-outlier
# de arriba): el trailing stop se calcula con el cierre oficial leído al
# INICIO del ciclo, pero portfolio_manager.evaluar_stops_por_cierre() no
# transmite la orden a IBKR hasta después de procesar el resto de la
# cartera (reqAllOpenOrders, resto de posiciones) — en el incidente real,
# 13-14 minutos de desfase entre "leer el cierre" y "colocar la orden".
# Verificado que el reordenamiento del ciclo (stops de cartera antes del
# escaneo de 500 símbolos) NO es la causa: esa fase ya se ejecuta antes
# del escaneo desde el 21/05/2026 (commit 29fb968) — el desfase ocurre
# DENTRO de la propia fase de gestión de cartera, entre símbolos.
# Esta guardia no sustituye al cálculo por cierre (que sigue siendo la
# fuente de verdad del nivel del stop) — solo verifica, con un precio VIVO
# pedido justo antes de placeOrder(), que el margen no se ha desplomado en
# el tiempo transcurrido desde el cálculo. Ver 00_LIBERTAD2045_CONTEXT.txt.
# --------------------------------------------------

def verificar_margen_stop_vivo(precio_vivo, stop_price, atr,
                                min_mult=MIN_STOP_DISTANCE_ATR, symbol=None):
    """
    Verifica, justo antes de transmitir un trailing stop, que el margen
    respecto a un precio VIVO (snapshot pedido en el instante del envío)
    sigue siendo válido — no solo respecto al cierre usado minutos antes
    para calcularlo.

    Reaplica el mismo umbral (min_mult × ATR) que usaba la guardia
    STOP_DEMASIADO_CERCA de calcular_trailing_stop() antes de su retirada
    el 22/08/2026 (ver comentario de cabecera del módulo) — pero contra
    el precio del instante de transmisión, no el cierre usado minutos antes.

    Es una guardia adicional, no la fuente de verdad: si no hay precio vivo
    disponible o no hay ATR, deja pasar sin bloquear (fail-open) — un
    snapshot fallido no debe impedir un trailing stop legítimo. Solo
    bloquea con una lectura vivo válida que confirme el margen colapsado.

    Retorna True si es seguro transmitir, False si debe abortarse (se
    conserva el stop GTC vigente y se alerta).
    """
    try:
        if precio_vivo is None or pd.isna(precio_vivo) or precio_vivo <= 0:
            return True
        if atr is None or pd.isna(atr) or atr <= 0:
            return True

        distancia = float(precio_vivo) - float(stop_price)

        if distancia < min_mult * float(atr):
            _alertar_anomalia_trailing(
                "STOP_DEMASIADO_CERCA_VIVO",
                f"stop={stop_price:.2f} a solo {distancia:.2f} del precio vivo="
                f"{precio_vivo:.2f} en el instante de transmisión (ATR={atr:.2f}) "
                f"— desfase temporal entre cálculo y envío",
                symbol=symbol,
            )
            return False

        return True

    except Exception:
        return True
