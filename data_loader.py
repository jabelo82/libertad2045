from ib_insync import Stock, util

import pandas as pd

from logger import log_event


def obtener_datos(ib, symbol, duration="1 Y", bar_size="1 day"):
    """
    Descarga datos históricos desde IBKR y devuelve un DataFrame
    con indicadores calculados y listos para el signal engine.

    Indicadores incluidos:
        SMA50  : media móvil simple de 50 periodos
        SMA200 : media móvil simple de 200 periodos
        ATR    : Average True Range de 14 periodos (fuente única del sistema)

    Retorna el DataFrame enriquecido, o None si los datos no son válidos.
    El orquestador trata None como señal de que el activo debe descartarse
    en este ciclo sin interrumpir el escaneo.
    """

    try:

        contract = Stock(symbol, "SMART", "USD")

        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
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

        return df

    except Exception as e:
        log_event("ERROR", f"Error descargando datos de {symbol}: {e}", symbol=symbol)
        return None