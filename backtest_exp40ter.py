"""
LIBERTAD_2045 — Backtest Experimento 40-ter
=============================================
Optimización del multiplicador de trailing stop.

Contexto: Experimento 40 mostró que el trailing agresivo ×0.70 (Variante C)
produce +240% de capital vs la línea base pero con DD marginalmente peor
(12.0% vs 11.2%). Este experimento busca el punto óptimo entre 0.70 y 1.00.

La lógica de stop es idéntica para todos los multiplicadores:
    mult         = obtener_multiplicador_B1(df, i) × factor
    nuevo_stop   = High - ATR × mult           (trailing)
    break-even   = entry + 0.5×ATR cuando Close ≥ entry + 1.5×ATR

Multiplicadores evaluados:
    ×0.70  ×0.75  ×0.80  ×0.85  ×0.90  ×0.95  ×1.00 (= línea base A)

Criterio de aprobación:
    El multiplicador óptimo debe mejorar simultáneamente PF, capital final
    y drawdown respecto al ×1.00. Si ninguno cumple las tres, se reporta el
    mejor equilibrio y se identifica el punto de inflexión del DD.

Los datos se descargan UNA sola vez y se reutilizan en los 7 backtests.
No se modifica ningún módulo de producción.

Uso:
    python backtest_exp40ter.py

Tiempo estimado con caché: ~10-12 minutos (7 × backtest completo)
"""

import warnings
warnings.filterwarnings("ignore")

import os
import time
from datetime import datetime

import pandas as pd
import numpy as np


# --------------------------------------------------
# Parámetros — idénticos a backtest_expandido.py
# --------------------------------------------------

START_DATE       = "2006-01-01"
END_DATE         = "2025-12-31"

CAPITAL_INICIAL  = 4000.0
APORTACION_ANUAL = 4000.0

RISK_PERCENT     = 0.0085
ATR_MULTIPLIER   = 3.1        # multiplicador base (sin ajuste percentil)
MAX_POSITION_PCT = 0.25
MAX_POSITIONS    = 10
BUFFER           = 0.05

B1_VENTANA  = 252
B1_MULT_MIN = 2.2
B1_MULT_MAX = 4.0

SALIDA_POR_CIERRE = True

RISK_MIN_CAPITAL  = 2000.0
RISK_MAX_DRAWDOWN = 0.10

REBALANCE_THRESHOLD  = 0.25
REBALANCE_MIN_SHARES = 5

SP500_COMP_CACHE = "sp500_composicion.csv"
SP500_COMP_URL   = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)

LOG_DIR = "backtest_results"

# Multiplicadores a evaluar — de agresivo a conservador
FACTORES = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00]


# --------------------------------------------------
# Universo S&P500
# --------------------------------------------------

SP500 = [
    "AAPL",  "MSFT",  "NVDA",  "AVGO",  "ORCL",
    "ADBE",  "CRM",   "AMD",   "QCOM",  "TXN",
    "INTC",  "IBM",   "AMAT",  "MU",    "KLAC",
    "LRCX",  "MRVL",  "SNPS",  "CDNS",  "ON",
    "TER",   "KEYS",  "ENPH",  "FTNT",  "CTSH",
    "HPQ",   "FFIV",  "EPAM",  "CSCO",  "INTU",
    "ADI",   "NXPI",  "MSI",   "ANET",  "APH",
    "AKAM",  "CDW",   "DXC",   "GDDY",  "GEN",
    "IT",    "JNPR",  "LDOS",  "NTAP",  "TRMB",
    "TYL",   "WEX",   "ZBRA",
    "GOOGL", "GOOG",  "META",  "NFLX",  "DIS",
    "CMCSA", "T",     "VZ",    "TMUS",  "CHTR",
    "WBD",   "OMC",   "FOXA",  "NWS",   "PARA",
    "NDAQ",
    "AMZN",  "TSLA",  "HD",    "MCD",   "NKE",
    "SBUX",  "LOW",   "TJX",   "BKNG",  "MAR",
    "HLT",   "RCL",   "CCL",   "EXPE",  "ETSY",
    "EBAY",  "ORLY",  "AZO",   "DLTR",  "DG",
    "BBY",   "ROST",  "KMX",   "PHM",   "DHI",
    "LEN",   "NVR",   "TOL",   "MHK",   "POOL",
    "CMG",   "YUM",   "DRI",   "QSR",   "APTV",
    "BWA",   "GPC",   "LKQ",   "ALK",   "DAL",
    "LUV",   "UAL",
    "WMT",   "PG",    "KO",    "PEP",   "COST",
    "PM",    "MO",    "CL",    "KMB",   "GIS",
    "CAG",   "SJM",   "HRL",   "MKC",   "CHD",
    "CLX",   "EL",    "COTY",  "KHC",   "MDLZ",
    "MNST",  "BG",    "SMG",
    "UNH",   "LLY",   "JNJ",   "MRK",   "ABBV",
    "TMO",   "ABT",   "DHR",   "BMY",   "AMGN",
    "ISRG",  "SYK",   "BSX",   "ZTS",   "EW",
    "BDX",   "IQV",   "IDXX",  "ALGN",  "PODD",
    "HOLX",  "DXCM",  "MTD",   "WAT",   "A",
    "RMD",   "BAX",   "VTRS",  "PTC",   "PFE",
    "MDT",   "GILD",  "REGN",  "VRTX",  "HCA",
    "CVS",   "MCK",   "ABC",   "CAH",   "COR",
    "HSIC",  "PKI",   "PRGO",  "STE",   "TFX",
    "XRAY",  "ZBH",   "ILMN",  "BIO",   "CRL",
    "TECH",  "WST",   "SRPT",
    "JPM",   "BAC",   "WFC",   "GS",    "MS",
    "BLK",   "SCHW",  "AXP",   "V",     "MA",
    "BRK-B", "C",     "USB",   "PNC",   "TFC",
    "COF",   "SYF",   "ALLY",  "CFG",   "FITB",
    "HBAN",  "RF",    "KEY",   "MTB",   "ZION",
    "CMA",   "FHN",   "AFL",   "ALL",   "AIG",
    "AJG",   "AIZ",   "AMP",   "ACGL",  "PFG",
    "L",     "LNC",   "MET",   "PRU",   "RE",
    "RGA",   "TRV",   "UNM",   "WRB",   "HIG",
    "GL",    "CI",    "AON",   "SPGI",  "MCO",
    "MSCI",  "ICE",   "CME",   "CBOE",  "FDS",
    "MKTX",
    "CAT",   "HON",   "UNP",   "RTX",   "LMT",
    "GE",    "DE",    "MMM",   "ETN",   "PH",
    "EMR",   "ROK",   "AME",   "FTV",   "IR",
    "XYL",   "CARR",  "OTIS",  "GWW",   "RSG",
    "WM",    "ROP",   "CTAS",  "VRSK",  "EFX",
    "BR",    "CHRW",  "EXPD",  "FDX",   "UPS",
    "GD",    "NOC",   "ITW",   "JCI",   "ADP",
    "CMI",   "TDG",   "TXT",   "FAST",  "HUBB",
    "IEX",   "LII",   "MAS",   "NDSN",  "NVT",
    "PNR",   "SNA",   "SWK",   "JBHT",  "ODFL",
    "XPO",   "GXO",   "SAIA",  "R",
    "XOM",   "CVX",   "COP",   "SLB",   "EOG",
    "MPC",   "PSX",   "VLO",   "DVN",   "FANG",
    "OXY",   "APA",   "HAL",   "BKR",   "NOV",
    "FTI",   "CVI",   "HES",   "MRO",   "OKE",
    "WMB",   "HP",
    "LIN",   "APD",   "ECL",   "NEM",   "FCX",
    "DOW",   "DD",    "PPG",   "SHW",   "AVY",
    "IP",    "PKG",   "SEE",   "SON",   "ALB",
    "FMC",   "CE",    "EMN",   "IFF",   "NUE",
    "RS",    "CF",    "MOS",   "WLK",
    "NEE",   "DUK",   "SO",    "D",     "AEP",
    "EXC",   "SRE",   "XEL",   "WEC",   "ES",
    "ETR",   "FE",    "PPL",   "CMS",   "NI",
    "AES",   "EIX",   "PEG",   "CNP",   "LNT",
    "AEE",   "ATO",   "DTE",   "ED",    "EVRG",
    "NRG",   "PCG",   "PNW",
    "PLD",   "AMT",   "EQIX",  "SPG",   "O",
    "PSA",   "WELL",  "AVB",   "EQR",   "VTR",
    "ARE",   "BXP",   "KIM",   "REG",   "FRT",
    "NNN",   "MPW",   "SBAC",  "CCI",   "DLR",
    "IRM",   "MAA",   "CPT",   "ESS",   "EXR",
    "HST",   "INVH",  "UDR",   "CUBE",  "REXR",
    "STAG",  "RHP",   "AMH",
]

_seen = set()
_unique = []
for t in SP500:
    if t not in _seen:
        _seen.add(t)
        _unique.append(t)
SP500 = _unique

CRYPTO          = ["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","AVAX-USD","DOGE-USD","DOT-USD","MATIC-USD"]
MATERIAS_PRIMAS = ["GLD","SLV","USO","UNG","CPER","CORN","WEAT","SOYB","DBA","GSG"]
ETFS            = ["XLK","XLV","XLF","XLE","XLI","XLY","XLP","XLB","XLU","XLRE","XLC","QQQ","IWM","EEM","EFA","VNQ","TAN","ICLN","ARKK","SMH"]


# ==================================================
# UNIVERSO DINÁMICO S&P500
# ==================================================

def cargar_composicion_sp500():
    if os.path.exists(SP500_COMP_CACHE):
        return pd.read_csv(SP500_COMP_CACHE, index_col=0, parse_dates=True)
    try:
        df = pd.read_csv(SP500_COMP_URL, index_col=0, parse_dates=True)
        df.to_csv(SP500_COMP_CACHE)
        return df
    except Exception:
        return pd.DataFrame()


def universo_historico_sp500(comp_df):
    if comp_df.empty:
        return list(SP500)
    import re
    _date_suffix  = re.compile(r'-\d{6,8}$')
    _valid_ticker = re.compile(r'^[A-Z]{1,5}$')
    todos = set()
    col = comp_df.columns[0]
    for val in comp_df[col].dropna():
        for ticker in str(val).split(","):
            ticker = ticker.strip()
            if not ticker:
                continue
            ticker = _date_suffix.sub('', ticker)
            if _valid_ticker.match(ticker):
                todos.add(ticker)
    return sorted(todos)


def sp500_en_fecha(comp_df, fecha):
    if comp_df.empty:
        return None
    try:
        idx = comp_df.index.asof(pd.Timestamp(fecha))
        if pd.isna(idx):
            return None
        val = comp_df.iloc[comp_df.index.get_loc(idx), 0]
        return {t.strip() for t in str(val).split(",") if t.strip()}
    except Exception:
        return None


# ==================================================
# INDICADORES
# ==================================================

def calcular_indicadores(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    for col in ["Close", "High", "Low", "Volume"]:
        if col not in df.columns:
            raise ValueError(f"Columna requerida: {col}")
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
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low  - prev_close).abs()
    df["TR"]  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(14).mean()
    df = df.drop(columns=["TR"])
    df["vol_media20"]   = volume.rolling(20).mean()
    df["ATR_PERCENTIL"] = df["ATR"].rolling(B1_VENTANA).rank(pct=True)
    return df


# ==================================================
# MULTIPLICADOR DINÁMICO B1
# ==================================================

def obtener_multiplicador_b1(df, i):
    percentil = df.iloc[i].get("ATR_PERCENTIL", np.nan)
    if pd.isna(percentil):
        return ATR_MULTIPLIER
    return round(B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil, 2)


# ==================================================
# SEÑAL — idéntica al baseline v2.3
# ==================================================

def detectar_senal(df, i):
    if i < 4:
        return False
    last = df.iloc[i]
    prev = df.iloc[i - 1]
    for val in [last["Close"], last["SMA50"], last["SMA200"],
                prev["Close"], prev["SMA50"], prev["SMA200"],
                last["ATR"]]:
        if pd.isna(val):
            return False
    if last["ATR"] <= 0:
        return False
    if not (last["Close"] > last["SMA200"] and last["SMA200"] > prev["SMA200"]):
        return False
    pullback = False
    for j in range(i - 3, i):
        row = df.iloc[j]
        if (pd.isna(row["Close"]) or pd.isna(row["SMA50"]) or
                pd.isna(row["ATR"]) or row["ATR"] <= 0):
            continue
        if row["Close"] < row["SMA50"] - row["ATR"] * 0.75:
            pullback = True
            break
    if not pullback:
        return False
    return last["Close"] > last["SMA50"]


# ==================================================
# POSITION SIZING — idéntico al baseline v2.3
# ==================================================

def calcular_posicion(df, i, capital):
    atr        = df.iloc[i]["ATR"]
    last_price = df.iloc[i]["Close"]
    if pd.isna(atr) or atr <= 0:
        return 0, None, None
    if pd.isna(last_price) or last_price <= 0.01:
        return 0, None, None
    mult          = obtener_multiplicador_b1(df, i)
    stop_distance = atr * mult
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
# DESCARGA DE DATOS — una sola vez para todos los runs
# ==================================================

def descargar_datos(universe, start, end):
    from data_manager import obtener_datos_cached
    print(f"\nCargando datos ({start} → {end})...")
    print(f"Universo total: {len(universe)} activos")
    datos   = {}
    errores = []
    for symbol in universe:
        try:
            df_raw = obtener_datos_cached(symbol, start, end)
            if df_raw is None or len(df_raw) < 200:
                errores.append(symbol)
                continue
            datos[symbol] = calcular_indicadores(df_raw)
        except Exception:
            errores.append(symbol)
    print(f"Activos cargados : {len(datos)}  |  Con error: {len(errores)}\n")
    return datos


# ==================================================
# MOTOR DEL BACKTEST — paramétrico por factor
# ==================================================

def ejecutar_backtest(datos, composicion_df, factor):
    """
    Corre el backtest completo con el factor multiplicador indicado.

    Stop trailing: High - ATR × obtener_multiplicador_b1(df, i) × factor
    Break-even   : entry + 0.5×ATR cuando Close ≥ entry + 1.5×ATR
    Todo lo demás: idéntico al baseline v2.3
    """
    if composicion_df is None:
        composicion_df = pd.DataFrame()

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

    for idx, fecha in enumerate(fechas):

        # 1. Aportación anual
        if idx > 0 and fecha.year > fechas[idx - 1].year:
            capital += APORTACION_ANUAL
            if capital > capital_pico:
                capital_pico = capital

        # 2. Actualizar capital pico
        if capital > capital_pico:
            capital_pico = capital

        # 3. Risk Guardian — capital mínimo
        if capital < RISK_MIN_CAPITAL:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # 4. Gestionar posiciones — trailing stop con factor variable
        cerradas = []
        for symbol, pos in posiciones.items():
            if symbol not in datos:
                continue
            df = datos[symbol]
            if fecha not in df.index:
                continue
            bar      = df.loc[fecha]
            atr      = df.loc[fecha, "ATR"]
            i_actual = df.index.get_loc(fecha)

            if not pd.isna(atr) and atr > 0:
                mult_b1    = obtener_multiplicador_b1(df, i_actual)
                nuevo_stop = round(bar["High"] - atr * mult_b1 * factor, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop

                # Break-even — idéntico al baseline v2.3
                be_stop = round(pos["entry"] + 0.5 * atr, 2)
                if bar["Close"] >= pos["entry"] + 1.5 * atr and be_stop > pos["stop"]:
                    pos["stop"] = be_stop

            precio_ref = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]
            if precio_ref <= pos["stop"]:
                pnl      = (pos["stop"] - pos["entry"]) * pos["shares"]
                capital += pnl
                trades.append({
                    "symbol"       : symbol,
                    "fecha_entrada": pos["fecha_entrada"],
                    "fecha_salida" : fecha,
                    "entrada"      : round(pos["entry"], 4),
                    "salida"       : round(pos["stop"], 4),
                    "shares"       : pos["shares"],
                    "pnl"          : round(pnl, 2),
                    "resultado"    : "LOSS" if pnl < 0 else "WIN",
                    "capital"      : round(capital, 2),
                })
                cerradas.append(symbol)

        for symbol in cerradas:
            del posiciones[symbol]

        # 5. Risk Guardian — drawdown máximo
        drawdown_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0
        if drawdown_actual > RISK_MAX_DRAWDOWN:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # 6. Rebalanceo dinámico — idéntico al baseline v2.3
        for symbol in list(posiciones.keys()):
            if symbol not in datos:
                continue
            df_reb = datos[symbol]
            if fecha not in df_reb.index:
                continue
            pos    = posiciones[symbol]
            i_reb  = df_reb.index.get_loc(fecha)
            precio = df_reb.iloc[i_reb]["Close"]
            if pd.isna(precio) or precio <= 0:
                continue
            shares_actual = pos["shares"]
            limite_valor  = capital * MAX_POSITION_PCT
            if capital > 0 and shares_actual * precio > limite_valor:
                shares_limite = int(limite_valor / precio)
                if abs(shares_limite - shares_actual) >= REBALANCE_MIN_SHARES and shares_limite > 0:
                    capital += (precio - pos["entry"]) * (shares_actual - shares_limite)
                    posiciones[symbol]["shares"] = shares_limite
                    continue
            shares_optimo, _, _ = calcular_posicion(df_reb, i_reb, capital)
            if shares_optimo <= 0:
                continue
            desviacion = (shares_actual - shares_optimo) / shares_optimo
            if abs(desviacion) <= REBALANCE_THRESHOLD:
                continue
            delta = shares_optimo - shares_actual
            if abs(delta) < REBALANCE_MIN_SHARES:
                continue
            if delta < 0:
                capital += (precio - pos["entry"]) * (-delta)
                posiciones[symbol]["shares"] = shares_optimo
            else:
                entry_blended = (pos["entry"] * shares_actual + precio * delta) / shares_optimo
                posiciones[symbol]["shares"] = shares_optimo
                posiciones[symbol]["entry"]  = round(entry_blended, 4)

        # 7. Portfolio lleno
        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # 8. Escanear señales
        señales   = []
        sp500_hoy = sp500_en_fecha(composicion_df, fecha)

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
            if not detectar_senal(df, i):
                continue
            shares, stop_distance, atr = calcular_posicion(df, i, capital)
            if shares <= 0:
                continue
            bar          = df.iloc[i]
            _sma200_5d   = df.iloc[i - 5]["SMA200"] if i >= 6 else float("nan")
            _sma200_slope = (
                (bar["SMA200"] - _sma200_5d) / bar["ATR"]
                if not pd.isna(_sma200_5d) else 0.0
            )
            score = (bar["Close"] - bar["SMA50"]) / bar["ATR"] + _sma200_slope
            señales.append({
                "symbol"       : symbol,
                "score"        : score,
                "shares"       : shares,
                "stop_distance": stop_distance,
                "high"         : bar["High"],
                "atr"          : atr,
            })

        señales      = sorted(señales, key=lambda x: x["score"], reverse=True)
        slots_libres = MAX_POSITIONS - len(posiciones)
        señales      = señales[:slots_libres]

        # 9. Abrir posiciones al día siguiente
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
                if df.loc[fecha_entrada, "High"] >= buy_stop:
                    coste = buy_stop * señal["shares"]
                    if coste > capital:
                        continue
                    posiciones[symbol] = {
                        "entry"        : buy_stop,
                        "stop"         : stop_loss,
                        "shares"       : señal["shares"],
                        "fecha_entrada": fecha_entrada,
                    }

        curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})

    # Cerrar posiciones abiertas al final del período
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
            "entrada"      : round(pos["entry"], 4),
            "salida"       : round(ultimo_cierre, 4),
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
    if not trades:
        return {
            "capital_final": round(capital_final, 2),
            "cagr": 0.0, "profit_factor": 0.0,
            "win_rate": 0.0, "max_drawdown": 0.0,
            "total_trades": 0,
        }

    dt_trades  = pd.DataFrame(trades)
    dt_capital = pd.DataFrame(curva_capital)

    total_trades = len(dt_trades)
    wins   = dt_trades[
        (dt_trades["resultado"] == "WIN") |
        ((dt_trades["resultado"] == "OPEN→CLOSE") & (dt_trades["pnl"] >= 0))
    ]
    losses = dt_trades[
        (dt_trades["resultado"] == "LOSS") |
        ((dt_trades["resultado"] == "OPEN→CLOSE") & (dt_trades["pnl"] < 0))
    ]

    win_rate      = len(wins) / total_trades if total_trades > 0 else 0
    ganancia      = wins["pnl"].sum()   if len(wins)   > 0 else 0
    perdida       = losses["pnl"].abs().sum() if len(losses) > 0 else 1
    profit_factor = ganancia / perdida if perdida > 0 else float("inf")

    capital_series = dt_capital["capital"].values
    pico           = capital_series[0]
    max_drawdown   = 0.0
    for c in capital_series:
        if c > pico:
            pico = c
        dd = (pico - c) / pico if pico > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt   = datetime.strptime(END_DATE,   "%Y-%m-%d")
    years    = (end_dt - start_dt).days / 365.25
    cagr     = (capital_final / CAPITAL_INICIAL) ** (1.0 / years) - 1

    return {
        "capital_final": round(capital_final, 2),
        "cagr"         : cagr,
        "profit_factor": round(profit_factor, 4),
        "win_rate"     : win_rate,
        "max_drawdown" : max_drawdown,
        "total_trades" : total_trades,
    }


# ==================================================
# TABLA COMPARATIVA + ANÁLISIS DE INFLEXIÓN
# ==================================================

def imprimir_tabla(resultados):
    sep  = "═" * 92
    sep2 = "─" * 92

    ref = resultados.get(1.00, {})
    ref_cap = ref.get("capital_final", 0)
    ref_pf  = ref.get("profit_factor", 0)
    ref_dd  = ref.get("max_drawdown",  0)
    ref_cagr= ref.get("cagr", 0)

    print(f"\n\n{sep}")
    print(f"  LIBERTAD_2045 — EXPERIMENTO 40-ter — Optimización del multiplicador de trailing stop")
    print(f"  Período: {START_DATE} → {END_DATE}  |  Capital: {CAPITAL_INICIAL:.0f} €  |  Aportación anual: {APORTACION_ANUAL:.0f} €")
    print(sep)

    print(f"\n  {'Factor':>7}  {'Capital final':>14}  {'CAGR':>7}  {'PF':>7}  {'WR':>7}  {'DD máx':>7}  {'Trades':>7}  {'vs ref'}  {'Aprobado?'}")
    print(f"  {sep2}")

    aprobados = []

    for factor in FACTORES:
        m = resultados.get(factor, {})
        if not m:
            continue

        cap    = m["capital_final"]
        cagr   = m["cagr"]
        pf     = m["profit_factor"]
        wr     = m["win_rate"]
        dd     = m["max_drawdown"]
        trades = m["total_trades"]

        if factor == 1.00:
            vs_ref   = "  (ref)"
            aprobado = "   —"
        else:
            delta_cap = cap - ref_cap
            sign      = "+" if delta_cap >= 0 else ""
            vs_ref    = f"{sign}{delta_cap/1e6:+.2f}M"
            mejora_pf  = pf  > ref_pf
            mejora_dd  = dd  < ref_dd
            mejora_cap = cap > ref_cap
            ok = mejora_pf and mejora_dd and mejora_cap
            aprobado = f"  ✓ PF DD Cap" if ok else (
                "  " +
                ("✓" if mejora_pf  else "✗") + "PF " +
                ("✓" if mejora_dd  else "✗") + "DD " +
                ("✓" if mejora_cap else "✗") + "Cap"
            )
            if ok:
                aprobados.append(factor)

        marker = " ◀ REF" if factor == 1.00 else ""

        print(
            f"  ×{factor:.2f}    "
            f"{cap:>14,.0f}  "
            f"{cagr:>7.2%}  "
            f"{pf:>7.4f}  "
            f"{wr:>7.1%}  "
            f"{dd:>7.1%}  "
            f"{trades:>7d}  "
            f"{vs_ref:>9}  "
            f"{aprobado}{marker}"
        )

    print(f"  {sep2}")

    # --------------------------------------------------
    # Análisis de inflexión
    # --------------------------------------------------
    print(f"\n  PUNTO DE INFLEXIÓN DEL DRAWDOWN")
    print(f"  {sep2}")
    print(f"  {'Factor':>7}  {'DD máx':>7}  {'Delta DD vs anterior':>22}  {'Capital':>14}")

    dd_anterior  = None
    cap_anterior = None
    inflexion    = None

    for factor in sorted(FACTORES):
        m  = resultados.get(factor, {})
        dd = m.get("max_drawdown", None)
        cap= m.get("capital_final", None)
        if dd is None:
            continue
        if dd_anterior is not None:
            delta_dd  = dd - dd_anterior
            delta_cap = cap - cap_anterior
            signo_dd  = "▲ empeora" if delta_dd > 0.001 else ("▼ mejora" if delta_dd < -0.001 else "≈ estable")
            nota = ""
            if delta_dd > 0.001 and delta_cap > 0 and inflexion is None:
                inflexion = factor
                nota = "  ← INFLEXIÓN: DD empeora, Capital sigue subiendo"
            print(f"  ×{factor:.2f}    {dd:>7.1%}  {delta_dd:>+8.1%} {signo_dd:<12}  {cap:>14,.0f}{nota}")
        else:
            print(f"  ×{factor:.2f}    {dd:>7.1%}  {'—':>22}  {cap:>14,.0f}")
        dd_anterior  = dd
        cap_anterior = cap

    # --------------------------------------------------
    # Veredicto
    # --------------------------------------------------
    print(f"\n  VEREDICTO")
    print(f"  {sep2}")

    if aprobados:
        mejor = max(aprobados, key=lambda f: resultados[f]["capital_final"])
        m      = resultados[mejor]
        delta_cap = m["capital_final"] - ref_cap
        print(f"  ✓ Multiplicadores que aprueban los 3 criterios: {[f'×{f:.2f}' for f in sorted(aprobados)]}")
        print(f"  ✓ Óptimo recomendado: ×{mejor:.2f}")
        print(f"    Capital: {m['capital_final']:,.0f} € (+{delta_cap:,.0f} vs ref)")
        print(f"    PF: {m['profit_factor']:.4f}  DD: {m['max_drawdown']:.1%}  WR: {m['win_rate']:.1%}")
    else:
        # Buscar el mejor equilibrio: maximizar capital con DD ≤ ref
        candidatos_dd = {f: r for f, r in resultados.items()
                         if f != 1.00 and r.get("max_drawdown", 1) <= ref_dd}
        if candidatos_dd:
            mejor_eq = max(candidatos_dd, key=lambda f: candidatos_dd[f]["capital_final"])
            m = candidatos_dd[mejor_eq]
            print(f"  ✗ Ningún multiplicador aprueba las 3 métricas simultáneamente.")
            print(f"  ↗ Mejor equilibrio (DD ≤ ref): ×{mejor_eq:.2f}")
            print(f"    Capital: {m['capital_final']:,.0f} €  PF: {m['profit_factor']:.4f}  DD: {m['max_drawdown']:.1%}")
        else:
            # Buscar mayor PF con menor exceso de DD
            mejor_pf = max(
                (f for f in resultados if f != 1.00),
                key=lambda f: resultados[f]["profit_factor"]
            )
            m = resultados[mejor_pf]
            print(f"  ✗ Ningún multiplicador aprueba las 3 métricas simultáneamente.")
            print(f"  ↗ Mejor PF encontrado: ×{mejor_pf:.2f}")
            print(f"    Capital: {m['capital_final']:,.0f} €  PF: {m['profit_factor']:.4f}  DD: {m['max_drawdown']:.1%}")

    if inflexion:
        print(f"\n  Punto de inflexión DD: en ×{inflexion:.2f} el DD empieza a empeorar")
        print(f"  mientras el capital sigue mejorando → límite de seguridad del sistema")

    print(f"\n{sep}\n")


# ==================================================
# GUARDAR CSV COMPARATIVO
# ==================================================

def guardar_comparativo(resultados):
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filas = []
    for factor in FACTORES:
        m = resultados.get(factor, {})
        if not m:
            continue
        filas.append({
            "factor"       : factor,
            "capital_final": m["capital_final"],
            "cagr"         : round(m["cagr"], 6),
            "profit_factor": m["profit_factor"],
            "win_rate"     : round(m["win_rate"], 4),
            "max_drawdown" : round(m["max_drawdown"], 4),
            "total_trades" : m["total_trades"],
        })
    path = f"{LOG_DIR}/exp40ter_comparativa_{timestamp}.csv"
    pd.DataFrame(filas).to_csv(path, index=False)
    print(f"  CSV guardado: {path}")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 70)
    print("  LIBERTAD_2045 — EXPERIMENTO 40-ter")
    print("  Optimización del multiplicador de trailing stop")
    print("=" * 70)
    print(f"  Período    : {START_DATE} → {END_DATE}")
    print(f"  Capital    : {CAPITAL_INICIAL:.0f} € + {APORTACION_ANUAL:.0f} €/año")
    print(f"  Riesgo/op  : {RISK_PERCENT:.2%}")
    print(f"  Max pos    : {MAX_POSITIONS}")
    print(f"  Factores   : {FACTORES}")
    print()

    # Datos descargados una sola vez
    comp_df  = cargar_composicion_sp500()
    universo = universo_historico_sp500(comp_df)
    print(f"  Universo   : {len(universo)} activos históricos únicos")

    datos = descargar_datos(universo, START_DATE, END_DATE)
    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    resultados = {}
    t_total = time.time()

    for factor in FACTORES:
        print(f"  ── Factor ×{factor:.2f} ", end="", flush=True)
        t0 = time.time()
        trades, curva_capital, capital_final = ejecutar_backtest(datos, comp_df, factor)
        metricas = calcular_metricas(trades, curva_capital, capital_final)
        resultados[factor] = metricas
        elapsed = time.time() - t0
        print(
            f"→ Capital: {capital_final:>13,.0f} €  "
            f"PF: {metricas['profit_factor']:.4f}  "
            f"DD: {metricas['max_drawdown']:.1%}  "
            f"Trades: {metricas['total_trades']}  "
            f"({elapsed:.0f}s)"
        )

    print(f"\n  Total: {time.time() - t_total:.0f}s")

    imprimir_tabla(resultados)
    guardar_comparativo(resultados)
