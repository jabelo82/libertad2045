"""
LIBERTAD_2045 — Backtest Experimento 45
=========================================
Piramidación — Matriz completa (6 variantes + baseline)

Diseño base (ya cerrado):
  Activación : posición abierta, sin señal de cierre,
               close_hoy >= entry_pos1 + K×ATR_hoy,
               flag pyramided=False, capital > 0, Risk Guardian activo.
               No consume slot (ampliación de la misma posición).
               Solo una vez por posición.
  Tamaño pos2: riesgo combinado ≤ RISK_PERCENT del capital.
               Como el trailing ya bloqueó el riesgo de pos1 (≈ 0),
               el presupuesto queda casi libre.
               shares_2 = riesgo_libre / stop_distance_2,
               limitado por MAX_POSITION_PCT.
  Stop comb. : trailing actual de pos1 (el más favorable, solo sube).
  Entry blend: (entry1 × sh1 + price2 × sh2) / (sh1 + sh2).

Variantes (K × timing):
  A1: K=2.5, cierre día activación
  A2: K=2.5, buy-stop día siguiente (High + BUFFER)
  B1: K=3.0, cierre día activación
  B2: K=3.0, buy-stop día siguiente (High + BUFFER)
  C1: K=4.0, cierre día activación
  C2: K=4.0, buy-stop día siguiente (High + BUFFER)

Buffer buy-stop: BUFFER = 0.05 USD fijo, idéntico al motor base
(backtest_expandido.py línea 54). El buy-stop vive solo el día
siguiente — si no se ejecuta ese día, se cancela.

Universo y parámetros: idénticos a baseline v3 (backtest_expandido.py).

Restricciones:
  · Variantes con DD > 15% marcadas como "NO VIABLE".
  · El motor base NO se modifica — utilidades importadas directamente.
  · Naming, formato logs y estructura de resultados consistentes con el proyecto.

Uso:
    cd ~/PROYECTO_LIBERTAD_2045
    venv/bin/python backtest_exp45.py

Tiempo estimado con caché: ~7-20 minutos (7 variantes × período completo).
"""

import warnings
warnings.filterwarnings("ignore")

import os
import sys
from datetime import datetime

import pandas as pd
import numpy as np


# ==================================================
# MOTOR BASE — importar sin duplicar código
# ==================================================
# Todas las funciones, constantes y universo del motor base se importan
# directamente de backtest_expandido.py (baseline v3).
# EXP45 solo añade la lógica de piramidación encima del motor.

import backtest_expandido as _motor


# ==================================================
# PARÁMETROS EXP45
# ==================================================

PYRAMID_MAX_DD = 0.15   # DD > 15% → variante marcada como NO VIABLE

# Matriz de variantes: (nombre, K, timing)
# K=None → baseline sin piramidación
VARIANTES = [
    ("BASELINE", None,  None),
    ("A1",       2.5,  "close"),
    ("A2",       2.5,  "buystop"),
    ("B1",       3.0,  "close"),
    ("B2",       3.0,  "buystop"),
    ("C1",       4.0,  "close"),
    ("C2",       4.0,  "buystop"),
]


# ==================================================
# MOTOR CON PIRAMIDACIÓN
# ==================================================

def ejecutar_backtest_exp45(datos, composicion_df, K, timing):
    """
    Motor del backtest con piramidación (EXP45).

    Réplica exacta del motor base (backtest_expandido.ejecutar_backtest)
    con la lógica de piramidación insertada en el paso 6.5, entre el
    rebalanceo y el escaneo de señales nuevas.

    Parámetros
    ----------
    datos         : dict {symbol: DataFrame} — de _motor.descargar_datos()
    composicion_df: DataFrame composición histórica S&P500
    K             : float — ATR multiplier de activación
                    (close >= entry_pos1 + K × ATR)
    timing        : "close"   → ejecutar al cierre del día de activación
                    "buystop" → buy-stop día siguiente a High + BUFFER;
                                si no se ejecuta ese día, se cancela.

    Retorna
    -------
    (trades, curva_capital, capital_final)
    Los dicts de trade incluyen campos adicionales:
      pyramided   : bool
      pnl_pos1    : float (PnL del tramo pos1)
      pnl_pos2    : float (PnL del tramo pos2, 0 si no piramidada)
    """

    # Constantes del motor base (leídas del módulo para garantizar coherencia)
    CAPITAL_INICIAL      = _motor.CAPITAL_INICIAL
    APORTACION_ANUAL     = _motor.APORTACION_ANUAL
    RISK_PERCENT         = _motor.RISK_PERCENT
    MAX_POSITION_PCT     = _motor.MAX_POSITION_PCT
    MAX_POSITIONS        = _motor.MAX_POSITIONS
    BUFFER               = _motor.BUFFER
    TRAILING_FACTOR      = _motor.TRAILING_FACTOR
    SALIDA_POR_CIERRE    = _motor.SALIDA_POR_CIERRE
    RISK_MIN_CAPITAL     = _motor.RISK_MIN_CAPITAL
    RISK_MAX_DRAWDOWN    = _motor.RISK_MAX_DRAWDOWN
    REBALANCE_THRESHOLD  = _motor.REBALANCE_THRESHOLD
    REBALANCE_MIN_SHARES = _motor.REBALANCE_MIN_SHARES

    if composicion_df is None:
        composicion_df = pd.DataFrame()

    print(f"\n  Ejecutando backtest...")
    print(f"  K={K} | timing={timing}")
    print(f"  Risk Guardian: capital_min={RISK_MIN_CAPITAL:.0f}€ | dd_max={RISK_MAX_DRAWDOWN:.0%}")
    universo_dinamico = not composicion_df.empty
    print(f"  Universo dinámico : {'SÍ (survivorship bias eliminado)' if universo_dinamico else 'NO (estático)'}")
    print()

    fechas = sorted(set(
        fecha
        for df in datos.values()
        for fecha in df.index
    ))

    capital       = CAPITAL_INICIAL
    capital_pico  = CAPITAL_INICIAL
    posiciones    = {}
    trades        = []
    curva_capital = []

    dias_bloqueados_drawdown  = 0
    dias_detenido_capital     = 0
    rebalanceos_ejecutados    = 0
    piramidaciones_ejecutadas = 0

    # pending_pyramids: symbol → {buy_stop, shares_2}
    # Solo para timing="buystop". Vive un único día (se cancela si no ejecuta).
    pending_pyramids = {}

    for idx, fecha in enumerate(fechas):

        # --------------------------------------------------
        # 0. Ejecutar buy-stops de piramidación pendientes
        #    Solo timing="buystop". Se procesa ANTES de los
        #    trailing stops para que la posición ampliada sea
        #    la que recibe la actualización del stop ese día.
        # --------------------------------------------------
        if timing == "buystop" and pending_pyramids:
            for symbol in list(pending_pyramids.keys()):
                pyr = pending_pyramids.pop(symbol)  # consume en cualquier caso

                if symbol not in posiciones:
                    continue

                pos = posiciones[symbol]
                if pos.get("pyramided", False):
                    continue

                df = datos.get(symbol)
                if df is None or fecha not in df.index:
                    continue

                bar    = df.loc[fecha]
                buy_st = pyr["buy_stop"]
                sh2    = pyr["shares_2"]

                if bar["High"] < buy_st:
                    continue  # buy-stop no ejecutado — se cancela

                price_2 = buy_st
                coste_2 = sh2 * price_2
                if coste_2 > capital or sh2 <= 0:
                    continue

                sh1         = pos["shares"]
                entry1      = pos.get("entry_pos1", pos["entry"])
                entry_blend = (entry1 * sh1 + price_2 * sh2) / (sh1 + sh2)

                pos["entry_pos1"]  = entry1
                pos["shares_pos1"] = sh1
                pos["price_pos2"]  = price_2
                pos["shares_pos2"] = sh2
                pos["entry"]       = round(entry_blend, 4)
                pos["shares"]      = sh1 + sh2
                pos["pyramided"]   = True

                piramidaciones_ejecutadas += 1

        # --------------------------------------------------
        # 1. Aportación anual — primer día de trading del año
        # --------------------------------------------------
        if idx > 0 and fecha.year > fechas[idx - 1].year:
            capital += APORTACION_ANUAL
            if capital > capital_pico:
                capital_pico = capital
            print(f"  Aportación anual: +{APORTACION_ANUAL:.0f}€ → capital: {capital:.2f}€")

        # --------------------------------------------------
        # 2. Actualizar capital pico
        # --------------------------------------------------
        if capital > capital_pico:
            capital_pico = capital

        # --------------------------------------------------
        # 3. Risk Guardian — capital mínimo
        # --------------------------------------------------
        if capital < RISK_MIN_CAPITAL:
            dias_detenido_capital += 1
            pending_pyramids.clear()
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 4. Gestionar posiciones con trailing stop
        # --------------------------------------------------
        cerradas = []

        for symbol, pos in posiciones.items():

            if symbol not in datos:
                continue

            df = datos[symbol]

            if fecha not in df.index:
                continue

            bar      = df.loc[fecha]
            atr      = bar["ATR"]
            i_actual = df.index.get_loc(fecha)

            # Actualizar trailing stop (solo sube)
            if not pd.isna(atr) and atr > 0:
                mult       = _motor.obtener_multiplicador(df, i_actual)
                nuevo_stop = round(bar["High"] - atr * mult * TRAILING_FACTOR, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop

            # Break-even — idéntico al motor base
            if not pd.isna(atr) and atr > 0:
                be_stop = round(pos["entry"] + 0.5 * atr, 2)
                if bar["Close"] >= pos["entry"] + 1.5 * atr and be_stop > pos["stop"]:
                    pos["stop"] = be_stop

            # Palanca 2B — salida por cierre
            precio_ref = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]

            if precio_ref <= pos["stop"]:

                precio_salida = pos["stop"]

                # Descomponer PnL pos1 / pos2
                if pos.get("pyramided", False):
                    sh1    = pos["shares_pos1"]
                    entry1 = pos["entry_pos1"]
                    sh2    = pos["shares_pos2"]
                    price2 = pos["price_pos2"]
                    pnl_p1 = (precio_salida - entry1) * sh1
                    pnl_p2 = (precio_salida - price2) * sh2
                    pnl    = pnl_p1 + pnl_p2
                else:
                    pnl_p1 = (precio_salida - pos["entry"]) * pos["shares"]
                    pnl_p2 = 0.0
                    pnl    = pnl_p1

                capital += pnl

                trades.append({
                    "symbol"        : symbol,
                    "clase"         : pos["clase"],
                    "fecha_entrada" : pos["fecha_entrada"],
                    "fecha_salida"  : fecha,
                    "entrada"       : round(pos["entry"], 4),
                    "salida"        : round(precio_salida, 4),
                    "shares"        : pos["shares"],
                    "pnl"           : round(pnl, 2),
                    "pnl_pos1"      : round(pnl_p1, 2),
                    "pnl_pos2"      : round(pnl_p2, 2),
                    "resultado"     : "LOSS" if pnl < 0 else "WIN",
                    "pyramided"     : pos.get("pyramided", False),
                    "capital"       : round(capital, 2),
                })

                cerradas.append(symbol)

        for symbol in cerradas:
            del posiciones[symbol]
            pending_pyramids.pop(symbol, None)

        # --------------------------------------------------
        # 5. Risk Guardian — drawdown máximo
        # --------------------------------------------------
        drawdown_actual = (
            (capital_pico - capital) / capital_pico if capital_pico > 0 else 0
        )

        if drawdown_actual > RISK_MAX_DRAWDOWN:
            dias_bloqueados_drawdown += 1
            pending_pyramids.clear()
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 6. Rebalanceo dinámico — idéntico al motor base
        # --------------------------------------------------
        for symbol in list(posiciones.keys()):

            if symbol not in datos:
                continue

            df_reb = datos[symbol]

            if fecha not in df_reb.index:
                continue

            pos      = posiciones[symbol]
            i_reb    = df_reb.index.get_loc(fecha)
            precio   = df_reb.iloc[i_reb]["Close"]

            if pd.isna(precio) or precio <= 0:
                continue

            shares_actual = pos["shares"]
            valor_actual  = shares_actual * precio
            limite_valor  = capital * MAX_POSITION_PCT

            # Protección MAX_POSITION_PCT
            if capital > 0 and valor_actual > limite_valor:
                shares_limite = int(limite_valor / precio)
                delta_lim     = shares_limite - shares_actual
                if abs(delta_lim) >= REBALANCE_MIN_SHARES and shares_limite > 0:
                    shares_vendidas = shares_actual - shares_limite
                    pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                    capital        += pnl_parcial
                    pos["shares"]   = shares_limite
                    # Ajuste proporcional de pos1/pos2 si fue piramidada
                    if pos.get("pyramided", False):
                        ratio          = shares_limite / max(1, shares_actual)
                        new_sh1        = max(1, round(pos["shares_pos1"] * ratio))
                        pos["shares_pos1"] = min(new_sh1, shares_limite)
                        pos["shares_pos2"] = max(0, shares_limite - pos["shares_pos1"])
                    rebalanceos_ejecutados += 1
                    continue

            shares_optimo, _, _ = _motor.calcular_posicion(df_reb, i_reb, capital)
            if shares_optimo <= 0:
                continue

            desviacion = (shares_actual - shares_optimo) / shares_optimo
            if abs(desviacion) <= REBALANCE_THRESHOLD:
                continue

            delta = shares_optimo - shares_actual
            if abs(delta) < REBALANCE_MIN_SHARES:
                continue

            if delta < 0:
                shares_vendidas = -delta
                pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                capital        += pnl_parcial
                pos["shares"]   = shares_optimo
                if pos.get("pyramided", False):
                    ratio          = shares_optimo / max(1, shares_actual)
                    new_sh1        = max(0, round(pos["shares_pos1"] * ratio))
                    pos["shares_pos1"] = min(new_sh1, shares_optimo)
                    pos["shares_pos2"] = max(0, shares_optimo - pos["shares_pos1"])
            else:
                entry_blended = (
                    (pos["entry"] * shares_actual + precio * delta) / shares_optimo
                )
                pos["shares"] = shares_optimo
                pos["entry"]  = round(entry_blended, 4)

            rebalanceos_ejecutados += 1

        # --------------------------------------------------
        # 6.5. Piramidación — evaluar posiciones abiertas
        #      Se ejecuta después del rebalanceo y antes del
        #      escaneo de señales nuevas. El Risk Guardian ya
        #      está validado en este punto.
        # --------------------------------------------------
        for symbol, pos in posiciones.items():

            if pos.get("pyramided", False):
                continue  # ya piramidada — una sola vez por posición

            df = datos.get(symbol)
            if df is None or fecha not in df.index:
                continue

            bar   = df.loc[fecha]
            atr   = bar["ATR"]
            close = bar["Close"]

            if pd.isna(atr) or atr <= 0 or pd.isna(close) or close <= 0:
                continue

            entry1 = pos.get("entry_pos1", pos["entry"])

            # Condición de activación: close >= entry_pos1 + K × ATR
            if close < entry1 + K * atr:
                continue

            # Stop actual del trailing (stop combinado si se piramida)
            current_stop = pos["stop"]

            if timing == "close":
                price_2 = close
            else:
                price_2 = round(bar["High"] + BUFFER, 4)

            stop_dist_2 = price_2 - current_stop
            if stop_dist_2 <= 0:
                continue

            risk_budget = capital * RISK_PERCENT
            sh2_risk    = int(risk_budget / stop_dist_2)
            sh2_cap     = int(capital * MAX_POSITION_PCT / price_2)
            shares_2    = min(sh2_risk, sh2_cap)

            if shares_2 <= 0:
                continue

            # Guard: coste no supera el capital disponible
            if shares_2 * price_2 > capital:
                shares_2 = int(capital / price_2)
                if shares_2 <= 0:
                    continue

            if timing == "close":
                # Ejecutar inmediatamente al cierre
                sh1         = pos["shares"]
                entry_blend = (entry1 * sh1 + price_2 * shares_2) / (sh1 + shares_2)

                pos["entry_pos1"]  = entry1
                pos["shares_pos1"] = sh1
                pos["price_pos2"]  = price_2
                pos["shares_pos2"] = shares_2
                pos["entry"]       = round(entry_blend, 4)
                pos["shares"]      = sh1 + shares_2
                pos["pyramided"]   = True

                piramidaciones_ejecutadas += 1

            else:  # "buystop"
                # Registrar buy-stop para el día siguiente
                # (se ejecutará en el paso 0 de la siguiente iteración)
                pending_pyramids[symbol] = {
                    "buy_stop" : price_2,
                    "shares_2" : shares_2,
                }

        # --------------------------------------------------
        # 7. Portfolio lleno — no escanear señales nuevas
        # --------------------------------------------------
        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 8. Escanear señales
        # --------------------------------------------------
        sp500_hoy = _motor.sp500_en_fecha(composicion_df, fecha)

        señales = []

        for symbol in datos:

            if symbol in posiciones:
                continue

            if sp500_hoy is not None and symbol not in sp500_hoy:
                continue

            df = datos[symbol]

            if fecha not in df.index:
                continue

            i = df.index.get_loc(fecha)

            if i < 200:
                continue

            if not _motor.detectar_senal(df, i):
                continue

            shares, stop_distance, atr = _motor.calcular_posicion(df, i, capital)

            if shares <= 0:
                continue

            bar         = df.iloc[i]
            _sma200_5d  = df.iloc[i - 5]["SMA200"] if i >= 6 else float("nan")
            _sma200_sl  = (
                (bar["SMA200"] - _sma200_5d) / bar["ATR"]
                if not pd.isna(_sma200_5d) else 0.0
            )
            score = (bar["Close"] - bar["SMA50"]) / bar["ATR"] + _sma200_sl

            if symbol in _motor.CRYPTO:
                clase = "CRIPTO"
            elif symbol in _motor.MATERIAS_PRIMAS:
                clase = "MATERIA_PRIMA"
            elif symbol in _motor.ETFS:
                clase = "ETF"
            else:
                clase = "ACCION"

            señales.append({
                "symbol"        : symbol,
                "clase"         : clase,
                "score"         : score,
                "shares"        : shares,
                "stop_distance" : stop_distance,
                "high"          : bar["High"],
                "atr"           : atr,
            })

        señales      = sorted(señales, key=lambda x: x["score"], reverse=True)
        slots_libres = MAX_POSITIONS - len(posiciones)
        señales      = señales[:slots_libres]

        # --------------------------------------------------
        # 9. Abrir posiciones al día siguiente
        # --------------------------------------------------
        if idx + 1 < len(fechas):

            fecha_entrada = fechas[idx + 1]

            for señal in señales:

                symbol    = señal["symbol"]
                buy_stop  = round(señal["high"] + BUFFER, 4)
                stop_loss = round(buy_stop - señal["stop_distance"], 4)

                if stop_loss <= 0:
                    continue

                df = datos[symbol]

                if fecha_entrada not in df.index:
                    continue

                bar_entrada = df.loc[fecha_entrada]

                if bar_entrada["High"] >= buy_stop:

                    coste = buy_stop * señal["shares"]

                    if coste > capital:
                        continue

                    posiciones[symbol] = {
                        "entry"        : buy_stop,
                        "stop"         : stop_loss,
                        "shares"       : señal["shares"],
                        "clase"        : señal["clase"],
                        "fecha_entrada": fecha_entrada,
                        "pyramided"    : False,
                    }

        curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})

    # --------------------------------------------------
    # Cerrar posiciones abiertas al final del período
    # --------------------------------------------------
    for symbol, pos in posiciones.items():

        df = datos[symbol]

        if df.empty:
            continue

        ultimo_cierre = df.iloc[-1]["Close"]

        if pos.get("pyramided", False):
            sh1    = pos["shares_pos1"]
            entry1 = pos["entry_pos1"]
            sh2    = pos["shares_pos2"]
            price2 = pos["price_pos2"]
            pnl_p1 = (ultimo_cierre - entry1) * sh1
            pnl_p2 = (ultimo_cierre - price2) * sh2
            pnl    = pnl_p1 + pnl_p2
        else:
            pnl_p1 = (ultimo_cierre - pos["entry"]) * pos["shares"]
            pnl_p2 = 0.0
            pnl    = pnl_p1

        capital += pnl

        trades.append({
            "symbol"        : symbol,
            "clase"         : pos["clase"],
            "fecha_entrada" : pos["fecha_entrada"],
            "fecha_salida"  : df.index[-1],
            "entrada"       : round(pos["entry"], 4),
            "salida"        : round(ultimo_cierre, 4),
            "shares"        : pos["shares"],
            "pnl"           : round(pnl, 2),
            "pnl_pos1"      : round(pnl_p1, 2),
            "pnl_pos2"      : round(pnl_p2, 2),
            "resultado"     : "OPEN→CLOSE",
            "pyramided"     : pos.get("pyramided", False),
            "capital"       : round(capital, 2),
        })

    print(f"\n  Risk Guardian — resumen:")
    print(f"    Días bloqueados por drawdown : {dias_bloqueados_drawdown}")
    print(f"    Días detenido por capital    : {dias_detenido_capital}")
    print(f"    Rebalanceos ejecutados       : {rebalanceos_ejecutados}")
    print(f"    Piramidaciones ejecutadas    : {piramidaciones_ejecutadas}")

    return trades, curva_capital, capital


# ==================================================
# MÉTRICAS EXTENDIDAS
# ==================================================

def calcular_metricas_exp45(trades, curva_capital, capital_final, nombre=""):
    """
    Calcula métricas completas compatibles con trades del motor base y EXP45.
    Añade CAGR, Sharpe, Calmar y estadísticas de piramidación.
    """

    if not trades:
        return {"nombre": nombre, "capital_final": round(capital_final, 2)}

    df_trades  = pd.DataFrame(trades)
    df_capital = pd.DataFrame(curva_capital)

    total_trades = len(df_trades)
    wins   = df_trades[
        (df_trades["resultado"] == "WIN") |
        ((df_trades["resultado"] == "OPEN→CLOSE") & (df_trades["pnl"] >= 0))
    ]
    losses = df_trades[
        (df_trades["resultado"] == "LOSS") |
        ((df_trades["resultado"] == "OPEN→CLOSE") & (df_trades["pnl"] < 0))
    ]

    win_rate      = len(wins) / total_trades if total_trades > 0 else 0
    ganancia      = wins["pnl"].sum()         if len(wins)   > 0 else 0.0
    perdida       = losses["pnl"].abs().sum() if len(losses) > 0 else 1.0
    profit_factor = ganancia / perdida        if perdida > 0 else float("inf")

    # Max drawdown desde curva de capital
    cap_arr  = df_capital["capital"].values
    pico     = cap_arr[0]
    max_dd   = 0.0
    for c in cap_arr:
        if c > pico:
            pico = c
        dd = (pico - c) / pico if pico > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # CAGR — misma fórmula que el proyecto: (final/inicial)^(1/años) - 1
    years = (
        pd.Timestamp(_motor.END_DATE) - pd.Timestamp(_motor.START_DATE)
    ).days / 365.25
    if _motor.CAPITAL_INICIAL > 0 and capital_final > 0 and years > 0:
        cagr = (capital_final / _motor.CAPITAL_INICIAL) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # Sharpe diario annualizado
    ret = pd.Series(cap_arr).pct_change().dropna()
    sharpe = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0

    # Calmar = CAGR / max_drawdown
    calmar = (cagr / max_dd) if max_dd > 0 else float("inf")

    # Estadísticas de piramidación
    has_pyramid_col = "pyramided" in df_trades.columns
    if has_pyramid_col:
        df_pyr       = df_trades[df_trades["pyramided"] == True]
        n_pyramided  = len(df_pyr)
        pct_pyramided = n_pyramided / total_trades if total_trades > 0 else 0.0
    else:
        n_pyramided   = 0
        pct_pyramided = 0.0

    has_pnl_cols = "pnl_pos2" in df_trades.columns and "pnl_pos1" in df_trades.columns
    if has_pnl_cols:
        pnl_pos1_total = df_trades["pnl_pos1"].sum()
        pnl_pos2_total = df_trades["pnl_pos2"].sum()
    else:
        pnl_pos1_total = df_trades["pnl"].sum()
        pnl_pos2_total = 0.0

    return {
        "nombre"         : nombre,
        "capital_final"  : round(capital_final, 2),
        "cagr"           : cagr,
        "win_rate"       : win_rate,
        "profit_factor"  : profit_factor,
        "max_drawdown"   : max_dd,
        "sharpe"         : sharpe,
        "calmar"         : calmar,
        "total_trades"   : total_trades,
        "n_pyramided"    : n_pyramided,
        "pct_pyramided"  : pct_pyramided,
        "pnl_pos1_total" : round(pnl_pos1_total, 2),
        "pnl_pos2_total" : round(pnl_pos2_total, 2),
        "viable"         : max_dd <= PYRAMID_MAX_DD,
    }


# ==================================================
# TABLA COMPARATIVA EN CONSOLA
# ==================================================

def imprimir_tabla_comparativa(resultados):
    """Imprime tabla comparativa de todas las variantes en consola."""

    sep  = "─" * 130
    sep2 = "═" * 130

    print(f"\n{sep2}")
    print(f"  LIBERTAD_2045 — EXP45: TABLA COMPARATIVA DE PIRAMIDACIÓN")
    print(f"  Período: {_motor.START_DATE} → {_motor.END_DATE}  |  Universo: baseline v3 (S&P500)")
    print(f"  DD no viable: >{PYRAMID_MAX_DD:.0%}  |  Buffer buy-stop: {_motor.BUFFER} USD")
    print(sep2)

    header = (
        f"  {'Variante':<10} "
        f"{'Capital €':>12} "
        f"{'CAGR':>7} "
        f"{'WR':>6} "
        f"{'PF':>6} "
        f"{'DD':>7} "
        f"{'Sharpe':>7} "
        f"{'Calmar':>7} "
        f"{'Trades':>7} "
        f"{'Pir.':>5} "
        f"{'%Pir.':>6} "
        f"{'PnL pos1 €':>12} "
        f"{'PnL pos2 €':>12} "
        f"{'Viable':>9}"
    )
    print(header)
    print(sep)

    for nombre, m in resultados:
        viable_str = "SI" if m.get("viable", True) else "✗ NO VIABLE"
        marker     = " *" if nombre == "BASELINE" else "  "
        print(
            f"{marker}{nombre:<10} "
            f"{m['capital_final']:>12,.0f} "
            f"{m['cagr']:>7.1%} "
            f"{m['win_rate']:>6.1%} "
            f"{m['profit_factor']:>6.3f} "
            f"{m['max_drawdown']:>7.1%} "
            f"{m['sharpe']:>7.2f} "
            f"{m['calmar']:>7.2f} "
            f"{m['total_trades']:>7d} "
            f"{m['n_pyramided']:>5d} "
            f"{m['pct_pyramided']:>6.1%} "
            f"{m['pnl_pos1_total']:>12,.0f} "
            f"{m['pnl_pos2_total']:>12,.0f} "
            f"{viable_str:>9}"
        )

    print(sep2)
    print(f"  * = baseline sin piramidación")
    print()


# ==================================================
# REPORTE MARKDOWN
# ==================================================

def generar_reporte_markdown(resultados):
    """
    Genera reporte markdown con tabla, análisis cuantitativo y
    recomendación de qué variante (si alguna) llevar a producción.
    """

    os.makedirs(_motor.LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath  = os.path.join(_motor.LOG_DIR, f"exp45_reporte_{timestamp}.md")

    dict_res   = {n: m for n, m in resultados}
    baseline_m = dict_res.get("BASELINE", {})

    viables = [
        (n, m) for n, m in resultados
        if m.get("viable", True) and n != "BASELINE"
    ]
    no_viables = [
        (n, m) for n, m in resultados
        if not m.get("viable", True)
    ]

    def _gain(m, key):
        bl_val = baseline_m.get(key, 0)
        return m.get(key, 0) - bl_val

    lines = [
        "# LIBERTAD_2045 — Backtest EXP45: Piramidación",
        "",
        f"**Período:** {_motor.START_DATE} → {_motor.END_DATE}",
        f"**Universo:** Baseline v3 — S&P500 histórico (~{len(_motor.SP500)} tickers)",
        f"**Parámetros estrategia:** Idénticos al motor base (backtest_expandido.py)",
        f"**Buffer buy-stop:** {_motor.BUFFER} USD fijo (igual que el motor base)",
        f"**Criterio no viable:** DD > {PYRAMID_MAX_DD:.0%}",
        f"**Generado:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 1. Tabla Comparativa",
        "",
        "| Variante | Capital Final | CAGR | WR | PF | DD | Sharpe | Calmar | Trades | Pir. | % Pir. | PnL pos2 | Viable |",
        "|:--------:|-------------:|-----:|---:|---:|---:|-------:|-------:|-------:|-----:|-------:|---------:|:------:|",
    ]

    for nombre, m in resultados:
        viable_str = "✓" if m.get("viable", True) else "✗"
        baseline_marker = " \\*" if nombre == "BASELINE" else ""
        lines.append(
            f"| **{nombre}**{baseline_marker} "
            f"| {m['capital_final']:,.0f} € "
            f"| {m['cagr']:.1%} "
            f"| {m['win_rate']:.1%} "
            f"| {m['profit_factor']:.3f} "
            f"| {m['max_drawdown']:.1%} "
            f"| {m['sharpe']:.2f} "
            f"| {m['calmar']:.2f} "
            f"| {m['total_trades']} "
            f"| {m['n_pyramided']} "
            f"| {m['pct_pyramided']:.1%} "
            f"| {m['pnl_pos2_total']:,.0f} € "
            f"| {viable_str} |"
        )

    lines += [
        "",
        "_\\* Baseline sin piramidación — referencia._",
        "",
        "---",
        "",
        "## 2. Variantes No Viables (DD > 15%)",
        "",
    ]

    if no_viables:
        for n, m in no_viables:
            dd_delta = m['max_drawdown'] - baseline_m.get('max_drawdown', 0)
            lines.append(
                f"- **{n}**: DD = {m['max_drawdown']:.1%} "
                f"(+{dd_delta:.1%} sobre baseline) — descartada."
            )
    else:
        lines.append(
            "Ninguna variante superó el DD máximo del 15%. "
            "Todas las variantes son técnicamente viables."
        )

    lines += [
        "",
        "---",
        "",
        "## 3. Análisis Cuantitativo",
        "",
        "### 3.1 CAGR ajustado por DD",
        "",
    ]

    if viables:
        viables_cagr_dd = sorted(
            viables, key=lambda x: x[1]["cagr"] / max(x[1]["max_drawdown"], 0.001),
            reverse=True
        )
        best_n, best_m = viables_cagr_dd[0]
        lines += [
            f"Mejor ratio CAGR/DD: **{best_n}**",
            f"  - CAGR = {best_m['cagr']:.1%} ({_gain(best_m,'cagr'):+.1%} vs baseline)",
            f"  - DD   = {best_m['max_drawdown']:.1%} ({_gain(best_m,'max_drawdown'):+.1%} vs baseline)",
            f"  - Ratio CAGR/DD = {best_m['cagr']/max(best_m['max_drawdown'],0.001):.2f}x",
            f"  - Ratio baseline = {baseline_m.get('cagr',0)/max(baseline_m.get('max_drawdown',0.001),0.001):.2f}x",
            "",
        ]
    else:
        lines.append("Sin variantes viables para comparar.")

    lines += [
        "### 3.2 Mejor Sharpe",
        "",
    ]

    if viables:
        viables_sharpe = sorted(viables, key=lambda x: x[1]["sharpe"], reverse=True)
        bn, bm = viables_sharpe[0]
        lines += [
            f"**{bn}**: Sharpe = {bm['sharpe']:.2f} "
            f"(delta vs baseline: {_gain(bm,'sharpe'):+.2f})",
            "",
        ]

    lines += [
        "### 3.3 Mejor Calmar",
        "",
    ]

    if viables:
        viables_calmar = sorted(viables, key=lambda x: x[1]["calmar"], reverse=True)
        bn, bm = viables_calmar[0]
        lines += [
            f"**{bn}**: Calmar = {bm['calmar']:.2f} "
            f"(delta vs baseline: {_gain(bm,'calmar'):+.2f})",
            "",
        ]

    lines += [
        "### 3.4 Contribución del tramo pos2 al PnL total",
        "",
    ]

    for nombre, m in resultados:
        if nombre == "BASELINE" or m["n_pyramided"] == 0:
            continue
        total_pnl = m["pnl_pos1_total"] + m["pnl_pos2_total"]
        pct_p2    = (m["pnl_pos2_total"] / total_pnl * 100) if total_pnl != 0 else 0.0
        lines.append(
            f"- **{nombre}**: {m['n_pyramided']} trades piramidados "
            f"({m['pct_pyramided']:.1%} del total) · "
            f"PnL pos2 = {m['pnl_pos2_total']:,.0f} € "
            f"({pct_p2:.1f}% del PnL total)"
        )

    lines += [
        "",
        "### 3.5 ¿La piramidación añade valor o es ruido?",
        "",
    ]

    if viables:
        best_sharpe_n, best_sharpe_m = sorted(
            viables, key=lambda x: x[1]["sharpe"], reverse=True
        )[0]
        sharpe_delta = _gain(best_sharpe_m, "sharpe")
        cagr_delta   = _gain(best_sharpe_m, "cagr")
        dd_delta     = _gain(best_sharpe_m, "max_drawdown")

        if sharpe_delta > 0.10 or cagr_delta > 0.02:
            lines += [
                f"La piramidación **añade valor estadísticamente relevante**. "
                f"La variante **{best_sharpe_n}** mejora el Sharpe en {sharpe_delta:+.2f} "
                f"y el CAGR en {cagr_delta:+.1%} sobre la baseline.",
                f"El DD adicional es de {dd_delta:+.1%}.",
            ]
        elif sharpe_delta < -0.05 or cagr_delta < -0.02:
            lines += [
                f"La piramidación **deteriora** las métricas respecto a la baseline. "
                f"Incluso la mejor variante ({best_sharpe_n}) tiene Sharpe {sharpe_delta:+.2f} "
                f"y CAGR {cagr_delta:+.1%}.",
                "Conclusión: la piramidación es contraproducente en este universo y período.",
            ]
        else:
            lines += [
                f"La piramidación es **neutra** — las diferencias ({sharpe_delta:+.2f} Sharpe, "
                f"{cagr_delta:+.1%} CAGR) están dentro del margen de ruido estadístico.",
                "No justifica el coste operativo adicional.",
            ]
    else:
        lines.append(
            "Sin variantes viables — la piramidación no es recomendable "
            "con ninguna configuración de las probadas."
        )

    lines += [
        "",
        "---",
        "",
        "## 4. Recomendación",
        "",
    ]

    if not viables:
        lines += [
            "**No recomendar piramidación.** Todas las variantes superan el DD límite del 15%.",
            "",
            "Mantener baseline v3 sin piramidación.",
        ]
    elif viables:
        viables_sharpe = sorted(viables, key=lambda x: x[1]["sharpe"], reverse=True)
        top_n, top_m   = viables_sharpe[0]
        sharpe_delta   = _gain(top_m, "sharpe")
        cagr_delta     = _gain(top_m, "cagr")
        dd_delta       = _gain(top_m, "max_drawdown")

        if sharpe_delta > 0.10 or cagr_delta > 0.02:
            lines += [
                f"**Candidata para producción: variante {top_n}**",
                "",
                f"| Métrica | Baseline | {top_n} | Delta |",
                f"|---------|---------|-------|-------|",
                f"| CAGR    | {baseline_m.get('cagr',0):.1%} | {top_m['cagr']:.1%} | {cagr_delta:+.1%} |",
                f"| DD      | {baseline_m.get('max_drawdown',0):.1%} | {top_m['max_drawdown']:.1%} | {dd_delta:+.1%} |",
                f"| Sharpe  | {baseline_m.get('sharpe',0):.2f} | {top_m['sharpe']:.2f} | {sharpe_delta:+.2f} |",
                f"| Calmar  | {baseline_m.get('calmar',0):.2f} | {top_m['calmar']:.2f} | {_gain(top_m,'calmar'):+.2f} |",
                "",
                "Pasos sugeridos antes de llevar a producción:",
                "1. Validar en walk-forward (ventanas out-of-sample).",
                "2. Paper trading con capital PAPER durante 1-2 meses.",
                "3. Confirmar que el coste operativo (comisiones, slippage) no erosiona la ventaja.",
            ]
        else:
            lines += [
                "**Mantener baseline.** La piramidación no supera el umbral de valor añadido significativo.",
                "",
                "Ninguna variante mejora el Sharpe en más de +0.10 ni el CAGR en más de +2% sobre la baseline.",
                "La diferencia está dentro del error estadístico del período analizado.",
            ]

    lines += [
        "",
        "---",
        "",
        "_Análisis generado automáticamente por backtest_exp45.py._",
        "_La decisión final la toma el arquitecto del sistema._",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n  Reporte markdown: {filepath}")
    return filepath


# ==================================================
# MAIN
# ==================================================

def main():

    print("=" * 65)
    print("  LIBERTAD_2045 — BACKTEST EXP45: PIRAMIDACIÓN")
    print("=" * 65)
    print(f"  Período      : {_motor.START_DATE} → {_motor.END_DATE}")
    print(f"  Capital      : {_motor.CAPITAL_INICIAL:.0f} €")
    print(f"  Aportación   : {_motor.APORTACION_ANUAL:.0f} €/año")
    print(f"  Riesgo/op    : {_motor.RISK_PERCENT:.2%}")
    print(f"  Max pos      : {_motor.MAX_POSITIONS}")
    print(f"  Buffer       : {_motor.BUFFER} USD (idéntico al motor base)")
    print(f"  DD no viable : >{PYRAMID_MAX_DD:.0%}")
    print()
    print(f"  Variantes a ejecutar: {len(VARIANTES)}")
    for nombre, K, timing in VARIANTES:
        if K is None:
            print(f"    {nombre:<10} → baseline sin piramidación")
        else:
            print(f"    {nombre:<10} → K={K}×ATR, timing={timing}")

    # Cargar universo y datos UNA sola vez — reutilizados por las 7 variantes
    print("\n" + "─" * 65)
    comp_df  = _motor.cargar_composicion_sp500()
    universo = _motor.universo_historico_sp500(comp_df)
    print(f"  Universo: {len(universo)} activos históricos únicos")

    datos = _motor.descargar_datos(universo, _motor.START_DATE, _motor.END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        return

    # Ejecutar todas las variantes
    resultados = []

    for nombre, K, timing in VARIANTES:

        print("\n" + "=" * 65)
        print(f"  Variante: {nombre}")
        print("=" * 65)

        if K is None:
            # Baseline — usar motor base directamente (sin modificaciones)
            trades, curva, capital = _motor.ejecutar_backtest(datos, comp_df)
            # Normalizar campos para compatibilidad con calcular_metricas_exp45
            for t in trades:
                if "pyramided" not in t:
                    t["pyramided"] = False
                if "pnl_pos1" not in t:
                    t["pnl_pos1"] = t["pnl"]
                if "pnl_pos2" not in t:
                    t["pnl_pos2"] = 0.0
        else:
            trades, curva, capital = ejecutar_backtest_exp45(
                datos, comp_df, K=K, timing=timing
            )

        metricas = calcular_metricas_exp45(trades, curva, capital, nombre=nombre)
        resultados.append((nombre, metricas))

        viable_tag = "VIABLE" if metricas.get("viable", True) else "NO VIABLE (DD > 15%)"
        print(
            f"\n  {nombre}: CAGR={metricas['cagr']:.1%} | "
            f"DD={metricas['max_drawdown']:.1%} | "
            f"Sharpe={metricas['sharpe']:.2f} | "
            f"Pir.={metricas['n_pyramided']} | "
            f"{viable_tag}"
        )

        # Guardar trades de cada variante
        os.makedirs(_motor.LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pd.DataFrame(trades).to_csv(
            f"{_motor.LOG_DIR}/exp45_{nombre}_trades_{ts}.csv", index=False
        )

    # Tabla comparativa en consola
    imprimir_tabla_comparativa(resultados)

    # Reporte markdown
    generar_reporte_markdown(resultados)

    # CSV con resumen de métricas de todas las variantes
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    metricas_rows = []
    for nombre, m in resultados:
        row = {"variante": nombre}
        row.update({k: v for k, v in m.items() if k != "nombre"})
        metricas_rows.append(row)
    pd.DataFrame(metricas_rows).to_csv(
        f"{_motor.LOG_DIR}/exp45_matriz_{ts}.csv", index=False
    )
    print(f"  CSV matriz: {_motor.LOG_DIR}/exp45_matriz_{ts}.csv")

    print("\n" + "=" * 65)
    print("  EXP45 completado. La decisión final la tomamos juntos.")
    print("=" * 65)


if __name__ == "__main__":
    main()
