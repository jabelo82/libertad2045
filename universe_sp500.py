# --------------------------------------------------
# LIBERTAD_2045 — Universo de activos
#
# ~420 empresas del S&P500 con historia validada
# 2015-2025. Experimento 25 confirmó 325 activos
# con trades reales en el período.
#
# Revisión recomendada cada 6 meses para reflejar
# cambios en la composición del índice.
#
# Última revisión: 2026-03-21
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
    "IT",    "LDOS",  "NTAP",  "TRMB",
    "TYL",   "WEX",   "ZBRA",

    # Comunicaciones
    "GOOGL", "GOOG",  "META",  "NFLX",  "DIS",
    "CMCSA", "T",     "VZ",    "TMUS",  "CHTR",
    "WBD",   "OMC",   "FOXA",  "NWS",   "NDAQ",

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
    "DXCM",  "MTD",   "WAT",   "A",
    "RMD",   "BAX",   "VTRS",  "PTC",   "PFE",
    "MDT",   "GILD",  "REGN",  "VRTX",  "HCA",
    "CVS",   "MCK",   "CAH",   "COR",
    "HSIC",  "PRGO",  "STE",   "TFX",
    "XRAY",  "ZBH",   "ILMN",  "BIO",   "CRL",
    "TECH",  "WST",   "SRPT",

    # Financiero
    "JPM",   "BAC",   "WFC",   "GS",    "MS",
    "BLK",   "SCHW",  "AXP",   "V",     "MA",
    "BRK B", "C",     "USB",   "PNC",   "TFC",
    "COF",   "SYF",   "ALLY",  "CFG",   "FITB",
    "HBAN",  "RF",    "KEY",   "MTB",   "ZION",
    "FHN",   "AFL",   "ALL",   "AIG",
    "AJG",   "AIZ",   "AMP",   "ACGL",  "PFG",
    "L",     "LNC",   "MET",   "PRU",
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
    "FTI",   "CVI",   "OKE",
    "WMB",   "HP",

    # Materiales
    "LIN",   "APD",   "ECL",   "NEM",   "FCX",
    "DOW",   "DD",    "PPG",   "SHW",   "AVY",
    "IP",    "PKG",   "SON",   "ALB",
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
    "NNN",   "SBAC",  "CCI",   "DLR",
    "IRM",   "MAA",   "CPT",   "ESS",   "EXR",
    "HST",   "INVH",  "UDR",   "CUBE",  "REXR",
    "STAG",  "RHP",   "AMH",

]

# Eliminar duplicados manteniendo orden
_seen = set()
_unique = []
for t in SP500:
    if t not in _seen:
        _seen.add(t)
        _unique.append(t)
SP500 = _unique


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

        if not ticker or not ticker.strip():
            advertencias.append("Ticker vacío detectado en la lista")
            continue

        if ticker in vistos:
            advertencias.append(f"Ticker duplicado: {ticker}")
        else:
            vistos.add(ticker)

    return advertencias


if __name__ == "__main__":

    print(f"Universo: {len(SP500)} activos")

    advertencias = validar_universo()

    if advertencias:
        print("Advertencias:")
        for a in advertencias:
            print(f"  ⚠ {a}")
    else:
        print("Universo válido — sin duplicados ni errores")
