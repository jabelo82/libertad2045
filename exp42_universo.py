"""
exp42_universo.py — Preparación universo combinado S&P500 + Nasdaq-100

Descarga composición Nasdaq-100 desde Wikipedia, calcula el diferencial
con el universo SP500 actual, valida histórico ≥200 días pre-2006-01-01
(necesario para SMA200 en backtest 2005-2025), y genera exp42_universo_combinado.py.

Sin efectos secundarios sobre archivos de producción.
"""

import sys
import warnings
from datetime import datetime
from pathlib import Path

import io
import time
import requests
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

# ── Universo S&P500 actual ────────────────────────────────────────────────────

from universe_sp500 import SP500
sp500_set = set(SP500)
print(f"Universo S&P500 actual : {len(sp500_set)} tickers")

# ── Descargar composición Nasdaq-100 desde Wikipedia ─────────────────────────

NDX_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

print(f"\nDescargando Nasdaq-100 desde Wikipedia...")
try:
    _headers = {"User-Agent": "Mozilla/5.0 (compatible; LIBERTAD_2045/exp42; +research)"}
    _resp = requests.get(NDX_WIKI_URL, headers=_headers, timeout=20)
    _resp.raise_for_status()
    tablas = pd.read_html(io.StringIO(_resp.text), attrs={"id": "constituents"})
    if not tablas:
        tablas = pd.read_html(io.StringIO(_resp.text))
    tabla_ndx = None
    for t in tablas:
        cols = [str(c).lower() for c in t.columns]
        if any("ticker" in c or "symbol" in c for c in cols):
            tabla_ndx = t
            break
    if tabla_ndx is None:
        tabla_ndx = tablas[0]
    col_ticker = next(
        c for c in tabla_ndx.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower()
    )
    ndx_tickers = sorted({str(t).strip().replace(".", "-") for t in tabla_ndx[col_ticker] if str(t).strip()})
    print(f"Nasdaq-100 tickers     : {len(ndx_tickers)}")
except Exception as e:
    print(f"ERROR descargando Nasdaq-100: {e}")
    sys.exit(1)

# ── Diferencial: Nasdaq-100 - S&P500 ─────────────────────────────────────────

diferencial = sorted(t for t in ndx_tickers if t not in sp500_set)
print(f"\nDiferencial (NDX - SP500) : {len(diferencial)} tickers")
print(f"  Tickers: {diferencial[:20]}{'...' if len(diferencial) > 20 else ''}")

# ── Validar histórico pre-2006-01-01 ─────────────────────────────────────────
# Necesitamos ≥200 barras antes de 2006-01-01 para que SMA200 sea válida
# al inicio del backtest 2005.

CUTOFF = "2006-01-01"
MIN_BARS = 200

DATA_DIR = PROJECT_DIR / "data"
DOWNLOAD_PAUSE = 4  # segundos entre descargas para evitar rate limiting de yfinance

print(f"\nValidando histórico pre-{CUTOFF} (mín. {MIN_BARS} barras)...")
print(f"  Estrategia: caché local primero, yfinance como fallback (pausa {DOWNLOAD_PAUSE}s)")

aprobados = []
descartados = []  # list of (ticker, motivo)

yf_count = 0
for i, ticker in enumerate(diferencial, 1):
    # -- Intento 1: caché local del backtest — tomar el archivo con start más antiguo --
    cache_files = sorted(DATA_DIR.glob(f"{ticker}_*.csv"))  # orden alfanumérico → el 2005 va primero
    if cache_files:
        best_n = 0
        for cf in cache_files:
            try:
                df_cache = pd.read_csv(cf)
                if "Date" not in df_cache.columns:
                    continue
                n = len(df_cache[df_cache["Date"] < CUTOFF])
                if n > best_n:
                    best_n = n
            except Exception:
                pass
        if best_n >= MIN_BARS:
            aprobados.append(ticker)
            print(f"  [{i:3d}/{len(diferencial)}] {ticker:12s} → ✓  {best_n} barras pre-{CUTOFF} (caché)")
        else:
            motivo = f"solo {best_n} barras pre-{CUTOFF} en caché (primer dato posterior)" if best_n > 0 else f"sin datos pre-{CUTOFF} en caché"
            descartados.append((ticker, motivo))
            print(f"  [{i:3d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")
        continue

    # -- Intento 2: yfinance (solo para tickers sin caché) --
    if yf_count > 0:
        time.sleep(DOWNLOAD_PAUSE)
    yf_count += 1
    try:
        df = yf.download(ticker, start="2000-01-01", end=CUTOFF,
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        n = len(df)
        if n >= MIN_BARS:
            aprobados.append(ticker)
            print(f"  [{i:3d}/{len(diferencial)}] {ticker:12s} → ✓  {n} barras pre-{CUTOFF} (yfinance)")
        else:
            motivo = f"solo {n} barras pre-{CUTOFF} (IPO reciente o datos insuficientes)" if n > 0 else f"sin datos pre-{CUTOFF} (IPO posterior a {CUTOFF})"
            descartados.append((ticker, motivo))
            print(f"  [{i:3d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")
    except Exception as e:
        motivo = f"error yfinance: {e}"
        descartados.append((ticker, motivo))
        print(f"  [{i:3d}/{len(diferencial)}] {ticker:12s} → ✗  {motivo}")

# ── Resumen ────────────────────────────────────────────────────────────────────

universo_combinado = sorted(sp500_set | set(aprobados))

print(f"\n{'='*60}")
print(f"  RESUMEN EXP42 — UNIVERSO COMBINADO")
print(f"{'='*60}")
print(f"  S&P500 actual          : {len(sp500_set):>4d} tickers")
print(f"  Diferencial NDX-SP500  : {len(diferencial):>4d} tickers")
print(f"  Aprobados (≥{MIN_BARS}b pre-{CUTOFF[2:]}) : {len(aprobados):>4d} tickers")
print(f"  Descartados            : {len(descartados):>4d} tickers")
print(f"  Universo combinado     : {len(universo_combinado):>4d} tickers")
print(f"{'='*60}")

if descartados:
    print(f"\n  Descartados ({len(descartados)}):")
    for t, m in descartados:
        print(f"    {t:12s}  {m}")

if aprobados:
    print(f"\n  Aprobados del diferencial ({len(aprobados)}):")
    print(f"    {', '.join(aprobados)}")

# ── Generar exp42_universo_combinado.py ──────────────────────────────────────

salida = PROJECT_DIR / "exp42_universo_combinado.py"
lineas = [
    "# exp42_universo_combinado.py — generado por exp42_universo.py",
    f"# Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    f"# S&P500: {len(sp500_set)} + diferencial NDX validado: {len(aprobados)} = {len(universo_combinado)} total",
    "#",
    "# IMPORTANTE: no tocar universe_sp500.py — este archivo es solo para Exp42",
    "",
    "SP500 = [",
]
for t in universo_combinado:
    lineas.append(f'    "{t}",')
lineas.append("]")
lineas.append("")

salida.write_text("\n".join(lineas))
print(f"\n  Guardado: {salida.name}")
print(f"  Listo para backtest Exp42 cuando se indique.")
