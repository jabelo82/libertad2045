"""
LIBERTAD_2045 — Backtest
========================
Valida la estrategia de señales sobre datos históricos reales
usando la misma lógica que opera el sistema en producción.

Uso:
    python backtest.py

Requisitos:
    pip install yfinance pandas numpy

Parámetros configurables al inicio del archivo:
    START_DATE, END_DATE, CAPITAL, RISK_PERCENT,
    ATR_MULTIPLIER, MAX_POSITIONS, UNIVERSE
"""

import warnings
warnings.filterwarnings("ignore")

import os
import time
from datetime import datetime

import pandas as pd
import numpy as np
import yfinance as yf


# --------------------------------------------------
# Parámetros del backtest
# --------------------------------------------------

START_DATE       = "2020-01-01"
END_DATE         = "2025-01-01"

CAPITAL_INICIAL  = 4000.0

RISK_PERCENT     = 0.0065     # 0.65% de riesgo por operación
ATR_MULTIPLIER   = 2          # Stop-loss = ATR × multiplicador
MAX_POSITION_PCT = 0.25       # Máximo 25% del capital por posición
MAX_POSITIONS    = 5          # Máximo de posiciones simultáneas
BUFFER           = 0.05       # Buffer sobre el máximo para la entrada

LOG_DIR          = "backtest_results"


# --------------------------------------------------
# Universo (mismo que universe_sp500.py)
# --------------------------------------------------

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL",
    "ADBE", "CRM",  "AMD",  "QCOM", "TXN",
    "INTC", "IBM",  "AMAT", "MU",   "KLAC",
    "GOOGL","META", "NFLX", "DIS",  "CMCSA",
    "T",    "VZ",
    "AMZN", "TSLA", "HD",   "MCD",  "NKE",
    "SBUX", "LOW",  "TJX",  "BKNG", "MAR",
    "WMT",  "PG",   "KO",   "PEP",  "COST",
    "PM",   "MO",   "CL",   "KMB",
    "UNH",  "LLY",  "JNJ",  "MRK",  "ABBV",
    "TMO",  "ABT",  "DHR",  "BMY",  "AMGN",
    "ISRG", "SYK",  "BSX",  "ZTS",
    "JPM",  "BAC",  "WFC",  "GS",   "MS",
    "BLK",  "SCHW", "AXP",  "V",    "MA",
    "BRK-B",
    "CAT",  "HON",  "UNP",  "RTX",  "LMT",
    "GE",   "DE",   "MMM",  "ETN",  "PH",
    "XOM",  "CVX",  "COP",  "SLB",  "EOG",
    "LIN",  "APD",  "ECL",  "NEM",  "FCX",
    "NEE",  "DUK",  "SO",   "D",    "AEP",
    "PLD",  "AMT",  "EQIX", "SPG",
]

# Nota: yfinance usa BRK-B en lugar de BRK B


# ==================================================
# FUNCIONES DE CÁLCULO DE INDICADORES
# ==================================================

def calcular_indicadores(df):
    """
    Calcula SMA50, SMA200, ATR y volumen medio.
    Misma lógica que data_loader.py.

    Maneja el MultiIndex que devuelve yfinance con pandas moderno:
    aplana las columnas antes de calcular para garantizar
    que Close, High, Low, Volume sean Series simples.
    """

    df = df.copy()

    # Aplanar MultiIndex si yfinance lo devuelve con columnas anidadas
    # Ej: ("Close", "AAPL") → "Close"
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Eliminar columnas duplicadas que puedan surgir del aplanado
    df = df.loc[:, ~df.columns.duplicated()]

    # Verificar columnas necesarias
    for col in ["Close", "High", "Low", "Volume"]:
        if col not in df.columns:
            raise ValueError(f"Columna requerida no encontrada: {col}")

    # Asegurar que son Series simples (no DataFrames)
    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    df["Close"]  = close
    df["High"]   = high
    df["Low"]    = low
    df["Volume"] = volume

    df["SMA50"]  = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()

    prev_close   = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low  - prev_close).abs()

    df["TR"]  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(14).mean()
    df = df.drop(columns=["TR"])

    df["vol_media20"] = volume.rolling(20).mean()

    return df


# ==================================================
# FUNCIÓN DE DETECCIÓN DE SEÑAL
# ==================================================

def detectar_senal(df, i):
    """
    Evalúa si existe señal en el índice i.
    Misma lógica que signal_engine.py.
    """

    if i < 1:
        return False

    last = df.iloc[i]
    prev = df.iloc[i - 1]

    # Verificar que los indicadores están disponibles
    for val in [last["Close"], last["SMA50"], last["SMA200"],
                prev["Close"], prev["SMA50"], prev["SMA200"],
                last["ATR"],   last["vol_media20"]]:
        if pd.isna(val):
            return False

    if last["ATR"] <= 0:
        return False

    # 1. Tendencia principal
    tendencia = (
        last["Close"]  > last["SMA200"] and
        last["SMA200"] > prev["SMA200"]
    )

    # 2. Pullback real
    pullback = prev["Close"] < prev["SMA50"] * 0.98

    # 3. Recuperación
    recuperacion = last["Close"] > last["SMA50"]

    # 4. Confirmación de volumen
    volumen = (
        last["vol_media20"] > 0 and
        last["Volume"] > last["vol_media20"]
    )

    return tendencia and pullback and recuperacion and volumen


# ==================================================
# FUNCIÓN DE CÁLCULO DE POSICIÓN
# ==================================================

def calcular_posicion(df, i, capital):
    """
    Calcula shares, stop_distance y atr en el índice i.
    Misma lógica que position_size.py.
    """

    atr        = df.iloc[i]["ATR"]
    last_price = df.iloc[i]["Close"]

    if pd.isna(atr) or atr <= 0:
        return 0, None, None

    if pd.isna(last_price) or last_price <= 1:
        return 0, None, None

    stop_distance = atr * ATR_MULTIPLIER

    if stop_distance <= 0:
        return 0, None, None

    risk_amount        = capital * RISK_PERCENT
    shares_risk        = int(risk_amount / stop_distance)

    max_position_value = capital * MAX_POSITION_PCT
    shares_capital     = int(max_position_value / last_price)

    shares = min(shares_risk, shares_capital)

    if shares <= 0:
        return 0, None, None

    return shares, stop_distance, atr


# ==================================================
# DESCARGA DE DATOS
# ==================================================

def descargar_datos(universe, start, end):
    """
    Descarga datos históricos de todos los activos del universo.
    Retorna un diccionario {symbol: DataFrame con indicadores}.
    """

    print(f"\nDescargando datos históricos ({start} → {end})...")
    print(f"Universo: {len(universe)} activos\n")

    datos = {}
    errores = []

    for i, symbol in enumerate(universe, 1):

        descargado = False

        for intento in range(3):

            try:
                df = yf.download(symbol, start=start, end=end,
                                 progress=False, auto_adjust=True)

                if df.empty or len(df) < 200:
                    break

                df = calcular_indicadores(df)
                datos[symbol] = df

                print(f"  [{i:3d}/{len(universe)}] {symbol:8s} → {len(df)} barras")
                descargado = True
                break

            except Exception as e:
                if "RateLimit" in str(e) or "Too Many" in str(e):
                    espera = (intento + 1) * 30
                    print(f"  [{i:3d}/{len(universe)}] {symbol:8s} → Rate limit, esperando {espera}s...")
                    time.sleep(espera)
                else:
                    print(f"  [{i:3d}/{len(universe)}] {symbol:8s} → ERROR: {e}")
                    break

        if not descargado and symbol not in datos:
            errores.append(symbol)

        # Pausa entre descargas para respetar el rate limit de Yahoo Finance
        # 2 segundos es suficiente para 95 activos sin disparar el límite
        time.sleep(2)

    print(f"\nActivos cargados   : {len(datos)}")
    print(f"Activos con error  : {len(errores)}")
    if errores:
        print(f"Errores            : {errores}")

    return datos


# ==================================================
# MOTOR DEL BACKTEST
# ==================================================

def ejecutar_backtest(datos):
    """
    Simula la operativa del sistema día a día sobre los datos históricos.

    Lógica de simulación:
        - Cada día de mercado se escanea el universo completo
        - Se detectan señales con la misma lógica que signal_engine.py
        - Las entradas son BUY STOP al máximo del día + buffer (día siguiente)
        - Los stops se gestionan diariamente
        - El capital se actualiza con cada trade cerrado
    """

    print("\nEjecutando backtest...\n")

    # Obtener todos los días de mercado del período
    fechas = sorted(set(
        fecha
        for df in datos.values()
        for fecha in df.index
    ))

    capital       = CAPITAL_INICIAL
    posiciones    = {}    # {symbol: {entry, stop, shares, fecha_entrada}}
    trades        = []    # Lista de todos los trades cerrados
    curva_capital = []    # Evolución del capital día a día

    for fecha in fechas:

        # --------------------------------------------------
        # 1. Gestionar posiciones abiertas
        #    Trailing stop: el stop sube cuando el precio
        #    marca nuevos máximos, nunca baja.
        #    Distancia fija: ATR × ATR_MULTIPLIER
        # --------------------------------------------------

        cerradas = []

        for symbol, pos in posiciones.items():

            if symbol not in datos:
                continue

            df = datos[symbol]

            if fecha not in df.index:
                continue

            bar = df.loc[fecha]
            atr = df.loc[fecha, "ATR"]

            # Actualizar trailing stop si el precio marca nuevo máximo
            # El stop sube para proteger beneficios, nunca baja
            if not pd.isna(atr) and atr > 0:
                nuevo_stop = round(bar["High"] - atr * ATR_MULTIPLIER, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop

            # Si el mínimo del día toca o baja del stop → cerrar posición
            if bar["Low"] <= pos["stop"]:

                precio_salida = pos["stop"]
                pnl           = (precio_salida - pos["entry"]) * pos["shares"]
                capital      += pnl

                trades.append({
                    "symbol"       : symbol,
                    "fecha_entrada": pos["fecha_entrada"],
                    "fecha_salida" : fecha,
                    "entrada"      : round(pos["entry"], 2),
                    "salida"       : round(precio_salida, 2),
                    "shares"       : pos["shares"],
                    "pnl"          : round(pnl, 2),
                    "resultado"    : "LOSS" if pnl < 0 else "WIN",
                    "capital"      : round(capital, 2),
                })

                cerradas.append(symbol)

        for symbol in cerradas:
            del posiciones[symbol]


        # --------------------------------------------------
        # 2. Escanear universo en busca de señales
        # --------------------------------------------------

        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        señales = []

        for symbol in datos:

            # No entrar en activos ya en cartera
            if symbol in posiciones:
                continue

            df = datos[symbol]

            if fecha not in df.index:
                continue

            i = df.index.get_loc(fecha)

            if i < 200:
                continue

            if not detectar_senal(df, i):
                continue

            shares, stop_distance, atr = calcular_posicion(df, i, capital)

            if shares <= 0:
                continue

            bar   = df.iloc[i]
            score = (bar["Close"] - bar["SMA50"]) / bar["ATR"]

            señales.append({
                "symbol"        : symbol,
                "score"         : score,
                "shares"        : shares,
                "stop_distance" : stop_distance,
                "high"          : bar["High"],
                "atr"           : atr,
            })

        # Ordenar por score descendente y tomar las mejores
        señales = sorted(señales, key=lambda x: x["score"], reverse=True)

        slots_libres = MAX_POSITIONS - len(posiciones)
        señales      = señales[:slots_libres]


        # --------------------------------------------------
        # 3. Abrir nuevas posiciones
        #    La entrada es al día siguiente (simulación realista)
        # --------------------------------------------------

        idx_fecha = fechas.index(fecha)

        if idx_fecha + 1 < len(fechas):

            fecha_entrada = fechas[idx_fecha + 1]

            for señal in señales:

                symbol        = señal["symbol"]
                buy_stop      = round(señal["high"] + BUFFER, 2)
                stop_loss     = round(buy_stop - señal["stop_distance"], 2)

                if stop_loss <= 0:
                    continue

                df = datos[symbol]

                if fecha_entrada not in df.index:
                    continue

                bar_entrada = df.loc[fecha_entrada]

                # La orden BUY STOP se activa solo si el precio
                # sube hasta buy_stop durante el día de entrada
                if bar_entrada["High"] >= buy_stop:

                    coste = buy_stop * señal["shares"]

                    # Verificar que hay capital suficiente
                    if coste > capital:
                        continue

                    posiciones[symbol] = {
                        "entry"         : buy_stop,
                        "stop"          : stop_loss,
                        "shares"        : señal["shares"],
                        "fecha_entrada" : fecha_entrada,
                    }

        # Registrar capital al final del día
        curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})


    # --------------------------------------------------
    # Cerrar posiciones abiertas al final del período
    # (al precio de cierre del último día disponible)
    # --------------------------------------------------

    for symbol, pos in posiciones.items():

        df = datos[symbol]

        if df.empty:
            continue

        ultimo_cierre = df.iloc[-1]["Close"]
        pnl           = (ultimo_cierre - pos["entry"]) * pos["shares"]
        capital      += pnl

        trades.append({
            "symbol"       : symbol,
            "fecha_entrada": pos["fecha_entrada"],
            "fecha_salida" : df.index[-1],
            "entrada"      : round(pos["entry"], 2),
            "salida"       : round(ultimo_cierre, 2),
            "shares"       : pos["shares"],
            "pnl"          : round(pnl, 2),
            "resultado"    : "OPEN→CLOSE",
            "capital"      : round(capital, 2),
        })

    return trades, curva_capital, capital


# ==================================================
# MÉTRICAS
# ==================================================

def calcular_metricas(trades, curva_capital, capital_final):
    """
    Calcula las métricas clave alineadas con la Constitución:
        - Supervivencia: drawdown máximo
        - Disciplina   : win rate, profit factor
        - Consistencia : curva de capital, retorno total
    """

    if not trades:
        return {}

    df_trades  = pd.DataFrame(trades)
    df_capital = pd.DataFrame(curva_capital)

    total_trades = len(df_trades)
    wins         = df_trades[df_trades["resultado"] == "WIN"]
    losses       = df_trades[df_trades["resultado"] == "LOSS"]

    win_rate     = len(wins) / total_trades if total_trades > 0 else 0

    ganancia_total = wins["pnl"].sum()   if len(wins)   > 0 else 0
    perdida_total  = losses["pnl"].abs().sum() if len(losses) > 0 else 1

    profit_factor  = ganancia_total / perdida_total if perdida_total > 0 else float("inf")

    # Drawdown máximo
    capital_series = df_capital["capital"].values
    pico           = capital_series[0]
    max_drawdown   = 0.0

    for c in capital_series:
        if c > pico:
            pico = c
        dd = (pico - c) / pico if pico > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    retorno_total = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL

    pnl_medio_win  = wins["pnl"].mean()   if len(wins)   > 0 else 0
    pnl_medio_loss = losses["pnl"].mean() if len(losses) > 0 else 0

    expectativa = (win_rate * pnl_medio_win) + ((1 - win_rate) * pnl_medio_loss)

    return {
        "total_trades"    : total_trades,
        "wins"            : len(wins),
        "losses"          : len(losses),
        "win_rate"        : win_rate,
        "profit_factor"   : profit_factor,
        "max_drawdown"    : max_drawdown,
        "retorno_total"   : retorno_total,
        "capital_inicial" : CAPITAL_INICIAL,
        "capital_final"   : round(capital_final, 2),
        "pnl_medio_win"   : round(pnl_medio_win, 2),
        "pnl_medio_loss"  : round(pnl_medio_loss, 2),
        "expectativa"     : round(expectativa, 2),
    }


# ==================================================
# GUARDAR RESULTADOS
# ==================================================

def guardar_resultados(trades, curva_capital, metricas):
    """
    Guarda los resultados del backtest en archivos CSV.
    """

    os.makedirs(LOG_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # -- Trades --
    if trades:
        df_trades = pd.DataFrame(trades)
        path_trades = f"{LOG_DIR}/backtest_trades_{timestamp}.csv"
        df_trades.to_csv(path_trades, index=False)
        print(f"\nTrades guardados    : {path_trades}")

    # -- Curva de capital --
    if curva_capital:
        df_capital = pd.DataFrame(curva_capital)
        path_capital = f"{LOG_DIR}/backtest_capital_{timestamp}.csv"
        df_capital.to_csv(path_capital, index=False)
        print(f"Curva de capital    : {path_capital}")

    # -- Métricas --
    if metricas:
        df_metricas = pd.DataFrame([metricas])
        path_metricas = f"{LOG_DIR}/backtest_metricas_{timestamp}.csv"
        df_metricas.to_csv(path_metricas, index=False)
        print(f"Métricas guardadas  : {path_metricas}")


# ==================================================
# INFORME EN CONSOLA
# ==================================================

def imprimir_informe(metricas):
    """
    Imprime el informe de resultados alineado con la Constitución.
    """

    separador = "─" * 45

    print(f"\n{separador}")
    print(f"  LIBERTAD_2045 — RESULTADOS DEL BACKTEST")
    print(f"  {START_DATE} → {END_DATE}")
    print(separador)

    print(f"\n  CAPITAL")
    print(f"  Inicial          : {metricas['capital_inicial']:>10.2f} €")
    print(f"  Final            : {metricas['capital_final']:>10.2f} €")
    print(f"  Retorno total    : {metricas['retorno_total']:>10.1%}")

    print(f"\n  OPERATIVA")
    print(f"  Total trades     : {metricas['total_trades']:>10d}")
    print(f"  Wins             : {metricas['wins']:>10d}")
    print(f"  Losses           : {metricas['losses']:>10d}")
    print(f"  Win rate         : {metricas['win_rate']:>10.1%}")

    print(f"\n  RIESGO")
    print(f"  Profit factor    : {metricas['profit_factor']:>10.2f}")
    print(f"  Drawdown máximo  : {metricas['max_drawdown']:>10.1%}")
    print(f"  PnL medio WIN    : {metricas['pnl_medio_win']:>10.2f} €")
    print(f"  PnL medio LOSS   : {metricas['pnl_medio_loss']:>10.2f} €")
    print(f"  Expectativa/trade: {metricas['expectativa']:>10.2f} €")

    print(f"\n  VEREDICTO")

    # Evaluación según los principios de la Constitución
    supervivencia = metricas["max_drawdown"] < 0.25
    disciplina    = metricas["profit_factor"] > 1.5
    consistencia  = metricas["win_rate"] > 0.35 and metricas["retorno_total"] > 0

    print(f"  Supervivencia    : {'✓ OK' if supervivencia else '✗ REVISAR'}"
          f"  (drawdown < 25%)")
    print(f"  Disciplina       : {'✓ OK' if disciplina    else '✗ REVISAR'}"
          f"  (profit factor > 1.5)")
    print(f"  Consistencia     : {'✓ OK' if consistencia  else '✗ REVISAR'}"
          f"  (win rate > 35% y retorno > 0)")

    apto = supervivencia and disciplina and consistencia

    print(f"\n  {'✓ SISTEMA APTO PARA PAPER TRADING' if apto else '✗ SISTEMA NO APTO — REVISAR ESTRATEGIA'}")
    print(f"\n{separador}\n")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 45)
    print("  LIBERTAD_2045 — BACKTEST")
    print("=" * 45)
    print(f"  Período   : {START_DATE} → {END_DATE}")
    print(f"  Capital   : {CAPITAL_INICIAL:.0f} €")
    print(f"  Riesgo/op : {RISK_PERCENT:.2%}")
    print(f"  Max pos   : {MAX_POSITIONS}")
    print(f"  Universo  : {len(UNIVERSE)} activos")

    # 1. Descargar datos
    datos = descargar_datos(UNIVERSE, START_DATE, END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos. Verificar conexión a internet.")
        exit(1)

    # 2. Ejecutar backtest
    trades, curva_capital, capital_final = ejecutar_backtest(datos)

    # 3. Calcular métricas
    metricas = calcular_metricas(trades, curva_capital, capital_final)

    # 4. Imprimir informe
    imprimir_informe(metricas)

    # 5. Guardar resultados
    guardar_resultados(trades, curva_capital, metricas)