"""
LIBERTAD_2045 — Exp.46 (retomado) — Fase 2: perfil de riesgo
================================================================

Compara el perfil de GAPS / VOLATILIDAD (ATR%) / VOLUMEN entre:
  A) S&P500 actual (501 tickers, universe_sp500.py) — universo real que
     el bot escanea hoy en producción.
  B) Diferencial Russell 1000 filtrado por liquidez (505 tickers con
     cache útil, de 512 tras el fix de cobertura del 22/08/2026) — lo
     que Exp.46 añadiría si se activara.

Métricas, con cola (no solo la media):
  - Gap de apertura diario: (Open_t / Close_t-1 - 1) * 100, pooled por
    grupo (todas las observaciones diarias de todos los tickers del
    grupo juntas, no promedio de promedios).
  - ATR(14) medio como % del precio de cierre, por ticker, luego media
    del grupo.
  - Volumen medio diario en $ (Volume * Close medio del ticker), como
    proxy de liquidez.

Usa exactamente la misma caché ya construida (data/*.csv,
2006-01-01_2025-12-31), sin tocar red.
"""

import importlib.util
import os

import numpy as np
import pandas as pd

START, END = "2006-01-01", "2025-12-31"
DATA_DIR = "data"

RUSSELL_FILE = "russell1000_diferencial_filtrado.txt"


def cargar_sp500():
    spec = importlib.util.spec_from_file_location("universe_sp500", "universe_sp500.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.SP500)


def cargar_russell_diferencial():
    with open(RUSSELL_FILE) as f:
        return [l.strip() for l in f if l.strip()]


def ruta_cache(symbol):
    safe = symbol.replace("-", "_").replace(" ", "_")
    return os.path.join(DATA_DIR, f"{safe}_{START}_{END}.csv")


def cargar_ticker(symbol):
    ruta = ruta_cache(symbol)
    if not os.path.exists(ruta) or os.path.getsize(ruta) <= 100:
        return None
    try:
        df = pd.read_csv(ruta, index_col=0, parse_dates=True)
    except Exception:
        return None
    if df.empty or len(df) < 200:
        return None
    for col in ["Open", "Close", "High", "Low", "Volume"]:
        if col not in df.columns:
            return None
    return df


def gaps_de_ticker(df):
    """Gap % de apertura respecto al cierre anterior, serie completa."""
    prev_close = df["Close"].shift(1)
    gap = (df["Open"] / prev_close - 1.0) * 100.0
    return gap.dropna()


def atr_pct_de_ticker(df):
    """ATR(14) medio como % del precio de cierre, para este ticker."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_pct = (atr / close) * 100.0
    return atr_pct.dropna().mean()


def volumen_dolar_medio(df):
    return (df["Volume"] * df["Close"]).mean()


def analizar_grupo(nombre, tickers):
    print(f"\nCargando grupo: {nombre} ({len(tickers)} tickers candidatos)...")

    todos_gaps = []
    atr_pcts = []
    vol_dolares = []
    usados = 0
    descartados = 0

    for t in tickers:
        df = cargar_ticker(t)
        if df is None:
            descartados += 1
            continue
        usados += 1
        todos_gaps.append(gaps_de_ticker(df))
        atr_pcts.append(atr_pct_de_ticker(df))
        vol_dolares.append(volumen_dolar_medio(df))

    print(f"  Usados    : {usados}")
    print(f"  Descartados (sin cache útil): {descartados}")

    gaps = pd.concat(todos_gaps) if todos_gaps else pd.Series(dtype=float)
    atr_pcts = pd.Series(atr_pcts).dropna()
    vol_dolares = pd.Series(vol_dolares).dropna()

    resultado = {
        "nombre": nombre,
        "n_tickers": usados,
        "n_obs_gap": len(gaps),
        "gap_mean": gaps.mean(),
        "gap_median": gaps.median(),
        "gap_p95": gaps.quantile(0.95),
        "gap_p99": gaps.quantile(0.99),
        "gap_max": gaps.max(),
        "gap_p05": gaps.quantile(0.05),
        "gap_p01": gaps.quantile(0.01),
        "gap_min": gaps.min(),
        "gap_abs_mean": gaps.abs().mean(),
        "gap_abs_p95": gaps.abs().quantile(0.95),
        "gap_abs_p99": gaps.abs().quantile(0.99),
        "atr_pct_mean": atr_pcts.mean(),
        "atr_pct_median": atr_pcts.median(),
        "vol_dolar_mean": vol_dolares.mean(),
        "vol_dolar_median": vol_dolares.median(),
    }
    return resultado, gaps


def imprimir_comparativa(res_a, res_b):
    def fmt(v, pct=True, money=False):
        if pd.isna(v):
            return "n/d"
        if money:
            return f"{v:,.0f} $"
        return f"{v:+.2f}%" if pct else f"{v:.2f}"

    print("\n" + "=" * 78)
    print("COMPARATIVA — Fase 2 Exp.46 (retomado)")
    print("=" * 78)
    print(f"{'Métrica':35s} {'S&P500 actual':>18s} {'Russell1000 diff.':>20s}")
    print("-" * 78)
    print(f"{'Tickers usados':35s} {res_a['n_tickers']:>18d} {res_b['n_tickers']:>20d}")
    print(f"{'Observaciones diarias (gap)':35s} {res_a['n_obs_gap']:>18,d} {res_b['n_obs_gap']:>20,d}")
    print("-" * 78)
    print("GAP DE APERTURA (Open vs Close anterior, %)")
    print(f"{'  media (con signo)':35s} {fmt(res_a['gap_mean']):>18s} {fmt(res_b['gap_mean']):>20s}")
    print(f"{'  mediana':35s} {fmt(res_a['gap_median']):>18s} {fmt(res_b['gap_median']):>20s}")
    print(f"{'  media |gap| (magnitud)':35s} {fmt(res_a['gap_abs_mean']):>18s} {fmt(res_b['gap_abs_mean']):>20s}")
    print(f"{'  p95 |gap|':35s} {fmt(res_a['gap_abs_p95']):>18s} {fmt(res_b['gap_abs_p95']):>20s}")
    print(f"{'  p99 |gap|':35s} {fmt(res_a['gap_abs_p99']):>18s} {fmt(res_b['gap_abs_p99']):>20s}")
    print(f"{'  p99 alcista (subida)':35s} {fmt(res_a['gap_p99']):>18s} {fmt(res_b['gap_p99']):>20s}")
    print(f"{'  máximo alcista':35s} {fmt(res_a['gap_max']):>18s} {fmt(res_b['gap_max']):>20s}")
    print(f"{'  p1 bajista (caída)':35s} {fmt(res_a['gap_p01']):>18s} {fmt(res_b['gap_p01']):>20s}")
    print(f"{'  mínimo (peor caída)':35s} {fmt(res_a['gap_min']):>18s} {fmt(res_b['gap_min']):>20s}")
    print("-" * 78)
    print("ATR(14) COMO % DEL PRECIO")
    print(f"{'  media':35s} {fmt(res_a['atr_pct_mean']):>18s} {fmt(res_b['atr_pct_mean']):>20s}")
    print(f"{'  mediana':35s} {fmt(res_a['atr_pct_median']):>18s} {fmt(res_b['atr_pct_median']):>20s}")
    print("-" * 78)
    print("VOLUMEN MEDIO DIARIO EN $ (proxy de liquidez)")
    print(f"{'  media':35s} {fmt(res_a['vol_dolar_mean'], money=True):>18s} {fmt(res_b['vol_dolar_mean'], money=True):>20s}")
    print(f"{'  mediana':35s} {fmt(res_a['vol_dolar_median'], money=True):>18s} {fmt(res_b['vol_dolar_median'], money=True):>20s}")
    print("=" * 78)


def main():
    sp500 = cargar_sp500()
    russell = cargar_russell_diferencial()

    res_sp500, gaps_sp500 = analizar_grupo("S&P500 actual", sp500)
    res_russell, gaps_russell = analizar_grupo("Russell1000 diferencial filtrado", russell)

    imprimir_comparativa(res_sp500, res_russell)

    # Guardar resultados para referencia
    os.makedirs("backtest_results", exist_ok=True)
    pd.DataFrame([res_sp500, res_russell]).to_csv(
        "backtest_results/exp46_fase2_perfil_riesgo.csv", index=False)
    print("\nGuardado: backtest_results/exp46_fase2_perfil_riesgo.csv")


if __name__ == "__main__":
    main()
