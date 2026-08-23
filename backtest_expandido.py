"""
LIBERTAD_2045 — Backtest Expandido
====================================
Experimento 30: simulación fiel con Risk Guardian completo.

    El backtest replica exactamente el comportamiento de producción:

    RISK GUARDIAN:
        - Capital mínimo operativo: si capital < RISK_MIN_CAPITAL
          el sistema se detiene completamente (no abre ni gestiona)
        - Drawdown máximo: si caída desde pico > RISK_MAX_DRAWDOWN
          el sistema no abre posiciones nuevas pero mantiene las abiertas
          con sus stops activos hasta que el capital se recupere
        - Capital pico: se actualiza en tiempo real igual que en producción

    PALANCA 2B:
        - Salida por precio de cierre (no por mínimo intradiario)

    STOP DINÁMICO B1:
        - Multiplicador ATR por percentil histórico (2.2-4.0)

Uso:
    python backtest_expandido.py

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
# Parámetros del backtest
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
TRAILING_FACTOR  = 0.75

B2_VENTANA       = 252
B2_UMBRAL_ALTO   = 0.40
B2_UMBRAL_BAJO   = 0.20
B2_MULT_ALTO     = 2.5
B2_MULT_MEDIO    = 3.1
B2_MULT_BAJO     = 3.7

# Slippage por impacto de mercado (22/08/2026, iniciativa "backtest más
# fiel a la realidad", Fase 2) — APROXIMACIÓN, no medición real: el
# backtest usa datos diarios (un precio por día), no tick-by-tick ni
# libro de órdenes, así que no puede calcularse el slippage exacto que
# habría sufrido cada orden. SLIPPAGE_K es un coeficiente de
# calibración genérico, NO estimado con datos de ejecución reales de
# este proyecto — ver calcular_slippage() para la fuente del modelo y
# el razonamiento completo.
SLIPPAGE_K       = 0.10

# Comisión IBKR (22/08/2026, iniciativa "backtest más fiel a la realidad",
# Fase 5) — fija por pata (entrada o salida), calibrada con la
# reconciliación real de la cuenta LIVE del 21/08/2026 (~1$/operación).
# No escala con el número de acciones — no hay evidencia en la
# reconciliación real de una estructura por-acción distinta en el rango
# de tamaños de posición de este sistema.
COMISION_IBKR    = 1.0  # USD por pata

# Palanca 2B — salida por cierre
SALIDA_POR_CIERRE = True

# --------------------------------------------------
# Risk Guardian — idéntico a producción
# --------------------------------------------------
RISK_MIN_CAPITAL  = 2000.0   # Capital mínimo operativo
RISK_MAX_DRAWDOWN = 0.10     # Drawdown máximo desde capital pico

# --------------------------------------------------
# Rebalanceo dinámico — idéntico a rebalance.py
# --------------------------------------------------
REBALANCE_THRESHOLD  = 0.25  # Desviación relativa para disparar ajuste
REBALANCE_MIN_SHARES = 5     # Delta mínimo de acciones (evita micro-operaciones)

# --------------------------------------------------
# Universo dinámico S&P500 — fja05680/sp500
# --------------------------------------------------
SP500_COMP_CACHE = "sp500_composicion.csv"
SP500_COMP_URL   = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)

LOG_DIR           = "backtest_results"
DELAY             = 2


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
    """
    Descarga (o carga desde caché) el dataset histórico de composición del S&P500
    de github.com/fja05680/sp500.

    Retorna un DataFrame con fecha como índice y la primera columna conteniendo
    los tickers del índice en cada fecha como string CSV.
    Si la descarga falla devuelve un DataFrame vacío y el llamador usa el
    universo estático como fallback.
    """
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
    """
    Extrae el conjunto de todos los tickers que alguna vez estuvieron en el
    S&P500 según el dataset histórico. Retorna lista deduplicada y ordenada.
    Si comp_df está vacío devuelve el universo estático SP500.
    """
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
            # Strip trailing date suffix like -199702 or -20031231
            ticker = _date_suffix.sub('', ticker)
            # Keep only pure uppercase-letter tickers, max 5 chars
            if _valid_ticker.match(ticker):
                todos.add(ticker)
    return sorted(todos)


def sp500_en_fecha(comp_df: pd.DataFrame, fecha) -> set:
    """
    Retorna el conjunto de tickers del S&P500 vigentes en `fecha`.
    Usa asof() para rellenar hacia adelante cuando no hay entrada exacta.

    Retorna None si comp_df está vacío → señal para no filtrar por composición.
    """
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


def calcular_precio_salida_stop(bar, stop):
    """
    Precio de ejecución simulado para una salida ya disparada por stop
    (Close del día <= stop, con SALIDA_POR_CIERRE=True).

    Simulación de gap de apertura (22/08/2026, iniciativa "backtest más
    fiel a la realidad", Fase 1): si el Open de este mismo día ya abrió
    por debajo del stop (gap bajista overnight que atraviesa el nivel
    antes de que el mercado pueda ejecutar al precio teórico — caso real
    ANET 05-06/08/2026, stop 196,40 ejecutado a 192,545 por el Open del
    día siguiente, ver sección 13 del contexto), el precio de ejecución
    real es ese Open, no el nivel teórico del stop.

    Si el Open sigue por encima del stop, el cruce fue intradía (no por
    gap) y el precio teórico del stop sigue siendo una aproximación
    razonable — comportamiento idéntico al anterior en ese caso.

    Solo cubre posiciones largas (LIBERTAD_2045 nunca opera en corto,
    ver Constitución del sistema).
    """
    if bar["Open"] <= stop:
        return bar["Open"]
    return stop


def calcular_precio_entrada_stop(bar_entrada, buy_stop):
    """
    Precio de ejecución simulado para una entrada BUY STOP ya disparada
    (High del día de entrada >= buy_stop).

    Mismo patrón que calcular_precio_salida_stop(), lado contrario: si
    el Open de ese día ya abrió por encima del buy_stop (gap alcista
    overnight que atraviesa el nivel antes de que el mercado pueda
    ejecutar al precio teórico), el precio de entrada real es ese Open
    — peor precio para el comprador (paga más), no mejor.

    Si el Open sigue por debajo del buy_stop, el cruce fue intradía (no
    por gap) y el precio teórico del buy_stop sigue siendo una
    aproximación razonable — comportamiento idéntico al anterior en ese
    caso.

    Solo cubre posiciones largas (LIBERTAD_2045 nunca opera en corto,
    ver Constitución del sistema).
    """
    if bar_entrada["Open"] >= buy_stop:
        return bar_entrada["Open"]
    return buy_stop


def calcular_slippage(shares_orden, volumen_dia, atr, k=SLIPPAGE_K):
    """
    Aproximación de slippage por impacto de mercado (22/08/2026,
    iniciativa "backtest más fiel a la realidad", Fase 2).

    LIMITACIÓN EXPLÍCITA: esto es una APROXIMACIÓN, no una simulación
    precisa. El backtest usa datos diarios (un precio por día), no
    tick-by-tick ni libro de órdenes real — no es posible calcular el
    slippage exacto que habría sufrido una orden concreta con estos
    datos. Esta función solo estima un orden de magnitud razonable en
    función de qué fracción del volumen diario representa la orden.

    MODELO: forma funcional de la "ley de raíz cuadrada" (square-root
    law) de impacto de mercado, empíricamente bien establecida en la
    literatura de microstructure — el impacto de una orden escala
    aproximadamente con la raíz cuadrada de su tamaño relativo al
    volumen diario, no linealmente:
        Almgren, R., Thum, C., Hauptmann, E., Li, H. (2005),
        "Direct Estimation of Equity Market Impact", Risk Magazine
        (exponente empírico ~0.5-0.6, redondeado a 0.5 en la
        formulación estándar más simple).
        Bouchaud, J-P., Farmer, J.D., Lillo, F. (2008), "How markets
        slowly digest changes in supply and demand" (revisión del
        mismo resultado empírico en múltiples mercados).

        slippage_precio = K · ATR · sqrt(shares_orden / volumen_dia)

    La FORMA (raíz cuadrada del % de volumen, escalado por la
    volatilidad diaria — aquí ATR como proxy ya calculado en el
    sistema) viene de esa literatura. La CONSTANTE K (SLIPPAGE_K) es
    una elección propia razonable, NO calibrada con datos de ejecución
    reales de este proyecto — no existe ese dataset. Ajustar K si en
    algún momento se dispone de datos reales de slippage con los que
    calibrar.

    Devuelve el desplazamiento de PRECIO, siempre >= 0 — el llamador
    decide el signo (sumar en entradas, restar en salidas). Con
    volumen_dia, atr o shares_orden no válidos (None, NaN, <= 0),
    devuelve 0.0 — fail-safe: sin datos de liquidez fiables, no se
    penaliza el precio en vez de bloquear el trade o inventar un
    número.
    """
    if shares_orden is None or shares_orden <= 0:
        return 0.0
    if volumen_dia is None or pd.isna(volumen_dia) or volumen_dia <= 0:
        return 0.0
    if atr is None or pd.isna(atr) or atr <= 0:
        return 0.0

    fraccion_volumen = shares_orden / volumen_dia
    return k * atr * (fraccion_volumen ** 0.5)


def calcular_comision_total(comision_por_pata=COMISION_IBKR):
    """
    Comisión IBKR de una operación completa (22/08/2026, iniciativa
    "backtest más fiel a la realidad", Fase 5) — dos patas: entrada y
    salida. Calibrada con la reconciliación real de la cuenta LIVE del
    21/08/2026 (~1$/operación, comisión fija).

    Se descuenta en el punto donde el motor realiza PnL (ninguna de las
    dos patas se deduce de "capital" en ningún otro sitio — la entrada
    solo comprueba disponibilidad contra "coste", no deduce caja real).
    """
    return 2 * comision_por_pata


def convertir_a_eur(valor_usd, rate):
    """
    Convierte un valor en USD a EUR usando el tipo de cambio EUR/USD
    (rate = USD por 1 EUR, mismo criterio que dashboard.py::
    _obtener_tipo_cambio_mercado() en producción).

    Fail-safe: rate inválido (None, NaN, <= 0) → devuelve NaN, nunca
    inventa un tipo de cambio ni bloquea nada — el valor en USD sigue
    siendo la fuente de verdad, esto es una capa de reporte aparte.
    """
    if valor_usd is None or rate is None or pd.isna(rate) or rate <= 0:
        return float("nan")
    return valor_usd / rate


def convertir_eur_a_usd(valor_eur, rate):
    """
    Convierte un valor en EUR a USD (rate = USD por 1 EUR) — inversa
    exacta de convertir_a_eur(). Fase 6 (22/08/2026), iniciativa
    "backtest más fiel a la realidad".

    Fail-safe: rate inválido (None, NaN, <= 0) → devuelve NaN, mismo
    criterio que convertir_a_eur().
    """
    if valor_eur is None or rate is None or pd.isna(rate) or rate <= 0:
        return float("nan")
    return valor_eur * rate


def calcular_pnl_eur(entry_value_usd, pnl_usd, rate_entrada, rate_salida):
    """
    Descompone el PnL de un trade individual en EUR en dos partes
    (22/08/2026, iniciativa "backtest más fiel a la realidad", Fase 5),
    separando el resultado de TRADING (en USD) del efecto de la DIVISA
    — nunca mezclados, tal como se pidió explícitamente:

      pnl_trading_eur : el PnL de trading en USD, convertido a EUR al
        tipo de cambio de SALIDA (lo que realmente se repatriaría).
      efecto_fx_eur   : cuánto cambió el valor en EUR de la posición
        ORIGINAL (entry_value_usd) solo por el movimiento del tipo de
        cambio entre entrada y salida — independiente de si el trade
        ganó o perdió en USD.

    pnl_total_eur = pnl_trading_eur + efecto_fx_eur, por construcción
    (efecto_fx_eur es el residuo, no una fórmula aparte inventada).

    El motor SIGUE dimensionando y decidiendo en USD sin cambios — esto
    es una capa de reporte en paralelo, nunca afecta al tamaño de
    posición ni al capital que usa el motor internamente.

    Fail-safe: cualquier tipo de cambio invalido (None, NaN, <= 0) →
    los tres valores devueltos son NaN — el PnL en USD (ya calculado
    aparte) sigue siendo la fuente de verdad, esto nunca bloquea nada.
    """
    if (rate_entrada is None or pd.isna(rate_entrada) or rate_entrada <= 0 or
            rate_salida is None or pd.isna(rate_salida) or rate_salida <= 0):
        return float("nan"), float("nan"), float("nan")

    exit_value_usd    = entry_value_usd + pnl_usd
    valor_entrada_eur = entry_value_usd / rate_entrada
    valor_salida_eur  = exit_value_usd / rate_salida
    pnl_total_eur     = valor_salida_eur - valor_entrada_eur
    pnl_trading_eur   = pnl_usd / rate_salida
    efecto_fx_eur     = pnl_total_eur - pnl_trading_eur

    return pnl_total_eur, pnl_trading_eur, efecto_fx_eur


def obtener_tipo_cambio(eurusd, fecha):
    """
    Busca el tipo de cambio EUR/USD (Close de EURUSD=X) vigente en
    `fecha`, usando asof() para rellenar hacia adelante cuando no hay
    entrada exacta ese día (fines de semana/festivos donde el mercado
    de acciones USA sí abrió pero el dato de FX cacheado no coincide
    exactamente, o huecos puntuales de la caché).

    `eurusd` : DataFrame con índice de fechas y columna "Close"
               (mismo formato que cualquier otro activo cacheado por
               data_manager.obtener_datos_cached("EURUSD=X", ...)).
               None → fail-safe, devuelve NaN (desactiva el reporte
               en EUR sin bloquear nada del motor en USD).

    Devuelve NaN si no hay dato disponible (fail-safe).
    """
    if eurusd is None or eurusd.empty:
        return float("nan")
    try:
        idx = eurusd.index.asof(pd.Timestamp(fecha))
        if pd.isna(idx):
            return float("nan")
        return float(eurusd.loc[idx, "Close"])
    except Exception:
        return float("nan")


def convertir_aportacion_a_usd(monto_eur, eurusd, fecha):
    """
    Convierte una aportación en EUR (CAPITAL_INICIAL o
    APORTACION_ANUAL) al USD nativo del motor, al tipo de cambio
    EUR/USD REAL de `fecha` — Fase 6 (22/08/2026), iniciativa
    "backtest más fiel a la realidad".

    A DIFERENCIA de convertir_a_eur()/calcular_pnl_eur() (Fase 5,
    22/08/2026 — capa de reporte que NUNCA toca el motor), esta
    conversión SÍ afecta al comportamiento real del motor: el capital
    disponible para dimensionar posiciones cada año pasa a depender
    del EUR/USD real de ese día, exactamente como en la cuenta IBKR
    real (Javier aporta EUR; IBKR los convierte a USD al tipo de
    cambio del día antes de poder comprar nada denominado en USD).
    CAMBIO DE COMPORTAMIENTO, no solo de reporte — v6 puede dar un
    número de trades distinto a v5, igual que pasó entre v4 y v5.

    Fail-safe (mismo patrón que el resto de la Fase 5, nunca rompe
    una llamada existente):
      - `eurusd` es None            → devuelve monto_eur SIN
        convertir. Comportamiento IDÉNTICO al de antes de la Fase 6
        — es el caso de TODAS las llamadas ya comiteadas de v3/v4/v5
        que no pasan este parámetro.
      - no hay tipo de cambio para `fecha` (NaN — huecos de caché,
        fecha anterior al histórico de EURUSD=X)
        → misma caída a monto_eur sin convertir. Nunca bloquea el
        backtest ni inventa un tipo de cambio.
    """
    if eurusd is None:
        return monto_eur
    rate = obtener_tipo_cambio(eurusd, fecha)
    convertido = convertir_eur_a_usd(monto_eur, rate)
    return convertido if not pd.isna(convertido) else monto_eur


def capital_inicial_usd_para_reporte(datos, eurusd):
    """
    Calcula el capital inicial REAL en USD (tras la conversión de la
    Fase 6) para que un caller externo se lo pase a
    calcular_metricas(capital_inicial_usd=...) — Fase 6 (22/08/2026),
    diseño "Opción A" aprobado por Javier.

    ejecutar_backtest() no puede devolver este valor como un 4º
    elemento de su tupla de retorno sin romper el
    "trades, curva_capital, capital_final = ejecutar_backtest(...)"
    de todos los callers ya comiteados que importan esta función de
    backtest_expandido (backtest_expandido.py::__main__,
    backtest_exp48_russell1000_v4.py, y los tests de las Fases 5/6).
    En vez de eso, este helper recalcula el mismo valor con la MISMA
    fórmula que usa ejecutar_backtest() por dentro (única fuente de
    verdad: convertir_aportacion_a_usd()) — nunca la duplica, solo la
    reutiliza — para que ambos lados queden sincronizados por
    construcción. Ver test_capital_inicial_usd_para_reporte_coincide_
    con_el_que_uso_el_motor en test_backtest_aportaciones_eur.py.

    `datos` : el mismo dict {symbol: DataFrame} que se le pasa a
              ejecutar_backtest() — se usa solo para hallar la primera
              fecha del backtest (mismo cálculo que fechas[0] dentro
              de ejecutar_backtest()).
    `eurusd`: igual que en ejecutar_backtest(). None (fail-safe) →
              devuelve None, así calcular_metricas() cae a
              CAPITAL_INICIAL — comportamiento idéntico al de antes de
              la Fase 6.
    """
    if eurusd is None or not datos:
        return None
    primera_fecha = min(fecha for df in datos.values() for fecha in df.index)
    return convertir_aportacion_a_usd(CAPITAL_INICIAL, eurusd, primera_fecha)


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

    tendencia = last["SMA50"] > last["SMA200"] and last["SMA200"] > prev["SMA200"]

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
# MOTOR DEL BACKTEST — con Risk Guardian completo
# ==================================================

def ejecutar_backtest(datos, composicion_df=None, eurusd=None):
    """
    Motor principal del backtest.

    Parámetros:
        datos          : dict {symbol: DataFrame} generado por descargar_datos()
        composicion_df : DataFrame con composición histórica del S&P500
                         (de cargar_composicion_sp500()).  None → sin filtro.
        eurusd         : DataFrame con histórico de EURUSD=X (columna
                         "Close", mismo formato que cualquier otro activo
                         cacheado). None (por defecto) → los trades no
                         llevan el desglose en EUR (pnl_eur/
                         pnl_trading_eur/efecto_fx_eur quedan ausentes)
                         — esa parte (Fase 5) es una capa de reporte en
                         paralelo que nunca afecta al motor. Las
                         comisiones (COMISION_IBKR) SÍ se aplican
                         siempre, independientemente de este parámetro.

                         AVISO (Fase 6, 22/08/2026): a diferencia de la
                         Fase 5, este parámetro YA NO es solo de
                         reporte. CAPITAL_INICIAL y APORTACION_ANUAL se
                         interpretan como EUR reales y se convierten al
                         USD nativo del motor al tipo de cambio real de
                         cada fecha (ver convertir_aportacion_a_usd()),
                         igual que Javier aporta EUR reales a la cuenta
                         IBKR. Con `eurusd` disponible, el resultado en
                         USD YA NO es idéntico al de `eurusd=None` — el
                         capital que dimensiona posiciones cada año
                         cambia, así que el nº de trades y el resultado
                         final pueden diferir de una ejecución sin este
                         parámetro. Sin `eurusd` (o sin tipo de cambio
                         disponible ese día), el comportamiento es
                         exactamente el mismo que antes de la Fase 6.
                         Ver obtener_tipo_cambio() y calcular_pnl_eur().
    """

    if composicion_df is None:
        composicion_df = pd.DataFrame()

    print("\nEjecutando backtest...\n")
    print(f"  Risk Guardian activo:")
    print(f"    Capital mínimo  : {RISK_MIN_CAPITAL:.0f}€")
    print(f"    Drawdown máximo : {RISK_MAX_DRAWDOWN:.0%}")
    universo_dinamico = not composicion_df.empty
    print(f"  Universo dinámico : {'SÍ (survivorship bias eliminado)' if universo_dinamico else 'NO (estático)'}")
    print(f"  Rebalanceo        : SÍ (umbral {REBALANCE_THRESHOLD:.0%}, mín. {REBALANCE_MIN_SHARES} acc.)")
    print()

    fechas = sorted(set(
        fecha
        for df in datos.values()
        for fecha in df.index
    ))

    # Fase 6 (22/08/2026): CAPITAL_INICIAL son EUR reales aportados a
    # la cuenta IBKR el primer día del backtest, convertidos al USD
    # nativo del motor al tipo de cambio EUR/USD REAL de ese día
    # exacto (ver convertir_aportacion_a_usd()). CAMBIO DE
    # COMPORTAMIENTO real (no solo de reporte, a diferencia de la
    # capa de la Fase 5): el capital que dimensiona posiciones desde
    # el día 1 pasa a depender del EUR/USD real de esa fecha. Sin
    # `eurusd` (fail-safe), CAPITAL_INICIAL se usa tal cual, sin
    # convertir — comportamiento idéntico al de antes de esta fase.
    capital        = CAPITAL_INICIAL if not fechas else \
        convertir_aportacion_a_usd(CAPITAL_INICIAL, eurusd, fechas[0])
    capital_pico   = capital   # Capital pico — se actualiza como en producción
    posiciones     = {}
    trades         = []
    curva_capital  = []

    def registrar_capital(fecha):
        """
        Construye la fila de curva_capital para `fecha`. Añade
        capital_eur (valor de mercado en EUR de la curva en USD, día a
        día, usando el tipo de cambio real de ese día) solo si `eurusd`
        está disponible — capa de reporte en paralelo, el motor sigue
        dimensionando en USD sin cambios (Fase 5, 22/08/2026).
        """
        fila = {"fecha": fecha, "capital": round(capital, 2)}
        if eurusd is not None:
            rate = obtener_tipo_cambio(eurusd, fecha)
            fila["capital_eur"] = round(convertir_a_eur(capital, rate), 2)
        return fila

    # Contadores Risk Guardian y rebalanceo para el informe
    dias_bloqueados_drawdown = 0
    dias_detenido_capital    = 0
    rebalanceos_ejecutados   = 0

    for fecha in fechas:

        idx = fechas.index(fecha)

        # --------------------------------------------------
        # 1. Aportación anual — primer día de trading del año
        # Fase 6 (22/08/2026): APORTACION_ANUAL son EUR reales,
        # convertidos al USD nativo del motor al tipo de cambio
        # EUR/USD de ESTE año concreto (no uno fijo ni el de hoy) —
        # ver convertir_aportacion_a_usd(). Mismo cambio de
        # comportamiento real y mismo fail-safe que el capital
        # inicial de arriba.
        # --------------------------------------------------
        if idx > 0 and fecha.year > fechas[idx - 1].year:
            aportacion = convertir_aportacion_a_usd(APORTACION_ANUAL, eurusd, fecha)
            capital += aportacion
            # La aportación también actualiza el pico si corresponde
            if capital > capital_pico:
                capital_pico = capital
            if eurusd is not None:
                print(f"  Aportación anual: +{APORTACION_ANUAL:.0f}€ "
                      f"(={aportacion:.2f}$ al cambio de {fecha.date()}) "
                      f"→ capital: {capital:.2f}€")
            else:
                print(f"  Aportación anual: +{APORTACION_ANUAL:.0f}€ → capital: {capital:.2f}€")

        # --------------------------------------------------
        # 2. Actualizar capital pico
        # --------------------------------------------------
        if capital > capital_pico:
            capital_pico = capital

        # --------------------------------------------------
        # 3. Risk Guardian — capital mínimo
        # Si el capital cae por debajo del mínimo el sistema
        # se detiene completamente — no gestiona ni abre nada
        # --------------------------------------------------
        if capital < RISK_MIN_CAPITAL:
            dias_detenido_capital += 1
            curva_capital.append(registrar_capital(fecha))
            continue

        # --------------------------------------------------
        # 4. Gestionar posiciones abiertas con trailing stop
        # Las posiciones se mantienen aunque el Risk Guardian
        # bloquee nuevas entradas — igual que en producción
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
                nuevo_stop = round(bar["High"] - atr * mult * TRAILING_FACTOR, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop

            # Mejora 4 — Break-even (idéntico a rebalance.py)
            # Si el cierre supera entry + 1.5×ATR → mover stop a entry + 0.5×ATR.
            # Solo sube el stop, nunca lo baja.
            if not pd.isna(atr) and atr > 0:
                be_stop = round(pos["entry"] + 0.5 * atr, 2)
                if bar["Close"] >= pos["entry"] + 1.5 * atr and be_stop > pos["stop"]:
                    pos["stop"] = be_stop

            # Palanca 2B — salida por cierre
            precio_referencia = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]

            if precio_referencia <= pos["stop"]:

                precio_salida_gap = calcular_precio_salida_stop(bar, pos["stop"])
                slippage          = calcular_slippage(
                    pos["shares"], bar["Volume"], atr)
                precio_salida     = precio_salida_gap - slippage  # cobra menos
                comision          = calcular_comision_total()
                pnl               = (precio_salida - pos["entry"]) * pos["shares"] - comision
                capital      += pnl

                trade = {
                    "symbol"       : symbol,
                    "clase"        : pos["clase"],
                    "fecha_entrada": pos["fecha_entrada"],
                    "fecha_salida" : fecha,
                    "entrada"      : round(pos["entry"], 4),
                    "salida"       : round(precio_salida, 4),
                    "shares"       : pos["shares"],
                    "comision"     : round(comision, 2),
                    "pnl"          : round(pnl, 2),
                    "resultado"    : "LOSS" if pnl < 0 else "WIN",
                    "capital"      : round(capital, 2),
                }

                if eurusd is not None:
                    entry_value_usd = pos["entry"] * pos["shares"]
                    rate_entrada    = obtener_tipo_cambio(eurusd, pos["fecha_entrada"])
                    rate_salida     = obtener_tipo_cambio(eurusd, fecha)
                    pnl_total_eur, pnl_trading_eur, efecto_fx_eur = calcular_pnl_eur(
                        entry_value_usd, pnl, rate_entrada, rate_salida)
                    trade["pnl_eur"]         = round(pnl_total_eur, 2)
                    trade["pnl_trading_eur"] = round(pnl_trading_eur, 2)
                    trade["efecto_fx_eur"]   = round(efecto_fx_eur, 2)

                trades.append(trade)

                cerradas.append(symbol)

        for symbol in cerradas:
            del posiciones[symbol]

        # --------------------------------------------------
        # 5. Risk Guardian — drawdown máximo
        # Si el drawdown supera el límite no se abren
        # posiciones nuevas pero las abiertas siguen activas
        # --------------------------------------------------
        drawdown_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0

        if drawdown_actual > RISK_MAX_DRAWDOWN:
            dias_bloqueados_drawdown += 1
            curva_capital.append(registrar_capital(fecha))
            continue

        # --------------------------------------------------
        # 6. Rebalanceo dinámico de posiciones abiertas
        # Ajusta tamaños que se desviaron > REBALANCE_THRESHOLD del
        # óptimo calculado con calcular_posicion().
        # Sigue la misma lógica que rebalance.py en producción:
        #   · REDUCIR : realiza PnL parcial → capital sube/baja
        #   · AMPLIAR : aumenta shares con entry ponderada
        #               (sin deducir coste — consistente con el modelo del backtest)
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

            # Protección MAX_POSITION_PCT — límite duro de concentración
            limite_valor = capital * MAX_POSITION_PCT
            if capital > 0 and valor_actual > limite_valor:
                shares_limite = int(limite_valor / precio)
                delta_lim     = shares_limite - shares_actual  # negativo
                if abs(delta_lim) >= REBALANCE_MIN_SHARES and shares_limite > 0:
                    shares_vendidas = shares_actual - shares_limite
                    pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                    capital        += pnl_parcial
                    posiciones[symbol]["shares"] = shares_limite
                    rebalanceos_ejecutados += 1
                    continue

            # Tamaño óptimo según calcular_posicion()
            shares_optimo, _, _ = calcular_posicion(df_reb, i_reb, capital)
            if shares_optimo <= 0:
                continue

            desviacion = (shares_actual - shares_optimo) / shares_optimo
            if abs(desviacion) <= REBALANCE_THRESHOLD:
                continue

            delta = shares_optimo - shares_actual  # positivo=ampliar, negativo=reducir
            if abs(delta) < REBALANCE_MIN_SHARES:
                continue

            if delta < 0:
                # REDUCIR — realizar PnL parcial de las acciones sobrantes
                shares_vendidas = -delta
                pnl_parcial     = (precio - pos["entry"]) * shares_vendidas
                capital        += pnl_parcial
                posiciones[symbol]["shares"] = shares_optimo

            else:
                # AMPLIAR — entry blended para no distorsionar el PnL final
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
            curva_capital.append(registrar_capital(fecha))
            continue

        # --------------------------------------------------
        # 8. Escanear señales
        # --------------------------------------------------
        señales = []

        # Composición del S&P500 vigente en esta fecha (para eliminar
        # survivorship bias — solo se escanean empresas que realmente
        # estaban en el índice ese día)
        sp500_hoy = sp500_en_fecha(composicion_df, fecha)

        for symbol in datos:

            if symbol in posiciones:
                continue

            # Filtro de composición histórica — elimina survivorship bias
            # Solo se evalúan empresas que realmente estaban en el S&P500
            # en esta fecha. Si sp500_hoy es None (fallback estático) se
            # escanean todos los activos del universo.
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

                    precio_entrada_gap = calcular_precio_entrada_stop(bar_entrada, buy_stop)
                    slippage           = calcular_slippage(
                        señal["shares"], bar_entrada["Volume"], bar_entrada["ATR"])
                    precio_entrada     = precio_entrada_gap + slippage  # paga más
                    coste              = precio_entrada * señal["shares"]

                    if coste > capital:
                        continue

                    posiciones[symbol] = {
                        "entry"         : precio_entrada,
                        "stop"          : stop_loss,
                        "shares"        : señal["shares"],
                        "clase"         : señal["clase"],
                        "fecha_entrada" : fecha_entrada,
                    }

        curva_capital.append(registrar_capital(fecha))

    # --------------------------------------------------
    # Cerrar posiciones abiertas al final del período
    # --------------------------------------------------
    for symbol, pos in posiciones.items():

        df = datos[symbol]

        if df.empty:
            continue

        ultimo_cierre = df.iloc[-1]["Close"]
        comision      = calcular_comision_total()
        pnl           = (ultimo_cierre - pos["entry"]) * pos["shares"] - comision
        capital      += pnl

        trade = {
            "symbol"       : symbol,
            "clase"        : pos["clase"],
            "fecha_entrada": pos["fecha_entrada"],
            "fecha_salida" : df.index[-1],
            "entrada"      : round(pos["entry"], 4),
            "salida"       : round(ultimo_cierre, 4),
            "shares"       : pos["shares"],
            "comision"     : round(comision, 2),
            "pnl"          : round(pnl, 2),
            "resultado"    : "OPEN→CLOSE",
            "capital"      : round(capital, 2),
        }

        if eurusd is not None:
            entry_value_usd = pos["entry"] * pos["shares"]
            rate_entrada    = obtener_tipo_cambio(eurusd, pos["fecha_entrada"])
            rate_salida     = obtener_tipo_cambio(eurusd, df.index[-1])
            pnl_total_eur, pnl_trading_eur, efecto_fx_eur = calcular_pnl_eur(
                entry_value_usd, pnl, rate_entrada, rate_salida)
            trade["pnl_eur"]         = round(pnl_total_eur, 2)
            trade["pnl_trading_eur"] = round(pnl_trading_eur, 2)
            trade["efecto_fx_eur"]   = round(efecto_fx_eur, 2)

        trades.append(trade)

    print(f"\n  Risk Guardian — resumen:")
    print(f"    Días bloqueados por drawdown : {dias_bloqueados_drawdown}")
    print(f"    Días detenido por capital    : {dias_detenido_capital}")
    print(f"    Rebalanceos ejecutados       : {rebalanceos_ejecutados}")

    return trades, curva_capital, capital


# ==================================================
# MÉTRICAS
# ==================================================

def calcular_metricas(trades, curva_capital, capital_final, capital_inicial_usd=None):
    """
    `capital_inicial_usd` (Fase 6, 22/08/2026, "Opción A" aprobada por
    Javier) — opcional, retrocompatible: None (por defecto) → cae
    exactamente al comportamiento de antes de la Fase 6, usando la
    constante CAPITAL_INICIAL para retorno_total y "capital_inicial".

    Con la Fase 6, cuando ejecutar_backtest() recibe `eurusd`,
    CAPITAL_INICIAL (EUR) se convierte a USD al tipo de cambio real
    del día 1 (ver convertir_aportacion_a_usd()) — capital_final ya
    viene en ese USD convertido, así que retorno_total DEBE calcularse
    contra ese mismo valor, no contra la constante EUR-nominal, o
    saldría mal calculado. ejecutar_backtest() no puede devolver ese
    valor real como un 4º elemento de su tupla sin romper a todos los
    callers existentes (ver capital_inicial_usd_para_reporte()) — por
    eso el caller se lo pasa aquí explícitamente cuando lo tiene.
    """

    if not trades:
        return {}

    capital_inicial = capital_inicial_usd if capital_inicial_usd is not None else CAPITAL_INICIAL

    df_trades  = pd.DataFrame(trades)
    df_capital = pd.DataFrame(curva_capital)

    total_trades = len(df_trades)
    # OPEN→CLOSE trades (still open at period end) are included in capital_final,
    # so they must also count here; classify them by PnL sign for consistency.
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

    # Fase 6 (22/08/2026): contra capital_inicial (real, convertido si
    # se pasó capital_inicial_usd — ver docstring de esta función), no
    # contra la constante CAPITAL_INICIAL a secas.
    retorno_total  = (capital_final - capital_inicial) / capital_inicial
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

    resultado = {
        "total_trades"    : total_trades,
        "wins"            : len(wins),
        "losses"          : len(losses),
        "win_rate"        : win_rate,
        "profit_factor"   : profit_factor,
        "max_drawdown"    : max_drawdown,
        "retorno_total"   : retorno_total,
        "capital_inicial" : capital_inicial,
        "capital_final"   : round(capital_final, 2),
        "pnl_medio_win"   : round(pnl_medio_win, 2),
        "pnl_medio_loss"  : round(pnl_medio_loss, 2),
        "expectativa"     : round(expectativa, 2),
        "por_clase"       : por_clase,
    }

    # Comisiones (Fase 5, 22/08/2026) — siempre presentes, se aplican
    # incondicionalmente en el motor.
    if "comision" in df_trades.columns:
        resultado["comision_total"] = round(df_trades["comision"].sum(), 2)

    # Desglose EUR (Fase 5, 22/08/2026) — solo si el run se ejecutó con
    # eurusd disponible. Capa de reporte en paralelo: nunca sustituye a
    # las cifras en USD de arriba, que siguen siendo la fuente de verdad
    # del motor.
    if "pnl_trading_eur" in df_trades.columns:
        resultado["pnl_trading_eur_total"] = round(df_trades["pnl_trading_eur"].sum(), 2)
        resultado["efecto_fx_eur_total"]   = round(df_trades["efecto_fx_eur"].sum(), 2)
        resultado["pnl_eur_total"]         = round(df_trades["pnl_eur"].sum(), 2)
    if "capital_eur" in df_capital.columns and not df_capital["capital_eur"].isna().all():
        resultado["capital_final_eur"] = round(df_capital["capital_eur"].iloc[-1], 2)

    return resultado


# ==================================================
# GUARDAR RESULTADOS
# ==================================================

def guardar_resultados(trades, curva_capital, metricas):

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if trades:
        pd.DataFrame(trades).to_csv(
            f"{LOG_DIR}/expandido_trades_{timestamp}.csv", index=False)
        print(f"\nTrades guardados    : {LOG_DIR}/expandido_trades_{timestamp}.csv")

    if curva_capital:
        pd.DataFrame(curva_capital).to_csv(
            f"{LOG_DIR}/expandido_capital_{timestamp}.csv", index=False)
        print(f"Curva de capital    : {LOG_DIR}/expandido_capital_{timestamp}.csv")

    if metricas:
        metricas_flat = {k: v for k, v in metricas.items() if k != "por_clase"}
        pd.DataFrame([metricas_flat]).to_csv(
            f"{LOG_DIR}/expandido_metricas_{timestamp}.csv", index=False)
        print(f"Métricas guardadas  : {LOG_DIR}/expandido_metricas_{timestamp}.csv")


# ==================================================
# INFORME EN CONSOLA
# ==================================================

def imprimir_informe(metricas):

    sep = "─" * 50

    print(f"\n{sep}")
    print(f"  LIBERTAD_2045 — BACKTEST EXPANDIDO")
    print(f"  {START_DATE} → {END_DATE}")
    print(f"  Experimento 30 — Simulación fiel con Risk Guardian")
    print(f"  Salida por cierre : {'SÍ' if SALIDA_POR_CIERRE else 'NO'}")
    print(f"  Min. capital      : {RISK_MIN_CAPITAL:.0f}€")
    print(f"  Max. drawdown     : {RISK_MAX_DRAWDOWN:.0%}")
    print(sep)

    print(f"\n  CAPITAL (USD — resultado de trading, motor sin cambios)")
    print(f"  Inicial          : {metricas['capital_inicial']:>12.2f} €")
    print(f"  Final            : {metricas['capital_final']:>12.2f} €")
    print(f"  Retorno total    : {metricas['retorno_total']:>12.1%}")
    if "comision_total" in metricas:
        print(f"  Comisiones total : {metricas['comision_total']:>12.2f} $")
    if "capital_final_eur" in metricas:
        print(f"\n  CAPITAL EN EUR (mark-to-market, capa de reporte — Fase 5)")
        print(f"  Final en EUR     : {metricas['capital_final_eur']:>12.2f} €")
        print(f"  PnL trading (EUR): {metricas['pnl_trading_eur_total']:>12.2f} €")
        print(f"  Efecto FX (EUR)  : {metricas['efecto_fx_eur_total']:>12.2f} €")

    print(f"\n  OPERATIVA GLOBAL")
    print(f"  Total trades     : {metricas['total_trades']:>12d}")
    print(f"  Wins             : {metricas['wins']:>12d}")
    print(f"  Losses           : {metricas['losses']:>12d}")
    print(f"  Win rate         : {metricas['win_rate']:>12.1%}")

    print(f"\n  RIESGO")
    print(f"  Profit factor    : {metricas['profit_factor']:>12.4f}")
    print(f"  Drawdown máximo  : {metricas['max_drawdown']:>12.1%}")
    print(f"  PnL medio WIN    : {metricas['pnl_medio_win']:>12.2f} €")
    print(f"  PnL medio LOSS   : {metricas['pnl_medio_loss']:>12.2f} €")
    print(f"  Expectativa/trade: {metricas['expectativa']:>12.2f} €")

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

    apto = supervivencia and disciplina and consistencia
    print(f"\n  {'✓ SISTEMA APTO PARA PAPER TRADING EXPANDIDO' if apto else '✗ SISTEMA REQUIERE AJUSTES'}")
    print(f"\n{sep}\n")


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    print("=" * 50)
    print("  LIBERTAD_2045 — BACKTEST EXPANDIDO")
    print("=" * 50)
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

    # --------------------------------------------------
    # Universo dinámico del S&P500
    # Descarga la composición histórica de fja05680/sp500 y construye
    # el universo como unión de todos los tickers que alguna vez
    # estuvieron en el índice, eliminando survivorship bias.
    # --------------------------------------------------
    comp_df  = cargar_composicion_sp500()
    universo = universo_historico_sp500(comp_df)

    print(f"  Universo     : {len(universo)} activos históricos únicos")

    datos = descargar_datos(universo, START_DATE, END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    # --------------------------------------------------
    # Desglose FX de reporte (Fase 5, commit 39f4f4d) + conversión de
    # capital inicial/aportaciones (Fase 6, 22/08/2026) — capa que se
    # activa solo si hay caché de EURUSD=X, se carga igual que
    # cualquier otro activo cacheado. AVISO: antes de la Fase 6 este
    # __main__ nunca pasaba `eurusd` — se añade aquí porque si no, el
    # parámetro capital_inicial_usd de calcular_metricas() de más
    # abajo (Opción A, aprobada por Javier) no tendría ningún caso real
    # que ejercitar en la ejecución canónica de este fichero. Fail-safe
    # sin caché: eurusd=None, comportamiento idéntico al de siempre.
    # --------------------------------------------------
    from data_manager import obtener_datos_cached
    eurusd = obtener_datos_cached("EURUSD=X", START_DATE, END_DATE)
    if eurusd is None:
        print("\n  AVISO: no se pudo cargar EURUSD=X — el backtest sigue en "
              "USD sin desglose en EUR (fail-safe, sin cambio de comportamiento).")

    trades, curva_capital, capital_final = ejecutar_backtest(
        datos, composicion_df=comp_df, eurusd=eurusd)
    capital_inicial_usd = capital_inicial_usd_para_reporte(datos, eurusd)
    metricas = calcular_metricas(
        trades, curva_capital, capital_final, capital_inicial_usd=capital_inicial_usd)
    imprimir_informe(metricas)
    guardar_resultados(trades, curva_capital, metricas)
