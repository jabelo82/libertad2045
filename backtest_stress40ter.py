"""
LIBERTAD_2045 — Stress Test Experimento 40-ter
===============================================
Compara multiplicadores ×0.75 vs ×1.00 en tres períodos de crisis.

Metodología:
  - Datos cargados desde caché 2006-01-01 → 2025-12-31 (sin re-descarga)
  - Indicadores calculados sobre la serie completa (warmup correcto)
  - Backtest ejecutado sólo sobre fechas de cada crisis period
  - Capital inicial fijo 100.000 € por período (sin aportaciones anuales)
    → aísla el efecto de mercado de la inyección de capital

No modifica ningún módulo de producción.

Uso:
    python backtest_stress40ter.py
"""

import warnings
warnings.filterwarnings("ignore")

import os
import time
from datetime import datetime

import pandas as pd
import numpy as np


# --------------------------------------------------
# Parámetros — idénticos a backtest_exp40ter.py
# --------------------------------------------------

CACHE_START = "2006-01-01"
CACHE_END   = "2025-12-31"

# Capital inicial del stress test (portfolio maduro entrando en crisis)
CAPITAL_STRESS = 100_000.0

RISK_PERCENT     = 0.0085
ATR_MULTIPLIER   = 3.1
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

# Multiplicadores bajo evaluación
FACTORES_STRESS = [0.75, 1.00]

# Períodos de crisis
CRISIS = {
    "Crisis 2008":      ("2007-01-01", "2009-12-31"),
    "COVID 2020":       ("2019-01-01", "2020-12-31"),
    "Bear Market 2022": ("2021-01-01", "2022-12-31"),
}


# --------------------------------------------------
# Universo — mismo que backtest_exp40ter.py
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


# ==================================================
# COMPOSICIÓN S&P500
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
# DESCARGA DE DATOS — reutiliza caché 2006-2025
# ==================================================

def cargar_datos_crisis(universe):
    """
    Carga datos desde la caché 2006-2025.
    No descarga nada nuevo; las fechas de crisis se filtran en el backtest.
    """
    from data_manager import obtener_datos_cached
    print(f"\nCargando datos desde caché ({CACHE_START} → {CACHE_END})...")
    print(f"Universo: {len(universe)} activos")
    datos   = {}
    errores = []
    for symbol in universe:
        try:
            df_raw = obtener_datos_cached(symbol, CACHE_START, CACHE_END)
            if df_raw is None or len(df_raw) < 200:
                errores.append(symbol)
                continue
            datos[symbol] = calcular_indicadores(df_raw)
        except Exception:
            errores.append(symbol)
    print(f"Activos cargados: {len(datos)}  |  Con error: {len(errores)}\n")
    return datos


# ==================================================
# MOTOR DEL BACKTEST — paramétrico por factor y período
# ==================================================

def ejecutar_backtest_crisis(datos, composicion_df, factor, period_start, period_end):
    """
    Ejecuta el backtest con lógica idéntica a exp40ter pero:
      - Solo itera sobre fechas en [period_start, period_end]
      - Capital inicial fijo = CAPITAL_STRESS (sin aportaciones)
      - Devuelve también capital_por_año para detectar años negativos
    """
    if composicion_df is None:
        composicion_df = pd.DataFrame()

    start_ts = pd.Timestamp(period_start)
    end_ts   = pd.Timestamp(period_end)

    # Fechas del período de crisis (los datos completos sirven para índices de indicadores)
    fechas = sorted({
        fecha
        for df in datos.values()
        for fecha in df.index
        if start_ts <= fecha <= end_ts
    })

    if not fechas:
        return [], [], CAPITAL_STRESS, {}

    capital      = CAPITAL_STRESS
    capital_pico = CAPITAL_STRESS
    posiciones   = {}
    trades       = []
    curva_capital = []
    capital_por_año = {}

    for idx, fecha in enumerate(fechas):

        # Registrar capital al final de cada año
        if idx > 0 and fecha.year != fechas[idx - 1].year:
            año_anterior = fechas[idx - 1].year
            if año_anterior not in capital_por_año:
                capital_por_año[año_anterior] = round(capital, 2)

        # Actualizar pico
        if capital > capital_pico:
            capital_pico = capital

        # Risk guardian — capital mínimo
        if capital < RISK_MIN_CAPITAL:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Gestionar posiciones — trailing stop con factor variable
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

                # Break-even
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

        # Risk guardian — drawdown máximo
        drawdown_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0
        if drawdown_actual > RISK_MAX_DRAWDOWN:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Rebalanceo dinámico
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

        # Portfolio lleno
        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Escanear señales
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

        # Abrir posiciones al día siguiente
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

    # Registrar el último año
    if fechas:
        último_año = fechas[-1].year
        if último_año not in capital_por_año:
            capital_por_año[último_año] = round(capital, 2)

    # Cerrar posiciones abiertas al final del período
    for symbol, pos in posiciones.items():
        df = datos[symbol]
        if df.empty:
            continue
        last_row = df[df.index <= end_ts]
        if last_row.empty:
            continue
        ultimo_cierre = last_row.iloc[-1]["Close"]
        pnl           = (ultimo_cierre - pos["entry"]) * pos["shares"]
        capital      += pnl
        trades.append({
            "symbol"       : symbol,
            "fecha_entrada": pos["fecha_entrada"],
            "fecha_salida" : last_row.index[-1],
            "entrada"      : round(pos["entry"], 4),
            "salida"       : round(ultimo_cierre, 4),
            "shares"       : pos["shares"],
            "pnl"          : round(pnl, 2),
            "resultado"    : "OPEN→CLOSE",
            "capital"      : round(capital, 2),
        })

    return trades, curva_capital, capital, capital_por_año


# ==================================================
# MÉTRICAS POR PERÍODO
# ==================================================

def calcular_metricas_periodo(trades, curva_capital, capital_final,
                              capital_por_año, period_start, period_end):
    if not trades and not curva_capital:
        return {
            "capital_final": round(capital_final, 2),
            "retorno_pct"  : 0.0,
            "profit_factor": 0.0,
            "win_rate"     : 0.0,
            "max_drawdown" : 0.0,
            "total_trades" : 0,
            "años_negativos": [],
        }

    dt_trades  = pd.DataFrame(trades)      if trades       else pd.DataFrame()
    dt_capital = pd.DataFrame(curva_capital) if curva_capital else pd.DataFrame()

    total_trades = len(dt_trades)
    profit_factor = 0.0
    win_rate      = 0.0

    if total_trades > 0:
        wins   = dt_trades[
            (dt_trades["resultado"] == "WIN") |
            ((dt_trades["resultado"] == "OPEN→CLOSE") & (dt_trades["pnl"] >= 0))
        ]
        losses = dt_trades[
            (dt_trades["resultado"] == "LOSS") |
            ((dt_trades["resultado"] == "OPEN→CLOSE") & (dt_trades["pnl"] < 0))
        ]
        win_rate = len(wins) / total_trades
        ganancia = wins["pnl"].sum()   if len(wins)   > 0 else 0
        perdida  = losses["pnl"].abs().sum() if len(losses) > 0 else 1
        profit_factor = ganancia / perdida if perdida > 0 else float("inf")

    max_drawdown = 0.0
    if not dt_capital.empty:
        capital_series = dt_capital["capital"].values
        pico           = capital_series[0]
        for c in capital_series:
            if c > pico:
                pico = c
            dd = (pico - c) / pico if pico > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd

    retorno_pct = (capital_final - CAPITAL_STRESS) / CAPITAL_STRESS

    # Detectar años negativos comparando capital inicio vs fin de año
    años_negativos = []
    años = sorted(capital_por_año.keys())
    cap_inicio = CAPITAL_STRESS
    for año in años:
        cap_fin = capital_por_año[año]
        if cap_fin < cap_inicio:
            pct = (cap_fin - cap_inicio) / cap_inicio
            años_negativos.append((año, pct))
        cap_inicio = cap_fin

    return {
        "capital_final" : round(capital_final, 2),
        "retorno_pct"   : retorno_pct,
        "profit_factor" : round(profit_factor, 4),
        "win_rate"       : win_rate,
        "max_drawdown"  : max_drawdown,
        "total_trades"  : total_trades,
        "años_negativos": años_negativos,
    }


# ==================================================
# TABLA DE RESULTADOS
# ==================================================

def imprimir_tabla(resultados):
    sep  = "═" * 100
    sep2 = "─" * 100

    print(f"\n\n{sep}")
    print(f"  LIBERTAD_2045 — STRESS TEST EXP 40-ter — ×0.75 vs ×1.00 en mercados adversos")
    print(f"  Capital inicial: {CAPITAL_STRESS:,.0f} €  (sin aportaciones anuales — aísla efecto mercado)")
    print(sep)

    for nombre_crisis, (p_start, p_end) in CRISIS.items():
        print(f"\n  {'─'*96}")
        print(f"  {nombre_crisis.upper()}  [{p_start} → {p_end}]")
        print(f"  {'─'*96}")
        print(f"  {'Factor':>8}  {'Capital final':>14}  {'Retorno':>8}  {'PF':>7}  {'WR':>7}  "
              f"{'DD máx':>7}  {'Trades':>7}  {'Años negativos'}")
        print(f"  {sep2}")

        ref = resultados.get((nombre_crisis, 1.00), {})
        ref_dd  = ref.get("max_drawdown",  0)
        ref_cap = ref.get("capital_final", 0)

        for factor in FACTORES_STRESS:
            key = (nombre_crisis, factor)
            m   = resultados.get(key, {})
            if not m:
                continue

            cap    = m["capital_final"]
            ret    = m["retorno_pct"]
            pf     = m["profit_factor"]
            wr     = m["win_rate"]
            dd     = m["max_drawdown"]
            trades = m["total_trades"]
            años_neg = m["años_negativos"]

            if años_neg:
                años_str = "  " + ", ".join(f"{a}({p:+.1%})" for a, p in años_neg)
            else:
                años_str = "  ninguno"

            marker = ""
            if factor == 1.00:
                marker = "  ◀ REF"
            elif factor == 0.75:
                mejor_dd  = "✓" if dd  <= ref_dd  + 0.005 else "✗"
                mejor_cap = "✓" if cap >= ref_cap - ref_cap * 0.02 else "✗"
                marker = f"  DD{mejor_dd} Cap{mejor_cap}"

            print(
                f"  ×{factor:.2f}    "
                f"{cap:>14,.0f}  "
                f"{ret:>8.1%}  "
                f"{pf:>7.4f}  "
                f"{wr:>7.1%}  "
                f"{dd:>7.1%}  "
                f"{trades:>7d}  "
                f"{años_str}{marker}"
            )

    print(f"\n  {sep2}")

    # --------------------------------------------------
    # Veredicto consolidado
    # --------------------------------------------------
    print(f"\n  VEREDICTO GLOBAL ×0.75 vs ×1.00")
    print(f"  {sep2}")

    periodos_ok  = 0
    periodos_tot = len(CRISIS)
    detalles     = []

    for nombre_crisis in CRISIS:
        m075 = resultados.get((nombre_crisis, 0.75), {})
        m100 = resultados.get((nombre_crisis, 1.00), {})
        if not m075 or not m100:
            continue

        dd_ok  = m075["max_drawdown"] <= m100["max_drawdown"] + 0.005
        cap_ok = m075["capital_final"] >= m100["capital_final"] * 0.98
        ok     = dd_ok and cap_ok

        if ok:
            periodos_ok += 1
        icono = "✓" if ok else "✗"
        dd_delta  = m075["max_drawdown"]  - m100["max_drawdown"]
        cap_delta = m075["capital_final"] - m100["capital_final"]
        detalles.append(
            f"  {icono} {nombre_crisis:<22}  "
            f"DD: {m075['max_drawdown']:.1%} vs {m100['max_drawdown']:.1%} "
            f"(Δ{dd_delta:+.1%})  |  "
            f"Capital: {m075['capital_final']:>12,.0f} vs {m100['capital_final']:>12,.0f} "
            f"(Δ{cap_delta:+,.0f} €)"
        )

    for d in detalles:
        print(d)

    print(f"\n  Períodos donde ×0.75 ≥ ×1.00: {periodos_ok}/{periodos_tot}")

    if periodos_ok == periodos_tot:
        print(f"\n  ✅ APROBADO — ×0.75 no amplifica pérdidas en ningún período de crisis.")
        print(f"     Queda habilitado para implementación en producción.")
    elif periodos_ok >= 2:
        print(f"\n  ⚠️  APROBADO PARCIAL — ×0.75 supera a ×1.00 en {periodos_ok}/{periodos_tot} períodos.")
        print(f"     Revisar el período conflictivo antes de producción.")
    else:
        print(f"\n  ❌ NO APROBADO — ×0.75 muestra peor comportamiento en crisis.")
        print(f"     Mantener ×1.00 como multiplicador de producción.")

    print(f"\n{sep}\n")


# ==================================================
# GUARDAR CSV
# ==================================================

def guardar_resultados(resultados):
    LOG_DIR = "backtest_results"
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filas = []
    for (crisis, factor), m in resultados.items():
        filas.append({
            "crisis"       : crisis,
            "factor"       : factor,
            "capital_final": m["capital_final"],
            "retorno_pct"  : round(m["retorno_pct"], 6),
            "profit_factor": m["profit_factor"],
            "win_rate"     : round(m["win_rate"], 4),
            "max_drawdown" : round(m["max_drawdown"], 4),
            "total_trades" : m["total_trades"],
            "años_negativos": "; ".join(f"{a}({p:+.2%})" for a, p in m["años_negativos"]),
        })
    path = f"{LOG_DIR}/stress40ter_{timestamp}.csv"
    pd.DataFrame(filas).to_csv(path, index=False)
    print(f"  CSV guardado: {path}")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 70)
    print("  LIBERTAD_2045 — STRESS TEST Experimento 40-ter")
    print("  Comparativa ×0.75 vs ×1.00 en períodos de crisis")
    print("=" * 70)
    print(f"  Capital inicial : {CAPITAL_STRESS:,.0f} € (sin aportaciones)")
    print(f"  Riesgo/op       : {RISK_PERCENT:.2%}")
    print(f"  Max posiciones  : {MAX_POSITIONS}")
    print(f"  Factores        : {FACTORES_STRESS}")
    print()
    for nombre, (s, e) in CRISIS.items():
        print(f"  {nombre:<22}: {s} → {e}")
    print()

    comp_df  = cargar_composicion_sp500()
    universo = universo_historico_sp500(comp_df)
    print(f"  Universo histórico: {len(universo)} activos")

    datos = cargar_datos_crisis(universo)
    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    resultados = {}
    t_total = time.time()

    for nombre_crisis, (p_start, p_end) in CRISIS.items():
        print(f"\n  ── {nombre_crisis} [{p_start} → {p_end}]")
        for factor in FACTORES_STRESS:
            t0 = time.time()
            trades, curva, capital_final, cap_año = ejecutar_backtest_crisis(
                datos, comp_df, factor, p_start, p_end
            )
            metricas = calcular_metricas_periodo(
                trades, curva, capital_final, cap_año, p_start, p_end
            )
            resultados[(nombre_crisis, factor)] = metricas
            elapsed = time.time() - t0
            años_neg = metricas["años_negativos"]
            años_str = (", ".join(f"{a}({p:+.1%})" for a, p in años_neg)
                        if años_neg else "—")
            print(
                f"     ×{factor:.2f}  Capital: {capital_final:>12,.0f} €  "
                f"Ret: {metricas['retorno_pct']:>+7.1%}  "
                f"DD: {metricas['max_drawdown']:.1%}  "
                f"PF: {metricas['profit_factor']:.4f}  "
                f"WR: {metricas['win_rate']:.1%}  "
                f"Trades: {metricas['total_trades']}  "
                f"Años neg: {años_str}  ({elapsed:.0f}s)"
            )

    print(f"\n  Total: {time.time() - t_total:.0f}s")

    imprimir_tabla(resultados)
    guardar_resultados(resultados)
