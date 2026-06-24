"""
EL MOTOR — Sistema de Trading Algorítmico (Experimento Libre)
=============================================================
Diseño desde cero. Sin restricciones de continuidad con LIBERTAD_2045.

FILOSOFÍA
─────────
Momentum sistemático puro: poseer siempre las acciones con mayor
impulso relativo del universo, con gestión de riesgo por ATR y
filtro de régimen de mercado.

La hipótesis central (respaldada por décadas de investigación académica):
las acciones que han subido más en los últimos 3-6 meses tienden a
seguir subiendo los próximos 3-12 meses más que las que han bajado.
Comprar fuerza, no debilidad.

DIFERENCIAS vs LIBERTAD_2045
─────────────────────────────
L2045 espera pullbacks: entra cuando el precio retrocede y se recupera.
EL MOTOR compra breakouts y momentum: entra cuando el precio confirma
fuerza relativa superior al resto del universo. Sin esperar correcciones.

L2045 tiene señal binaria (pullback sí/no) y rellena slots con lo que
aparezca en cada ciclo. EL MOTOR rankea TODOS los candidatos y siempre
mantiene los N de mayor score, reemplazando los más débiles.

L2045 no tiene filtro de régimen de mercado global. EL MOTOR usa un
filtro de amplitud de mercado (% stocks > SMA200) para reducir exposición
en bear markets y proteger el capital.

COMPONENTES
───────────
1. Universo      : S&P500 (~420 activos) — mismo que baseline v3
2. Filtro stock  : SMA50 > SMA200 (tendencia alcista individual confirmada)
3. Filtro mercado: % stocks > SMA200 ≥ 50% → mercado alcista
                   % stocks > SMA200 < 50% → mercado bajista (solo 3 slots)
4. Score         : 0.60 × ROC_6M + 0.40 × ROC_3M (momentum compuesto)
                   Normalizado por volatilidad (ATR%) → momentum ajustado
5. Portfolio     : Top 10 por score en mercado alcista (3 en bajista)
6. Sizing        : Riesgo 1.0% capital por posición, stop = 2.5 × ATR(14)
                   Cap máx por posición: 20% del portfolio
7. Trailing stop : Chandelier exit = max(close desde entrada) - 2.5×ATR(14)
8. Reemplazo     : Si un candidato supera el score de la posición más débil
                   en más de 15%, se reemplaza (evita churning excesivo)
9. Aportación    : 4.000€/año (1 enero) — mismo que L2045

PARÁMETROS ELEGIDOS Y RAZÓN
────────────────────────────
Score 6M + 3M    : El horizonte 6M captura el core del momentum documentado
                   (Jegadeesh-Titman 1993). 3M añade sensibilidad a la
                   aceleración reciente. Peso 60/40 empírico bien documentado.
Normalización    : Dividir por ATR% ajusta por volatilidad — una subida del
                   20% en una acción de baja vol vale más que en una de alta.
2.5 × ATR stop   : Suficientemente ajustado para proteger ganancias, suficiente-
                   mente amplio para soportar ruido normal. Testeado en miles
                   de sistemas.
Umbral de reemplazo 15%: Elimina churning inútil y costes de transacción.
Filtro amplitud 50%: El Hindenburg Omen y otros indicadores de amplitud muestran
                   que cuando <50% de stocks están en tendencia el mercado es
                   estructuralmente débil. Reducimos a 3 posiciones (capital
                   más defensivo sin liquidar todo).
"""

import warnings
warnings.filterwarnings("ignore")

import sys
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

# Acceso al data_manager del proyecto principal
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from data_manager import obtener_datos_cached

# ─────────────────────────────────────────────────────────────────
# PARÁMETROS
# ─────────────────────────────────────────────────────────────────

START_DATE       = "2006-01-01"
END_DATE         = "2025-12-31"

CAPITAL_INICIAL  = 4_000.0
APORTACION_ANUAL = 4_000.0

# Momentum
ATR_PERIOD       = 14
ROC_6M           = 126   # barras ~6 meses
ROC_3M           = 63    # barras ~3 meses

# Portfolio
MAX_SLOTS_BULL   = 10    # posiciones en mercado alcista
MAX_SLOTS_BEAR   = 3     # posiciones en mercado bajista

# Risk sizing
RISK_PCT         = 0.010  # 1.0% de capital arriesgado por trade
ATR_STOP_MULT    = 2.5    # stop = 2.5 × ATR desde entrada
MAX_POS_PCT      = 0.20   # máximo 20% en una sola posición

# Reemplazo (anti-churning)
REPLACEMENT_EDGE = 0.15   # candidato debe superar al más débil en ≥15%

# Filtro de régimen de mercado (% stocks > SMA200)
MARKET_BULL_THRESH = 0.50  # ≥50% en tendencia → mercado alcista

# Risk guardian
RISK_MIN_CAPITAL  = 2_000.0
RISK_MAX_DRAWDOWN = 0.10

# Output
LOG_DIR = str(PROJECT_DIR / "backtest_results")

# ─────────────────────────────────────────────────────────────────
# UNIVERSO — Mismo S&P500 que baseline v3
# ─────────────────────────────────────────────────────────────────

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
for _t in SP500:
    if _t not in _seen:
        _seen.add(_t)
        _unique.append(_t)
SP500 = _unique

# ─────────────────────────────────────────────────────────────────
# INDICADORES TÉCNICOS
# ─────────────────────────────────────────────────────────────────

def calcular_indicadores(df_raw: pd.DataFrame) -> pd.DataFrame:
    df = df_raw.copy()
    close = df["Close"]

    df["SMA50"]  = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()

    # ATR
    high  = df["High"]
    low   = df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df["ATR_PCT"] = df["ATR"] / close  # ATR como % del precio

    # Momentum
    df["ROC_6M"] = close.pct_change(ROC_6M)
    df["ROC_3M"] = close.pct_change(ROC_3M)

    # Score de momentum ajustado por volatilidad
    # (momentum / vol relativa) — premia consistencia sobre explosividad
    df["SCORE"] = (
        0.60 * df["ROC_6M"] +
        0.40 * df["ROC_3M"]
    ) / df["ATR_PCT"].clip(lower=0.005)

    # Trailing stop buffer (máximo close en ventana de 3 días, para evitar whipsaws)
    df["HIGH_3D"] = close.rolling(3).max()

    return df

# ─────────────────────────────────────────────────────────────────
# CARGA DE DATOS
# ─────────────────────────────────────────────────────────────────

def cargar_datos(universe, start, end):
    print(f"\nCargando datos ({start} → {end})...")
    print(f"Universo: {len(universe)} activos\n")
    datos   = {}
    errores = []
    for i, sym in enumerate(universe, 1):
        try:
            df_raw = obtener_datos_cached(sym, start, end)
            if df_raw is None or len(df_raw) < max(200, ROC_6M + 20):
                errores.append(sym)
                continue
            df = calcular_indicadores(df_raw)
            datos[sym] = df
            if i % 50 == 0:
                print(f"  [{i}/{len(universe)}] cargados...")
        except Exception as e:
            errores.append(sym)
    print(f"\nActivos cargados : {len(datos)}")
    print(f"Errores          : {len(errores)}")
    return datos

# ─────────────────────────────────────────────────────────────────
# FILTRO DE RÉGIMEN DE MERCADO
# ─────────────────────────────────────────────────────────────────

def calcular_regimen(datos: dict, fecha) -> tuple[bool, float]:
    """
    Retorna (mercado_alcista: bool, pct_sobre_sma200: float).
    Alcista si ≥50% de los activos con datos en 'fecha' están sobre SMA200.
    """
    total = 0
    sobre = 0
    for sym, df in datos.items():
        if fecha not in df.index:
            continue
        row = df.loc[fecha]
        sma200 = row.get("SMA200")
        close  = row.get("Close")
        if pd.isna(sma200) or pd.isna(close):
            continue
        total += 1
        if close > sma200:
            sobre += 1
    if total == 0:
        return True, 0.5  # sin datos → asumimos alcista
    pct = sobre / total
    return pct >= MARKET_BULL_THRESH, pct

# ─────────────────────────────────────────────────────────────────
# SEÑAL Y SCORING
# ─────────────────────────────────────────────────────────────────

def escanear_candidatos(datos: dict, fecha, posiciones_actuales: set) -> list:
    """
    Devuelve lista de (symbol, score, precio, atr) de candidatos válidos
    que NO están ya en cartera, ordenados por score descendente.

    Criterios de entrada:
    - SMA50 > SMA200 (tendencia alcista)
    - Precio > SMA50 (no en pullback extremo)
    - ROC_6M > 0 y ROC_3M > 0 (momentum positivo en ambos horizontes)
    - ATR_PCT < 0.08 (volatilidad diaria < 8% — evita acciones hiperactivas)
    - SCORE válido (no NaN)
    """
    candidatos = []
    for sym, df in datos.items():
        if sym in posiciones_actuales:
            continue
        if fecha not in df.index:
            continue
        row = df.loc[fecha]
        close  = row.get("Close",  np.nan)
        sma50  = row.get("SMA50",  np.nan)
        sma200 = row.get("SMA200", np.nan)
        atr    = row.get("ATR",    np.nan)
        roc6m  = row.get("ROC_6M", np.nan)
        roc3m  = row.get("ROC_3M", np.nan)
        score  = row.get("SCORE",  np.nan)
        atr_p  = row.get("ATR_PCT", np.nan)

        if any(pd.isna(x) for x in [close, sma50, sma200, atr, roc6m, roc3m, score]):
            continue
        if sma50 <= sma200:
            continue   # no en tendencia
        if close < sma50:
            continue   # precio por debajo de SMA50 (no confirma fuerza)
        if roc6m <= 0 or roc3m <= 0:
            continue   # momentum negativo en algún horizonte
        if atr_p > 0.08:
            continue   # demasiado volátil
        if score <= 0:
            continue

        candidatos.append((sym, score, close, atr))

    candidatos.sort(key=lambda x: x[1], reverse=True)
    return candidatos

# ─────────────────────────────────────────────────────────────────
# SIMULACIÓN
# ─────────────────────────────────────────────────────────────────

def ejecutar_backtest(datos: dict, fechas: pd.DatetimeIndex) -> dict:

    capital       = CAPITAL_INICIAL
    capital_peak  = CAPITAL_INICIAL
    anio_actual   = int(START_DATE[:4])

    # Posiciones: dict symbol → {shares, entry, stop, max_close, entry_date}
    posiciones    = {}
    trades        = []
    curva_capital = []

    print("\nEjecutando simulación...")

    for fecha in fechas:

        # ── Aportación anual ──────────────────────────────────────────────────
        if fecha.year > anio_actual:
            for _ in range(fecha.year - anio_actual):
                capital += APORTACION_ANUAL
            anio_actual = fecha.year

        # ── Precio de cierre para cada posición ──────────────────────────────
        valor_posiciones = 0.0
        for sym, pos in posiciones.items():
            df = datos.get(sym)
            if df is None or fecha not in df.index:
                valor_posiciones += pos["shares"] * pos["entry"]
                continue
            valor_posiciones += pos["shares"] * df.loc[fecha, "Close"]

        capital_total = capital + valor_posiciones
        capital_peak  = max(capital_peak, capital_total)
        dd_actual     = (capital_peak - capital_total) / capital_peak

        # ── Risk Guardian ─────────────────────────────────────────────────────
        guardian_ok = (capital_total >= RISK_MIN_CAPITAL)
        dd_gate     = (dd_actual < RISK_MAX_DRAWDOWN)

        # ── Gestión de stops y trailing ───────────────────────────────────────
        cerrar = []
        for sym, pos in posiciones.items():
            df = datos.get(sym)
            if df is None or fecha not in df.index:
                continue
            row    = df.loc[fecha]
            close  = row["Close"]
            atr    = row["ATR"]

            # Actualizar máximo desde entrada
            pos["max_close"] = max(pos["max_close"], close)

            # Chandelier exit: max_close - 2.5×ATR
            nuevo_stop = pos["max_close"] - ATR_STOP_MULT * atr
            pos["stop"] = max(pos["stop"], nuevo_stop)  # stop solo sube

            # ¿Tocó el stop?
            if close <= pos["stop"]:
                pnl = (close - pos["entry"]) * pos["shares"]
                trades.append({
                    "symbol":     sym,
                    "entry_date": pos["entry_date"],
                    "exit_date":  str(fecha.date()),
                    "entry":      pos["entry"],
                    "exit":       close,
                    "shares":     pos["shares"],
                    "pnl":        round(pnl, 2),
                    "motivo":     "stop",
                })
                capital += close * pos["shares"]
                cerrar.append(sym)

        for sym in cerrar:
            del posiciones[sym]

        # ── Régimen de mercado ───────────────────────────────────────────────
        mercado_alcista, pct_bull = calcular_regimen(datos, fecha)
        max_slots = MAX_SLOTS_BULL if mercado_alcista else MAX_SLOTS_BEAR

        # ── Reemplazo: ¿hay candidatos que superen al más débil? ─────────────
        if guardian_ok and dd_gate and len(posiciones) > 0 and len(posiciones) >= max_slots:
            # Score actual de las posiciones abiertas
            scores_pos = {}
            for sym, pos in posiciones.items():
                df = datos.get(sym)
                if df is not None and fecha in df.index:
                    scores_pos[sym] = df.loc[fecha, "SCORE"]
                else:
                    scores_pos[sym] = 0.0

            sym_debil  = min(scores_pos, key=scores_pos.get)
            score_debil = scores_pos[sym_debil]

            candidatos = escanear_candidatos(datos, fecha, set(posiciones.keys()))
            if candidatos:
                top_sym, top_score, top_close, top_atr = candidatos[0]
                if top_score > score_debil * (1 + REPLACEMENT_EDGE):
                    # Cerrar la posición más débil
                    pos  = posiciones[sym_debil]
                    df_d = datos.get(sym_debil)
                    if df_d is not None and fecha in df_d.index:
                        exit_price = df_d.loc[fecha, "Close"]
                    else:
                        exit_price = pos["entry"]
                    pnl = (exit_price - pos["entry"]) * pos["shares"]
                    trades.append({
                        "symbol":     sym_debil,
                        "entry_date": pos["entry_date"],
                        "exit_date":  str(fecha.date()),
                        "entry":      pos["entry"],
                        "exit":       exit_price,
                        "shares":     pos["shares"],
                        "pnl":        round(pnl, 2),
                        "motivo":     "reemplazo",
                    })
                    capital += exit_price * pos["shares"]
                    del posiciones[sym_debil]

        # ── Nuevas entradas ───────────────────────────────────────────────────
        if guardian_ok and dd_gate and len(posiciones) < max_slots:
            slots_libres = max_slots - len(posiciones)
            candidatos   = escanear_candidatos(datos, fecha, set(posiciones.keys()))

            for sym, score, precio, atr in candidatos[:slots_libres]:
                if precio <= 0 or atr <= 0:
                    continue

                # Sizing: riesgo / (ATR_MULT × ATR) = shares
                riesgo_euros = capital_total * RISK_PCT
                riesgo_acc   = ATR_STOP_MULT * atr
                if riesgo_acc <= 0:
                    continue

                shares_raw   = riesgo_euros / riesgo_acc
                coste_total  = shares_raw * precio

                # Cap: no más de MAX_POS_PCT del portfolio
                if coste_total > capital_total * MAX_POS_PCT:
                    shares_raw = (capital_total * MAX_POS_PCT) / precio
                    coste_total = shares_raw * precio

                shares = int(shares_raw)
                if shares < 1:
                    continue
                coste = shares * precio
                if coste > capital:
                    shares = int(capital / precio)
                    coste  = shares * precio
                if shares < 1 or coste > capital:
                    continue

                stop_inicial = precio - ATR_STOP_MULT * atr
                capital -= coste
                posiciones[sym] = {
                    "shares":     shares,
                    "entry":      precio,
                    "stop":       stop_inicial,
                    "max_close":  precio,
                    "entry_date": str(fecha.date()),
                }

        # ── Curva de capital ─────────────────────────────────────────────────
        valor_pos = sum(
            pos["shares"] * (
                datos[sym].loc[fecha, "Close"]
                if sym in datos and fecha in datos[sym].index
                else pos["entry"]
            )
            for sym, pos in posiciones.items()
        )
        curva_capital.append({
            "date":           str(fecha.date()),
            "capital_cash":   round(capital, 2),
            "valor_posiciones": round(valor_pos, 2),
            "capital_total":  round(capital + valor_pos, 2),
            "capital_peak":   round(capital_peak, 2),
            "drawdown":       round(dd_actual, 6),
            "pct_bull":       round(pct_bull, 4),
            "n_posiciones":   len(posiciones),
        })

    # ── Cierre forzado al final del período ──────────────────────────────────
    ultima_fecha = fechas[-1]
    for sym, pos in list(posiciones.items()):
        df = datos.get(sym)
        if df is not None and ultima_fecha in df.index:
            exit_price = df.loc[ultima_fecha, "Close"]
        else:
            exit_price = pos["entry"]
        pnl = (exit_price - pos["entry"]) * pos["shares"]
        trades.append({
            "symbol":     sym,
            "entry_date": pos["entry_date"],
            "exit_date":  str(ultima_fecha.date()),
            "entry":      pos["entry"],
            "exit":       exit_price,
            "shares":     pos["shares"],
            "pnl":        round(pnl, 2),
            "motivo":     "fin_backtest",
        })
        capital += exit_price * pos["shares"]

    return {
        "capital_final": capital,
        "trades":        trades,
        "curva":         curva_capital,
    }

# ─────────────────────────────────────────────────────────────────
# MÉTRICAS
# ─────────────────────────────────────────────────────────────────

def calcular_metricas(resultado: dict) -> dict:
    trades = resultado["trades"]
    curva  = resultado["curva"]
    cap_f  = resultado["capital_final"]

    if not trades:
        return {}

    df_trades = pd.DataFrame(trades)
    pnls = df_trades["pnl"]

    wins   = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr     = len(wins) / len(pnls) if len(pnls) else 0

    gross_win  = wins.sum()
    gross_loss = abs(losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    df_curva = pd.DataFrame(curva)
    max_dd = df_curva["drawdown"].max()

    total_contrib = CAPITAL_INICIAL + APORTACION_ANUAL * (
        int(END_DATE[:4]) - int(START_DATE[:4])
    )
    retorno_total = (cap_f - total_contrib) / total_contrib * 100

    n_years = int(END_DATE[:4]) - int(START_DATE[:4])
    cagr    = (cap_f / CAPITAL_INICIAL) ** (1 / n_years) - 1 if n_years > 0 else 0

    return {
        "total_trades":  len(pnls),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(wr, 4),
        "profit_factor": round(pf, 4),
        "max_drawdown":  round(max_dd, 4),
        "retorno_total": round(retorno_total, 2),
        "capital_inicial": CAPITAL_INICIAL,
        "capital_final":   round(cap_f, 2),
        "cagr":          round(cagr, 4),
        "pnl_medio_win":  round(wins.mean(), 2) if len(wins) else 0,
        "pnl_medio_loss": round(losses.mean(), 2) if len(losses) else 0,
        "expectativa":    round(pnls.mean(), 2),
    }

# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t0 = time.time()
    os.makedirs(LOG_DIR, exist_ok=True)

    datos = cargar_datos(SP500, START_DATE, END_DATE)

    # Fechas comunes de todos los activos
    todas_fechas = sorted(set.union(*[set(df.index) for df in datos.values()]))
    todas_fechas = [f for f in todas_fechas if START_DATE <= str(f.date()) <= END_DATE]
    fechas = pd.DatetimeIndex(todas_fechas)

    resultado  = ejecutar_backtest(datos, fechas)
    metricas   = calcular_metricas(resultado)

    elapsed = time.time() - t0

    print(f"\n{'─'*50}")
    print(f"  EL MOTOR — BACKTEST COMPLETO")
    print(f"  {START_DATE} → {END_DATE}")
    print(f"  Tiempo: {elapsed:.1f}s")
    print(f"{'─'*50}")
    print(f"\n  CAPITAL")
    print(f"  Inicial          : {CAPITAL_INICIAL:>14,.2f} €")
    print(f"  Final            : {metricas['capital_final']:>14,.2f} €")
    print(f"  Retorno total    : {metricas['retorno_total']:>14.1f}%")
    print(f"  CAGR             : {metricas['cagr']*100:>14.2f}%")
    print(f"\n  OPERATIVA GLOBAL")
    print(f"  Total trades     : {metricas['total_trades']:>14d}")
    print(f"  Wins             : {metricas['wins']:>14d}")
    print(f"  Losses           : {metricas['losses']:>14d}")
    print(f"  Win rate         : {metricas['win_rate']*100:>14.1f}%")
    print(f"\n  RIESGO")
    print(f"  Profit factor    : {metricas['profit_factor']:>14.4f}")
    print(f"  Drawdown máximo  : {metricas['max_drawdown']*100:>14.1f}%")
    print(f"  PnL medio WIN    : {metricas['pnl_medio_win']:>14,.2f} €")
    print(f"  PnL medio LOSS   : {metricas['pnl_medio_loss']:>14,.2f} €")
    print(f"  Expectativa/trade: {metricas['expectativa']:>14,.2f} €")
    print(f"{'─'*50}")

    # Veredicto respecto a baseline v3
    BASELINE_CAPITAL = 8_888_418.0
    BASELINE_PF      = 2.6071
    BASELINE_DD      = 0.104
    BASELINE_WR      = 0.541
    mejor  = metricas["capital_final"] > BASELINE_CAPITAL
    print(f"\n  COMPARATIVA vs BASELINE v3 (L2045 Exp30)")
    print(f"  Capital final: {metricas['capital_final']:,.0f}€ vs {BASELINE_CAPITAL:,.0f}€ → {'MEJOR ✓' if mejor else 'PEOR ✗'}")
    print(f"  PF:  {metricas['profit_factor']:.4f} vs {BASELINE_PF:.4f}")
    print(f"  DD:  {metricas['max_drawdown']*100:.1f}%  vs {BASELINE_DD*100:.1f}%")
    print(f"  WR:  {metricas['win_rate']*100:.1f}%  vs {BASELINE_WR*100:.1f}%")
    print(f"{'─'*50}\n")

    # Guardar resultados
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix    = f"{LOG_DIR}/elmotor"

    pd.DataFrame(resultado["trades"]).to_csv(f"{prefix}_trades_{timestamp}.csv", index=False)
    pd.DataFrame(resultado["curva"]).to_csv(f"{prefix}_curva_{timestamp}.csv", index=False)
    pd.DataFrame([metricas]).to_csv(f"{prefix}_metricas_{timestamp}.csv", index=False)

    print(f"  Trades   : {prefix}_trades_{timestamp}.csv")
    print(f"  Curva    : {prefix}_curva_{timestamp}.csv")
    print(f"  Métricas : {prefix}_metricas_{timestamp}.csv")
