"""
LIBERTAD_2045 — Exp.48 (provisional, a confirmar numeracion con Javier)
Fase 4 de la iniciativa Exp.46 (Russell 1000)
==========================================================================

Backtest completo con universo combinado S&P500 histórico + diferencial
Russell 1000, usando el motor YA MEJORADO (gaps de apertura + slippage
por liquidez, commits 8dfcd4d y 4a64acc — reutiliza backtest_expandido.py
sin modificarlo, así que ambos fixes ya están incluidos automáticamente).

Línea base a batir: v4 (commits 6f23769/47b9860), NO v3 — capital final
2.143.244€, PF 1,8889, DD 10,7%, WR 51,3%, 2.340 trades.

Universo combinado, sin duplicados:
    S&P500 histórico (universo_historico_sp500(), survivorship-bias
    corregido, igual que v4) ∪ S&P500 actual (501, por si la composición
    histórica cacheada está desactualizada respecto a altas recientes
    como ECHO/FERG/FLEX/MRVL) ∪ diferencial Russell 1000 filtrado por
    liquidez (511/512, Fase 1/2 del Exp.46 retomado, 22/08/2026).

Deduplicación (hallazgo ampliado sobre lo pedido): el diferencial
Russell solapa no solo con el S&P500 ACTUAL (5 casos: ECHO, FERG, FLEX,
MRVL, Q) sino con el S&P500 HISTÓRICO completo (78 casos — empresas que
estuvieron en el índice en algún momento de 2006-2025 y ya no están,
pero siguen siendo parte de universo_historico_sp500()). Los tickers
"Russell-only" (genuinamente incrementales) se calculan restando AMBOS
conjuntos, no solo el actual.
"""

import importlib.util

import pandas as pd

from backtest_expandido import (
    cargar_composicion_sp500,
    universo_historico_sp500,
    descargar_datos,
    ejecutar_backtest,
    calcular_metricas,
    imprimir_informe,
    guardar_resultados,
    START_DATE,
    END_DATE,
)

RUSSELL_FILE = "russell1000_diferencial_filtrado.txt"


def cargar_sp500_actual():
    spec = importlib.util.spec_from_file_location("universe_sp500", "universe_sp500.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.SP500)


def cargar_russell_diferencial():
    with open(RUSSELL_FILE) as f:
        return set(l.strip() for l in f if l.strip())


def main():

    print("=" * 60)
    print("  EXP.48 (provisional) — Fase 4 Exp.46: S&P500 + Russell 1000")
    print("  Motor v4 (gaps + slippage ya incluidos, sin cambios de código)")
    print("=" * 60)

    comp_df = cargar_composicion_sp500()
    sp500_historico = set(universo_historico_sp500(comp_df))
    sp500_actual = cargar_sp500_actual()
    russell = cargar_russell_diferencial()

    solapa_actual = russell & sp500_actual
    solapa_historico = russell & sp500_historico
    russell_incremental = russell - sp500_historico - sp500_actual

    print(f"\n  S&P500 histórico (universo_historico_sp500) : {len(sp500_historico)}")
    print(f"  S&P500 actual (universe_sp500.py)            : {len(sp500_actual)}")
    print(f"  Russell 1000 diferencial filtrado            : {len(russell)}")
    print(f"  Solapa Russell ∩ S&P500 actual                : {len(solapa_actual)} {sorted(solapa_actual)}")
    print(f"  Solapa Russell ∩ S&P500 histórico             : {len(solapa_historico)}")
    print(f"  Russell INCREMENTAL (sin solape con ninguno)  : {len(russell_incremental)}")

    universo_combinado = sorted(sp500_historico | sp500_actual | russell)
    print(f"\n  Universo combinado total (sin duplicados)     : {len(universo_combinado)}")

    # Guardar el conjunto de tickers "Russell-only" para el desglose
    # posterior (Fase 4, punto 4 y 5 del encargo) — se usa después de
    # correr el backtest para filtrar el CSV de trades.
    with open("exp48_russell_incremental.txt", "w") as f:
        for t in sorted(russell_incremental):
            f.write(t + "\n")
    print(f"  Guardado: exp48_russell_incremental.txt ({len(russell_incremental)} tickers)")

    datos = descargar_datos(universo_combinado, START_DATE, END_DATE)

    if not datos:
        print("ERROR: no se pudieron cargar datos.")
        return

    # --------------------------------------------------
    # BYPASS del filtro de composición histórica para los tickers
    # Russell-incrementales (mismo mecanismo que usó backtest_exp46.py
    # con _russell2000_extra, pero implementado como transformación de
    # los DATOS de entrada — no se toca backtest_expandido.py). Sin
    # esto, sp500_en_fecha() nunca reconoce estos símbolos como parte
    # del índice en ninguna fecha, y ejecutar_backtest() los descarta
    # silenciosamente del escaneo de señales cada día — verificado
    # empíricamente: sin este bypass, el resultado combinado sale
    # BIT A BIT IDÉNTICO a v4 (el Russell nunca llega a operar).
    # --------------------------------------------------
    sufijo = "," + ",".join(sorted(russell_incremental))
    comp_df_bypass = comp_df.copy()
    comp_df_bypass["tickers"] = comp_df_bypass["tickers"].astype(str) + sufijo
    print(f"\n  Bypass de composición aplicado: {len(russell_incremental)} tickers "
          f"Russell-incrementales añadidos a TODAS las fechas del comp_df "
          f"(elegibles para señal todos los días, igual que el S&P500 real)")

    trades, curva_capital, capital_final = ejecutar_backtest(datos, composicion_df=comp_df_bypass)
    metricas = calcular_metricas(trades, curva_capital, capital_final)
    imprimir_informe(metricas)
    guardar_resultados(trades, curva_capital, metricas)


if __name__ == "__main__":
    main()
