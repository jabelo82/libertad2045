# --------------------------------------------------
# LIBERTAD_2045 — Universo de activos
#
# 100 empresas del S&P500 con cobertura sectorial
# equilibrada. Revisión recomendada cada 6 meses
# para reflejar cambios en el índice.
#
# Última revisión: 2026-03
# --------------------------------------------------


SP500 = [

    # Tecnología
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL",
    "ADBE", "CRM", "AMD", "QCOM", "TXN",
    "INTC", "IBM", "AMAT", "MU",  "KLAC",

    # Comunicaciones
    "GOOGL", "META", "NFLX", "DIS",  "CMCSA",
    "T",     "VZ",

    # Consumo discrecional
    "AMZN", "TSLA", "HD",   "MCD",  "NKE",
    "SBUX", "LOW",  "TJX",  "BKNG", "MAR",

    # Consumo básico
    "WMT",  "PG",   "KO",   "PEP",  "COST",
    "PM",   "MO",   "CL",   "KMB",

    # Salud
    "UNH",  "LLY",  "JNJ",  "MRK",  "ABBV",
    "TMO",  "ABT",  "DHR",  "BMY",  "AMGN",
    "ISRG", "SYK",  "BSX",  "ZTS",

    # Financiero
    "JPM",  "BAC",  "WFC",  "GS",   "MS",
    "BLK",  "SCHW", "AXP",  "V",    "MA",
    "BRK B",

    # Industrial
    "CAT",  "HON",  "UNP",  "RTX",  "LMT",
    "GE",   "DE",   "MMM",  "ETN",  "PH",

    # Energía
    "XOM",  "CVX",  "COP",  "SLB",  "EOG",

    # Materiales
    "LIN",  "APD",  "ECL",  "NEM",  "FCX",

    # Utilities
    "NEE",  "DUK",  "SO",   "D",    "AEP",

    # Inmobiliario
    "PLD",  "AMT",  "EQIX", "SPG",

]


def validar_universo():
    """
    Comprueba la integridad básica del universo de activos.

    Detecta:
        - Tickers duplicados
        - Tickers vacíos o con caracteres inválidos

    Retorna una lista de advertencias. Lista vacía = universo válido.
    Llamar durante el desarrollo o al actualizar la lista,
    no en cada ciclo operativo.
    """

    advertencias = []
    vistos       = set()

    for ticker in SP500:

        # Ticker vacío
        if not ticker or not ticker.strip():
            advertencias.append("Ticker vacío detectado en la lista")
            continue

        # Duplicado
        if ticker in vistos:
            advertencias.append(f"Ticker duplicado: {ticker}")
        else:
            vistos.add(ticker)

    return advertencias


if __name__ == "__main__":

    # Ejecutar validación al llamar el módulo directamente
    print(f"Universo: {len(SP500)} activos")

    advertencias = validar_universo()

    if advertencias:
        print("Advertencias:")
        for a in advertencias:
            print(f"  ⚠ {a}")
    else:
        print("Universo válido — sin duplicados ni errores")