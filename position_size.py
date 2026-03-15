import pandas as pd


RISK_PERCENT       = 0.0065   # Riesgo por operación: 0.65% del capital
ATR_MULTIPLIER     = 2        # Distancia del stop-loss en múltiplos de ATR
MAX_POSITION_PCT   = 0.25     # Tamaño máximo de una posición: 25% del capital


def calcular_posicion(df, capital):
    """
    Calcula el tamaño de posición basado en riesgo real.

    Parámetros:
        df      : DataFrame con columnas ATR y close ya calculadas por data_loader
        capital : capital real de la cuenta, leído de IBKR en cada ciclo

    Retorna:
        shares        : número de acciones a comprar (0 si no se debe operar)
        stop_distance : distancia en precio hasta el stop-loss
        atr           : valor ATR usado en el cálculo
    """

    # --------------------------------------------------
    # Validaciones previas
    # --------------------------------------------------

    if df is None or len(df) < 20:
        return 0, None, None

    if capital <= 0:
        return 0, None, None

    # Leer ATR calculado por data_loader (sin recalcular)
    atr = df["ATR"].iloc[-1]

    if pd.isna(atr) or atr <= 0:
        return 0, None, None

    last_price = df["close"].iloc[-1]

    if pd.isna(last_price) or last_price <= 1:
        return 0, None, None

    # --------------------------------------------------
    # Cálculo del tamaño de posición
    # --------------------------------------------------

    stop_distance = atr * ATR_MULTIPLIER

    if stop_distance <= 0:
        return 0, None, None

    # Shares por riesgo: cuántas acciones puedo comprar
    # para no perder más de RISK_PERCENT del capital si toca el stop
    risk_amount   = capital * RISK_PERCENT
    shares_risk   = int(risk_amount / stop_distance)

    # Shares por capital: límite máximo de exposición por posición
    max_position_value = capital * MAX_POSITION_PCT
    shares_capital     = int(max_position_value / last_price)

    # El tamaño final es el mínimo de ambos límites
    shares = min(shares_risk, shares_capital)

    # Si el cálculo da 0, no se opera — nunca forzar una posición
    if shares <= 0:
        return 0, None, None

    return shares, stop_distance, atr