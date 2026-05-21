#!/usr/bin/env python3
"""
Análisis de Montecarlo — LIBERTAD_2045 (2005-2025)
1000 simulaciones barajando retornos DIARIOS de la curva de capital.
Usar la curva diaria (no trades individuales) evita el problema de
posiciones concurrentes y captura correctamente el compounding real.
"""

import os
import glob
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

RNG       = np.random.default_rng(seed=42)
N_SIMS    = 1000
# Bootstrap con reposición: muestrea n_days retornos aleatoriamente CON reemplazo
# → cada sim tiene distinta combinación de días, dando distribuciones reales
# de capital final Y de drawdown máximo.
# (Permutación pura da siempre el mismo capital final por conmutatividad del producto)
RESULTS_DIR = os.path.expanduser("~/PROYECTO_LIBERTAD_2045/backtest_results")
OUTPUT    = os.path.expanduser("~/PROYECTO_LIBERTAD_2045/montecarlo_2005.html")

# ── 1. Cargar archivos más recientes ──────────────────────────────────────────
capital_files  = sorted(glob.glob(os.path.join(RESULTS_DIR, "expandido_capital_*.csv")))
metrics_files  = sorted(glob.glob(os.path.join(RESULTS_DIR, "expandido_metricas_*.csv")))
trades_files   = sorted(glob.glob(os.path.join(RESULTS_DIR, "expandido_trades_*.csv")))

latest_capital  = capital_files[-1]
latest_metrics  = metrics_files[-1]
latest_trades   = trades_files[-1]

print(f"Capital : {os.path.basename(latest_capital)}")
print(f"Métricas: {os.path.basename(latest_metrics)}")

df_cap     = pd.read_csv(latest_capital, parse_dates=["fecha"])
df_metrics = pd.read_csv(latest_metrics)
df_trades  = pd.read_csv(latest_trades)

CAPITAL_INICIAL  = float(df_metrics["capital_inicial"].iloc[0])
MAX_DD_REAL      = float(df_metrics["max_drawdown"].iloc[0])
TOTAL_TRADES     = int(df_metrics["total_trades"].iloc[0])
WIN_RATE_REAL    = float(df_metrics["win_rate"].iloc[0])
PROFIT_FACTOR    = float(df_metrics["profit_factor"].iloc[0])
EXPECTATIVA      = float(df_metrics["expectativa"].iloc[0])

df_cap = df_cap.sort_values("fecha").reset_index(drop=True)
CAPITAL_REAL = float(df_cap["capital"].iloc[-1])

print(f"\nCapital inicial : ${CAPITAL_INICIAL:,.0f}")
print(f"Capital final   : ${CAPITAL_REAL:,.0f}  ({CAPITAL_REAL/CAPITAL_INICIAL:.0f}x)")
print(f"Max Drawdown    : {MAX_DD_REAL:.2%}")
print(f"Total trades    : {TOTAL_TRADES}")

# ── 2. Calcular retornos diarios de la curva de capital ───────────────────────
equity_real = df_cap["capital"].values
# Retorno diario: (hoy - ayer) / ayer
daily_returns = np.diff(equity_real) / equity_real[:-1]
n_days = len(daily_returns)

print(f"\nDías de trading  : {n_days}")
print(f"Ret. diario medio: {daily_returns.mean():.4%}")
print(f"Ret. diario geo  : {(CAPITAL_REAL/CAPITAL_INICIAL)**(1/n_days)-1:.4%}")
print(f"Ret. diario std  : {daily_returns.std():.4%}")
print(f"Días con retorno 0: {(daily_returns == 0).sum()} ({(daily_returns == 0).mean():.1%})")

# ── 3. Función de drawdown máximo ─────────────────────────────────────────────
def max_drawdown_from_equity(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    dd   = (equity - peak) / peak
    return float(dd.min())   # negativo, e.g. -0.12

# ── 4. Montecarlo ─────────────────────────────────────────────────────────────
print(f"\nEjecutando {N_SIMS:,} simulaciones Montecarlo (bootstrap diario)...")
t0 = time.time()

final_capitals = np.empty(N_SIMS)
max_drawdowns  = np.empty(N_SIMS)

# Para el spaghetti almacenamos equity normalizada (200 paths, mensual ≈ cada 21 días)
N_TRAJ   = 200
STEP     = max(1, n_days // 500)          # ~500 puntos por trayectoria
idx_keep = np.arange(0, n_days + 1, STEP)
if idx_keep[-1] != n_days:
    idx_keep = np.append(idx_keep, n_days)
trajectories = np.empty((N_TRAJ, len(idx_keep)))

# Bootstrap con reposición: cada sim muestrea n_days retornos CON reemplazo
all_shuffles = RNG.choice(daily_returns, size=(N_SIMS, n_days), replace=True)

for i in range(N_SIMS):
    rets = all_shuffles[i]
    # Equity con compounding
    equity = CAPITAL_INICIAL * np.concatenate([[1.0], np.cumprod(1 + rets)])
    final_capitals[i] = equity[-1]
    max_drawdowns[i]  = max_drawdown_from_equity(equity)
    if i < N_TRAJ:
        trajectories[i] = equity[idx_keep] / CAPITAL_INICIAL   # indexado a 1

elapsed = time.time() - t0
print(f"Completado en {elapsed:.2f}s")

# ── 5. Estadísticas ───────────────────────────────────────────────────────────
dd_abs = np.abs(max_drawdowns)
ruina  = np.mean(final_capitals < CAPITAL_INICIAL)

p50_dd = np.percentile(dd_abs, 50)
p95_dd = np.percentile(dd_abs, 95)
p99_dd = np.percentile(dd_abs, 99)

p5_cap  = np.percentile(final_capitals,  5)
p25_cap = np.percentile(final_capitals, 25)
p50_cap = np.percentile(final_capitals, 50)
p75_cap = np.percentile(final_capitals, 75)
p95_cap = np.percentile(final_capitals, 95)

retorno_real   = CAPITAL_REAL / CAPITAL_INICIAL - 1
retorno_p50    = p50_cap / CAPITAL_INICIAL - 1
retorno_p5     = p5_cap  / CAPITAL_INICIAL - 1
retorno_p95    = p95_cap / CAPITAL_INICIAL - 1

print(f"\n{'═'*50}")
print(f"  DRAWDOWN MÁXIMO (1000 simulaciones)")
print(f"  p50  : {p50_dd:.2%}")
print(f"  p95  : {p95_dd:.2%}")
print(f"  p99  : {p99_dd:.2%}")
print(f"  Real : {MAX_DD_REAL:.2%}  ← posición en la distribución")
pct_rank_dd = (dd_abs < MAX_DD_REAL).mean()
print(f"  Percentil real (DD): {pct_rank_dd:.0%}")

print(f"\n  CAPITAL FINAL")
print(f"  p5   : ${p5_cap:>12,.0f}  ({retorno_p5:+.0%})")
print(f"  p25  : ${p25_cap:>12,.0f}  ({p25_cap/CAPITAL_INICIAL-1:+.0%})")
print(f"  p50  : ${p50_cap:>12,.0f}  ({retorno_p50:+.0%})")
print(f"  p75  : ${p75_cap:>12,.0f}  ({p75_cap/CAPITAL_INICIAL-1:+.0%})")
print(f"  p95  : ${p95_cap:>12,.0f}  ({retorno_p95:+.0%})")
print(f"  Real : ${CAPITAL_REAL:>12,.0f}  ({retorno_real:+.0%})")
pct_rank_cap = (final_capitals < CAPITAL_REAL).mean()
print(f"  Percentil real (Cap): {pct_rank_cap:.0%}")

print(f"\n  Prob. de ruina (cap < inicio): {ruina:.2%}")
print(f"  Min capital simulado: ${final_capitals.min():,.0f}")
print(f"  Max capital simulado: ${final_capitals.max():,.0f}")
print(f"{'═'*50}")

# ── 6. Gráfico ────────────────────────────────────────────────────────────────
DARK_BG    = "#0D0D0D"
PANEL_BG   = "#141414"
GRID       = "rgba(255,255,255,0.06)"
WHITE      = "#E8E8E8"
GREEN      = "#00E676"
RED        = "#FF4C4C"
AMBER      = "#FFB300"
BLUE       = "#40C4FF"
PURPLE     = "#CE93D8"
TEAL       = "#26C6DA"

fig = make_subplots(
    rows=2, cols=2,
    subplot_titles=[
        "Trayectorias de Capital — 200 simulaciones (indexado Base 1)",
        "Distribución de Drawdowns Máximos",
        "Distribución de Capitales Finales",
        "Panel de Riesgo — Resumen Estadístico",
    ],
    specs=[
        [{"type": "scatter"}, {"type": "histogram"}],
        [{"type": "histogram"}, {"type": "table"}],
    ],
    vertical_spacing=0.15,
    horizontal_spacing=0.10,
)

# ── Panel 1: Trayectorias ─────────────────────────────────────────────────────
x_traj = idx_keep / n_days * 100    # eje: % del período completado

# Percentiles de equity normalizada
all_traj_full = np.empty((N_SIMS, len(idx_keep)))
for i in range(N_SIMS):
    equity = CAPITAL_INICIAL * np.concatenate([[1.0], np.cumprod(1 + all_shuffles[i])])
    all_traj_full[i] = equity[idx_keep] / CAPITAL_INICIAL

p5_eq  = np.percentile(all_traj_full,  5, axis=0)
p25_eq = np.percentile(all_traj_full, 25, axis=0)
p50_eq = np.percentile(all_traj_full, 50, axis=0)
p75_eq = np.percentile(all_traj_full, 75, axis=0)
p95_eq = np.percentile(all_traj_full, 95, axis=0)

# Spaghetti: trayectorias individuales
for i in range(N_TRAJ):
    fig.add_trace(go.Scatter(
        x=x_traj, y=trajectories[i],
        mode="lines",
        line=dict(color="rgba(0,230,118,0.035)", width=1),
        showlegend=False, hoverinfo="skip",
    ), row=1, col=1)

# Banda p5–p95
fig.add_trace(go.Scatter(
    x=np.concatenate([x_traj, x_traj[::-1]]),
    y=np.concatenate([p95_eq, p5_eq[::-1]]),
    fill="toself", fillcolor="rgba(64,196,255,0.07)",
    line=dict(color="rgba(0,0,0,0)"),
    name="Banda p5–p95", showlegend=True, hoverinfo="skip",
), row=1, col=1)

# Banda p25–p75
fig.add_trace(go.Scatter(
    x=np.concatenate([x_traj, x_traj[::-1]]),
    y=np.concatenate([p75_eq, p25_eq[::-1]]),
    fill="toself", fillcolor="rgba(0,230,118,0.10)",
    line=dict(color="rgba(0,0,0,0)"),
    name="Banda p25–p75", showlegend=True, hoverinfo="skip",
), row=1, col=1)

# Mediana MC
fig.add_trace(go.Scatter(
    x=x_traj, y=p50_eq,
    mode="lines", line=dict(color=GREEN, width=2),
    name="Mediana MC (p50)",
), row=1, col=1)

# Resultado real del backtest (normalizado)
real_norm = equity_real / CAPITAL_INICIAL
real_x    = np.linspace(0, 100, len(real_norm))
fig.add_trace(go.Scatter(
    x=real_x, y=real_norm,
    mode="lines", line=dict(color=AMBER, width=2.5, dash="dot"),
    name=f"Backtest real ({retorno_real:.0%})",
), row=1, col=1)

# Línea base = 1
fig.add_hline(y=1, line_dash="dot", line_color="rgba(255,255,255,0.2)",
              line_width=1, row=1, col=1)

# ── Panel 2: Distribución de drawdowns ───────────────────────────────────────
fig.add_trace(go.Histogram(
    x=dd_abs * 100, nbinsx=60,
    marker_color=RED, opacity=0.80,
    marker_line=dict(color="rgba(0,0,0,0.3)", width=0.4),
    name="DD máx. (%)", showlegend=False,
    hovertemplate="DD: %{x:.1f}%<br>Frec.: %{y}<extra></extra>",
), row=1, col=2)

for label, val, color, pos in [
    ("p50",  p50_dd * 100,  WHITE,  "top left"),
    ("p95",  p95_dd * 100,  AMBER,  "top right"),
    ("p99",  p99_dd * 100,  RED,    "top right"),
    ("Real", MAX_DD_REAL * 100, GREEN, "top left"),
]:
    fig.add_vline(
        x=val, line_dash="dash" if label != "Real" else "solid",
        line_color=color, line_width=2,
        annotation_text=f"<b>{label}: {val:.1f}%</b>",
        annotation_position=pos,
        annotation_font=dict(color=color, size=11),
        row=1, col=2,
    )

# ── Panel 3: Distribución de capitales finales ────────────────────────────────
fig.add_trace(go.Histogram(
    x=final_capitals / 1_000, nbinsx=60,
    marker_color=BLUE, opacity=0.80,
    marker_line=dict(color="rgba(0,0,0,0.3)", width=0.4),
    name="Capital final ($K)", showlegend=False,
    hovertemplate="Capital: $%{x:,.0f}K<br>Frec.: %{y}<extra></extra>",
), row=2, col=1)

for label, val, color, pos in [
    ("p5",   p5_cap / 1_000,   RED,    "top left"),
    ("p50",  p50_cap / 1_000,  WHITE,  "top left"),
    ("p95",  p95_cap / 1_000,  GREEN,  "top right"),
    ("Real", CAPITAL_REAL / 1_000, AMBER, "top right"),
]:
    fig.add_vline(
        x=val, line_dash="dash" if label not in ("Real",) else "solid",
        line_color=color, line_width=2,
        annotation_text=f"<b>{label}: ${val:,.0f}K</b>",
        annotation_position=pos,
        annotation_font=dict(color=color, size=11),
        row=2, col=1,
    )

# Sombreado zona de ruina
if CAPITAL_INICIAL / 1_000 > 0:
    fig.add_vrect(
        x0=0, x1=CAPITAL_INICIAL / 1_000,
        fillcolor="rgba(255,76,76,0.07)", line_width=0,
        annotation_text="Zona ruina", annotation_position="top left",
        annotation_font=dict(color="rgba(255,76,76,0.6)", size=10),
        row=2, col=1,
    )

# ── Panel 4: Tabla resumen ────────────────────────────────────────────────────
ruina_n = int(ruina * N_SIMS)

col_metrics = [
    "Capital final",
    "Retorno total",
    "Max Drawdown",
    "Prob. ruina",
    "Percentil cap. real",
    "Percentil DD real",
    "Win rate (trades)",
    "Profit factor",
    "Expectativa/trade",
    "N° días simulados",
    "N° trades",
]
col_real = [
    f"${CAPITAL_REAL:>12,.0f}",
    f"{retorno_real:+.0%}",
    f"{MAX_DD_REAL:.2%}",
    "—",
    f"Top {100-pct_rank_cap*100:.0f}%",
    f"Top {pct_rank_dd*100:.0f}%",
    f"{WIN_RATE_REAL:.1%}",
    f"{PROFIT_FACTOR:.2f}",
    f"${EXPECTATIVA:,.0f}",
    f"{n_days:,}",
    f"{TOTAL_TRADES:,}",
]
col_mc = [
    f"${p50_cap:>12,.0f}",
    f"{retorno_p50:+.0%}",
    f"{p50_dd:.2%}",
    f"{ruina:.2%}  ({ruina_n}/{N_SIMS})",
    "—",
    "—",
    "—",
    "—",
    "—",
    f"{n_days:,}",
    f"{TOTAL_TRADES:,}",
]
col_p95 = [
    f"${np.percentile(final_capitals,5):>12,.0f}",
    f"{retorno_p5:+.0%}",
    f"{p95_dd:.2%}",
    "—",
    "—",
    "—",
    "—",
    "—",
    "—",
    "—",
    "—",
]

# Colores por fila
row_colors_real = [AMBER] * len(col_metrics)
row_colors_mc   = [GREEN]  + [GREEN] + [RED] + [RED if ruina > 0.05 else GREEN] + ["#AAAAAA"] * 7
row_colors_p95  = [RED]    * 3 + ["#AAAAAA"] * 8

fig.add_trace(go.Table(
    header=dict(
        values=["<b>Métrica</b>", "<b>Backtest Real</b>",
                "<b>MC Mediana (p50)</b>", "<b>MC Worst 5% (p5/p95)</b>"],
        fill_color="#1E1E3A",
        font=dict(color=WHITE, size=12, family="Inter, Arial"),
        align="left",
        line_color="rgba(255,255,255,0.15)",
        height=32,
    ),
    cells=dict(
        values=[col_metrics, col_real, col_mc, col_p95],
        fill_color=["#1A1A2E"] * len(col_metrics),
        font=dict(
            color=[WHITE, row_colors_real, row_colors_mc, row_colors_p95],
            size=11, family="Inter, Arial",
        ),
        align="left",
        line_color="rgba(255,255,255,0.07)",
        height=26,
    ),
), row=2, col=2)

# ── Layout global ─────────────────────────────────────────────────────────────
fig.update_layout(
    title=dict(
        text=(
            "<b>Análisis de Montecarlo — LIBERTAD_2045 (2005–2025)</b>"
            f"<br><sup>{N_SIMS:,} simulaciones · {n_days:,} retornos diarios barajados · "
            f"Capital inicial: ${CAPITAL_INICIAL:,.0f} · "
            f"Bootstrap con reposición sobre {n_days:,} retornos diarios · "
            f"Win rate: {WIN_RATE_REAL:.1%} · Profit factor: {PROFIT_FACTOR:.2f}</sup>"
        ),
        x=0.5, xanchor="center",
        font=dict(size=19, color=WHITE),
    ),
    paper_bgcolor=DARK_BG,
    plot_bgcolor=PANEL_BG,
    font=dict(family="Inter, Arial, sans-serif", color="#CCCCCC", size=12),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.01, xanchor="center", x=0.25,
        bgcolor="rgba(20,20,20,0.85)",
        bordercolor="rgba(255,255,255,0.15)", borderwidth=1,
    ),
    hovermode="x unified",
    hoverlabel=dict(bgcolor="#1A1A1A", font=dict(size=12, color=WHITE)),
    margin=dict(l=65, r=40, t=130, b=65),
    width=1440, height=920,
)

axis_style = dict(
    showgrid=True, gridcolor=GRID, zeroline=False,
    tickfont=dict(size=11), linecolor="rgba(255,255,255,0.08)",
)
fig.update_xaxes(**axis_style)
fig.update_yaxes(**axis_style)

fig.update_xaxes(title_text="Período completado (%)", row=1, col=1,
                 ticksuffix="%")
fig.update_yaxes(title_text="Capital / Capital inicial", row=1, col=1,
                 tickformat=".0f", ticksuffix="×")
fig.update_xaxes(title_text="Drawdown máximo (%)", row=1, col=2,
                 ticksuffix="%")
fig.update_yaxes(title_text="Frecuencia", row=1, col=2)
fig.update_xaxes(title_text="Capital final (miles $)", row=2, col=1,
                 tickformat="$,.0f")
fig.update_yaxes(title_text="Frecuencia", row=2, col=1)

for ann in fig.layout.annotations:
    if ann.text and not ann.text.startswith("<b>"):
        ann.font = dict(color="#999999", size=12)

# ── Guardar ────────────────────────────────────────────────────────────────────
fig.write_html(
    OUTPUT,
    include_plotlyjs="cdn",
    full_html=True,
    config={
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "displaylogo": False,
        "toImageButtonOptions": {
            "format": "png", "filename": "montecarlo_libertad2045",
            "height": 920, "width": 1440, "scale": 2,
        },
    },
)

print(f"\n✓ HTML guardado en: {OUTPUT}")
print(f"  Tamaño: {os.path.getsize(OUTPUT) / 1024:.0f} KB")
