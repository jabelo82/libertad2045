"""
LIBERTAD_2045 — Backtest Experimento 40-bis
=============================================
Filtro de estado de mercado SPY (market_filter.py integrado en backtest).

    El backtest replica exactamente backtest_expandido.py (v2.3 baseline) con
    una única diferencia: antes de escanear señales se evalúa el estado del SPY.

    FILTRO SPY (lógica idéntica a market_filter.py):
        ALCISTA : close > SMA50  y  SMA50 > SMA200  → opera normal
        NEUTRO  : close > SMA200 pero close <= SMA50 → opera normal
        BAJISTA : close <= SMA200                    → no abre nuevas posiciones

    Las posiciones existentes mantienen sus stops activos en estado BAJISTA,
    igual que el Risk Guardian en drawdown.

    Si los datos de SPY no están disponibles en una fecha concreta → NEUTRO
    (falla segura — no bloquea por error de datos).

Baseline v2.3 (backtest_expandido.py):
    PF: 2.163 | TIR: ~29% | Win Rate: 48.6%
    Drawdown máx: 12.9% | Capital final: 2.711.947 € | Trades: 1.535

Criterio de aprobación:
    Las tres métricas principales (PF, DD, capital final) deben mejorar
    simultáneamente para aprobar la integración en producción.

Uso:
    python backtest_exp40bis.py

Tiempo estimado con caché: <1 minuto
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
# Parámetros del backtest — idénticos a backtest_expandido.py
# --------------------------------------------------

START_DATE       = "2006-01-01"
END_DATE         = "2025-12-31"

CAPITAL_INICIAL  = 4000.0
APORTACION_ANUAL = 4000.0

RISK_PERCENT     = 0.0085
ATR_MULTIPLIER   = 3.1
MAX_POSITION_PCT = 0.25
MAX_POSITIONS    = 10
BUFFER           = 0.05

VOLATILITY_MODE  = "B1"
B1_VENTANA       = 252
B1_MULT_MIN      = 2.2
B1_MULT_MAX      = 4.0

B2_VENTANA       = 252
B2_UMBRAL_ALTO   = 0.40
B2_UMBRAL_BAJO   = 0.20
B2_MULT_ALTO     = 2.5
B2_MULT_MEDIO    = 3.1
B2_MULT_BAJO     = 3.7

SALIDA_POR_CIERRE = True

# --------------------------------------------------
# Risk Guardian — idéntico a producción
# --------------------------------------------------
RISK_MIN_CAPITAL  = 2000.0
RISK_MAX_DRAWDOWN = 0.10

# --------------------------------------------------
# Rebalanceo dinámico — idéntico a rebalance.py
# --------------------------------------------------
REBALANCE_THRESHOLD  = 0.25
REBALANCE_MIN_SHARES = 5

# --------------------------------------------------
# Universo dinámico S&P500 — fja05680/sp500
# --------------------------------------------------
SP500_COMP_CACHE = "sp500_composicion.csv"
SP500_COMP_URL   = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)

LOG_DIR = "backtest_results"
DELAY   = 2


# --------------------------------------------------
# Universo S&P500 completo (~420 activos)
# --------------------------------------------------

SP500 = [

    # Tecnología
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

    # Comunicaciones
    "GOOGL", "GOOG",  "META",  "NFLX",  "DIS",
    "CMCSA", "T",     "VZ",    "TMUS",  "CHTR",
    "WBD",   "OMC",   "FOXA",  "NWS",   "PARA",
    "NDAQ",

    # Consumo discrecional
    "AMZN",  "TSLA",  "HD",    "MCD",   "NKE",
    "SBUX",  "LOW",   "TJX",   "BKNG",  "MAR",
    "HLT",   "RCL",   "CCL",   "EXPE",  "ETSY",
    "EBAY",  "ORLY",  "AZO",   "DLTR",  "DG",
    "BBY",   "ROST",  "KMX",   "PHM",   "DHI",
    "LEN",   "NVR",   "TOL",   "MHK",   "POOL",
    "CMG",   "YUM",   "DRI",   "QSR",   "APTV",
    "BWA",   "GPC",   "LKQ",   "ALK",   "DAL",
    "LUV",   "UAL",

    # Consumo básico
    "WMT",   "PG",    "KO",    "PEP",   "COST",
    "PM",    "MO",    "CL",    "KMB",   "GIS",
    "CAG",   "SJM",   "HRL",   "MKC",   "CHD",
    "CLX",   "EL",    "COTY",  "KHC",   "MDLZ",
    "MNST",  "BG",    "SMG",

    # Salud
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

    # Financiero
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

    # Industrial
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

    # Energía
    "XOM",   "CVX",   "COP",   "SLB",   "EOG",
    "MPC",   "PSX",   "VLO",   "DVN",   "FANG",
    "OXY",   "APA",   "HAL",   "BKR",   "NOV",
    "FTI",   "CVI",   "HES",   "MRO",   "OKE",
    "WMB",   "HP",

    # Materiales
    "LIN",   "APD",   "ECL",   "NEM",   "FCX",
    "DOW",   "DD",    "PPG",   "SHW",   "AVY",
    "IP",    "PKG",   "SEE",   "SON",   "ALB",
    "FMC",   "CE",    "EMN",   "IFF",   "NUE",
    "RS",    "CF",    "MOS",   "WLK",

    # Utilities
    "NEE",   "DUK",   "SO",    "D",     "AEP",
    "EXC",   "SRE",   "XEL",   "WEC",   "ES",
    "ETR",   "FE",    "PPL",   "CMS",   "NI",
    "AES",   "EIX",   "PEG",   "CNP",   "LNT",
    "AEE",   "ATO",   "DTE",   "ED",    "EVRG",
    "NRG",   "PCG",   "PNW",

    # Inmobiliario
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

UNIVERSE_COMPLETO = SP500


# ==================================================
# UNIVERSO DINÁMICO S&P500
# ==================================================

def cargar_composicion_sp500() -> pd.DataFrame:
    if os.path.exists(SP500_COMP_CACHE):
        print("  Composición S&P500 : caché local")
        return pd.read_csv(SP500_COMP_CACHE, index_col=0, parse_dates=True)

    print("  Composición S&P500 : descargando desde GitHub…")
    try:
        df = pd.read_csv(SP500_COMP_URL, index_col=0, parse_dates=True)
        df.to_csv(SP500_COMP_CACHE)
        print(f"  Guardado en        : {SP500_COMP_CACHE}")
        return df
    except Exception as e:
        print(f"  ADVERTENCIA: no se pudo descargar composición S&P500: {e}")
        print("  Usando universo estático como fallback.")
        return pd.DataFrame()


def universo_historico_sp500(comp_df: pd.DataFrame) -> list:
    if comp_df.empty:
        return list(SP500)

    import re
    _date_suffix = re.compile(r'-\d{6,8}$')
    _valid_ticker = re.compile(r'^[A-Z]{1,5}$')

    todos: set = set()
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


def sp500_en_fecha(comp_df: pd.DataFrame, fecha) -> set:
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
# FILTRO SPY — Experimento 40-bis
# ==================================================

def cargar_spy_historico(start: str, end: str) -> pd.DataFrame | None:
    """
    Descarga datos históricos de SPY para el período completo del backtest.
    Calcula SMA50 y SMA200. Retorna un DataFrame indexado por fecha, o None si falla.

    Usa obtener_datos_cached si está disponible (beneficia del caché local),
    con fallback directo a yfinance.
    """
    print("  Datos SPY          : ", end="", flush=True)
    try:
        try:
            from data_manager import obtener_datos_cached
            df = obtener_datos_cached("SPY", start, end)
        except Exception:
            df = yf.download("SPY", start=start, end=end, auto_adjust=True, progress=False)

        if df is None or len(df) < 200:
            print(f"insuficientes ({len(df) if df is not None else 0} barras) — filtro desactivado")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.copy()
        close = df["Close"].squeeze()
        df["SMA50"]  = close.rolling(50).mean()
        df["SMA200"] = close.rolling(200).mean()
        df.index = pd.to_datetime(df.index).tz_localize(None)

        print(f"{len(df)} barras OK")
        return df

    except Exception as e:
        print(f"ERROR ({e}) — filtro desactivado")
        return None


def estado_mercado_spy(spy_df: pd.DataFrame, fecha) -> str:
    """
    Clasifica el estado del mercado en `fecha` usando los datos históricos de SPY.
    Replica exactamente la lógica de market_filter.evaluar_mercado().

    Retorna "ALCISTA", "NEUTRO" o "BAJISTA".
    Si los datos no están disponibles en esa fecha → "NEUTRO" (falla segura).
    """
    if spy_df is None:
        return "NEUTRO"

    try:
        fecha_ts = pd.Timestamp(fecha).tz_localize(None)

        # asof: toma el último dato disponible hasta esa fecha inclusive
        idx = spy_df.index.asof(fecha_ts)
        if pd.isna(idx):
            return "NEUTRO"

        row    = spy_df.loc[idx]
        close  = row["Close"]
        sma50  = row["SMA50"]
        sma200 = row["SMA200"]

        if pd.isna(sma50) or pd.isna(sma200) or pd.isna(close):
            return "NEUTRO"

        if close <= sma200:
            return "BAJISTA"
        elif close <= sma50:
            return "NEUTRO"
        else:
            return "ALCISTA"

    except Exception:
        return "NEUTRO"


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
            raise ValueError(f"Columna requerida no encontrada: {col}")

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

    df["vol_media20"] = volume.rolling(20).mean()

    if VOLATILITY_MODE == "B1":
        df["ATR_PERCENTIL"] = df["ATR"].rolling(B1_VENTANA).rank(pct=True)

    if VOLATILITY_MODE == "B2":
        retornos = close.pct_change()
        df["VOL_ANUAL"] = retornos.rolling(B2_VENTANA).std() * np.sqrt(252)

    return df


# ==================================================
# MULTIPLICADOR DINÁMICO
# ==================================================

def obtener_multiplicador(df, i):

    if VOLATILITY_MODE == "OFF":
        return ATR_MULTIPLIER

    row = df.iloc[i]

    if VOLATILITY_MODE == "B1":
        percentil = row.get("ATR_PERCENTIL", np.nan)
        if pd.isna(percentil):
            return ATR_MULTIPLIER
        mult = B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil
        return round(mult, 2)

    if VOLATILITY_MODE == "B2":
        vol = row.get("VOL_ANUAL", np.nan)
        if pd.isna(vol):
            return ATR_MULTIPLIER
        if vol > B2_UMBRAL_ALTO:
            return B2_MULT_ALTO
        elif vol < B2_UMBRAL_BAJO:
            return B2_MULT_BAJO
        else:
            return B2_MULT_MEDIO

    return ATR_MULTIPLIER


# ==================================================
# SEÑAL
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

    tendencia = last["Close"] > last["SMA200"] and last["SMA200"] > prev["SMA200"]

    if not tendencia:
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

    recuperacion = last["Close"] > last["SMA50"]

    return recuperacion


# ==================================================
# POSITION SIZING
# ==================================================

def calcular_posicion(df, i, capital):

    atr        = df.iloc[i]["ATR"]
    last_price = df.iloc[i]["Close"]

    if pd.isna(atr) or atr <= 0:
        return 0, None, None

    if pd.isna(last_price) or last_price <= 0.01:
        return 0, None, None

    multiplicador = obtener_multiplicador(df, i)
    stop_distance = atr * multiplicador

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

    from data_manager import obtener_datos_cached

    print(f"\nCargando datos ({start} → {end})...")
    print(f"Universo total: {len(universe)} activos\n")

    datos   = {}
    errores = []

    for i, symbol in enumerate(universe, 1):

        try:
            df_raw = obtener_datos_cached(symbol, start, end)

            if df_raw is None or len(df_raw) < 200:
                errores.append(symbol)
                print(f"  [{i:3d}/{len(universe)}] {symbol:12s} → sin datos")
                continue

            df = calcular_indicadores(df_raw)
            datos[symbol] = df

            print(f"  [{i:3d}/{len(universe)}] {symbol:12s} → {len(df)} barras")

        except Exception as e:
            errores.append(symbol)
            print(f"  [{i:3d}/{len(universe)}] {symbol:12s} → ERROR: {e}")

    print(f"\nActivos cargados   : {len(datos)}")
    print(f"Activos con error  : {len(errores)}")

    return datos


# ==================================================
# MOTOR DEL BACKTEST — con Risk Guardian + Filtro SPY
# ==================================================

def ejecutar_backtest(datos, composicion_df=None, spy_df=None):
    """
    Motor principal del backtest.

    Parámetros:
        datos          : dict {symbol: DataFrame} generado por descargar_datos()
        composicion_df : DataFrame con composición histórica del S&P500.  None → sin filtro.
        spy_df         : DataFrame con datos históricos de SPY (SMA50, SMA200).
                         None → filtro SPY desactivado (opera siempre).
    """

    if composicion_df is None:
        composicion_df = pd.DataFrame()

    filtro_spy_activo = spy_df is not None

    print("\nEjecutando backtest...\n")
    print(f"  Risk Guardian activo:")
    print(f"    Capital mínimo  : {RISK_MIN_CAPITAL:.0f}€")
    print(f"    Drawdown máximo : {RISK_MAX_DRAWDOWN:.0%}")
    universo_dinamico = not composicion_df.empty
    print(f"  Universo dinámico : {'SÍ (survivorship bias eliminado)' if universo_dinamico else 'NO (estático)'}")
    print(f"  Rebalanceo        : SÍ (umbral {REBALANCE_THRESHOLD:.0%}, mín. {REBALANCE_MIN_SHARES} acc.)")
    print(f"  Filtro SPY        : {'ACTIVO (BAJISTA=no entradas)' if filtro_spy_activo else 'INACTIVO (datos no disponibles)'}")
    print()

    fechas = sorted(set(
        fecha
        for df in datos.values()
        for fecha in df.index
    ))

    capital        = CAPITAL_INICIAL
    capital_pico   = CAPITAL_INICIAL
    posiciones     = {}
    trades         = []
    curva_capital  = []

    dias_bloqueados_drawdown = 0
    dias_detenido_capital    = 0
    rebalanceos_ejecutados   = 0
    dias_bloqueados_spy      = 0   # días con mercado BAJISTA
    dias_alcista             = 0
    dias_neutro              = 0

    for fecha in fechas:

        idx = fechas.index(fecha)

        # --------------------------------------------------
        # 1. Aportación anual
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
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 4. Gestionar posiciones abiertas con trailing stop
        # --------------------------------------------------
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
                mult       = obtener_multiplicador(df, i_actual)
                nuevo_stop = round(bar["High"] - atr * mult, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop

            # Break-even (idéntico a rebalance.py)
            if not pd.isna(atr) and atr > 0:
                be_stop = round(pos["entry"] + 0.5 * atr, 2)
                if bar["Close"] >= pos["entry"] + 1.5 * atr and be_stop > pos["stop"]:
                    pos["stop"] = be_stop

            precio_referencia = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]

            if precio_referencia <= pos["stop"]:

                precio_salida = pos["stop"]
                pnl           = (precio_salida - pos["entry"]) * pos["shares"]
                capital      += pnl

                trades.append({
                    "symbol"       : symbol,
                    "clase"        : pos["clase"],
                    "fecha_entrada": pos["fecha_entrada"],
                    "fecha_salida" : fecha,
                    "entrada"      : round(pos["entry"], 4),
                    "salida"       : round(precio_salida, 4),
                    "shares"       : pos["shares"],
                    "pnl"          : round(pnl, 2),
                    "resultado"    : "LOSS" if pnl < 0 else "WIN",
                    "capital"      : round(capital, 2),
                })

                cerradas.append(symbol)

        for symbol in cerradas:
            del posiciones[symbol]

        # --------------------------------------------------
        # 5. Risk Guardian — drawdown máximo
        # --------------------------------------------------
        drawdown_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0

        if drawdown_actual > RISK_MAX_DRAWDOWN:
            dias_bloqueados_drawdown += 1
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 6. Rebalanceo dinámico de posiciones abiertas
        # --------------------------------------------------
        for symbol in list(posiciones.keys()):

            if symbol not in datos:
                continue

            df_reb = datos[symbol]

            if fecha not in df_reb.index:
                continue

            pos       = posiciones[symbol]
            i_reb     = df_reb.index.get_loc(fecha)
            precio    = df_reb.iloc[i_reb]["Close"]

            if pd.isna(precio) or precio <= 0:
                continue

            shares_actual = pos["shares"]
            valor_actual  = shares_actual * precio

            limite_valor = capital * MAX_POSITION_PCT
            if capital > 0 and valor_actual > limite_valor:
                shares_limite = int(limite_valor / precio)
                delta_lim     = shares_limite - shares_actual
                if abs(delta_lim) >= REBALANCE_MIN_SHARES and shares_limite > 0:
                    shares_vendidas = shares_actual - shares_limite
                    pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                    capital        += pnl_parcial
                    posiciones[symbol]["shares"] = shares_limite
                    rebalanceos_ejecutados += 1
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
                shares_vendidas = -delta
                pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                capital        += pnl_parcial
                posiciones[symbol]["shares"] = shares_optimo

            else:
                entry_blended = (
                    (pos["entry"] * shares_actual + precio * delta) / shares_optimo
                )
                posiciones[symbol]["shares"] = shares_optimo
                posiciones[symbol]["entry"]  = round(entry_blended, 4)

            rebalanceos_ejecutados += 1

        # --------------------------------------------------
        # 7. Portfolio lleno — no escanear señales
        # --------------------------------------------------
        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # --------------------------------------------------
        # 7.5 Filtro SPY — Experimento 40-bis
        # Evalúa el estado del mercado en esta fecha.
        # BAJISTA (SPY <= SMA200) → no abrir nuevas posiciones.
        # Las posiciones existentes mantienen sus stops activos.
        # NEUTRO/ALCISTA → opera con normalidad.
        # --------------------------------------------------
        estado_spy = estado_mercado_spy(spy_df, fecha)

        if estado_spy == "BAJISTA":
            dias_bloqueados_spy += 1
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue
        elif estado_spy == "NEUTRO":
            dias_neutro += 1
        else:
            dias_alcista += 1

        # --------------------------------------------------
        # 8. Escanear señales
        # --------------------------------------------------
        señales = []

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

            if symbol in CRYPTO:
                clase = "CRIPTO"
            elif symbol in MATERIAS_PRIMAS:
                clase = "MATERIA_PRIMA"
            elif symbol in ETFS:
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
                        "entry"         : buy_stop,
                        "stop"          : stop_loss,
                        "shares"        : señal["shares"],
                        "clase"         : señal["clase"],
                        "fecha_entrada" : fecha_entrada,
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
        pnl           = (ultimo_cierre - pos["entry"]) * pos["shares"]
        capital      += pnl

        trades.append({
            "symbol"       : symbol,
            "clase"        : pos["clase"],
            "fecha_entrada": pos["fecha_entrada"],
            "fecha_salida" : df.index[-1],
            "entrada"      : round(pos["entry"], 4),
            "salida"       : round(ultimo_cierre, 4),
            "shares"       : pos["shares"],
            "pnl"          : round(pnl, 2),
            "resultado"    : "OPEN→CLOSE",
            "capital"      : round(capital, 2),
        })

    total_dias = len(fechas)
    print(f"\n  Risk Guardian — resumen:")
    print(f"    Días bloqueados por drawdown : {dias_bloqueados_drawdown}")
    print(f"    Días detenido por capital    : {dias_detenido_capital}")
    print(f"    Rebalanceos ejecutados       : {rebalanceos_ejecutados}")
    print(f"\n  Filtro SPY — resumen:")
    print(f"    Días ALCISTA                 : {dias_alcista}  ({dias_alcista/total_dias:.1%})")
    print(f"    Días NEUTRO                  : {dias_neutro}  ({dias_neutro/total_dias:.1%})")
    print(f"    Días BAJISTA (bloqueados)    : {dias_bloqueados_spy}  ({dias_bloqueados_spy/total_dias:.1%})")

    spy_stats = {
        "dias_alcista" : dias_alcista,
        "dias_neutro"  : dias_neutro,
        "dias_bajista" : dias_bloqueados_spy,
        "total_dias"   : total_dias,
    }

    return trades, curva_capital, capital, spy_stats


# ==================================================
# MÉTRICAS
# ==================================================

def calcular_metricas(trades, curva_capital, capital_final):

    if not trades:
        return {}

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
    ganancia      = wins["pnl"].sum()   if len(wins)   > 0 else 0
    perdida       = losses["pnl"].abs().sum() if len(losses) > 0 else 1
    profit_factor = ganancia / perdida if perdida > 0 else float("inf")

    capital_series = df_capital["capital"].values
    pico           = capital_series[0]
    max_drawdown   = 0.0

    for c in capital_series:
        if c > pico:
            pico = c
        dd = (pico - c) / pico if pico > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    retorno_total  = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL
    pnl_medio_win  = wins["pnl"].mean()   if len(wins)   > 0 else 0
    pnl_medio_loss = losses["pnl"].mean() if len(losses) > 0 else 0
    expectativa    = (win_rate * pnl_medio_win) + ((1 - win_rate) * pnl_medio_loss)

    por_clase = {}
    for clase in ["ACCION", "CRIPTO", "MATERIA_PRIMA", "ETF"]:
        subset = df_trades[df_trades["clase"] == clase]
        if len(subset) > 0:
            w = subset[subset["resultado"] == "WIN"]
            por_clase[clase] = {
                "trades"   : len(subset),
                "wins"     : len(w),
                "win_rate" : len(w) / len(subset),
                "pnl_total": round(subset["pnl"].sum(), 2),
            }

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
        "por_clase"       : por_clase,
    }


# ==================================================
# GUARDAR RESULTADOS
# ==================================================

def guardar_resultados(trades, curva_capital, metricas):

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if trades:
        pd.DataFrame(trades).to_csv(
            f"{LOG_DIR}/exp40bis_trades_{timestamp}.csv", index=False)
        print(f"\nTrades guardados    : {LOG_DIR}/exp40bis_trades_{timestamp}.csv")

    if curva_capital:
        pd.DataFrame(curva_capital).to_csv(
            f"{LOG_DIR}/exp40bis_capital_{timestamp}.csv", index=False)
        print(f"Curva de capital    : {LOG_DIR}/exp40bis_capital_{timestamp}.csv")

    if metricas:
        metricas_flat = {k: v for k, v in metricas.items() if k != "por_clase"}
        pd.DataFrame([metricas_flat]).to_csv(
            f"{LOG_DIR}/exp40bis_metricas_{timestamp}.csv", index=False)
        print(f"Métricas guardadas  : {LOG_DIR}/exp40bis_metricas_{timestamp}.csv")


# ==================================================
# INFORME EN CONSOLA
# ==================================================

def imprimir_informe(metricas, spy_stats):

    sep = "─" * 55

    # Baseline v2.3 para comparativa directa
    BASELINE = {
        "profit_factor" : 2.163,
        "max_drawdown"  : 0.129,
        "capital_final" : 2_711_947,
        "win_rate"      : 0.486,
        "total_trades"  : 1535,
    }

    def delta(val, base, higher_is_better=True):
        d = val - base
        arrow = "▲" if d > 0 else "▼"
        return f"{arrow} {abs(d):.4f}" if higher_is_better == (d > 0) else f"{arrow} {abs(d):.4f}"

    print(f"\n{sep}")
    print(f"  LIBERTAD_2045 — EXPERIMENTO 40-bis")
    print(f"  Filtro SPY (market_filter.py integrado en backtest)")
    print(f"  {START_DATE} → {END_DATE}")
    print(f"  Salida por cierre : {'SÍ' if SALIDA_POR_CIERRE else 'NO'}")
    print(f"  Min. capital      : {RISK_MIN_CAPITAL:.0f}€")
    print(f"  Max. drawdown RG  : {RISK_MAX_DRAWDOWN:.0%}")
    print(sep)

    print(f"\n  CAPITAL")
    print(f"  Inicial          : {metricas['capital_inicial']:>14.2f} €")
    cap_delta = metricas['capital_final'] - BASELINE['capital_final']
    cap_sign  = "+" if cap_delta >= 0 else ""
    print(f"  Final            : {metricas['capital_final']:>14.2f} €   "
          f"[baseline: {BASELINE['capital_final']:,.0f} | {cap_sign}{cap_delta:,.0f}]")
    print(f"  Retorno total    : {metricas['retorno_total']:>14.1%}")

    print(f"\n  OPERATIVA GLOBAL")
    trades_delta = metricas['total_trades'] - BASELINE['total_trades']
    print(f"  Total trades     : {metricas['total_trades']:>14d}   "
          f"[baseline: {BASELINE['total_trades']} | {'+' if trades_delta >= 0 else ''}{trades_delta}]")
    print(f"  Wins             : {metricas['wins']:>14d}")
    print(f"  Losses           : {metricas['losses']:>14d}")
    wr_delta = metricas['win_rate'] - BASELINE['win_rate']
    print(f"  Win rate         : {metricas['win_rate']:>14.1%}   "
          f"[baseline: {BASELINE['win_rate']:.1%} | {'+' if wr_delta >= 0 else ''}{wr_delta:.1%}]")

    print(f"\n  RIESGO")
    pf_delta = metricas['profit_factor'] - BASELINE['profit_factor']
    print(f"  Profit factor    : {metricas['profit_factor']:>14.4f}   "
          f"[baseline: {BASELINE['profit_factor']:.3f} | {'+' if pf_delta >= 0 else ''}{pf_delta:.4f}]")
    dd_delta = metricas['max_drawdown'] - BASELINE['max_drawdown']
    print(f"  Drawdown máximo  : {metricas['max_drawdown']:>14.1%}   "
          f"[baseline: {BASELINE['max_drawdown']:.1%} | {'+' if dd_delta >= 0 else ''}{dd_delta:.1%}]")
    print(f"  PnL medio WIN    : {metricas['pnl_medio_win']:>14.2f} €")
    print(f"  PnL medio LOSS   : {metricas['pnl_medio_loss']:>14.2f} €")
    print(f"  Expectativa/trade: {metricas['expectativa']:>14.2f} €")

    if spy_stats:
        total = spy_stats["total_dias"]
        print(f"\n  FILTRO SPY")
        print(f"  Días ALCISTA     : {spy_stats['dias_alcista']:>14d}   ({spy_stats['dias_alcista']/total:.1%})")
        print(f"  Días NEUTRO      : {spy_stats['dias_neutro']:>14d}   ({spy_stats['dias_neutro']/total:.1%})")
        print(f"  Días BAJISTA     : {spy_stats['dias_bajista']:>14d}   ({spy_stats['dias_bajista']/total:.1%})  ← entradas bloqueadas")

    print(f"\n  VEREDICTO")

    supervivencia = metricas["max_drawdown"] < 0.25
    disciplina    = metricas["profit_factor"] > 1.5
    consistencia  = metricas["win_rate"] > 0.35 and metricas["retorno_total"] > 0

    print(f"  Supervivencia    : {'✓ OK' if supervivencia else '✗ REVISAR'}"
          f"  (drawdown < 25%)")
    print(f"  Disciplina       : {'✓ OK' if disciplina    else '✗ REVISAR'}"
          f"  (profit factor > 1.5)")
    print(f"  Consistencia     : {'✓ OK' if consistencia  else '✗ REVISAR'}"
          f"  (win rate > 35% y retorno > 0)")

    # Criterio de aprobación del experimento 40-bis
    mejora_pf  = metricas["profit_factor"] > BASELINE["profit_factor"]
    mejora_dd  = metricas["max_drawdown"]  < BASELINE["max_drawdown"]
    mejora_cap = metricas["capital_final"] > BASELINE["capital_final"]

    print(f"\n  APROBACIÓN EXP 40-bis (las 3 métricas deben mejorar sobre baseline v2.3):")
    print(f"  PF > {BASELINE['profit_factor']:.3f}  : {'✓' if mejora_pf  else '✗'}  ({metricas['profit_factor']:.4f})")
    print(f"  DD < {BASELINE['max_drawdown']:.1%}  : {'✓' if mejora_dd  else '✗'}  ({metricas['max_drawdown']:.1%})")
    print(f"  Cap > {BASELINE['capital_final']:,.0f} : {'✓' if mejora_cap else '✗'}  ({metricas['capital_final']:,.0f} €)")

    aprobado = mejora_pf and mejora_dd and mejora_cap
    if aprobado:
        print(f"\n  ✓ FILTRO SPY APROBADO — integrar en libertad2045.py")
    else:
        print(f"\n  ✗ FILTRO SPY DESCARTADO — no mejora las 3 métricas simultáneamente")

    print(f"\n{sep}\n")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 55)
    print("  LIBERTAD_2045 — EXPERIMENTO 40-bis")
    print("  Filtro SPY (market_filter.py integrado en backtest)")
    print("=" * 55)
    print(f"  Período      : {START_DATE} → {END_DATE}")
    print(f"  Capital      : {CAPITAL_INICIAL:.0f} €")
    print(f"  Aportación   : {APORTACION_ANUAL:.0f} €/año")
    print(f"  Riesgo/op    : {RISK_PERCENT:.2%}")
    print(f"  ATR base     : {ATR_MULTIPLIER}")
    print(f"  Max pos      : {MAX_POSITIONS}")
    print(f"  Modo vol     : {VOLATILITY_MODE}")
    print(f"  Salida       : {'CIERRE' if SALIDA_POR_CIERRE else 'MÍNIMO'}")
    print(f"  Min. capital : {RISK_MIN_CAPITAL:.0f}€")
    print(f"  Max. DD      : {RISK_MAX_DRAWDOWN:.0%}")
    print(f"  Rebalanceo   : umbral {REBALANCE_THRESHOLD:.0%} / mín. {REBALANCE_MIN_SHARES} acc.")
    print(f"  Filtro SPY   : ALCISTA/NEUTRO=opera | BAJISTA=no entradas")
    print()

    # Datos SPY — cargados antes del universo para detectar fallos pronto
    spy_df = cargar_spy_historico(START_DATE, END_DATE)

    # Universo dinámico del S&P500
    comp_df  = cargar_composicion_sp500()
    universo = universo_historico_sp500(comp_df)

    print(f"  Universo     : {len(universo)} activos históricos únicos")

    datos = descargar_datos(universo, START_DATE, END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    trades, curva_capital, capital_final, spy_stats = ejecutar_backtest(
        datos, composicion_df=comp_df, spy_df=spy_df
    )
    metricas = calcular_metricas(trades, curva_capital, capital_final)
    imprimir_informe(metricas, spy_stats)
    guardar_resultados(trades, curva_capital, metricas)
