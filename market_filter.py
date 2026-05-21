import os
import pandas as pd
import yfinance as yf

from logger import log_event


# --------------------------------------------------
# Configuración
# --------------------------------------------------

MARKET_PROXY       = os.getenv("MARKET_PROXY", "SPY")       # ETF de referencia del mercado
MARKET_DATA_PERIOD = os.getenv("MARKET_DATA_PERIOD", "1y")  # Período de datos históricos
BYPASS_FILTER      = os.getenv("MARKET_FILTER_BYPASS", "false").lower() == "true"

# Estados del mercado
ALCISTA = "ALCISTA"
NEUTRO  = "NEUTRO"
BAJISTA = "BAJISTA"


def _descargar_datos_spy():
    """
    Descarga los datos históricos del proxy de mercado (SPY por defecto).
    Devuelve un DataFrame con columnas normalizadas, o None si falla.
    """
    try:
        ticker = yf.Ticker(MARKET_PROXY)
        df = ticker.history(period=MARKET_DATA_PERIOD, auto_adjust=True)

        if df is None or len(df) < 200:
            log_event("WARN", f"Market Filter: datos insuficientes para {MARKET_PROXY} "
                               f"({len(df) if df is not None else 0} barras)")
            return None

        df.index = pd.to_datetime(df.index)
        df.columns = [c.lower() for c in df.columns]

        df["SMA50"]  = df["close"].rolling(50).mean()
        df["SMA200"] = df["close"].rolling(200).mean()

        return df

    except Exception as e:
        log_event("ERROR", f"Market Filter: error descargando {MARKET_PROXY}: {e}")
        return None


def evaluar_mercado():
    """
    Evalúa el estado actual del mercado usando el proxy configurado (SPY).

    Lógica de clasificación:
        ALCISTA : close > SMA50  y  SMA50 > SMA200
                  Tendencia primaria alcista confirmada en ambos plazos.
                  El sistema opera con normalidad.

        NEUTRO  : close > SMA200  pero  close <= SMA50
                  El mercado está por encima de su media de largo plazo
                  pero ha perdido impulso de corto plazo.
                  El sistema opera con normalidad — señal de precaución.

        BAJISTA : close <= SMA200
                  El mercado ha perdido su soporte de largo plazo.
                  El sistema NO abre nuevas posiciones.
                  Las posiciones existentes mantienen sus stops activos.

    Si los datos no están disponibles, devuelve NEUTRO para no bloquear
    el sistema por un fallo externo de datos.

    Retorna una tupla (estado, detalle) donde:
        estado  : str  — "ALCISTA" | "NEUTRO" | "BAJISTA"
        detalle : dict — valores numéricos para trazabilidad
    """

    # --------------------------------------------------
    # Bypass manual (variable de entorno)
    # --------------------------------------------------

    if BYPASS_FILTER:
        log_event("INFO", "Market Filter: bypass activado — mercado tratado como ALCISTA")
        return ALCISTA, {"bypass": True}

    # --------------------------------------------------
    # Descargar y calcular indicadores
    # --------------------------------------------------

    df = _descargar_datos_spy()

    if df is None:
        log_event("WARN", "Market Filter: datos no disponibles — operando en modo NEUTRO")
        return NEUTRO, {"error": "datos no disponibles"}

    last = df.iloc[-1]

    close  = last["close"]
    sma50  = last["SMA50"]
    sma200 = last["SMA200"]

    if pd.isna(sma50) or pd.isna(sma200):
        log_event("WARN", "Market Filter: SMA no calculable — operando en modo NEUTRO")
        return NEUTRO, {"error": "SMA no disponible"}

    detalle = {
        "proxy":  MARKET_PROXY,
        "close":  round(close,  2),
        "SMA50":  round(sma50,  2),
        "SMA200": round(sma200, 2),
        "close_vs_SMA50":  round(close - sma50,  2),
        "close_vs_SMA200": round(close - sma200, 2),
        "SMA50_vs_SMA200": round(sma50  - sma200, 2),
    }

    # --------------------------------------------------
    # Clasificación
    # --------------------------------------------------

    if close <= sma200:
        estado = BAJISTA

    elif close <= sma50:
        estado = NEUTRO

    else:
        estado = ALCISTA

    log_event("INFO",
              f"Market Filter: {MARKET_PROXY} → {estado} | "
              f"close={close:.2f} | SMA50={sma50:.2f} | SMA200={sma200:.2f}")

    return estado, detalle


def mercado_permite_entradas():
    """
    Interfaz simplificada para el orquestador.

    Retorna True si el mercado está en estado ALCISTA o NEUTRO.
    Retorna False si el mercado está en estado BAJISTA.

    En caso de error de datos, retorna True para no bloquear
    el sistema por un fallo externo.
    """
    estado, detalle = evaluar_mercado()

    if estado == BAJISTA:
        log_event("WARN",
                  f"Market Filter: mercado BAJISTA — nuevas entradas bloqueadas | "
                  f"SPY close={detalle.get('close', '?')} | "
                  f"SMA200={detalle.get('SMA200', '?')}")
        return False

    return True