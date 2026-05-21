from ib_insync import Stock, util

import numpy as np
import pandas as pd

from logger import log_event


# --------------------------------------------------
# Configuración del percentil ATR (experimento 16 — B1)
# --------------------------------------------------

ATR_PERCENTIL_VENTANA = 252   # Días para calcular el percentil histórico del ATR


def obtener_datos(ib, symbol, duration="1 Y", bar_size="1 day"):
    """
    Descarga datos históricos desde IBKR y devuelve un DataFrame
    con indicadores calculados y listos para el signal engine.

    Indicadores incluidos:
        SMA50         : media móvil simple de 50 periodos
        SMA200        : media móvil simple de 200 periodos
        ATR           : Average True Range de 14 periodos (fuente única del sistema)
        ATR_PERCENTIL : percentil del ATR actual respecto a los últimos 252 días
                        → usado por position_size para el stop loss dinámico B1

    Retorna el DataFrame enriquecido, o None si los datos no son válidos.
    El orquestador trata None como señal de que el activo debe descartarse
    en este ciclo sin interrumpir el escaneo.
    """

    try:

        contract = Stock(symbol, "SMART", "USD")

        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="2 Y",
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True
        )

        if not bars:
            log_event("WARN", f"Sin datos para {symbol}: IBKR devolvió respuesta vacía",
                      symbol=symbol)
            return None

        df = util.df(bars)

        if df.empty:
            log_event("WARN", f"DataFrame vacío para {symbol}", symbol=symbol)
            return None


        # --------------------------------------------------
        # Limpiar filas con datos OHLC corruptos o parciales
        # --------------------------------------------------

        df = df.dropna(subset=["open", "high", "low", "close"])

        if df.empty:
            log_event("WARN", f"DataFrame sin filas válidas tras limpiar NaN: {symbol}",
                      symbol=symbol)
            return None

        df = df.reset_index(drop=True)


        # --------------------------------------------------
        # Medias móviles
        # --------------------------------------------------

        df["SMA50"]  = df["close"].rolling(50).mean()
        df["SMA200"] = df["close"].rolling(200).mean()


        # --------------------------------------------------
        # ATR (Average True Range, 14 periodos)
        # Fuente única del sistema — position_size lo lee de aquí
        # --------------------------------------------------

        df["prev_close"] = df["close"].shift(1)

        df["tr1"] = df["high"] - df["low"]
        df["tr2"] = (df["high"] - df["prev_close"]).abs()
        df["tr3"] = (df["low"]  - df["prev_close"]).abs()

        df["TR"]  = df[["tr1", "tr2", "tr3"]].max(axis=1)
        df["ATR"] = df["TR"].rolling(14).mean()

        # Limpiar columnas intermedias del cálculo
        df = df.drop(columns=["prev_close", "tr1", "tr2", "tr3", "TR"])


        # --------------------------------------------------
        # ATR_PERCENTIL — stop loss dinámico B1 (experimento 16)
        # Percentil del ATR actual respecto a su historial de 252 días.
        # Percentil alto = volatilidad alta ahora = stop más ajustado.
        # Percentil bajo = volatilidad baja ahora = stop más holgado.
        # --------------------------------------------------

        df["ATR_PERCENTIL"] = df["ATR"].rolling(ATR_PERCENTIL_VENTANA).rank(pct=True)

        return df

    except Exception as e:
        log_event("ERROR", f"Error descargando datos de {symbol}: {e}", symbol=symbol)
        return None
