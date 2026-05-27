"""
LIBERTAD_2045 — Montecarlo trade-level ×0.75
=============================================
1. Ejecuta el backtest completo (2006-2025) con factor ×0.75 para obtener
   los trades individuales (PnL y capital en cada cierre).
2. Corre 1.000 simulaciones bootstrap: muestrea los retornos por trade
   con reposición sobre 4.000 € de capital inicial (igual que backtest_expandido.py).
3. Genera montecarlo_075.html con curvas de capital, histogramas y métricas.

Uso:
    source venv/bin/activate
    python montecarlo_trades_075.py
"""

import warnings
warnings.filterwarnings("ignore")

import os, time, json
from datetime import datetime

import numpy as np
import pandas as pd

# ── Parámetros Montecarlo ─────────────────────────────────────────────────────

FACTOR          = 0.75
N_SIMS          = 1_000
CAPITAL_MC      = 4_000.0            # Igual que backtest_expandido.py (CAPITAL_INICIAL)
RUIN_THRESHOLD  = 0.50               # Ruina si capital < 50% del inicial
N_CURVES_PLOT   = 200                # Curvas individuales a mostrar en gráfico
N_BANDS_POINTS  = 300                # Puntos de muestreo para bandas percentil
RNG             = np.random.default_rng(seed=42)
OUTPUT          = "montecarlo_075.html"

# Parámetros del backtest — idénticos a backtest_exp40ter.py
START_DATE       = "2006-01-01"
END_DATE         = "2025-12-31"
CACHE_START      = "2006-01-01"
CACHE_END        = "2025-12-31"

# ── Importar funciones del motor ──────────────────────────────────────────────

from backtest_exp40ter import (
    cargar_composicion_sp500,
    universo_historico_sp500,
    descargar_datos,
    ejecutar_backtest,
    calcular_metricas,
    CAPITAL_INICIAL,
    APORTACION_ANUAL,
)

# ── 1. BACKTEST ×0.75 ─────────────────────────────────────────────────────────

print("=" * 65)
print("  LIBERTAD_2045 — Montecarlo trade-level ×0.75")
print("=" * 65)
print(f"\n  Capital MC : {CAPITAL_MC:,.0f} € (= CAPITAL_INICIAL backtest)")
print(f"  Simulaciones: {N_SIMS:,}")
print(f"  Metodología : bootstrap con reposición (trade returns)")
print()

t0_total = time.time()

from pathlib import Path

comp_df  = cargar_composicion_sp500()
universo = universo_historico_sp500(comp_df)

# Filtrar a solo activos ya en caché — evita descargas
cached_stems = {
    p.stem.rsplit(f"_{CACHE_START}_{CACHE_END}", 1)[0].replace("_", "-")
    for p in Path("data").glob(f"*_{CACHE_START}_{CACHE_END}.csv")
}
universo = [t for t in universo if t in cached_stems or t.replace("-", "_") in {
    p.stem.rsplit(f"_{CACHE_START}_{CACHE_END}", 1)[0]
    for p in Path("data").glob(f"*_{CACHE_START}_{CACHE_END}.csv")
}]

print(f"  Universo (cacheado): {len(universo)} activos")
datos = descargar_datos(universo, CACHE_START, CACHE_END)

if not datos:
    print("ERROR: no se pudieron cargar datos.")
    exit(1)

TRADES_CACHE = f"backtest_results/mc075_trades_cache.csv"

if os.path.exists(TRADES_CACHE):
    print(f"\n  Cargando trades desde caché: {TRADES_CACHE}")
    df_cached = pd.read_csv(TRADES_CACHE)
    trades        = df_cached.to_dict("records")
    capital_final = float(df_cached["capital"].iloc[-1])
    # Curva proxy: capital tras cada trade (suficiente para calcular_metricas)
    curva_capital = [{"fecha": r["fecha_salida"], "capital": r["capital"]} for r in trades]
else:
    print(f"\n  Ejecutando backtest ×0.75...", end="", flush=True)
    t0 = time.time()
    trades, curva_capital, capital_final = ejecutar_backtest(datos, comp_df, FACTOR)
    elapsed = time.time() - t0
    print(f" {elapsed:.0f}s")
    os.makedirs("backtest_results", exist_ok=True)
    pd.DataFrame(trades).to_csv(TRADES_CACHE, index=False)
    print(f"  Trades guardados en caché: {TRADES_CACHE}")

metricas = calcular_metricas(trades, curva_capital, capital_final)
print(f"  Capital final : {capital_final:,.0f} €")
print(f"  PF            : {metricas['profit_factor']:.4f}")
print(f"  Win Rate      : {metricas['win_rate']:.1%}")
print(f"  DD máx        : {metricas['max_drawdown']:.1%}")
print(f"  Total trades  : {metricas['total_trades']}")

# ── 2. PREPARAR RETORNOS POR TRADE ────────────────────────────────────────────

df_trades = pd.DataFrame(trades)
df_trades = df_trades[df_trades["resultado"] != "OPEN→CLOSE"].copy()  # excluir cierres al final

# r_i = pnl / capital_antes
# capital_antes = capital_despues - pnl
df_trades["cap_antes"] = df_trades["capital"] - df_trades["pnl"]
df_trades = df_trades[df_trades["cap_antes"] > 0]
df_trades["r"] = df_trades["pnl"] / df_trades["cap_antes"]

returns = df_trades["r"].values
n_trades = len(returns)

print(f"\n  Trades para MC: {n_trades}")
print(f"  Ret. medio    : {returns.mean():.4%}")
print(f"  Ret. mediana  : {np.median(returns):.4%}")
print(f"  Ret. std      : {returns.std():.4%}")

# ── 3. MONTECARLO ─────────────────────────────────────────────────────────────

print(f"\n  Corriendo {N_SIMS:,} simulaciones...", end="", flush=True)
t0 = time.time()

final_caps = np.empty(N_SIMS)
max_dds    = np.empty(N_SIMS)
ruin_hits  = np.zeros(N_SIMS, dtype=bool)
ruin_level = CAPITAL_MC * RUIN_THRESHOLD

# Almacenar todas las curvas para calcular bandas
all_equity = np.empty((N_SIMS, n_trades + 1))

for i in range(N_SIMS):
    sample  = RNG.choice(returns, size=n_trades, replace=True)
    equity  = np.empty(n_trades + 1)
    equity[0] = CAPITAL_MC
    for j in range(n_trades):
        equity[j + 1] = equity[j] * (1.0 + sample[j])
    all_equity[i] = equity
    final_caps[i] = equity[-1]
    peak = np.maximum.accumulate(equity)
    dds  = (equity - peak) / peak
    max_dds[i] = dds.min()
    if (equity < ruin_level).any():
        ruin_hits[i] = True

elapsed = time.time() - t0
print(f" {elapsed:.1f}s")

# ── 4. MÉTRICAS ───────────────────────────────────────────────────────────────

ruin_prob = ruin_hits.mean()
p5, p10, p25, p50, p75, p90, p95 = np.percentile(final_caps, [5, 10, 25, 50, 75, 90, 95])
dd_abs    = -max_dds
dd_mean   = dd_abs.mean()
dd_p50    = np.percentile(dd_abs, 50)
dd_p95    = np.percentile(dd_abs, 95)
dd_p99    = np.percentile(dd_abs, 99)

print(f"\n  ── Resultados Montecarlo ──────────────────────────────")
print(f"  Prob. ruina (< {CAPITAL_MC*RUIN_THRESHOLD:,.0f}€): {ruin_prob:.2%}")
print(f"  Capital final p5  : {p5:>14,.0f} €  ({p5/CAPITAL_MC:.1f}x)")
print(f"  Capital final p50 : {p50:>14,.0f} €  ({p50/CAPITAL_MC:.1f}x)")
print(f"  Capital final p95 : {p95:>14,.0f} €  ({p95/CAPITAL_MC:.1f}x)")
print(f"  DD medio          : {dd_mean:.2%}")
print(f"  DD p50            : {dd_p50:.2%}")
print(f"  DD p95            : {dd_p95:.2%}")
print(f"  DD p99            : {dd_p99:.2%}")
print(f"  Real (backtest)   : {capital_final:>14,.0f} €  DD={metricas['max_drawdown']:.2%}")

# ── 5. PREPARAR DATOS PARA GRÁFICOS ──────────────────────────────────────────

# Subsamplear trades para el eje X (evitar JSON enorme)
step = max(1, n_trades // N_BANDS_POINTS)
idx_x = list(range(0, n_trades + 1, step))
if idx_x[-1] != n_trades:
    idx_x.append(n_trades)
n_pts = len(idx_x)

# Bandas percentil (subsampled)
sub = all_equity[:, idx_x]
band_p5  = np.percentile(sub, 5,  axis=0).tolist()
band_p25 = np.percentile(sub, 25, axis=0).tolist()
band_p50 = np.percentile(sub, 50, axis=0).tolist()
band_p75 = np.percentile(sub, 75, axis=0).tolist()
band_p95 = np.percentile(sub, 95, axis=0).tolist()
x_labels = [str(i) for i in idx_x]

# Curvas individuales sample (subsampled)
curve_indices = RNG.choice(N_SIMS, size=min(N_CURVES_PLOT, N_SIMS), replace=False)
sample_curves = [all_equity[i, idx_x].tolist() for i in curve_indices]

# Histograma capital final (50 bins)
hist_cap_counts, hist_cap_edges = np.histogram(final_caps / 1000, bins=50)
hist_cap_labels = [f"{e:.0f}k" for e in hist_cap_edges[:-1]]
hist_cap_counts = hist_cap_counts.tolist()
hist_cap_edges  = hist_cap_edges.tolist()

# Histograma DD max (50 bins)
hist_dd_counts, hist_dd_edges = np.histogram(dd_abs * 100, bins=40)
hist_dd_labels = [f"{e:.1f}%" for e in hist_dd_edges[:-1]]
hist_dd_counts = hist_dd_counts.tolist()
hist_dd_edges  = hist_dd_edges.tolist()

# ── 6. GENERAR HTML ──────────────────────────────────────────────────────────

# Curva real en base 100,000€ (mismos returns, orden original)
real_equity_curve = np.empty(len(returns) + 1)
real_equity_curve[0] = CAPITAL_MC
for k, r in enumerate(returns):
    real_equity_curve[k + 1] = real_equity_curve[k] * (1.0 + r)

# Valores de referencia — Montecarlo original ×1.00 (montecarlo_2005.html, 4.000 € inicio)
ORIG_P5      =   946_000
ORIG_P50     = 3_324_000
ORIG_P95     = 16_435_000
ORIG_DD_P50  = 7.6
ORIG_DD_P95  = 11.1
ORIG_CAP_INI = 4_000

fecha_gen = datetime.now().strftime("%Y-%m-%d %H:%M")

html_data = {
    "x_labels"    : x_labels,
    "band_p5"     : band_p5,
    "band_p25"    : band_p25,
    "band_p50"    : band_p50,
    "band_p75"    : band_p75,
    "band_p95"    : band_p95,
    "sample_curves": sample_curves,
    "real_curve"  : real_equity_curve[idx_x].tolist(),
    "hist_cap_counts": hist_cap_counts,
    "hist_cap_edges" : hist_cap_edges,
    "hist_dd_counts" : hist_dd_counts,
    "hist_dd_edges"  : hist_dd_edges,
}

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LIBERTAD_2045 — Montecarlo ×0.75</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:#080c14;--card:#0f1624;--bdr:#1e293b;--txt:#e2e8f0;--muted:#64748b;
  --blue:#3b82f6;--green:#10b981;--gold:#f59e0b;--red:#ef4444;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);font-family:'Courier New',monospace;font-size:13px;padding:20px;max-width:1400px;margin:0 auto;line-height:1.7}}
.hdr{{text-align:center;padding:40px 20px 28px;border-bottom:1px solid var(--bdr);margin-bottom:36px}}
.hdr h1{{font-size:22px;font-weight:700;letter-spacing:3px;color:var(--gold);margin-bottom:6px}}
.hdr .sub{{color:var(--muted);font-size:11px;letter-spacing:1px}}
.sec{{margin-bottom:50px}}
.sec-hdr{{display:flex;align-items:baseline;gap:14px;margin-bottom:18px;padding-bottom:10px;border-bottom:1px solid var(--bdr)}}
.sec-title{{font-size:14px;font-weight:700;color:var(--blue);letter-spacing:2px;text-transform:uppercase}}
.sec-sub{{font-size:11px;color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:22px}}
.kcard{{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:14px;text-align:center}}
.kcard .v{{font-size:20px;font-weight:700;color:var(--gold);margin-bottom:3px}}
.kcard .l{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.chart-box{{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:18px;margin-bottom:20px}}
.chart-box canvas{{max-height:380px}}
.chart-title{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:14px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
.cmp-tbl{{width:100%;border-collapse:collapse;margin-bottom:8px}}
.cmp-tbl th{{background:#0b1220;color:var(--muted);padding:9px 14px;text-align:right;font-size:10px;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid var(--bdr)}}
.cmp-tbl th:first-child{{text-align:left}}
.cmp-tbl td{{padding:9px 14px;text-align:right;border-bottom:1px solid var(--bdr);color:var(--txt)}}
.cmp-tbl td:first-child{{text-align:left;color:var(--muted)}}
.cmp-tbl tr:last-child td{{border-bottom:none}}
.hi{{color:var(--green);font-weight:700}}
.note{{font-size:11px;color:var(--muted);padding:6px 4px}}
.verdict{{background:linear-gradient(135deg,rgba(245,158,11,.12),rgba(245,158,11,.04));border:2px solid var(--gold);border-radius:12px;padding:36px;text-align:center;margin-top:36px}}
.vcheck{{font-size:36px;margin-bottom:12px}}
.vtitle{{font-size:22px;font-weight:700;color:var(--gold);letter-spacing:4px;margin-bottom:8px}}
.vdate{{font-size:11px;color:var(--muted);margin-bottom:20px}}
.vgrid{{display:inline-grid;grid-template-columns:repeat(4,1fr);border:1px solid rgba(245,158,11,.2);border-radius:8px;overflow:hidden;max-width:600px;margin-bottom:18px}}
.vk{{padding:14px 20px;border-right:1px solid rgba(245,158,11,.15)}}
.vk:last-child{{border-right:none}}
.vkv{{font-size:18px;font-weight:700;color:var(--gold);margin-bottom:2px}}
.vkl{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.vsub{{font-size:12px;color:var(--muted)}}
.foot{{text-align:center;padding:20px 0;border-top:1px solid var(--bdr);margin-top:36px;color:var(--muted);font-size:10px;letter-spacing:1px}}
@media(max-width:700px){{.grid2{{grid-template-columns:1fr}}.vgrid{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>

<header class="hdr">
  <h1>LIBERTAD_2045 — MONTECARLO ×0.75</h1>
  <div class="sub">Análisis de riesgo trade-level · {N_SIMS:,} simulaciones bootstrap · Capital: {CAPITAL_MC:,.0f} € · Generado {fecha_gen}</div>
</header>

<!-- KPIs principales -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Métricas Clave</span>
    <span class="sec-sub">Backtest ×0.75 (2006–2025) + {N_SIMS:,} simulaciones bootstrap</span>
  </div>
  <div class="kpis">
    <div class="kcard"><div class="v" style="color:var(--red)">{ruin_prob:.2%}</div><div class="l">Prob. ruina (&lt;{CAPITAL_MC*RUIN_THRESHOLD:,.0f}€)</div></div>
    <div class="kcard"><div class="v" style="color:var(--red)">{p5/1000:.0f}k€</div><div class="l">Capital p5</div></div>
    <div class="kcard"><div class="v">{p50/1000:.0f}k€</div><div class="l">Capital p50 (mediana)</div></div>
    <div class="kcard"><div class="v" style="color:var(--green)">{p95/1000:.0f}k€</div><div class="l">Capital p95</div></div>
    <div class="kcard"><div class="v">{dd_mean:.1%}</div><div class="l">DD medio</div></div>
    <div class="kcard"><div class="v" style="color:var(--gold)">{dd_p95:.1%}</div><div class="l">DD p95</div></div>
    <div class="kcard"><div class="v">{capital_final/1000:.0f}k€</div><div class="l">Real (backtest)</div></div>
    <div class="kcard"><div class="v">{metricas['max_drawdown']:.1%}</div><div class="l">DD real (backtest)</div></div>
  </div>
</div>

<!-- Curvas de capital -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Curvas de Capital</span>
    <span class="sec-sub">{N_CURVES_PLOT} caminos muestreados + bandas percentil p5/p25/p50/p75/p95</span>
  </div>
  <div class="chart-box">
    <div class="chart-title">Evolución del capital por trade (log scale) — {N_SIMS:,} simulaciones</div>
    <canvas id="chartCurves"></canvas>
  </div>
</div>

<!-- Histogramas -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Distribuciones</span>
    <span class="sec-sub">Distribución de capital final y drawdown máximo sobre {N_SIMS:,} simulaciones</span>
  </div>
  <div class="grid2">
    <div class="chart-box">
      <div class="chart-title">Distribución de capital final (miles €)</div>
      <canvas id="chartCapHist"></canvas>
    </div>
    <div class="chart-box">
      <div class="chart-title">Distribución de drawdown máximo (%)</div>
      <canvas id="chartDDHist"></canvas>
    </div>
  </div>
</div>

<!-- Comparativa con Montecarlo original -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Comparativa</span>
    <span class="sec-sub">×0.75 trade-level bootstrap vs ×1.00 original diario (montecarlo_2005) — mismo capital inicial: 4.000 €</span>
  </div>
  <div style="overflow-x:auto;border:1px solid var(--bdr);border-radius:8px;margin-bottom:12px">
    <table class="cmp-tbl">
      <thead>
        <tr>
          <th>Métrica</th>
          <th>×0.75 trade-level (este)</th>
          <th>×1.00 original diario</th>
          <th>Δ</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Capital inicial</td>
          <td>{CAPITAL_MC:,.0f} €</td>
          <td>4.000 €</td>
          <td>—</td>
        </tr>
        <tr>
          <td>Capital final p5</td>
          <td class="{'hi' if p5 > ORIG_P5 else ''}">{p5:,.0f} € ({p5/CAPITAL_MC:.0f}x)</td>
          <td>{ORIG_P5:,.0f} € ({ORIG_P5/ORIG_CAP_INI:.0f}x)</td>
          <td class="{'hi' if p5 > ORIG_P5 else ''}">{'+' if p5 > ORIG_P5 else ''}{(p5-ORIG_P5)/ORIG_P5*100:.0f}%</td>
        </tr>
        <tr>
          <td>Capital final p50</td>
          <td class="{'hi' if p50 > ORIG_P50 else ''}">{p50:,.0f} € ({p50/CAPITAL_MC:.0f}x)</td>
          <td>{ORIG_P50:,.0f} € ({ORIG_P50/ORIG_CAP_INI:.0f}x)</td>
          <td class="{'hi' if p50 > ORIG_P50 else ''}">{'+' if p50 > ORIG_P50 else ''}{(p50-ORIG_P50)/ORIG_P50*100:.0f}%</td>
        </tr>
        <tr>
          <td>Capital final p95</td>
          <td class="{'hi' if p95 > ORIG_P95 else ''}">{p95:,.0f} € ({p95/CAPITAL_MC:.0f}x)</td>
          <td>{ORIG_P95:,.0f} € ({ORIG_P95/ORIG_CAP_INI:.0f}x)</td>
          <td class="{'hi' if p95 > ORIG_P95 else ''}">{'+' if p95 > ORIG_P95 else ''}{(p95-ORIG_P95)/ORIG_P95*100:.0f}%</td>
        </tr>
        <tr>
          <td>DD máx p50</td>
          <td>{dd_p50:.1%}</td>
          <td>{ORIG_DD_P50:.1f}%</td>
          <td class="{'hi' if dd_p50 < ORIG_DD_P50/100 else ''}">{(dd_p50 - ORIG_DD_P50/100)*100:+.1f}pp</td>
        </tr>
        <tr>
          <td>DD máx p95</td>
          <td>{dd_p95:.1%}</td>
          <td>{ORIG_DD_P95:.1f}%</td>
          <td class="{'hi' if dd_p95 < ORIG_DD_P95/100 else ''}">{(dd_p95 - ORIG_DD_P95/100)*100:+.1f}pp</td>
        </tr>
        <tr>
          <td>Prob. ruina</td>
          <td>{ruin_prob:.2%}</td>
          <td>~0.00%</td>
          <td>—</td>
        </tr>
        <tr>
          <td>Metodología</td>
          <td>Bootstrap trade returns</td>
          <td>Bootstrap retornos diarios</td>
          <td>—</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="note">* Comparativa orientativa: metodologías distintas (trade-level vs diario). Ambos parten de 4.000 € de capital inicial.</div>
</div>

<!-- Veredicto -->
<div class="verdict">
  <div class="vcheck">{'✅' if ruin_prob < 0.02 else '⚠️'}</div>
  <div class="vtitle">{'RIESGO CONTROLADO' if ruin_prob < 0.02 else 'REVISAR RIESGO'}</div>
  <div class="vdate">Montecarlo ×0.75 · {N_SIMS:,} simulaciones bootstrap · Capital: {CAPITAL_MC:,.0f} € · {fecha_gen}</div>
  <div class="vgrid">
    <div class="vk"><div class="vkv">{ruin_prob:.2%}</div><div class="vkl">Prob. ruina</div></div>
    <div class="vk"><div class="vkv">{p50/1000:.0f}k€</div><div class="vkl">Mediana</div></div>
    <div class="vk"><div class="vkv">{dd_p95:.1%}</div><div class="vkl">DD p95</div></div>
    <div class="vk"><div class="vkv">{n_trades}</div><div class="vkl">Trades</div></div>
  </div>
  <div class="vsub">p5={p5/1000:.0f}k€ · p50={p50/1000:.0f}k€ · p95={p95/1000:.0f}k€ · DD medio={dd_mean:.1%} · PF real={metricas['profit_factor']:.3f}</div>
</div>

<footer class="foot">
  LIBERTAD_2045 · Montecarlo ×0.75 · backtest_exp40ter.py + montecarlo_trades_075.py · {fecha_gen}
</footer>

<script>
const DATA = {json.dumps(html_data)};

Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = 'Courier New, monospace';
Chart.defaults.font.size = 11;

// ── Curvas de capital ──────────────────────────────────────────────────
const ctxC = document.getElementById('chartCurves').getContext('2d');
const curveDSs = [];

// Curvas individuales (muy transparentes)
DATA.sample_curves.forEach(c => {{
  curveDSs.push({{
    data: c,
    borderColor: 'rgba(100,116,139,0.07)',
    borderWidth: 1,
    pointRadius: 0,
    fill: false,
    tension: 0,
  }});
}});

// Banda p5-p95
curveDSs.push({{
  label: 'p5–p95',
  data: DATA.band_p95,
  borderColor: 'rgba(59,130,246,0)',
  backgroundColor: 'rgba(59,130,246,0.08)',
  fill: '+1',
  pointRadius: 0, tension: 0,
}});
curveDSs.push({{
  data: DATA.band_p5,
  borderColor: 'rgba(59,130,246,0)',
  fill: false,
  pointRadius: 0, tension: 0,
}});

// Banda p25-p75
curveDSs.push({{
  label: 'p25–p75',
  data: DATA.band_p75,
  borderColor: 'rgba(59,130,246,0)',
  backgroundColor: 'rgba(59,130,246,0.14)',
  fill: '+1',
  pointRadius: 0, tension: 0,
}});
curveDSs.push({{
  data: DATA.band_p25,
  borderColor: 'rgba(59,130,246,0)',
  fill: false,
  pointRadius: 0, tension: 0,
}});

// Mediana
curveDSs.push({{
  label: 'p50 (mediana)',
  data: DATA.band_p50,
  borderColor: '#e2e8f0',
  borderWidth: 2,
  pointRadius: 0,
  fill: false, tension: 0,
}});

// p5 y p95 visibles
curveDSs.push({{
  label: 'p5',
  data: DATA.band_p5,
  borderColor: '#ef4444',
  borderWidth: 1.5,
  borderDash: [4,3],
  pointRadius: 0, fill: false, tension: 0,
}});
curveDSs.push({{
  label: 'p95',
  data: DATA.band_p95,
  borderColor: '#10b981',
  borderWidth: 1.5,
  borderDash: [4,3],
  pointRadius: 0, fill: false, tension: 0,
}});

// Curva real (orden original de trades sobre 100k€)
curveDSs.push({{
  label: 'Real (backtest ×0.75)',
  data: DATA.real_curve,
  borderColor: '#f59e0b',
  borderWidth: 2.5,
  pointRadius: 0, fill: false, tension: 0,
}});

new Chart(ctxC, {{
  type: 'line',
  data: {{
    labels: DATA.x_labels,
    datasets: curveDSs,
  }},
  options: {{
    animation: false,
    responsive: true,
    plugins: {{
      legend: {{
        display: true,
        labels: {{
          filter: item => item.datasetIndex >= DATA.sample_curves.length,
          color: '#94a3b8', font: {{size: 11}},
        }}
      }},
      tooltip: {{enabled: false}},
    }},
    scales: {{
      x: {{
        ticks: {{maxTicksLimit: 10, color:'#475569'}},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display: true, text: 'Trade #', color:'#475569'}},
      }},
      y: {{
        type: 'logarithmic',
        ticks: {{
          color:'#475569',
          callback: v => v >= 1000 ? (v/1000).toFixed(0)+'k€' : v+'€',
        }},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display:true, text:'Capital (log)', color:'#475569'}},
      }},
    }},
  }},
}});

// ── Histograma capital final ───────────────────────────────────────────
const ctxH = document.getElementById('chartCapHist').getContext('2d');
const capColors = DATA.hist_cap_edges.slice(0,-1).map((e,i) => {{
  const frac = i / DATA.hist_cap_counts.length;
  const r = Math.round(239*frac + 59*(1-frac));
  const g = Math.round(68*frac  + 130*(1-frac));
  const b = Math.round(68*frac  + 246*(1-frac));
  return `rgba(${{r}},${{g}},${{b}},0.75)`;
}});

new Chart(ctxH, {{
  type: 'bar',
  data: {{
    labels: DATA.hist_cap_edges.slice(0,-1).map(e => e.toFixed(0)+'k'),
    datasets: [{{
      label: '# simulaciones',
      data: DATA.hist_cap_counts,
      backgroundColor: capColors,
      borderWidth: 0,
    }}],
  }},
  options: {{
    animation: false,
    responsive: true,
    plugins: {{
      legend: {{display: false}},
      tooltip: {{callbacks: {{label: ctx => ` ${{ctx.raw}} sims`}}}},
    }},
    scales: {{
      x: {{
        ticks: {{maxTicksLimit:8, color:'#475569'}},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display:true, text:'Capital final (miles €)', color:'#475569'}},
      }},
      y: {{
        ticks: {{color:'#475569'}},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display:true, text:'Simulaciones', color:'#475569'}},
      }},
    }},
  }},
}});

// ── Histograma DD ──────────────────────────────────────────────────────
const ctxD = document.getElementById('chartDDHist').getContext('2d');
const ddColors = DATA.hist_dd_edges.slice(0,-1).map((e,i) => {{
  const bad = Math.min(1, e / 20);
  return `rgba(${{Math.round(239*bad+59*(1-bad))}},${{Math.round(68*bad+130*(1-bad))}},${{Math.round(68*bad+246*(1-bad))}},0.75)`;
}});

new Chart(ctxD, {{
  type: 'bar',
  data: {{
    labels: DATA.hist_dd_edges.slice(0,-1).map(e => e.toFixed(1)+'%'),
    datasets: [{{
      label: '# simulaciones',
      data: DATA.hist_dd_counts,
      backgroundColor: ddColors,
      borderWidth: 0,
    }}],
  }},
  options: {{
    animation: false,
    responsive: true,
    plugins: {{
      legend: {{display:false}},
      tooltip: {{callbacks: {{label: ctx => ` ${{ctx.raw}} sims`}}}},
    }},
    scales: {{
      x: {{
        ticks: {{maxTicksLimit:8, color:'#475569'}},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display:true, text:'Drawdown máximo (%)', color:'#475569'}},
      }},
      y: {{
        ticks: {{color:'#475569'}},
        grid: {{color:'rgba(30,41,59,0.8)'}},
        title: {{display:true, text:'Simulaciones', color:'#475569'}},
      }},
    }},
  }},
}});
</script>
</body>
</html>"""

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)

total = time.time() - t0_total
print(f"\n  HTML generado: {OUTPUT}")
print(f"  Tamaño: {os.path.getsize(OUTPUT)/1024:.0f} KB")
print(f"  Tiempo total: {total:.0f}s")
print()
