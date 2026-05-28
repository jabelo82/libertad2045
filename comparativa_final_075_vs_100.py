"""
LIBERTAD_2045 — Comparativa Final ×0.75 vs ×1.00
==================================================
Parámetros canónicos en TODOS los tests:
    Capital inicial  : 4.000 €
    Aportación anual : 4.000 €/año
    Sin excepciones  — ningún test usa capital fijo arbitrario.

Fases:
  1. Carga datos desde caché 2005-2025 (una sola vez)
  2. Backtest completo 2005-2025 — mismo motor (backtest_exp40ter) para ×1.00 y ×0.75
  3. Stress tests: Crisis 2008, COVID 2020, Bear 2022 (4 k + aportes)
  4. Montecarlo: 1.000 sims bootstrap trade-level para ×1.00 y ×0.75
  5. Genera comparativa_final_075_vs_100.html + publica

Uso:
    source venv/bin/activate
    python comparativa_final_075_vs_100.py
"""

import warnings; warnings.filterwarnings("ignore")
import os, time, json, glob
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ─── Parámetros canónicos ─────────────────────────────────────────────────────
CAPITAL_INICIAL  = 4_000.0
APORTACION_ANUAL = 4_000.0
START_DATE       = "2005-01-01"
END_DATE         = "2025-12-31"
CACHE_START      = "2005-01-01"    # 766 archivos ya cacheados con este rango
CACHE_END        = "2025-12-31"
YEARS            = 21.0            # 2005-2025

N_SIMS           = 1_000
RUIN_THRESHOLD   = 0.50           # capital < 50 % del inicial = ruina
RNG              = np.random.default_rng(seed=42)

OUTPUT  = "comparativa_final_075_vs_100.html"
LOG_DIR = "backtest_results"

# Caché de trades para el backtest completo 2005-2025
CACHE_100 = f"{LOG_DIR}/mc100_2005_trades_cache.csv"
CACHE_075 = f"{LOG_DIR}/mc075_2005_trades_cache.csv"

CRISIS = {
    "Crisis 2008":  ("2007-01-01", "2009-12-31"),
    "COVID 2020":   ("2019-01-01", "2020-12-31"),
    "Bear 2022":    ("2021-01-01", "2022-12-31"),
}

# ─── Importar motor de backtest ───────────────────────────────────────────────
from backtest_exp40ter import (
    cargar_composicion_sp500,
    universo_historico_sp500,
    descargar_datos,
    calcular_metricas,
    calcular_indicadores,
    obtener_multiplicador_b1,
    detectar_senal,
    calcular_posicion,
    sp500_en_fecha,
    RISK_PERCENT,
    MAX_POSITION_PCT,
    MAX_POSITIONS,
    BUFFER,
    SALIDA_POR_CIERRE,
    RISK_MIN_CAPITAL,
    RISK_MAX_DRAWDOWN,
    REBALANCE_THRESHOLD,
    REBALANCE_MIN_SHARES,
)

print("=" * 70)
print("  LIBERTAD_2045 — Comparativa Final ×0.75 vs ×1.00")
print("  Parámetros canónicos: 4.000 € inicio + 4.000 €/año")
print("=" * 70)
t_global = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# 1. CARGA DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

print("\n[1/4] Cargando datos desde caché...")
comp_df  = cargar_composicion_sp500()
universo = universo_historico_sp500(comp_df)

cached_stems = {
    p.stem.rsplit(f"_{CACHE_START}_{CACHE_END}", 1)[0].replace("_", "-")
    for p in Path("data").glob(f"*_{CACHE_START}_{CACHE_END}.csv")
}
universo_filtrado = [t for t in universo if t in cached_stems or t.replace("-", "_") in {
    p.stem.rsplit(f"_{CACHE_START}_{CACHE_END}", 1)[0]
    for p in Path("data").glob(f"*_{CACHE_START}_{CACHE_END}.csv")
}]
print(f"  Universo cacheado: {len(universo_filtrado)} activos")
datos = descargar_datos(universo_filtrado, CACHE_START, CACHE_END)
print(f"  Datos cargados: {len(datos)} activos")


# ══════════════════════════════════════════════════════════════════════════════
# 2. BACKTEST COMPLETO 2005-2025 — mismo motor para ×1.00 y ×0.75
# ══════════════════════════════════════════════════════════════════════════════

print("\n[2/4] Backtest completo 2005-2025 (mismo motor: backtest_exp40ter)...")

def cargar_o_ejecutar_backtest_completo(factor):
    """
    Carga trades desde caché o ejecuta el backtest 2005-2025.
    Usa ejecutar_stress con el período completo — mismo motor que los stress tests,
    garantizando metodología idéntica para ×1.00 y ×0.75.
    """
    cache_path = CACHE_075 if factor == 0.75 else CACHE_100

    if os.path.exists(cache_path):
        df    = pd.read_csv(cache_path)
        cap_0 = df["capital"].iloc[0] - df["pnl"].iloc[0]
        if cap_0 < 10_000:
            print(f"  ×{factor:.2f}: cargando desde caché ({len(df)} trades, cap inicial ~{cap_0:,.0f}€)")
            trades        = df.to_dict("records")
            capital_final = float(df["capital"].iloc[-1])
            curva_capital = [{"fecha": r["fecha_salida"], "capital": r["capital"]} for r in trades]
            return trades, curva_capital, capital_final

    print(f"  ×{factor:.2f}: ejecutando backtest 2005-2025...", flush=True)
    t0 = time.time()
    # ejecutar_stress corre cualquier período con 4k+aportes — aquí el período completo
    trades, curva_capital, capital_final, _ = ejecutar_stress(
        datos, comp_df, factor, START_DATE, END_DATE
    )
    print(f"  ×{factor:.2f}: listo en {time.time()-t0:.0f}s  |  capital final: {capital_final:,.0f}€")
    os.makedirs(LOG_DIR, exist_ok=True)
    pd.DataFrame(trades).to_csv(cache_path, index=False)
    print(f"  ×{factor:.2f}: trades guardados en {cache_path}")
    return trades, curva_capital, capital_final

# Las llamadas al backtest completo se realizan más abajo,
# después de que ejecutar_stress está definida (sección 3).


# ══════════════════════════════════════════════════════════════════════════════
# 3. STRESS TESTS — 4.000 € + aportaciones anuales
# ══════════════════════════════════════════════════════════════════════════════

print("\n[3/4] Stress tests (3 crisis × 2 factores)...")

def ejecutar_stress(datos, comp_df, factor, period_start, period_end):
    """
    Backtest sobre período de crisis con:
      - Capital inicial:  CAPITAL_INICIAL (4.000 €)
      - Aportación anual: APORTACION_ANUAL al primer día de cada año nuevo
      - Factor trailing stop: factor (0.75 o 1.00)
    Los indicadores se calculan sobre la serie completa 2006-2025 (warm-up correcto).
    """
    start_ts = pd.Timestamp(period_start)
    end_ts   = pd.Timestamp(period_end)

    fechas = sorted({
        f for df in datos.values() for f in df.index
        if start_ts <= f <= end_ts
    })
    if not fechas:
        return [], [], CAPITAL_INICIAL, {}

    capital       = CAPITAL_INICIAL
    capital_pico  = CAPITAL_INICIAL
    posiciones    = {}
    trades        = []
    curva_capital = []
    capital_por_año = {}

    for idx, fecha in enumerate(fechas):

        # Aportación anual: primer día de trading de cada año nuevo dentro del período
        if idx > 0 and fecha.year > fechas[idx - 1].year:
            capital += APORTACION_ANUAL
            if capital > capital_pico:
                capital_pico = capital

        if capital > capital_pico:
            capital_pico = capital

        # Registrar capital al cierre del año anterior
        if idx > 0 and fecha.year != fechas[idx - 1].year:
            año_ant = fechas[idx - 1].year
            if año_ant not in capital_por_año:
                capital_por_año[año_ant] = round(capital, 2)

        # Risk guardian: capital mínimo
        if capital < RISK_MIN_CAPITAL:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Gestionar posiciones con trailing stop escalado por factor
        cerradas = []
        for symbol, pos in posiciones.items():
            if symbol not in datos:
                continue
            df = datos[symbol]
            if fecha not in df.index:
                continue
            bar      = df.loc[fecha]
            atr      = df.loc[fecha, "ATR"]
            i_actual = df.index.get_loc(fecha)

            if not pd.isna(atr) and atr > 0:
                mult_b1    = obtener_multiplicador_b1(df, i_actual)
                nuevo_stop = round(bar["High"] - atr * mult_b1 * factor, 2)
                if nuevo_stop > pos["stop"]:
                    pos["stop"] = nuevo_stop
                be_stop = round(pos["entry"] + 0.5 * atr, 2)
                if bar["Close"] >= pos["entry"] + 1.5 * atr and be_stop > pos["stop"]:
                    pos["stop"] = be_stop

            precio_ref = bar["Close"] if SALIDA_POR_CIERRE else bar["Low"]
            if precio_ref <= pos["stop"]:
                pnl      = (pos["stop"] - pos["entry"]) * pos["shares"]
                capital += pnl
                trades.append({
                    "symbol"       : symbol,
                    "fecha_entrada": str(pos["fecha_entrada"]),
                    "fecha_salida" : str(fecha),
                    "entrada"      : round(pos["entry"], 4),
                    "salida"       : round(pos["stop"], 4),
                    "shares"       : pos["shares"],
                    "pnl"          : round(pnl, 2),
                    "resultado"    : "LOSS" if pnl < 0 else "WIN",
                    "capital"      : round(capital, 2),
                })
                cerradas.append(symbol)

        for s in cerradas:
            del posiciones[s]

        # Risk guardian: drawdown máximo
        dd_actual = (capital_pico - capital) / capital_pico if capital_pico > 0 else 0
        if dd_actual > RISK_MAX_DRAWDOWN:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Rebalanceo dinámico
        for symbol in list(posiciones.keys()):
            if symbol not in datos:
                continue
            df_reb = datos[symbol]
            if fecha not in df_reb.index:
                continue
            pos    = posiciones[symbol]
            i_reb  = df_reb.index.get_loc(fecha)
            precio = df_reb.iloc[i_reb]["Close"]
            if pd.isna(precio) or precio <= 0:
                continue
            shares_actual = pos["shares"]
            limite_valor  = capital * MAX_POSITION_PCT
            if capital > 0 and shares_actual * precio > limite_valor:
                shares_lim = int(limite_valor / precio)
                if abs(shares_lim - shares_actual) >= REBALANCE_MIN_SHARES and shares_lim > 0:
                    capital += (precio - pos["entry"]) * (shares_actual - shares_lim)
                    posiciones[symbol]["shares"] = shares_lim
                    continue
            shares_opt, _, _ = calcular_posicion(df_reb, i_reb, capital)
            if shares_opt <= 0:
                continue
            desv = (shares_actual - shares_opt) / shares_opt
            if abs(desv) <= REBALANCE_THRESHOLD:
                continue
            delta = shares_opt - shares_actual
            if abs(delta) < REBALANCE_MIN_SHARES:
                continue
            if delta < 0:
                capital += (precio - pos["entry"]) * (-delta)
                posiciones[symbol]["shares"] = shares_opt
            else:
                eb = (pos["entry"] * shares_actual + precio * delta) / shares_opt
                posiciones[symbol]["shares"] = shares_opt
                posiciones[symbol]["entry"]  = round(eb, 4)

        # Portfolio lleno
        if len(posiciones) >= MAX_POSITIONS:
            curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})
            continue

        # Escanear señales
        señales   = []
        sp500_hoy = sp500_en_fecha(comp_df, fecha)

        for symbol in datos:
            if symbol in posiciones:
                continue
            if sp500_hoy is not None and symbol not in sp500_hoy:
                continue
            df = datos[symbol]
            if fecha not in df.index:
                continue
            i = df.index.get_loc(fecha)
            if i < 200:
                continue
            if not detectar_senal(df, i):
                continue
            shares, stop_dist, atr = calcular_posicion(df, i, capital)
            if shares <= 0:
                continue
            bar = df.iloc[i]
            _sma5   = df.iloc[i-5]["SMA200"] if i >= 6 else float("nan")
            _slope  = (bar["SMA200"] - _sma5) / bar["ATR"] if not pd.isna(_sma5) else 0.0
            score   = (bar["Close"] - bar["SMA50"]) / bar["ATR"] + _slope
            señales.append({
                "symbol": symbol, "score": score,
                "shares": shares, "stop_distance": stop_dist,
                "high": bar["High"], "atr": atr,
            })

        señales = sorted(señales, key=lambda x: x["score"], reverse=True)
        slots   = MAX_POSITIONS - len(posiciones)
        señales = señales[:slots]

        # Abrir posiciones al día siguiente
        if idx + 1 < len(fechas):
            fecha_entrada = fechas[idx + 1]
            for señal in señales:
                symbol    = señal["symbol"]
                buy_stop  = round(señal["high"] + BUFFER, 4)
                stop_loss = round(buy_stop - señal["stop_distance"], 4)
                if stop_loss <= 0:
                    continue
                df = datos[symbol]
                if fecha_entrada not in df.index:
                    continue
                if df.loc[fecha_entrada, "High"] >= buy_stop:
                    if buy_stop * señal["shares"] > capital:
                        continue
                    posiciones[symbol] = {
                        "entry"        : buy_stop,
                        "stop"         : stop_loss,
                        "shares"       : señal["shares"],
                        "fecha_entrada": fecha_entrada,
                    }

        curva_capital.append({"fecha": fecha, "capital": round(capital, 2)})

    # Registrar último año
    if fechas:
        ultimo_año = fechas[-1].year
        if ultimo_año not in capital_por_año:
            capital_por_año[ultimo_año] = round(capital, 2)

    # Cerrar posiciones abiertas al final del período
    for symbol, pos in posiciones.items():
        df = datos[symbol]
        if df.empty:
            continue
        ultimos = df[df.index <= end_ts]
        if ultimos.empty:
            continue
        uc    = float(ultimos.iloc[-1]["Close"])
        pnl   = (uc - pos["entry"]) * pos["shares"]
        capital += pnl
        trades.append({
            "symbol"       : symbol,
            "fecha_entrada": str(pos["fecha_entrada"]),
            "fecha_salida" : str(ultimos.index[-1]),
            "entrada"      : round(pos["entry"], 4),
            "salida"       : round(uc, 4),
            "shares"       : pos["shares"],
            "pnl"          : round(pnl, 2),
            "resultado"    : "OPEN→CLOSE",
            "capital"      : round(capital, 2),
        })

    return trades, curva_capital, capital, capital_por_año


def metricas_stress(trades, curva_capital, capital_final, capital_por_año):
    dt = pd.DataFrame(trades) if trades else pd.DataFrame()
    dc = pd.DataFrame(curva_capital) if curva_capital else pd.DataFrame()

    total = len(dt)
    pf    = 0.0
    wr    = 0.0
    if total > 0:
        wins   = dt[(dt["resultado"] == "WIN") | ((dt["resultado"] == "OPEN→CLOSE") & (dt["pnl"] >= 0))]
        losses = dt[(dt["resultado"] == "LOSS") | ((dt["resultado"] == "OPEN→CLOSE") & (dt["pnl"] < 0))]
        wr     = len(wins) / total
        g      = wins["pnl"].sum()   if len(wins)   > 0 else 0
        p      = losses["pnl"].abs().sum() if len(losses) > 0 else 1
        pf     = g / p if p > 0 else float("inf")

    max_dd = 0.0
    if not dc.empty:
        caps = dc["capital"].values
        pico = caps[0]
        for c in caps:
            if c > pico: pico = c
            dd = (pico - c) / pico if pico > 0 else 0
            if dd > max_dd: max_dd = dd

    retorno = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL

    # Años con rentabilidad negativa (año-a-año dentro del período)
    años_neg = []
    años     = sorted(capital_por_año.keys())
    cap_prev = CAPITAL_INICIAL
    for año in años:
        cap_fin = capital_por_año[año]
        if cap_fin < cap_prev:
            años_neg.append((año, (cap_fin - cap_prev) / cap_prev))
        cap_prev = cap_fin

    return {
        "capital_final" : round(capital_final, 2),
        "retorno_pct"   : retorno,
        "profit_factor" : round(pf, 4),
        "win_rate"      : wr,
        "max_drawdown"  : max_dd,
        "total_trades"  : total,
        "años_negativos": años_neg,
    }


# ── Ejecutar backtest completo 2005-2025 ─────────────────────────────────────
trades_100, curva_100, cap_final_100 = cargar_o_ejecutar_backtest_completo(1.00)
trades_075, curva_075, cap_final_075 = cargar_o_ejecutar_backtest_completo(0.75)
metricas_100 = calcular_metricas(trades_100, curva_100, cap_final_100)
metricas_075 = calcular_metricas(trades_075, curva_075, cap_final_075)
print(f"\n  Backtest ×1.00: capital {cap_final_100:,.0f}€  PF={metricas_100['profit_factor']:.3f}"
      f"  WR={metricas_100['win_rate']:.1%}  DD={metricas_100['max_drawdown']:.1%}"
      f"  Trades={metricas_100['total_trades']}")
print(f"  Backtest ×0.75: capital {cap_final_075:,.0f}€  PF={metricas_075['profit_factor']:.3f}"
      f"  WR={metricas_075['win_rate']:.1%}  DD={metricas_075['max_drawdown']:.1%}"
      f"  Trades={metricas_075['total_trades']}")


stress_resultados = {}
for nombre, (p_start, p_end) in CRISIS.items():
    for factor in [1.00, 0.75]:
        t0 = time.time()
        tr, curva, cap, cap_año = ejecutar_stress(datos, comp_df, factor, p_start, p_end)
        m  = metricas_stress(tr, curva, cap, cap_año)
        stress_resultados[(nombre, factor)] = m
        años_str = ", ".join(f"{a}({v:+.1%})" for a, v in m["años_negativos"]) or "—"
        print(f"  {nombre} ×{factor:.2f}: {cap:>12,.0f}€  "
              f"ret={m['retorno_pct']:>+7.1%}  DD={m['max_drawdown']:.1%}  "
              f"PF={m['profit_factor']:.3f}  WR={m['win_rate']:.1%}  "
              f"Trades={m['total_trades']}  Años neg: {años_str}  ({time.time()-t0:.0f}s)")


# ══════════════════════════════════════════════════════════════════════════════
# 4. MONTECARLO — bootstrap trade-level, 1.000 sims, capital 4.000 €
# ══════════════════════════════════════════════════════════════════════════════

print("\n[4/4] Montecarlo (1.000 sims × 2 factores)...")

def ejecutar_montecarlo(trades_list, label):
    """Bootstrap trade-level: muestrea retornos con reposición desde 4.000 €."""
    df = pd.DataFrame(trades_list)
    df = df[df["resultado"] != "OPEN→CLOSE"].copy()
    df["cap_antes"] = df["capital"] - df["pnl"]
    df = df[df["cap_antes"] > 0]
    df["r"] = df["pnl"] / df["cap_antes"]
    returns  = df["r"].values
    n_trades = len(returns)

    final_caps = np.empty(N_SIMS)
    max_dds    = np.empty(N_SIMS)
    ruin_hits  = np.zeros(N_SIMS, dtype=bool)
    ruin_level = CAPITAL_INICIAL * RUIN_THRESHOLD

    for i in range(N_SIMS):
        sample = RNG.choice(returns, size=n_trades, replace=True)
        eq     = np.empty(n_trades + 1)
        eq[0]  = CAPITAL_INICIAL
        for j in range(n_trades):
            eq[j+1] = eq[j] * (1.0 + sample[j])
        final_caps[i] = eq[-1]
        peak = np.maximum.accumulate(eq)
        dds  = (eq - peak) / peak
        max_dds[i] = dds.min()
        if (eq < ruin_level).any():
            ruin_hits[i] = True

    p5, p25, p50, p75, p95 = np.percentile(final_caps, [5, 25, 50, 75, 95])
    dd_abs = -max_dds
    dd_p50 = np.percentile(dd_abs, 50)
    dd_p95 = np.percentile(dd_abs, 95)
    ruin   = ruin_hits.mean()

    result = {
        "n_trades": n_trades,
        "r_mean"  : returns.mean(),
        "r_std"   : returns.std(),
        "p5"      : p5,   "p25": p25, "p50": p50,
        "p75"     : p75,  "p95": p95,
        "dd_p50"  : dd_p50, "dd_p95": dd_p95,
        "ruin_prob": ruin,
        "all_final": final_caps.tolist(),
        "all_dd"   : dd_abs.tolist(),
    }
    print(f"  MC ×{label}: n_trades={n_trades}  p5={p5/1000:.0f}k  p50={p50/1000:.0f}k  "
          f"p95={p95/1000:.0f}k  DD_p95={dd_p95:.1%}  Ruina={ruin:.2%}")
    return result


mc_100 = ejecutar_montecarlo(trades_100, "1.00")
mc_075 = ejecutar_montecarlo(trades_075, "0.75")


# ══════════════════════════════════════════════════════════════════════════════
# 5. GENERAR HTML
# ══════════════════════════════════════════════════════════════════════════════

print(f"\nGenerando {OUTPUT}...")
fecha_gen = datetime.now().strftime("%Y-%m-%d %H:%M")

def fmt_cap(v):
    """Formatea capital de forma legible."""
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M€"
    if v >= 1_000:
        return f"{v/1_000:.1f}k€"
    return f"{v:.0f}€"

def fmt_pct(v, decimals=1):
    return f"{v*100:.{decimals}f}%"

def delta_cls(val, ref, better="higher"):
    """CSS class: 'hi' si val es mejor que ref, '' si igual, 'lo' si peor."""
    if better == "higher":
        return "hi" if val > ref * 1.001 else ("lo" if val < ref * 0.999 else "")
    else:
        return "hi" if val < ref * 0.999 else ("lo" if val > ref * 1.001 else "")

def delta_str(val, ref, pct=True):
    d = val - ref
    if pct:
        return f"{'+' if d>=0 else ''}{d*100:.1f}pp"
    return f"{'+' if d>=0 else ''}{d/1000:.1f}k€"

# ── Histogramas para MC ────────────────────────────────────────────────────
def histograma(values, bins=40):
    counts, edges = np.histogram(values, bins=bins)
    return counts.tolist(), edges.tolist()

hist100_cap, edges100_cap = histograma([v/1000 for v in mc_100["all_final"]])
hist075_cap, edges075_cap = histograma([v/1000 for v in mc_075["all_final"]])
hist100_dd,  edges100_dd  = histograma([v*100 for v in mc_100["all_dd"]])
hist075_dd,  edges075_dd  = histograma([v*100 for v in mc_075["all_dd"]])

chart_data = {
    "h100_cap": hist100_cap, "e100_cap": edges100_cap,
    "h075_cap": hist075_cap, "e075_cap": edges075_cap,
    "h100_dd" : hist100_dd,  "e100_dd" : edges100_dd,
    "h075_dd" : hist075_dd,  "e075_dd" : edges075_dd,
}

# ── CAGR helper ────────────────────────────────────────────────────────────
def cagr(capital_final, capital_inicial, years):
    return (capital_final / capital_inicial) ** (1.0 / years) - 1

YEARS = 21.0   # 2005-2025

# ── Stress veredicto ───────────────────────────────────────────────────────
def veredicto_stress(r075, r100):
    dd_ok  = r075["max_drawdown"] <= r100["max_drawdown"] + 0.005
    cap_ok = r075["capital_final"] >= r100["capital_final"] * 0.98
    if dd_ok and cap_ok:
        return "✅ Mejor", "hi"
    if dd_ok or cap_ok:
        return "⚠️ Mixto", ""
    return "❌ Peor", "lo"

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>LIBERTAD_2045 — Comparativa Final ×0.75 vs ×1.00</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:#080c14;--card:#0f1624;--bdr:#1e293b;--txt:#e2e8f0;--muted:#64748b;
  --blue:#3b82f6;--green:#10b981;--gold:#f59e0b;--red:#ef4444;--purple:#a78bfa;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);font-family:'Courier New',monospace;font-size:13px;
     padding:20px;max-width:1440px;margin:0 auto;line-height:1.7}}
.hdr{{text-align:center;padding:40px 20px 28px;border-bottom:1px solid var(--bdr);margin-bottom:36px}}
.hdr h1{{font-size:22px;font-weight:700;letter-spacing:3px;color:var(--gold);margin-bottom:6px}}
.hdr .sub{{color:var(--muted);font-size:11px;letter-spacing:1px}}
.hdr .params{{margin-top:14px;display:inline-flex;gap:20px;background:var(--card);
              border:1px solid var(--bdr);border-radius:8px;padding:10px 24px}}
.hdr .param{{font-size:12px;color:var(--txt)}}
.hdr .param span{{color:var(--gold);font-weight:700}}
.sec{{margin-bottom:50px}}
.sec-hdr{{display:flex;align-items:baseline;gap:14px;margin-bottom:18px;
         padding-bottom:10px;border-bottom:1px solid var(--bdr)}}
.sec-title{{font-size:14px;font-weight:700;color:var(--blue);letter-spacing:2px;text-transform:uppercase}}
.sec-sub{{font-size:11px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse}}
th{{background:#0b1220;color:var(--muted);padding:9px 14px;text-align:right;
   font-size:10px;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid var(--bdr)}}
th:first-child,th.left{{text-align:left}}
td{{padding:9px 14px;text-align:right;border-bottom:1px solid var(--bdr);color:var(--txt)}}
td:first-child,td.left{{text-align:left;color:var(--muted)}}
tr:last-child td{{border-bottom:none}}
.hi{{color:var(--green);font-weight:700}}
.lo{{color:var(--red)}}
.neutral{{color:var(--muted)}}
.factor-075{{color:var(--purple);font-weight:700}}
.factor-100{{color:var(--blue);font-weight:700}}
.tbl-wrap{{overflow-x:auto;border:1px solid var(--bdr);border-radius:8px;margin-bottom:16px}}
.chart-box{{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:18px;margin-bottom:20px}}
.chart-box canvas{{max-height:320px}}
.chart-title{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:14px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
.crisis-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}}
.crisis-card{{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:16px}}
.crisis-title{{font-size:11px;color:var(--gold);letter-spacing:1px;font-weight:700;
              text-transform:uppercase;margin-bottom:12px;padding-bottom:8px;
              border-bottom:1px solid var(--bdr)}}
.verdict-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:28px}}
.vcard{{background:var(--card);border:1px solid var(--bdr);border-radius:8px;
        padding:16px;text-align:center}}
.vcard .v{{font-size:22px;font-weight:700;margin-bottom:4px}}
.vcard .l{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700}}
.badge-green{{background:rgba(16,185,129,.15);color:var(--green);border:1px solid rgba(16,185,129,.3)}}
.badge-red{{background:rgba(239,68,68,.15);color:var(--red);border:1px solid rgba(239,68,68,.3)}}
.badge-yellow{{background:rgba(245,158,11,.15);color:var(--gold);border:1px solid rgba(245,158,11,.3)}}
.final-verdict{{background:linear-gradient(135deg,rgba(245,158,11,.10),rgba(245,158,11,.03));
               border:2px solid var(--gold);border-radius:12px;padding:36px;
               text-align:center;margin-top:36px}}
.fv-title{{font-size:20px;font-weight:700;color:var(--gold);letter-spacing:4px;margin-bottom:8px}}
.fv-sub{{font-size:12px;color:var(--muted);margin-bottom:20px}}
.fv-grid{{display:inline-grid;grid-template-columns:repeat(3,1fr);
          border:1px solid rgba(245,158,11,.2);border-radius:8px;overflow:hidden;
          max-width:700px;margin-bottom:18px}}
.fvk{{padding:16px 24px;border-right:1px solid rgba(245,158,11,.15)}}
.fvk:last-child{{border-right:none}}
.fvkv{{font-size:20px;font-weight:700;color:var(--gold);margin-bottom:2px}}
.fvkl{{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.note{{font-size:11px;color:var(--muted);padding:6px 4px;margin-top:8px}}
.foot{{text-align:center;padding:20px 0;border-top:1px solid var(--bdr);
       margin-top:36px;color:var(--muted);font-size:10px;letter-spacing:1px}}
@media(max-width:900px){{.crisis-grid{{grid-template-columns:1fr}}.grid2{{grid-template-columns:1fr}}
  .verdict-row{{grid-template-columns:repeat(2,1fr)}}.fv-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<header class="hdr">
  <h1>LIBERTAD_2045 — COMPARATIVA FINAL ×0.75 vs ×1.00</h1>
  <div class="sub">Backtest completo · Stress tests · Montecarlo · {fecha_gen}</div>
  <div class="params">
    <div class="param">Capital inicial <span>4.000 €</span></div>
    <div class="param">Aportación anual <span>4.000 €</span></div>
    <div class="param">Período <span>2005 – 2025</span></div>
    <div class="param">Metodología MC <span>Bootstrap trade-level</span></div>
    <div class="param">Simulaciones MC <span>{N_SIMS:,}</span></div>
  </div>
</header>

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- SECCIÓN 1: RESUMEN EJECUTIVO                                          -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Resumen Ejecutivo</span>
    <span class="sec-sub">Backtest completo 2005-2025 — 4.000 € + 4.000 €/año</span>
  </div>
  <div class="verdict-row">
    <div class="vcard">
      <div class="v factor-100">{fmt_cap(cap_final_100)}</div>
      <div class="l">Capital final ×1.00</div>
    </div>
    <div class="vcard">
      <div class="v factor-075">{fmt_cap(cap_final_075)}</div>
      <div class="l">Capital final ×0.75</div>
    </div>
    <div class="vcard">
      <div class="v {'hi' if metricas_075['max_drawdown'] < metricas_100['max_drawdown'] else 'lo'}">{fmt_pct(metricas_075['max_drawdown'])} vs {fmt_pct(metricas_100['max_drawdown'])}</div>
      <div class="l">DD máx (×0.75 vs ×1.00)</div>
    </div>
    <div class="vcard">
      <div class="v {'hi' if mc_075['ruin_prob'] <= mc_100['ruin_prob'] else 'lo'}">{mc_075['ruin_prob']:.2%} vs {mc_100['ruin_prob']:.2%}</div>
      <div class="l">Prob. ruina MC (×0.75 vs ×1.00)</div>
    </div>
  </div>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th class="left">Métrica</th>
          <th>×1.00 (baseline)</th>
          <th>×0.75</th>
          <th>Δ</th>
          <th>Veredicto ×0.75</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="left">Capital final</td>
          <td class="factor-100">{fmt_cap(cap_final_100)}</td>
          <td class="factor-075">{fmt_cap(cap_final_075)}</td>
          <td class="{delta_cls(cap_final_075, cap_final_100)}">{'+' if cap_final_075>=cap_final_100 else ''}{(cap_final_075-cap_final_100)/cap_final_100*100:.0f}%</td>
          <td><span class="badge {'badge-green' if cap_final_075 >= cap_final_100*0.95 else 'badge-red'}">{'' if cap_final_075>=cap_final_100 else ''} {'↑ Mejor' if cap_final_075>cap_final_100 else '≈ Similar' if cap_final_075>=cap_final_100*0.95 else '↓ Inferior'}</span></td>
        </tr>
        <tr>
          <td class="left">CAGR (21 años)</td>
          <td class="factor-100">{cagr(cap_final_100,CAPITAL_INICIAL,YEARS):.1%}</td>
          <td class="factor-075">{cagr(cap_final_075,CAPITAL_INICIAL,YEARS):.1%}</td>
          <td class="{delta_cls(cagr(cap_final_075,CAPITAL_INICIAL,YEARS), cagr(cap_final_100,CAPITAL_INICIAL,YEARS))}">{delta_str(cagr(cap_final_075,CAPITAL_INICIAL,YEARS), cagr(cap_final_100,CAPITAL_INICIAL,YEARS))}</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">Profit Factor</td>
          <td>{metricas_100['profit_factor']:.3f}</td>
          <td>{metricas_075['profit_factor']:.3f}</td>
          <td class="{delta_cls(metricas_075['profit_factor'], metricas_100['profit_factor'])}">{metricas_075['profit_factor']-metricas_100['profit_factor']:+.3f}</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">Win Rate</td>
          <td>{metricas_100['win_rate']:.1%}</td>
          <td>{metricas_075['win_rate']:.1%}</td>
          <td class="{delta_cls(metricas_075['win_rate'], metricas_100['win_rate'])}">{delta_str(metricas_075['win_rate'], metricas_100['win_rate'])}</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">Drawdown máximo</td>
          <td>{metricas_100['max_drawdown']:.1%}</td>
          <td class="{delta_cls(metricas_075['max_drawdown'], metricas_100['max_drawdown'], 'lower')}">{metricas_075['max_drawdown']:.1%}</td>
          <td class="{delta_cls(metricas_075['max_drawdown'], metricas_100['max_drawdown'], 'lower')}">{delta_str(metricas_075['max_drawdown'], metricas_100['max_drawdown'])}</td>
          <td><span class="badge {'badge-green' if metricas_075['max_drawdown'] < metricas_100['max_drawdown'] else 'badge-yellow'}">{'↓ Mejor DD' if metricas_075['max_drawdown'] < metricas_100['max_drawdown'] else '≈ Similar'}</span></td>
        </tr>
        <tr>
          <td class="left">Total trades</td>
          <td>{metricas_100['total_trades']}</td>
          <td>{metricas_075['total_trades']}</td>
          <td class="neutral">{metricas_075['total_trades']-metricas_100['total_trades']:+d}</td>
          <td></td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="note">* Capital inicial 4.000 € + 4.000 €/año (2005-2025). ×1.00 y ×0.75 = mismo motor backtest_exp40ter.py. Factor aplicado al trailing stop ATR.</div>
</div>

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- SECCIÓN 2: STRESS TESTS                                               -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Stress Tests</span>
    <span class="sec-sub">3 períodos de crisis · Capital 4.000 € + 4.000 €/año · Mismo motor para ×1.00 y ×0.75</span>
  </div>

  <div class="crisis-grid">"""

# Tarjetas de stress por crisis
for nombre_crisis, (p_start, p_end) in CRISIS.items():
    m100 = stress_resultados[(nombre_crisis, 1.00)]
    m075 = stress_resultados[(nombre_crisis, 0.75)]
    vtext, vcls = veredicto_stress(m075, m100)
    años_neg_100 = ", ".join(f"{a}({v:+.1%})" for a, v in m100["años_negativos"]) or "ninguno"
    años_neg_075 = ", ".join(f"{a}({v:+.1%})" for a, v in m075["años_negativos"]) or "ninguno"

    html += f"""
    <div class="crisis-card">
      <div class="crisis-title">{nombre_crisis}<br><span style="font-size:10px;color:var(--muted)">{p_start} → {p_end}</span></div>
      <table>
        <thead>
          <tr><th class="left">Métrica</th><th>×1.00</th><th>×0.75</th></tr>
        </thead>
        <tbody>
          <tr>
            <td class="left">Capital final</td>
            <td class="factor-100">{fmt_cap(m100['capital_final'])}</td>
            <td class="{delta_cls(m075['capital_final'], m100['capital_final'])} factor-075">{fmt_cap(m075['capital_final'])}</td>
          </tr>
          <tr>
            <td class="left">Retorno período</td>
            <td class="{'hi' if m100['retorno_pct']>=0 else 'lo'}">{m100['retorno_pct']:+.1%}</td>
            <td class="{'hi' if m075['retorno_pct']>=0 else 'lo'}">{m075['retorno_pct']:+.1%}</td>
          </tr>
          <tr>
            <td class="left">Drawdown máx.</td>
            <td>{m100['max_drawdown']:.1%}</td>
            <td class="{delta_cls(m075['max_drawdown'], m100['max_drawdown'], 'lower')}">{m075['max_drawdown']:.1%}</td>
          </tr>
          <tr>
            <td class="left">Profit Factor</td>
            <td>{m100['profit_factor']:.3f}</td>
            <td class="{delta_cls(m075['profit_factor'], m100['profit_factor'])}">{m075['profit_factor']:.3f}</td>
          </tr>
          <tr>
            <td class="left">Win Rate</td>
            <td>{m100['win_rate']:.1%}</td>
            <td>{m075['win_rate']:.1%}</td>
          </tr>
          <tr>
            <td class="left">Trades</td>
            <td>{m100['total_trades']}</td>
            <td>{m075['total_trades']}</td>
          </tr>
          <tr>
            <td class="left" style="font-size:10px">Años neg. ×1.00</td>
            <td colspan="2" style="text-align:left;font-size:10px;color:var(--muted)">{años_neg_100}</td>
          </tr>
          <tr>
            <td class="left" style="font-size:10px">Años neg. ×0.75</td>
            <td colspan="2" style="text-align:left;font-size:10px;color:var(--muted)">{años_neg_075}</td>
          </tr>
          <tr>
            <td class="left" style="font-weight:700">Veredicto ×0.75</td>
            <td colspan="2" style="text-align:center"><span class="badge {'badge-green' if vcls=='hi' else 'badge-yellow' if vcls=='' else 'badge-red'}">{vtext}</span></td>
          </tr>
        </tbody>
      </table>
    </div>"""

html += """
  </div>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th class="left">Crisis</th>
          <th>Período</th>
          <th>×1.00 Capital</th>
          <th>×0.75 Capital</th>
          <th>Δ Capital</th>
          <th>×1.00 DD</th>
          <th>×0.75 DD</th>
          <th>Δ DD</th>
          <th>Veredicto ×0.75</th>
        </tr>
      </thead>
      <tbody>"""

for nombre_crisis, (p_start, p_end) in CRISIS.items():
    m100 = stress_resultados[(nombre_crisis, 1.00)]
    m075 = stress_resultados[(nombre_crisis, 0.75)]
    vtext, vcls = veredicto_stress(m075, m100)
    dc = m075['capital_final'] - m100['capital_final']
    ddd = m075['max_drawdown'] - m100['max_drawdown']
    html += f"""
        <tr>
          <td class="left">{nombre_crisis}</td>
          <td class="neutral">{p_start[:4]}–{p_end[:4]}</td>
          <td>{fmt_cap(m100['capital_final'])}</td>
          <td class="{delta_cls(m075['capital_final'],m100['capital_final'])}">{fmt_cap(m075['capital_final'])}</td>
          <td class="{delta_cls(m075['capital_final'],m100['capital_final'])}">{'+' if dc>=0 else ''}{fmt_cap(abs(dc)) if abs(dc)>=100 else f'{dc:.0f}€'}</td>
          <td>{m100['max_drawdown']:.1%}</td>
          <td class="{delta_cls(m075['max_drawdown'],m100['max_drawdown'],'lower')}">{m075['max_drawdown']:.1%}</td>
          <td class="{delta_cls(m075['max_drawdown'],m100['max_drawdown'],'lower')}">{ddd*100:+.1f}pp</td>
          <td><span class="badge {'badge-green' if vcls=='hi' else 'badge-yellow' if vcls=='' else 'badge-red'}">{vtext}</span></td>
        </tr>"""

html += f"""
      </tbody>
    </table>
  </div>
  <div class="note">* Capital inicial 4.000 € + 4.000 €/año durante el período de crisis. Motor idéntico para ×1.00 y ×0.75 (solo varía el multiplicador del trailing stop).</div>
</div>

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- SECCIÓN 3: MONTECARLO                                                 -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->
<div class="sec">
  <div class="sec-hdr">
    <span class="sec-title">Análisis Montecarlo</span>
    <span class="sec-sub">{N_SIMS:,} simulaciones bootstrap trade-level · Capital inicio 4.000 € · Misma metodología para ×1.00 y ×0.75</span>
  </div>

  <div class="tbl-wrap" style="margin-bottom:20px">
    <table>
      <thead>
        <tr>
          <th class="left">Métrica</th>
          <th>×1.00 (baseline)</th>
          <th>×0.75</th>
          <th>Δ</th>
          <th>Veredicto ×0.75</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="left">Prob. ruina (&lt;{CAPITAL_INICIAL*RUIN_THRESHOLD:,.0f}€)</td>
          <td>{mc_100['ruin_prob']:.2%}</td>
          <td class="{delta_cls(mc_075['ruin_prob'],mc_100['ruin_prob'],'lower')}">{mc_075['ruin_prob']:.2%}</td>
          <td class="{delta_cls(mc_075['ruin_prob'],mc_100['ruin_prob'],'lower')}">{(mc_075['ruin_prob']-mc_100['ruin_prob'])*100:+.2f}pp</td>
          <td><span class="badge {'badge-green' if mc_075['ruin_prob']<=mc_100['ruin_prob'] else 'badge-red'}">{'≤ Igual' if mc_075['ruin_prob']<=mc_100['ruin_prob'] else '↑ Mayor'}</span></td>
        </tr>
        <tr>
          <td class="left">Capital p5</td>
          <td>{fmt_cap(mc_100['p5'])}</td>
          <td class="{delta_cls(mc_075['p5'],mc_100['p5'])}">{fmt_cap(mc_075['p5'])}</td>
          <td class="{delta_cls(mc_075['p5'],mc_100['p5'])}">{(mc_075['p5']-mc_100['p5'])/mc_100['p5']*100:+.0f}%</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">Capital p50 (mediana)</td>
          <td>{fmt_cap(mc_100['p50'])}</td>
          <td class="{delta_cls(mc_075['p50'],mc_100['p50'])}">{fmt_cap(mc_075['p50'])}</td>
          <td class="{delta_cls(mc_075['p50'],mc_100['p50'])}">{(mc_075['p50']-mc_100['p50'])/mc_100['p50']*100:+.0f}%</td>
          <td><span class="badge {'badge-green' if mc_075['p50']>=mc_100['p50']*0.95 else 'badge-yellow'}">{'↑ Similar+' if mc_075['p50']>=mc_100['p50'] else '≈ Similar' if mc_075['p50']>=mc_100['p50']*0.95 else '↓ Menor'}</span></td>
        </tr>
        <tr>
          <td class="left">Capital p95</td>
          <td>{fmt_cap(mc_100['p95'])}</td>
          <td class="{delta_cls(mc_075['p95'],mc_100['p95'])}">{fmt_cap(mc_075['p95'])}</td>
          <td class="{delta_cls(mc_075['p95'],mc_100['p95'])}">{(mc_075['p95']-mc_100['p95'])/mc_100['p95']*100:+.0f}%</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">DD máx p50</td>
          <td>{mc_100['dd_p50']:.1%}</td>
          <td class="{delta_cls(mc_075['dd_p50'],mc_100['dd_p50'],'lower')}">{mc_075['dd_p50']:.1%}</td>
          <td class="{delta_cls(mc_075['dd_p50'],mc_100['dd_p50'],'lower')}">{(mc_075['dd_p50']-mc_100['dd_p50'])*100:+.1f}pp</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">DD máx p95</td>
          <td>{mc_100['dd_p95']:.1%}</td>
          <td class="{delta_cls(mc_075['dd_p95'],mc_100['dd_p95'],'lower')}">{mc_075['dd_p95']:.1%}</td>
          <td class="{delta_cls(mc_075['dd_p95'],mc_100['dd_p95'],'lower')}">{(mc_075['dd_p95']-mc_100['dd_p95'])*100:+.1f}pp</td>
          <td><span class="badge {'badge-green' if mc_075['dd_p95']<mc_100['dd_p95'] else 'badge-yellow'}">{'↓ Mejor DD' if mc_075['dd_p95']<mc_100['dd_p95'] else '≈ Similar'}</span></td>
        </tr>
        <tr>
          <td class="left">Retorno medio por trade</td>
          <td>{mc_100['r_mean']:.4%}</td>
          <td>{mc_075['r_mean']:.4%}</td>
          <td class="{delta_cls(mc_075['r_mean'],mc_100['r_mean'])}">{(mc_075['r_mean']-mc_100['r_mean'])*100:+.4f}pp</td>
          <td></td>
        </tr>
        <tr>
          <td class="left">Trades usados en MC</td>
          <td>{mc_100['n_trades']}</td>
          <td>{mc_075['n_trades']}</td>
          <td class="neutral">{mc_075['n_trades']-mc_100['n_trades']:+d}</td>
          <td></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="grid2">
    <div class="chart-box">
      <div class="chart-title">Distribución capital final — ×1.00 vs ×0.75 ({N_SIMS:,} sims)</div>
      <canvas id="chartCap"></canvas>
    </div>
    <div class="chart-box">
      <div class="chart-title">Distribución drawdown máximo — ×1.00 vs ×0.75 ({N_SIMS:,} sims)</div>
      <canvas id="chartDD"></canvas>
    </div>
  </div>
  <div class="note">* Bootstrap trade-level: se muestrean los retornos por trade con reposición desde 4.000 € de capital.
  Sin aportaciones anuales en la simulación (metodología trade-level). Los retornos de cada trade ya incorporan la dinámica del capital real del backtest.</div>
</div>

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- VEREDICTO FINAL                                                        -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->
<div class="final-verdict">
  <div style="font-size:36px;margin-bottom:12px">
    {('✅' if (cap_final_075 >= cap_final_100*0.95 and metricas_075['max_drawdown'] <= metricas_100['max_drawdown'] + 0.01 and mc_075['dd_p95'] <= mc_100['dd_p95'] + 0.01) else '⚠️')}
  </div>
  <div class="fv-title">VEREDICTO FINAL — ×0.75</div>
  <div class="fv-sub">Comparativa completa · {N_SIMS:,} simulaciones Montecarlo · Capital 4.000 € + 4.000 €/año · {fecha_gen}</div>
  <div class="fv-grid">
    <div class="fvk">
      <div class="fvkv">{fmt_cap(cap_final_075)}</div>
      <div class="fvkl">Capital final ×0.75</div>
    </div>
    <div class="fvk">
      <div class="fvkv {'hi' if metricas_075['max_drawdown'] < metricas_100['max_drawdown'] else ''}">{metricas_075['max_drawdown']:.1%}</div>
      <div class="fvkl">DD máx (vs {metricas_100['max_drawdown']:.1%} ×1.00)</div>
    </div>
    <div class="fvk">
      <div class="fvkv {'hi' if mc_075['dd_p95'] < mc_100['dd_p95'] else ''}">{mc_075['dd_p95']:.1%}</div>
      <div class="fvkl">MC DD p95 (vs {mc_100['dd_p95']:.1%} ×1.00)</div>
    </div>
  </div>
  <div class="fv-grid" style="margin-top:8px">
    <div class="fvk">
      <div class="fvkv">{fmt_cap(mc_075['p50'])}</div>
      <div class="fvkl">MC p50 (vs {fmt_cap(mc_100['p50'])} ×1.00)</div>
    </div>
    <div class="fvk">
      <div class="fvkv">{mc_075['ruin_prob']:.2%}</div>
      <div class="fvkl">Prob. ruina</div>
    </div>
    <div class="fvk">
      <div class="fvkv">{metricas_075['profit_factor']:.3f}</div>
      <div class="fvkl">PF (vs {metricas_100['profit_factor']:.3f} ×1.00)</div>
    </div>
  </div>
  <div style="font-size:12px;color:var(--muted);margin-top:16px">
    Backtest: {fmt_cap(cap_final_100)} (×1.00) vs {fmt_cap(cap_final_075)} (×0.75) ·
    Stress: {sum(1 for n in CRISIS if veredicto_stress(stress_resultados[(n,0.75)],stress_resultados[(n,1.00)])[1]=='hi')}/{len(CRISIS)} períodos ×0.75 ≥ ×1.00 ·
    MC DD_p95: {mc_075['dd_p95']:.1%} vs {mc_100['dd_p95']:.1%}
  </div>
</div>

<footer class="foot">
  LIBERTAD_2045 · Comparativa Final ×0.75 vs ×1.00 · {fecha_gen} ·
  Motor: backtest_expandido.py (×1.00) + backtest_exp40ter.py (×0.75) ·
  Sin módulos de producción modificados
</footer>

<script>
const D = {json.dumps(chart_data)};
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = 'Courier New, monospace';
Chart.defaults.font.size = 11;

// ── Capital final ─────────────────────────────────────────────────────────
const ctxC = document.getElementById('chartCap').getContext('2d');
new Chart(ctxC, {{
  type: 'bar',
  data: {{
    labels: D.e100_cap.slice(0,-1).map(e => e.toFixed(0)+'k'),
    datasets: [
      {{
        label: '×1.00',
        data: D.h100_cap,
        backgroundColor: 'rgba(59,130,246,0.6)',
        borderColor: 'rgba(59,130,246,0.9)',
        borderWidth: 1,
      }},
      {{
        label: '×0.75',
        data: D.h075_cap,
        backgroundColor: 'rgba(167,139,250,0.6)',
        borderColor: 'rgba(167,139,250,0.9)',
        borderWidth: 1,
      }},
    ],
  }},
  options: {{
    animation: false, responsive: true,
    plugins: {{
      legend: {{display:true,labels:{{color:'#94a3b8'}}}},
      tooltip: {{callbacks: {{label: ctx => ` ${{ctx.raw}} sims`}}}},
    }},
    scales: {{
      x: {{ticks:{{maxTicksLimit:8,color:'#475569'}},grid:{{color:'rgba(30,41,59,0.8)'}},
           title:{{display:true,text:'Capital final (miles €)',color:'#475569'}}}},
      y: {{ticks:{{color:'#475569'}},grid:{{color:'rgba(30,41,59,0.8)'}},
           title:{{display:true,text:'Simulaciones',color:'#475569'}}}},
    }},
  }},
}});

// ── Drawdown máximo ───────────────────────────────────────────────────────
const ctxD = document.getElementById('chartDD').getContext('2d');
new Chart(ctxD, {{
  type: 'bar',
  data: {{
    labels: D.e100_dd.slice(0,-1).map(e => e.toFixed(1)+'%'),
    datasets: [
      {{
        label: '×1.00',
        data: D.h100_dd,
        backgroundColor: 'rgba(59,130,246,0.6)',
        borderColor: 'rgba(59,130,246,0.9)',
        borderWidth: 1,
      }},
      {{
        label: '×0.75',
        data: D.h075_dd,
        backgroundColor: 'rgba(167,139,250,0.6)',
        borderColor: 'rgba(167,139,250,0.9)',
        borderWidth: 1,
      }},
    ],
  }},
  options: {{
    animation: false, responsive: true,
    plugins: {{
      legend: {{display:true,labels:{{color:'#94a3b8'}}}},
      tooltip: {{callbacks: {{label: ctx => ` ${{ctx.raw}} sims`}}}},
    }},
    scales: {{
      x: {{ticks:{{maxTicksLimit:8,color:'#475569'}},grid:{{color:'rgba(30,41,59,0.8)'}},
           title:{{display:true,text:'Drawdown máximo (%)',color:'#475569'}}}},
      y: {{ticks:{{color:'#475569'}},grid:{{color:'rgba(30,41,59,0.8)'}},
           title:{{display:true,text:'Simulaciones',color:'#475569'}}}},
    }},
  }},
}});
</script>
</body>
</html>"""

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)

size_kb = os.path.getsize(OUTPUT) / 1024
print(f"  HTML generado: {OUTPUT} ({size_kb:.0f} KB)")

# ── Publicar ──────────────────────────────────────────────────────────────
print(f"\nPublicando {OUTPUT}...")
try:
    from github_publisher import publicar_pagina
    ok, msg = publicar_pagina(OUTPUT)
    print(f"  {'✓' if ok else '✗'} {msg}")
except Exception as e:
    print(f"  Error publicando: {e}")

total_elapsed = time.time() - t_global
print(f"\n{'='*70}")
print(f"  Total: {total_elapsed:.0f}s")
print(f"{'='*70}")
