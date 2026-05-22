"""
LIBERTAD_2045 — Experimento 40
================================
Comparativa de 5 variantes de trailing stop (2005-2025).

  Variante A — Línea base    : trailing dinámico B1 actual (referencia)
  Variante B — Break-even    : una sola subida a entry+0.5×ATR cuando
                               precio > entry+1.5×ATR; sin trailing posterior
  Variante C — Agresivo      : B1 con multiplicador × 0.70
  Variante D — Conservador   : B1 con multiplicador × 1.30
  Variante E — Fijo sin pct. : High - ATR × 3.1 (sin ajuste de percentil)

Los datos se cargan UNA VEZ y se reutilizan en los 5 backtests.
No se toca ningún módulo de producción.

Uso:
    python backtest_exp40.py
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
ATR_MULTIPLIER   = 3.1
MAX_POSITION_PCT = 0.25
MAX_POSITIONS    = 10
BUFFER           = 0.05

VOLATILITY_MODE  = "B1"
B1_VENTANA       = 252
B1_MULT_MIN      = 2.2
B1_MULT_MAX      = 4.0

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


# --------------------------------------------------
# Universo S&P500 completo (~420 activos)
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

UNIVERSE_COMPLETO = SP500


# ==================================================
# UNIVERSO DINÁMICO S&P500
# ==================================================

def cargar_composicion_sp500():
    if os.path.exists(SP500_COMP_CACHE):
        print("  Composición S&P500 : caché local")
        return pd.read_csv(SP500_COMP_CACHE, index_col=0, parse_dates=True)
    print("  Composición S&P500 : descargando desde GitHub…")
    try:
        df = pd.read_csv(SP500_COMP_URL, index_col=0, parse_dates=True)
        df.to_csv(SP500_COMP_CACHE)
        return df
    except Exception as e:
        print(f"  ADVERTENCIA: {e} — usando universo estático.")
        return pd.DataFrame()


def universo_historico_sp500(comp_df):
    if comp_df.empty:
        return list(SP500)
    import re
    _date_suffix = re.compile(r'-\d{6,8}$')
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
    df["vol_media20"]  = volume.rolling(20).mean()
    df["ATR_PERCENTIL"] = df["ATR"].rolling(B1_VENTANA).rank(pct=True)
    return df


# ==================================================
# MULTIPLICADOR DINÁMICO B1
# ==================================================

def obtener_multiplicador(df, i):
    percentil = df.iloc[i].get("ATR_PERCENTIL", np.nan)
    if pd.isna(percentil):
        return ATR_MULTIPLIER
    mult = B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil
    return round(mult, 2)


# ==================================================
# SEÑAL
# ==================================================

def detectar_senal(df, i):
    if i < 1:
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
    tendencia    = last["Close"] > last["SMA200"] and last["SMA200"] > prev["SMA200"]
    pullback     = prev["Close"] < prev["SMA50"] * 0.98
    recuperacion = last["Close"] > last["SMA50"]
    return tendencia and pullback and recuperacion


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
    multiplicador  = obtener_multiplicador(df, i)
    stop_distance  = atr * multiplicador
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
                continue
            df = calcular_indicadores(df_raw)
            datos[symbol] = df
        except Exception as e:
            errores.append(symbol)
    print(f"  Activos cargados  : {len(datos)}")
    print(f"  Activos con error : {len(errores)}")
    return datos


# ==================================================
# MOTOR DEL BACKTEST — con variante de trailing stop
# ==================================================

def ejecutar_backtest(datos, variante, composicion_df=None):
    """
    Corre el backtest completo con la variante de trailing stop indicada.

    variante: "A" | "B" | "C" | "D" | "E"
    """
    if composicion_df is None:
        composicion_df = pd.DataFrame()

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

    for fecha in fechas:

        idx = fechas.index(fecha)

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

        # 4. Gestionar posiciones abiertas — trailing stop según variante
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

            # --- LÓGICA DE TRAILING STOP POR VARIANTE ---
            if variante == "A":
                # Línea base B1
                if not pd.isna(atr) and atr > 0:
                    mult       = obtener_multiplicador(df, i_actual)
                    nuevo_stop = round(bar["High"] - atr * mult, 2)
                    if nuevo_stop > pos["stop"]:
                        pos["stop"] = nuevo_stop

            elif variante == "B":
                # Break-even estático: una sola subida, sin trailing posterior
                if not pos.get("stop_moved", False) and not pd.isna(atr) and atr > 0:
                    if bar["Close"] > pos["entry"] + 1.5 * atr:
                        nuevo_stop = round(pos["entry"] + 0.5 * atr, 2)
                        if nuevo_stop > pos["stop"]:
                            pos["stop"]      = nuevo_stop
                            pos["stop_moved"] = True

            elif variante == "C":
                # Trailing agresivo — multiplicador × 0.70
                if not pd.isna(atr) and atr > 0:
                    mult       = obtener_multiplicador(df, i_actual)
                    nuevo_stop = round(bar["High"] - atr * mult * 0.70, 2)
                    if nuevo_stop > pos["stop"]:
                        pos["stop"] = nuevo_stop

            elif variante == "D":
                # Trailing conservador — multiplicador × 1.30
                if not pd.isna(atr) and atr > 0:
                    mult       = obtener_multiplicador(df, i_actual)
                    nuevo_stop = round(bar["High"] - atr * mult * 1.30, 2)
                    if nuevo_stop > pos["stop"]:
                        pos["stop"] = nuevo_stop

            elif variante == "E":
                # Trailing fijo sin percentil — ATR × 3.1
                if not pd.isna(atr) and atr > 0:
                    nuevo_stop = round(bar["High"] - atr * 3.1, 2)
                    if nuevo_stop > pos["stop"]:
                        pos["stop"] = nuevo_stop

            # Palanca 2B — salida por cierre
            precio_ref = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]

            if precio_ref <= pos["stop"]:
                precio_salida = pos["stop"]
                pnl           = (precio_salida - pos["entry"]) * pos["shares"]
                capital      += pnl
                trades.append({
                    "symbol"       : symbol,
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

        # 5. Risk Guardian — drawdown máximo
        drawdown_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0
        if drawdown_actual > RISK_MAX_DRAWDOWN:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # 6. Rebalanceo dinámico
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
            valor_actual  = shares_actual * precio
            limite_valor  = capital * MAX_POSITION_PCT
            if capital > 0 and valor_actual > limite_valor:
                shares_limite = int(limite_valor / precio)
                if abs(shares_limite - shares_actual) >= REBALANCE_MIN_SHARES and shares_limite > 0:
                    pnl_parcial = (precio - pos["entry"]) * (shares_actual - shares_limite)
                    capital    += pnl_parcial
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
                pnl_parcial = (precio - pos["entry"]) * (-delta)
                capital    += pnl_parcial
                posiciones[symbol]["shares"] = shares_optimo
            else:
                entry_blended = (
                    (pos["entry"] * shares_actual + precio * delta) / shares_optimo
                )
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
            bar   = df.iloc[i]
            score = (bar["Close"] - bar["SMA50"]) / bar["ATR"]
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
                bar_entrada = df.loc[fecha_entrada]
                if bar_entrada["High"] >= buy_stop:
                    coste = buy_stop * señal["shares"]
                    if coste > capital:
                        continue
                    entry = {
                        "entry"         : buy_stop,
                        "stop"          : stop_loss,
                        "shares"        : señal["shares"],
                        "fecha_entrada" : fecha_entrada,
                    }
                    if variante == "B":
                        entry["stop_moved"] = False
                    posiciones[symbol] = entry

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

    # CAGR basado en capital inicial (comparable entre variantes)
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt   = datetime.strptime(END_DATE,   "%Y-%m-%d")
    years    = (end_dt - start_dt).days / 365.25
    cagr     = (capital_final / CAPITAL_INICIAL) ** (1 / years) - 1 if CAPITAL_INICIAL > 0 else 0

    return {
        "capital_final": round(capital_final, 2),
        "cagr"         : cagr,
        "profit_factor": round(profit_factor, 4),
        "win_rate"     : win_rate,
        "max_drawdown" : max_drawdown,
        "total_trades" : total_trades,
    }


# ==================================================
# TABLA COMPARATIVA
# ==================================================

def imprimir_tabla(resultados):
    sep  = "─" * 90
    sep2 = "═" * 90

    print(f"\n{sep2}")
    print(f"  LIBERTAD_2045 — EXPERIMENTO 40 — Comparativa trailing stop  ({START_DATE} → {END_DATE})")
    print(f"{sep2}")

    header = (
        f"  {'Var':<4} {'Descripción':<26} {'Capital €':>12} "
        f"{'CAGR':>8} {'Prof.F':>8} {'WinRate':>8} {'MaxDD':>8} {'Trades':>7}"
    )
    print(header)
    print(f"  {sep[2:]}")

    descripciones = {
        "A": "Línea base B1 (referencia)",
        "B": "Break-even estático",
        "C": "Trailing agresivo ×0.70",
        "D": "Trailing conservador ×1.30",
        "E": "Trailing fijo ATR×3.1",
    }

    ref_capital = resultados["A"]["capital_final"] if "A" in resultados else None

    for var in ["A", "B", "C", "D", "E"]:
        if var not in resultados:
            continue
        m    = resultados[var]
        diff = ""
        if ref_capital and var != "A":
            delta = m["capital_final"] - ref_capital
            sign  = "+" if delta >= 0 else ""
            diff  = f"  ({sign}{delta:,.0f}€ vs A)"

        marker = " ◀ REF" if var == "A" else ""

        print(
            f"  {var:<4} {descripciones[var]:<26} "
            f"{m['capital_final']:>12,.2f} "
            f"{m['cagr']:>8.2%} "
            f"{m['profit_factor']:>8.4f} "
            f"{m['win_rate']:>8.1%} "
            f"{m['max_drawdown']:>8.1%} "
            f"{m['total_trades']:>7d}"
            f"{diff}{marker}"
        )

    print(f"  {sep[2:]}")
    print(f"\n  CAGR  = retorno anualizado sobre capital inicial {CAPITAL_INICIAL:.0f}€ (comparativo)")
    print(f"  MaxDD = drawdown máximo sobre curva de capital")
    print(f"{sep2}\n")


# ==================================================
# GUARDAR CSV COMPARATIVO
# ==================================================

def guardar_comparativo(resultados):
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filas = []
    descripciones = {
        "A": "Línea base B1 (referencia)",
        "B": "Break-even estático",
        "C": "Trailing agresivo x0.70",
        "D": "Trailing conservador x1.30",
        "E": "Trailing fijo ATR×3.1",
    }
    for var in ["A", "B", "C", "D", "E"]:
        if var not in resultados:
            continue
        m = resultados[var]
        filas.append({
            "variante"     : var,
            "descripcion"  : descripciones[var],
            "capital_final": m["capital_final"],
            "cagr"         : round(m["cagr"], 6),
            "profit_factor": m["profit_factor"],
            "win_rate"     : round(m["win_rate"], 4),
            "max_drawdown" : round(m["max_drawdown"], 4),
            "total_trades" : m["total_trades"],
        })

    path = f"{LOG_DIR}/exp40_comparativa_{timestamp}.csv"
    pd.DataFrame(filas).to_csv(path, index=False)
    print(f"  CSV guardado: {path}")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 60)
    print("  LIBERTAD_2045 — EXPERIMENTO 40")
    print("  Comparativa de 5 variantes de trailing stop")
    print("=" * 60)
    print(f"  Período   : {START_DATE} → {END_DATE}")
    print(f"  Capital   : {CAPITAL_INICIAL:.0f}€  +{APORTACION_ANUAL:.0f}€/año")
    print(f"  Riesgo/op : {RISK_PERCENT:.2%}")
    print(f"  Max pos   : {MAX_POSITIONS}")
    print()

    # Cargar datos UNA sola vez — compartidos entre todas las variantes
    comp_df  = cargar_composicion_sp500()
    universo = universo_historico_sp500(comp_df)
    print(f"  Universo  : {len(universo)} activos históricos únicos\n")

    datos = descargar_datos(universo, START_DATE, END_DATE)
    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    # Ejecutar las 5 variantes
    variantes = {
        "A": "Línea base B1",
        "B": "Break-even estático",
        "C": "Trailing agresivo ×0.70",
        "D": "Trailing conservador ×1.30",
        "E": "Trailing fijo ATR×3.1",
    }

    resultados = {}

    for var, desc in variantes.items():
        print(f"\n{'─'*60}")
        print(f"  Variante {var} — {desc}")
        print(f"{'─'*60}")
        t0 = time.time()
        trades, curva_capital, capital_final = ejecutar_backtest(
            datos, variante=var, composicion_df=comp_df
        )
        metricas = calcular_metricas(trades, curva_capital, capital_final)
        elapsed  = time.time() - t0
        resultados[var] = metricas
        print(
            f"  Capital final: {capital_final:,.2f}€  "
            f"| Trades: {metricas['total_trades']}  "
            f"| WinRate: {metricas['win_rate']:.1%}  "
            f"| PF: {metricas['profit_factor']:.4f}  "
            f"| MaxDD: {metricas['max_drawdown']:.1%}  "
            f"| {elapsed:.1f}s"
        )

    # Tabla comparativa final
    imprimir_tabla(resultados)
    guardar_comparativo(resultados)
