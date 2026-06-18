"""
Compara el backtest CON y SIN rebalanceo dinámico.
Reutiliza el caché de datos de backtest_expandido.py.
"""

import warnings
warnings.filterwarnings("ignore")

import backtest_expandido as bt

LINEA_BASE_V3 = {
    "capital_final" : 8_888_418,
    "win_rate"      : 0.541,
    "profit_factor" : 2.6071,
    "max_drawdown"  : 0.104,
    "total_trades"  : 2402,
}


def run(threshold, label, datos, comp_df):
    bt.REBALANCE_THRESHOLD = threshold
    trades, curva, capital = bt.ejecutar_backtest(datos, composicion_df=comp_df)
    m = bt.calcular_metricas(trades, curva, capital)
    m["_label"] = label
    m["_rebalanceos"] = sum(
        1 for t in trades if t.get("resultado") not in ("WIN", "LOSS", "OPEN→CLOSE")
    )
    return m


def delta(a, b, key, pct=False):
    va, vb = a[key], b[key]
    d = va - vb
    if pct:
        return f"{va:.1%} vs {vb:.1%}  (Δ {d:+.1%})"
    if key == "capital_final":
        return f"{va:,.0f}€ vs {vb:,.0f}€  (Δ {d:+,.0f}€)"
    return f"{va:.4f} vs {vb:.4f}  (Δ {d:+.4f})"


if __name__ == "__main__":

    print("=" * 58)
    print("  COMPARATIVA REBALANCEO — LIBERTAD_2045")
    print("=" * 58)

    comp_df  = bt.cargar_composicion_sp500()
    universo = bt.universo_historico_sp500(comp_df)
    datos    = bt.descargar_datos(universo, bt.START_DATE, bt.END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        exit(1)

    print("\n[1/2] Backtest CON rebalanceo (umbral 25%)...")
    m_on  = run(0.25,  "CON rebalanceo",  datos, comp_df)

    print("\n[2/2] Backtest SIN rebalanceo (desactivado)...")
    m_off = run(999.0, "SIN rebalanceo", datos, comp_df)

    sep = "─" * 58

    print(f"\n{sep}")
    print(f"  RESULTADOS COMPARADOS")
    print(f"  {'Métrica':<22}  {'CON rebalanceo':>18}  {'SIN rebalanceo':>18}")
    print(sep)

    rows = [
        ("Capital final",   "capital_final",   False),
        ("Win rate",        "win_rate",        True),
        ("Profit factor",   "profit_factor",   False),
        ("Max drawdown",    "max_drawdown",    True),
        ("Total trades",    "total_trades",    False),
    ]

    for label, key, is_pct in rows:
        vc = m_on[key]
        vs = m_off[key]
        d  = vc - vs

        if key == "capital_final":
            s_on  = f"{vc:>15,.0f} €"
            s_off = f"{vs:>15,.0f} €"
            s_d   = f"Δ {d:+,.0f} €"
        elif is_pct:
            s_on  = f"{vc:>17.1%}"
            s_off = f"{vs:>17.1%}"
            s_d   = f"Δ {d:+.1%}"
        else:
            s_on  = f"{vc:>18.4f}"
            s_off = f"{vs:>18.4f}"
            s_d   = f"Δ {d:+.4f}"

        print(f"  {label:<22}  {s_on}  {s_off}   {s_d}")

    print(sep)

    print(f"\n{sep}")
    print(f"  COMPARATIVA VS LÍNEA BASE v3")
    print(f"  {'Métrica':<22}  {'CON rebalanceo':>18}  {'Línea base v3':>18}")
    print(sep)

    for label, key, is_pct in rows:
        vc = m_on[key]
        vb = LINEA_BASE_V3[key]
        d  = vc - vb

        if key == "capital_final":
            s_on = f"{vc:>15,.0f} €"
            s_b  = f"{vb:>15,.0f} €"
            s_d  = f"Δ {d:+,.0f} €"
        elif is_pct:
            s_on = f"{vc:>17.1%}"
            s_b  = f"{vb:>17.1%}"
            s_d  = f"Δ {d:+.1%}"
        else:
            s_on = f"{vc:>18.4f}"
            s_b  = f"{vb:>18.4f}"
            s_d  = f"Δ {d:+.4f}"

        print(f"  {label:<22}  {s_on}  {s_b}   {s_d}")

    print(sep)

    # ── Conclusión ──────────────────────────────────────────────────────────
    diff_capital = m_on["capital_final"] - m_off["capital_final"]
    diff_pct     = diff_capital / m_off["capital_final"] * 100
    diff_dd      = m_on["max_drawdown"] - m_off["max_drawdown"]

    print(f"\n── CONCLUSIÓN ───────────────────────────────────────────────")
    if abs(diff_pct) < 1.0:
        veredicto = "NEUTRO"
        detalle   = f"diferencia de capital < 1% ({diff_capital:+,.0f}€ sobre 20 años)"
    elif diff_capital > 0:
        veredicto = "POSITIVO"
        detalle   = f"+{diff_capital:,.0f}€ con rebalanceo ({diff_pct:+.1f}%)"
    else:
        veredicto = "NEGATIVO"
        detalle   = f"{diff_capital:,.0f}€ con rebalanceo ({diff_pct:+.1f}%)"

    print(f"  Impacto del rebalanceo: {veredicto}")
    print(f"  Capital: {detalle}")
    if abs(diff_dd) > 0.005:
        print(f"  Drawdown: {diff_dd:+.1%} con rebalanceo "
              f"({'más conservador' if diff_dd < 0 else 'más agresivo'})")
    else:
        print(f"  Drawdown: sin cambio significativo ({diff_dd:+.1%})")
    print()
