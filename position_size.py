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
# Guardias anti-outlier del trailing stop — incidente ANET 05/08/2026
# El stop de ANET quedó a 0,91 del cierre (196,40 vs 197,31). CAUSA REAL
# (verificada 06/08/2026 con datos oficiales de IBKR, get_price_history):
# el "bad tick" NO se confirmo -- el 05/08 ANET tuvo un dia real y
# extraordinariamente volatil (apertura 210,19, high oficial 214,89,
# cierre 197,31, volumen 2-3x lo normal, probable reaccion a resultados).
# El high de 214,89 era un dato REAL, no corrupto. calcular_trailing_stop()
# confiaba en
# df["high"].iloc[-1] sin validar que fuera coherente con la volatilidad
# reciente. Estas guardias rechazan el ciclo (se conserva el stop GTC
# vigente y se alerta) en vez de trasladar un dato corrupto a una orden
# real. Ver 00_LIBERTAD2045_CONTEXT.txt para el análisis completo.
# --------------------------------------------------

ATR_RATIO_MIN         = 0.3   # ATR no puede caer a menos del 30% del ciclo anterior en un día
ATR_RATIO_MAX          = 3.0  # ATR no puede multiplicarse por más de 3x en un día
MIN_STOP_DISTANCE_ATR  = 1.0  # El stop nunca puede quedar a menos de 1×ATR del cierre de hoy


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
        symbol         : ticker, opcional — solo para identificar el activo en logs/alertas

    Incluye guardias anti-outlier (ver comentario de cabecera del módulo): si el
    ATR salta de forma extrema respecto al ciclo anterior, o si el stop resultante
    queda a menos de 1×ATR del cierre, el ciclo se descarta y se alerta en vez de
    devolver un stop no confiable.

    Retorna (nuevo_stop, mult) o (None, None) si los datos son insuficientes o
    si una guardia anti-outlier descarta el ciclo.
    """
    import pandas as pd
    try:
        atr_val       = df["ATR"].iloc[-1]
        percentil_val = df["ATR_PERCENTIL"].iloc[-1]
        high_hoy      = df["high"].iloc[-1]
        close_hoy     = df["close"].iloc[-1]

        if pd.isna(atr_val) or pd.isna(percentil_val) or atr_val <= 0:
            return None, None

        # ── Guardia 1: salto extremo de ATR entre ciclos consecutivos ──
        if len(df) >= 2:
            atr_prev = df["ATR"].iloc[-2]
            if not pd.isna(atr_prev) and atr_prev > 0:
                ratio = float(atr_val) / float(atr_prev)
                if ratio < ATR_RATIO_MIN or ratio > ATR_RATIO_MAX:
                    _alertar_anomalia_trailing(
                        "ATR_SALTO",
                        f"ATR paso de {atr_prev:.2f} a {atr_val:.2f} ({ratio:.2f}x) en un ciclo",
                        symbol=symbol,
                    )
                    return None, None

        mult       = round((B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * float(percentil_val)) * trailing_factor, 2)
        nuevo_stop = round(float(high_hoy) - float(atr_val) * mult, 2)

        # ── Guardia 2: el stop no puede quedar pegado al precio ──
        if not pd.isna(close_hoy):
            distancia = float(close_hoy) - nuevo_stop
            if distancia < MIN_STOP_DISTANCE_ATR * float(atr_val):
                _alertar_anomalia_trailing(
                    "STOP_DEMASIADO_CERCA",
                    f"stop={nuevo_stop:.2f} a solo {distancia:.2f} del cierre={close_hoy:.2f} "
                    f"(ATR={atr_val:.2f}, high={high_hoy:.2f}, mult={mult})",
                    symbol=symbol,
                )
                return None, None

        return nuevo_stop, mult

    except Exception:
        return None, None
