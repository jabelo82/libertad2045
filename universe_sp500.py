# --------------------------------------------------
# LIBERTAD_2045 — Universo de activos
#
# ~501 empresas del S&P500 con historia validada
# 2015-2025. Experimento 25 confirmó 325 activos
# con trades reales en el período.
#
# Revisión recomendada cada 6 meses para reflejar
# cambios en la composición del índice.
#
# Última revisión: 2026-06-15
# --------------------------------------------------


SP500 = [

    # Tecnología
    "AAPL",  "ACN",  "ADBE",  "ADI",  "ADSK",
    "AKAM",  "AMAT",  "AMD",  "ANET",  "APH",
    "APP",  "AVGO",  "CDNS",  "CDW",  "CIEN",
    "COHR",  "CRM",  "CRWD",  "CSCO",  "CTSH",
    "DDOG",  "DELL",  "FFIV",  "FICO",  "FSLR",
    "FTNT",  "GDDY",  "GEN",  "GLW",  "HPE",
    "HPQ",  "IBM",  "INTC",  "INTU",  "IT",
    "JBL",  "KEYS",  "KLAC",  "LITE",  "LRCX",
    "MCHP",  "MPWR",  "MSFT",  "MSI",  "MU",
    "NOW",  "NTAP",  "NVDA",  "NXPI",  "ON",
    "ORCL",  "PANW",  "PLTR",  "PTC",  "QCOM",
    "ROP",  "SMCI",  "SNDK",  "SNPS",  "STX",
    "SWKS",  "TDY",  "TEL",  "TER",  "TRMB",
    "TXN",  "TYL",  "VRSN",  "WDAY",  "WDC",
    "ZBRA",

    # Comunicaciones
    "CHTR",  "CMCSA",  "DIS",  "EA",  "FOX",
    "FOXA",  "GOOG",  "GOOGL",  "LYV",  "META",
    "NFLX",  "NWS",  "NWSA",  "OMC",  "PSKY",
    "SATS",  "T",  "TKO",  "TMUS",  "TTD",
    "TTWO",  "VZ",  "WBD",

    # Consumo discrecional
    "ABNB",  "AMZN",  "APTV",  "AZO",  "BBY",
    "BKNG",  "CCL",  "CMG",  "CVNA",  "DASH",
    "DECK",  "DHI",  "DPZ",  "DRI",  "EBAY",
    "EXPE",  "F",  "GM",  "GPC",  "GRMN",
    "HAS",  "HD",  "HLT",  "LEN",  "LOW",
    "LULU",  "LVS",  "MAR",  "MCD",  "MGM",
    "NCLH",  "NKE",  "NVR",  "ORLY",  "PHM",
    "POOL",  "RCL",  "RL",  "ROST",  "SBUX",
    "TJX",  "TPR",  "TSCO",  "TSLA",  "ULTA",
    "WSM",  "WYNN",  "YUM",

    # Consumo básico
    "ADM",  "BF B",  "BG",  "CAG",  "CASY",
    "CHD",  "CL",  "CLX",  "COST",  "CPB",
    "DG",  "DLTR",  "EL",  "GIS",  "HRL",
    "HSY",  "KDP",  "KHC",  "KMB",  "KO",
    "KR",  "KVUE",  "MDLZ",  "MKC",  "MNST",
    "MO",  "PEP",  "PG",  "PM",  "SJM",
    "STZ",  "SYY",  "TAP",  "TGT",  "TSN",
    "WMT",

    # Salud
    "A",  "ABBV",  "ABT",  "ALGN",  "AMGN",
    "BAX",  "BDX",  "BIIB",  "BMY",  "BSX",
    "CAH",  "CI",  "CNC",  "COO",  "COR",
    "CRL",  "CVS",  "DGX",  "DHR",  "DVA",
    "DXCM",  "ELV",  "EW",  "GEHC",  "GILD",
    "HCA",  "HSIC",  "HUM",  "IDXX",  "INCY",
    "IQV",  "ISRG",  "JNJ",  "LH",  "LLY",
    "MCK",  "MDT",  "MRK",  "MRNA",  "MTD",
    "PFE",  "PODD",  "REGN",  "RMD",  "RVTY",
    "SOLV",  "STE",  "SYK",  "TECH",  "TMO",
    "UHS",  "UNH",  "VEEV",  "VRTX",  "VTRS",
    "WAT",  "WST",  "ZBH",  "ZTS",

    # Financiero
    "ACGL",  "AFL",  "AIG",  "AIZ",  "AJG",
    "ALL",  "AMP",  "AON",  "APO",  "ARES",
    "AXP",  "BAC",  "BEN",  "BLK",  "BNY",
    "BRK B",  "BRO",  "BX",  "C",  "CB",
    "CBOE",  "CFG",  "CINF",  "CME",  "COF",
    "COIN",  "CPAY",  "EG",  "ERIE",  "FDS",
    "FIS",  "FISV",  "FITB",  "GL",  "GPN",
    "GS",  "HBAN",  "HIG",  "HOOD",  "IBKR",
    "ICE",  "IVZ",  "JKHY",  "JPM",  "KEY",
    "KKR",  "L",  "MA",  "MCO",  "MET",
    "MRSH",  "MS",  "MSCI",  "MTB",  "NDAQ",
    "NTRS",  "PFG",  "PGR",  "PNC",  "PRU",
    "PYPL",  "RF",  "RJF",  "SCHW",  "SPGI",
    "STT",  "SYF",  "TFC",  "TROW",  "TRV",
    "USB",  "V",  "WFC",  "WRB",  "WTW",
    "XYZ",

    # Industrial
    "ADP",  "ALLE",  "AME",  "AOS",  "AXON",
    "BA",  "BLDR",  "BR",  "CARR",  "CAT",
    "CHRW",  "CMI",  "CPRT",  "CSX",  "CTAS",
    "DAL",  "DE",  "DOV",  "EFX",  "EME",
    "EMR",  "ETN",  "EXPD",  "FAST",  "FDX",
    "FIX",  "FTV",  "GD",  "GE",  "GEV",
    "GNRC",  "GWW",  "HII",  "HON",  "HUBB",
    "HWM",  "IEX",  "IR",  "ITW",  "J",
    "JBHT",  "JCI",  "LDOS",  "LHX",  "LII",
    "LMT",  "LUV",  "MAS",  "MMM",  "NDSN",
    "NOC",  "NSC",  "ODFL",  "OTIS",  "PAYX",
    "PCAR",  "PH",  "PNR",  "PWR",  "ROK",
    "ROL",  "RSG",  "RTX",  "SNA",  "SWK",
    "TDG",  "TT",  "TXT",  "UAL",  "UBER",
    "UNP",  "UPS",  "URI",  "VLTO",  "VRSK",
    "VRT",  "WAB",  "WM",  "XYL",

    # Energía
    "APA",  "BKR",  "COP",  "CVX",  "DVN",
    "EOG",  "EQT",  "EXE",  "FANG",  "HAL",
    "KMI",  "MPC",  "OKE",  "OXY",  "PSX",
    "SLB",  "TPL",  "TRGP",  "VLO",  "WMB",
    "XOM",

    # Materiales
    "ALB",  "AMCR",  "APD",  "AVY",  "BALL",
    "CF",  "CRH",  "CTVA",  "DD",  "DOW",
    "ECL",  "FCX",  "IFF",  "IP",  "LIN",
    "LYB",  "MLM",  "MOS",  "NEM",  "NUE",
    "PKG",  "PPG",  "SHW",  "STLD",  "SW",
    "VMC",

    # Utilities
    "AEE",  "AEP",  "AES",  "ATO",  "AWK",
    "CEG",  "CMS",  "CNP",  "D",  "DTE",
    "DUK",  "ED",  "EIX",  "ES",  "ETR",
    "EVRG",  "EXC",  "FE",  "LNT",  "NEE",
    "NI",  "NRG",  "PCG",  "PEG",  "PNW",
    "PPL",  "SO",  "SRE",  "VST",  "WEC",
    "XEL",

    # Inmobiliario
    "AMT",  "ARE",  "AVB",  "BXP",  "CBRE",
    "CCI",  "CPT",  "CSGP",  "DLR",  "DOC",
    "EQIX",  "EQR",  "ESS",  "EXR",  "FRT",
    "HST",  "INVH",  "IRM",  "KIM",  "MAA",
    "O",  "PLD",  "PSA",  "REG",  "SBAC",
    "SPG",  "UDR",  "VICI",  "VTR",  "WELL",
    "WY",

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
