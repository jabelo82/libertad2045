"""
exp44_universo.py — Preparación universo combinado S&P500 + Russell 2000

Descarga composición Russell 2000 desde fuentes públicas (iShares IWM holdings
o GitHub datasets), calcula el diferencial con el universo SP500 actual, valida
histórico ≥200 días pre-2006-01-01 contra caché local y yfinance, y genera
exp44_universo_combinado.py.

El Russell 2000 tiene ~2000 componentes actuales. Solo incorporamos el diferencial
que supere el umbral de historia mínima. Si la descarga de la lista completa falla,
el script avisa e intenta una lista alternativa conocida de small-caps.

Sin efectos secundarios sobre archivos de producción.

Uso:
    python exp44_universo.py
"""

import sys
import io
import json
import time
import warnings
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from universe_sp500 import SP500
sp500_set = set(SP500)
print(f"Universo S&P500 actual : {len(sp500_set)} tickers")

# ── Fuentes para Russell 2000 ─────────────────────────────────────────────────
# Estrategia: intentamos 3 fuentes en orden de fiabilidad.
# Fuente 1: iShares IWM holdings CSV (lista actual, ~2000 tickers)
# Fuente 2: GitHub datasets/russell2000 (lista estática curada)
# Fuente 3: Lista representativa hardcoded de ~150 small-caps conocidas

IWM_URL = (
    "https://www.ishares.com/us/products/239710/ISHARES-RUSSELL-2000-ETF/"
    "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
)

GITHUB_RUT_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/"
    "data/constituents.csv"  # fallback — diferente índice, para testing
)

# Lista representativa de small-caps Russell 2000 con historia larga (pre-2001)
# usada como último fallback si las descargas fallan
RUSSELL2000_FALLBACK = [
    "ACAD", "ACHC", "ACIW", "ACM", "AEIS", "AEO", "AFCG", "AGCO", "AGR", "AGYS",
    "AIT", "AKTS", "ALGT", "ALKS", "ALRM", "AMAG", "AMBA", "AMBC", "AME", "AMED",
    "AMN", "AMRC", "AMRN", "AMRS", "AMWD", "ANET", "ANF", "ANGI", "AOSL", "APAM",
    "APEI", "ARC", "ARCB", "ARCO", "ARDX", "AROC", "ARW", "ASIX", "ASND", "ASTE",
    "ATI", "ATNI", "ATRE", "ATRO", "ATU", "ATUS", "AVD", "AVNS", "AVNW", "AVXL",
    "AXGN", "AXL", "AXNX", "AXON", "AXTA", "AYX", "AZTA", "AZUL", "B", "BANF",
    "BANR", "BATRK", "BCO", "BCPC", "BCYC", "BDN", "BELFB", "BGC", "BGFV", "BHB",
    "BHF", "BIG", "BIGC", "BJRI", "BLDR", "BLX", "BMI", "BMS", "BNCN", "BNED",
    "BNTX", "BOX", "BRKL", "BRKR", "BRP", "BSET", "BSIG", "BV", "BWEN", "BXC",
    "BYD", "CACI", "CADE", "CAR", "CATC", "CATY", "CBB", "CBNK", "CBT", "CBU",
    "CCOI", "CCO", "CCS", "CDNA", "CDRE", "CDXS", "CECO", "CEIX", "CENT", "CEVA",
    "CFFI", "CFFT", "CFNL", "CG", "CGBD", "CGEN", "CHCO", "CHEF", "CHUY", "CIR",
    "CIVB", "CIZN", "CKH", "CLAR", "CLFD", "CLH", "CLNE", "CLPS", "CLRO", "CLXT",
    "CMCO", "CMLS", "CMPX", "CMRE", "CMTG", "CNCE", "CNSL", "CNVY", "COHU", "COLL",
    "CONN", "COOP", "CORT", "COVA", "CPRT", "CPS", "CPSI", "CRAI", "CRAY", "CRDF",
    "CRGE", "CRIS", "CRNX", "CROX", "CRUS", "CRWD", "CSGP", "CSGS", "CSV", "CSWI",
    "CTAS", "CTOS", "CTXS", "CUTR", "CVBF", "CVCO", "CVET", "CVG", "CWH", "CWST",
    "CZWI", "DBRG", "DCGO", "DCPH", "DENN", "DFIN", "DGII", "DIOD", "DJCO", "DLHC",
    "DNLI", "DORM", "DOVR", "DRH", "DRRX", "DRQ", "DSGN", "DSGX", "DV", "DVAX",
    "DXPE", "DYAI", "EARN", "EBC", "EBIX", "EBTC", "ECPG", "EDUC", "EFC", "EGY",
    "EIG", "ELIF", "EME", "EMKR", "ENOV", "ENPH", "ENSG", "ENTA", "ENTG", "ENVA",
    "EPRT", "ERI", "ERIE", "ESE", "ESES", "ESSA", "ESTE", "ETSY", "EVBG", "EVGO",
    "EVTC", "EXC", "EXLS", "EXR", "EXTN", "EXTR", "EYE", "EZPW", "FARO", "FATE",
    "FBK", "FBMS", "FBP", "FCF", "FCEA", "FCFS", "FCNCA", "FCPT", "FDBC", "FDP",
    "FELE", "FGEN", "FHB", "FIHL", "FINV", "FIXX", "FIVN", "FIX", "FLIC", "FLL",
    "FLNC", "FLWS", "FMBH", "FMBI", "FMC", "FMX", "FNB", "FNKO", "FNWB", "FOSL",
    "FOX", "FOXF", "FRBA", "FRBK", "FRD", "FREE", "FRPH", "FRTX", "FSP", "FSRV",
    "FSTR", "FTDR", "FTLF", "FULT", "GABC", "GBCI", "GBNK", "GCBC", "GCM", "GDEN",
    "GDOT", "GFF", "GFED", "GHM", "GIII", "GIL", "GLDD", "GLT", "GLUU", "GMAB",
    "GNBC", "GNSS", "GOF", "GOLF", "GPMT", "GPRE", "GPRO", "GRVY", "GSBC", "GSIT",
    "GTHX", "GTLS", "GTN", "GURE", "HALO", "HBI", "HCKT", "HCC", "HCCI", "HCSG",
    "HGV", "HI", "HIL", "HIMAX", "HIMX", "HLF", "HLNE", "HMST", "HMSY", "HNGR",
    "HOLI", "HOME", "HOPE", "HQI", "HRB", "HROW", "HS", "HSII", "HSTM", "HTBK",
    "HTGC", "HTH", "HTLD", "HTLF", "HURN", "HWBK", "HWC", "HXL", "HYMC", "HZNP",
    "IART", "IBN", "IBP", "IBTX", "ICG", "ICHR", "ICL", "ICLR", "IDCC", "IDEX",
    "IDTI", "IEC", "IESC", "IFN", "IFON", "IIIV", "IIVI", "IKAN", "ILMN", "INBK",
    "INFU", "INGN", "INMD", "INOD", "INVA", "INVE", "INVH", "IOSP", "IPGP", "IQLT",
    "IRBT", "IRDM", "ISCO", "ISRG", "ISTR", "ITCI", "ITGR", "ITT", "ITUS", "IVAC",
    "IVR", "JACK", "JBGS", "JBI", "JBSS", "JCOM", "JJSF", "JNCE", "JOBY", "JOE",
    "JOUT", "JPI", "JRVR", "KAI", "KALU", "KAMN", "KAR", "KBAL", "KBH", "KBSF",
    "KFRC", "KFY", "KLIC", "KMPR", "KMT", "KN", "KNF", "KNX", "KOPIN", "KRNY",
    "KRO", "KROS", "KRT", "KW", "LADR", "LAND", "LANC", "LAWS", "LAZR", "LBAI",
    "LBC", "LCI", "LCII", "LCNB", "LCUT", "LDOS", "LGIH", "LGND", "LHCG", "LKQ",
    "LLNW", "LMNR", "LMNX", "LNDC", "LNTH", "LOAN", "LOCK", "LOCO", "LOMA", "LPRO",
    "LPSN", "LQDA", "LQDT", "LRN", "LSCC", "LSTR", "LTBR", "LTRPA", "LWAY", "LYFT",
    "LYTS", "MANT", "MASI", "MAT", "MATX", "MBCN", "MBFI", "MBII", "MBI", "MBWM",
    "MCBC", "MCRB", "MCRN", "MDVX", "MESA", "MGP", "MGPI", "MGY", "MLAB", "MLKN",
    "MMS", "MNDO", "MNKD", "MNRO", "MNST", "MODG", "MOFG", "MOTN", "MPAA", "MPWR",
    "MRAM", "MRCY", "MRKR", "MRTN", "MSA", "MSEX", "MSTR", "MTH", "MTRX", "MTRN",
    "MTYF", "MVBF", "MWA", "MXL", "MYPS", "NABL", "NARI", "NBHC", "NBTB", "NCBS",
    "NCLH", "NCSM", "NDLS", "NEON", "NESR", "NETI", "NFBK", "NGVT", "NKTR", "NLSN",
    "NMFC", "NMIH", "NMRK", "NNCN", "NOVA", "NOVT", "NSP", "NTCT", "NTIC", "NTNX",
    "NTUS", "NURO", "NUVL", "NWBI", "NWFL", "NWPX", "NX", "NXRT", "NYT", "OCUL",
    "OFG", "OGS", "OLLI", "OLPX", "OMCL", "OMER", "OMEX", "OMGA", "OMI", "OPCH",
    "OPI", "OPY", "ORBC", "ORGO", "ORLY", "ORRF", "OTC", "OTTR", "OUST", "OXM",
    "PACK", "PAHC", "PANL", "PAR", "PATK", "PAYA", "PBCT", "PBFX", "PBHC", "PBYI",
    "PCBC", "PCVX", "PEBO", "PERI", "PFBC", "PFIS", "PFLT", "PFSI", "PGNY", "PHAT",
    "PI", "PIXY", "PKBK", "PKE", "PKOH", "PL", "PLAB", "PLAY", "PLMR", "PLXS",
    "PMVP", "PNFP", "PNM", "PODD", "POWL", "PPBI", "PPIH", "PRAA", "PRAX", "PRCT",
    "PRDO", "PRFT", "PRGO", "PRLB", "PRO", "PROV", "PRSC", "PRST", "PSMT", "PSNL",
    "PTCT", "PTGX", "PTLO", "PTVE", "PUBM", "PVBC", "PW", "PWOD", "PZG", "QNST",
    "QDEL", "QLYS", "QNST", "QS", "QTWO", "QUAD", "RADNW", "RAIL", "RAND", "RAVN",
    "RBC", "RBCAA", "RBZ", "RCII", "RCKT", "RCKY", "RCM", "RCR", "RDFN", "REX",
    "REYN", "RFIL", "RICK", "RLAY", "RLGY", "RLI", "RMBS", "RMNI", "RNET", "ROG",
    "RPAI", "RPID", "RPMT", "RPRX", "RRBI", "RRR", "RSKIA", "RTLR", "RUBI", "RVSB",
    "RWT", "RXO", "RYAM", "SABR", "SAFE", "SAIA", "SANM", "SASR", "SBRA", "SBSI",
    "SCHL", "SCSC", "SCVL", "SD", "SEEL", "SENEA", "SFIX", "SFNC", "SFST", "SGBX",
    "SGMO", "SGRP", "SHLS", "SHOO", "SHYF", "SIG", "SILK", "SIMO", "SITE", "SKYW",
    "SLCA", "SLGN", "SMG", "SMPL", "SMRT", "SNBR", "SNEX", "SNV", "SOFI", "SONA",
    "SONO", "SPFI", "SPKE", "SPWH", "SPWR", "SQSP", "SRCE", "SSB", "SSBI", "SSFN",
    "SSTI", "STAA", "STBA", "STHO", "STRA", "STRM", "STRR", "STU", "STXB", "SUMO",
    "SUPN", "SV", "SVRA", "SWBI", "SWKH", "SYBT", "SYNA", "SYX", "TACO", "TAST",
    "TBBK", "TBI", "TBPH", "TCS", "TDOC", "TEMP", "TFSL", "TGTX", "TILE", "TIS",
    "TITN", "TLIS", "TLND", "TLRY", "TMBR", "TMST", "TNGO", "TNP", "TPHS", "TPVG",
    "TPXC", "TRAN", "TRNO", "TROW", "TRST", "TRVI", "TRWH", "TTEC", "TTGT", "TTI",
    "TTOO", "TTPH", "TTSH", "TUSK", "TUX", "TVTX", "TWIN", "TXMD", "TXN", "TYPE",
    "UCB", "UCBI", "UCTT", "UE", "UEIC", "UHAL", "UMBF", "UNAM", "UNFI", "UNIT",
    "UNTY", "UPBD", "UPH", "UPL", "UPST", "USAK", "USAP", "USFD", "USLM", "USNA",
    "USPH", "UTMD", "UTZ", "UVSP", "VBTX", "VCNX", "VCRA", "VCYT", "VECO", "VERV",
    "VGR", "VIAV", "VIEW", "VINC", "VIRT", "VITL", "VLGEA", "VLRS", "VNDA", "VNET",
    "VNRX", "VOXX", "VRAR", "VRNS", "VSEC", "VST", "VTLE", "VVPR", "VYGR", "WABC",
    "WAFD", "WASH", "WD", "WERN", "WEYS", "WFRD", "WHR", "WILC", "WINA", "WKHS",
    "WLDN", "WLL", "WLY", "WLYW", "WMGI", "WNEB", "WNS", "WOLF", "WSBF", "WSBC",
    "WSFS", "WTBA", "WTFC", "WTRE", "WTRG", "WW", "WWD", "WTTR", "XNCR", "XOMA",
    "XPEL", "XTLB", "YEXT", "YMAB", "YGMZ", "YRCW", "YORW", "ZD", "ZGN", "ZEUS",
    "ZGNX", "ZION", "ZLAB", "ZNTL", "ZUO",
]

DOWNLOAD_PAUSE = 5      # segundos entre descargas yfinance
CUTOFF = "2006-01-01"
MIN_BARS = 200
CHECKPOINT_FILE = PROJECT_DIR / "exp44_checkpoint.json"
MAX_RETRIES = 3         # reintentos por ticker con backoff exponencial


def descargar_russell2000_ishares() -> list:
    """Descarga la lista de componentes del Russell 2000 desde iShares IWM."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LIBERTAD_2045/exp44; +research)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = requests.get(IWM_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        # El CSV de iShares tiene algunas líneas de cabecera antes del DataFrame
        # Intentamos leer saltando cabeceras hasta encontrar la fila con "Ticker"
        content = resp.text
        lines = content.splitlines()
        header_idx = None
        for idx, line in enumerate(lines):
            if "Ticker" in line or "ticker" in line:
                header_idx = idx
                break
        if header_idx is None:
            raise ValueError("No se encontró columna 'Ticker' en el CSV de iShares")
        csv_content = "\n".join(lines[header_idx:])
        df = pd.read_csv(io.StringIO(csv_content))
        col = next(c for c in df.columns if "ticker" in str(c).lower())
        tickers = [
            str(t).strip().replace(".", "-")
            for t in df[col].dropna()
            if str(t).strip() and str(t).strip() != "nan"
        ]
        # Filtramos solo tickers válidos (letras mayúsculas, 1-5 chars)
        import re
        valid = re.compile(r'^[A-Z]{1,5}(-[A-Z])?$')
        tickers = [t for t in tickers if valid.match(t)]
        return sorted(set(tickers))
    except Exception as e:
        print(f"  iShares IWM: fallo — {e}")
        return []


def cargar_checkpoint() -> dict:
    """Carga checkpoint de una ejecución anterior (si existe)."""
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            aprobados = data.get("aprobados", [])
            descartados = data.get("descartados", [])
            procesados = set(data.get("procesados", []))
            print(f"  Checkpoint cargado: {len(procesados)} tickers ya procesados "
                  f"({len(aprobados)} aprobados, {len(descartados)} descartados)")
            return {"aprobados": aprobados, "descartados": descartados, "procesados": procesados}
        except Exception as e:
            print(f"  Checkpoint corrupto ({e}), empezando desde cero")
    return {"aprobados": [], "descartados": [], "procesados": set()}


def guardar_checkpoint(aprobados: list, descartados: list, procesados: set):
    """Guarda progreso en disco para poder reanudar si hay error."""
    CHECKPOINT_FILE.write_text(json.dumps({
        "aprobados": aprobados,
        "descartados": descartados,
        "procesados": list(procesados),
    }))


def yf_download_con_retry(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Descarga datos de yfinance con reintentos y backoff exponencial."""
    for intento in range(1, MAX_RETRIES + 1):
        try:
            df = yf.download(ticker, start=start, end=end,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            return df
        except Exception as e:
            msg = str(e).lower()
            # Rate limit: esperar más
            if any(x in msg for x in ("rate", "429", "too many", "throttl")):
                wait = DOWNLOAD_PAUSE * (2 ** intento)
                print(f"      Rate limit — esperando {wait}s (intento {intento}/{MAX_RETRIES})")
                time.sleep(wait)
            elif intento < MAX_RETRIES:
                wait = DOWNLOAD_PAUSE * intento
                print(f"      Error temporal ({e}) — reintento {intento}/{MAX_RETRIES} en {wait}s")
                time.sleep(wait)
            else:
                raise
    return pd.DataFrame()


def descargar_russell2000_github() -> list:
    """Intenta obtener Russell 2000 desde GitHub datasets."""
    # Nota: este repo tiene S&P500, no Russell 2000 — solo como sanity check
    # En la práctica, si iShares falla usamos el fallback hardcoded
    return []


# ── Obtener lista Russell 2000 ────────────────────────────────────────────────

print("\nObteniendo componentes Russell 2000...")

russell_tickers = descargar_russell2000_ishares()

if len(russell_tickers) < 100:
    print(f"  iShares devolvió {len(russell_tickers)} tickers — usando lista representativa interna")
    russell_tickers = RUSSELL2000_FALLBACK
    print(f"  Fallback: {len(russell_tickers)} tickers representativos de small-caps")
else:
    print(f"  iShares IWM: {len(russell_tickers)} tickers descargados")

# ── Diferencial: Russell 2000 - S&P500 ───────────────────────────────────────

diferencial = sorted(t for t in russell_tickers if t not in sp500_set)
print(f"\nDiferencial (RUT - SP500) : {len(diferencial)} tickers")
if diferencial:
    print(f"  Muestra: {diferencial[:15]}{'...' if len(diferencial) > 15 else ''}")

if len(diferencial) > 500:
    print(f"\n  AVISO: {len(diferencial)} tickers en diferencial.")
    print(f"  Con pausa de {DOWNLOAD_PAUSE}s entre descargas sin caché, la validación completa")
    print(f"  podría tardar hasta {len(diferencial) * DOWNLOAD_PAUSE // 60} minutos.")
    print(f"  Los tickers con caché local se validarán instantáneamente.")

# ── Validar histórico pre-2006-01-01 ─────────────────────────────────────────

DATA_DIR = PROJECT_DIR / "data"

print(f"\nValidando histórico pre-{CUTOFF} (mín. {MIN_BARS} barras)...")

# ── Reanudar desde checkpoint si existe ──────────────────────────────────────
chk = cargar_checkpoint()
aprobados   = chk["aprobados"]
descartados = chk["descartados"]
procesados  = chk["procesados"]

pendientes = [t for t in diferencial if t not in procesados]
print(f"  Pendientes: {len(pendientes)} de {len(diferencial)} tickers")

yf_count = 0
for i_rel, ticker in enumerate(pendientes, 1):
    i_total = diferencial.index(ticker) + 1

    # Intento 1: caché local
    cache_files = sorted(DATA_DIR.glob(f"{ticker}_*.csv"))
    if cache_files:
        best_n = 0
        for cf in cache_files:
            try:
                df_cache = pd.read_csv(cf)
                date_col = "Date" if "Date" in df_cache.columns else df_cache.columns[0]
                n = len(df_cache[df_cache[date_col] < CUTOFF])
                if n > best_n:
                    best_n = n
            except Exception:
                pass
        if best_n >= MIN_BARS:
            aprobados.append(ticker)
            print(f"  [{i_total:4d}/{len(diferencial)}] {ticker:12s} → ✓  {best_n} barras pre-{CUTOFF} (caché)")
        else:
            motivo = f"solo {best_n} barras pre-{CUTOFF} en caché"
            descartados.append((ticker, motivo))
            print(f"  [{i_total:4d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")
        procesados.add(ticker)
        if i_rel % 50 == 0:
            guardar_checkpoint(aprobados, descartados, procesados)
        continue

    # Intento 2: yfinance con reintentos
    if yf_count > 0:
        time.sleep(DOWNLOAD_PAUSE)
    yf_count += 1
    try:
        df = yf_download_con_retry(ticker, start="2000-01-01", end=CUTOFF)
        n = len(df)
        if n >= MIN_BARS:
            aprobados.append(ticker)
            print(f"  [{i_total:4d}/{len(diferencial)}] {ticker:12s} → ✓  {n} barras pre-{CUTOFF} (yfinance)")
        else:
            motivo = f"solo {n} barras pre-{CUTOFF}"
            descartados.append((ticker, motivo))
            print(f"  [{i_total:4d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")
    except Exception as e:
        motivo = f"error yfinance: {e}"
        descartados.append((ticker, motivo))
        print(f"  [{i_total:4d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")

    procesados.add(ticker)
    # Guardar checkpoint cada 25 tickers descargados via yfinance
    if yf_count % 25 == 0:
        guardar_checkpoint(aprobados, descartados, procesados)

# Checkpoint final y limpieza
guardar_checkpoint(aprobados, descartados, procesados)

# ── Resumen ────────────────────────────────────────────────────────────────────

universo_combinado = sorted(sp500_set | set(aprobados))

print(f"\n{'='*60}")
print(f"  RESUMEN EXP44 — UNIVERSO COMBINADO")
print(f"{'='*60}")
print(f"  S&P500 actual              : {len(sp500_set):>4d} tickers")
print(f"  Russell 2000 descargado    : {len(russell_tickers):>4d} tickers")
print(f"  Diferencial (RUT - SP500)  : {len(diferencial):>4d} tickers")
print(f"  Aprobados (≥{MIN_BARS}b pre-{CUTOFF[2:]})  : {len(aprobados):>4d} tickers")
print(f"  Descartados                : {len(descartados):>4d} tickers")
print(f"  Universo combinado         : {len(universo_combinado):>4d} tickers")
print(f"{'='*60}")

if descartados:
    n_show = min(20, len(descartados))
    print(f"\n  Descartados (primeros {n_show} de {len(descartados)}):")
    for t, m in descartados[:n_show]:
        print(f"    {t:12s}  {m}")

if aprobados:
    print(f"\n  Aprobados del diferencial ({len(aprobados)}):")
    muestra = aprobados[:30]
    print(f"    {', '.join(muestra)}{'...' if len(aprobados) > 30 else ''}")

# ── Generar exp44_universo_combinado.py ──────────────────────────────────────

salida = PROJECT_DIR / "exp44_universo_combinado.py"
lineas = [
    "# exp44_universo_combinado.py — generado por exp44_universo.py",
    f"# Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    f"# S&P500: {len(sp500_set)} + diferencial RUT validado: {len(aprobados)} = {len(universo_combinado)} total",
    "#",
    "# IMPORTANTE: no tocar universe_sp500.py — este archivo es solo para Exp44",
    "",
    "SP500 = [",
]
for t in universo_combinado:
    lineas.append(f'    "{t}",')
lineas.append("]")
lineas.append("")

salida.write_text("\n".join(lineas))
print(f"\n  Guardado: {salida.name}")
print(f"  Listo para backtest_exp44.py cuando se indique.")

# Eliminar checkpoint — la validación se completó con éxito
if CHECKPOINT_FILE.exists():
    CHECKPOINT_FILE.unlink()
    print(f"  Checkpoint eliminado.")
