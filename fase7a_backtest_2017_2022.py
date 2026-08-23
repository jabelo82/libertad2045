"""
LIBERTAD_2045 -- Fase 7A de la iniciativa Exp.46/48
Parte 2: dos backtests, misma ventana exacta, motor v6 completo
==========================================================================

Backtest acotado 2017-06-26 -> 2022-06-24 (rango de validez de los 6
snapshots verificados de la Parte 1), S&P500 solo vs S&P500+Russell1000
con composicion PUNTO-EN-EL-TIEMPO real del Russell 1000 -- corrige el
sesgo de anticipacion del Exp.48 (que proyectaba la foto actual del
Russell hacia atras 20 años).

Motor: backtest_expandido.py sin modificar (v6 completo -- gaps,
slippage, comisiones IBKR, desglose FX, capital/aportaciones
convertidos a USD real). Mismo patron que backtest_exp48_russell1000_v4.py
y el __main__ del propio backtest_expandido.py.

DISEÑO APROBADO POR JAVIER (23/08/2026), puntos clave:
  1. Fuente S&P500: fichero "(Updated)" de fja05680/sp500 (llega hasta
     2026-06-30), con CACHE PROPIA Y SEPARADA
     (sp500_composicion_updated_fase7a.csv) -- NUNCA toca ni sobrescribe
     sp500_composicion.csv (la cache congelada en 2019-01-11, hallazgo
     documentado aparte en 00_LIBERTAD2045_CONTEXT.txt, sin resolver
     todavia por decision explicita de Javier).
  2. Composicion combinada = union de conjuntos en cada fecha de cambio
     (S&P500 real ∪ Russell1000 real), no el bypass estatico del Exp.48.
  3. SIN buffer de calentamiento antes de 2017-06-26 -- mismo
     tratamiento del arranque que v4/v5/v6 con su propio 2006-01-01
     (los primeros ~200 dias/~10 meses no tendran trades por SMA200,
     proporcionalmente mas visible en una ventana de 5 años).
  4. Desglose Russell-only vs S&P500 por FECHA REAL de cada trade (no
     una lista estatica como el Exp.48) -- un ticker puede haber estado
     en ambos indices en momentos distintos de la ventana.
  5. Ningun fichero existente (Exp.48/v4/v5/v6, sp500_composicion.csv)
     se sobrescribe -- todas las salidas nuevas llevan prefijo fase7a_.

NO SE EJECUTA DESDE ESTE COMMIT -- Javier revisa el script completo
primero (ver conversacion, "no la lances todavia").
"""

import os
import shutil
from datetime import datetime

import pandas as pd

import backtest_expandido as bt
from backtest_expandido import (
    descargar_datos,
    ejecutar_backtest,
    calcular_metricas,
    capital_inicial_usd_para_reporte,
    imprimir_informe,
    guardar_resultados,
    sp500_en_fecha,
)
from data_manager import obtener_datos_cached

# --------------------------------------------------
# Ventana exacta de la Fase 7A -- NO son los START_DATE/END_DATE del
# modulo backtest_expandido (esos siguen en 2006-01-01/2025-12-31 para
# v4/v5/v6, sin tocar). Se sobreescriben temporalmente los ATRIBUTOS
# del modulo bt.START_DATE/bt.END_DATE justo antes de cada
# imprimir_informe() para que el informe muestre las fechas correctas
# de ESTA ventana -- no se modifica el fichero backtest_expandido.py.
# --------------------------------------------------
START_DATE = "2017-06-26"
END_DATE = "2022-06-24"

RUSSELL_COMP_FILE = "russell1000_composicion_historica_2017_2022.csv"

# Cache PROPIA del S&P500 "(Updated)" -- ruta deliberadamente distinta
# de SP500_COMP_CACHE ("sp500_composicion.csv") para no tocar ni
# invalidar esa cache existente, que se queda congelada en 2019-01-11
# hasta que Javier decida que hacer con ella (hallazgo documentado
# aparte, 23/08/2026).
SP500_UPDATED_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
SP500_UPDATED_CACHE = "sp500_composicion_updated_fase7a.csv"

# Los 9 candidatos a cambio de ticker/fusion de la Parte 1 (todos
# verificados como reales) -- se comprueba si el ticker ANTIGUO
# aparece en los trades del run combinado con un patron de corte
# artificial (salida justo en la fecha de transicion del snapshot, sin
# que el precio haya tocado el stop). Ver punto 8 del diseño.
CANDIDATOS_RENAME = [
    ("HCN", "WELL"), ("CBG", "CBRE"), ("DVMT", "DELL"), ("MSG", "MSGE"),
    ("BPR", "BPYU"), ("JEC", "J"), ("AAXN", "AXON"), ("BLL", "BALL"),
    ("WLTW", "WTW"),
]

OUT_DIR = "backtest_results"


def cargar_composicion_sp500_updated():
    """
    Version "(Updated)" del historico S&P500 (llega hasta 2026-06-30,
    ver hallazgo documentado aparte) -- cache en SP500_UPDATED_CACHE,
    NUNCA en SP500_COMP_CACHE (sp500_composicion.csv).
    """
    if os.path.exists(SP500_UPDATED_CACHE):
        print(f"  Composición S&P500 (Updated) : caché local ({SP500_UPDATED_CACHE})")
        return pd.read_csv(SP500_UPDATED_CACHE, index_col=0, parse_dates=True)
    print("  Composición S&P500 (Updated) : descargando desde GitHub…")
    df = pd.read_csv(SP500_UPDATED_URL, index_col=0, parse_dates=True)
    df.to_csv(SP500_UPDATED_CACHE)
    print(f"  Guardado en                  : {SP500_UPDATED_CACHE}")
    return df


def _filtrar_comp_df_ventana(comp_df, start, end):
    """
    Sub-DataFrame con la fila-ancla (la ultima <= start, para que
    sp500_en_fecha()/asof() resuelva bien desde el primer dia) mas
    todas las filas dentro de [start, end].
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    antes = comp_df.index[comp_df.index <= start_ts]
    idx_ancla = [antes.max()] if len(antes) else []
    mask_ventana = (comp_df.index >= start_ts) & (comp_df.index <= end_ts)
    fechas = sorted(set(idx_ancla) | set(comp_df.index[mask_ventana]))
    return comp_df.loc[fechas]


def universo_en_ventana(comp_df, start, end):
    """Union de todos los tickers de _filtrar_comp_df_ventana(), lista
    ordenada -- para pasar a descargar_datos()."""
    sub = _filtrar_comp_df_ventana(comp_df, start, end)
    todos = set()
    col = sub.columns[0]
    for val in sub[col].dropna():
        todos |= {t.strip() for t in str(val).split(",") if t.strip()}
    return sorted(todos)


def construir_composicion_combinada(sp500_df, russell_df, start, end):
    """
    Composicion combinada S&P500 ∪ Russell1000, dedup por fecha real
    -- NO el bypass estatico del Exp.48. Indice = union de fechas de
    cambio de ambas fuentes dentro de la ventana; en cada fecha, union
    de conjuntos (sp500_en_fecha() reutilizada sin cambios, es
    generica pese al nombre).
    """
    sp_sub = _filtrar_comp_df_ventana(sp500_df, start, end)
    ru_sub = _filtrar_comp_df_ventana(russell_df, start, end)
    fechas_combinadas = sorted(set(sp_sub.index) | set(ru_sub.index))

    filas = []
    for fecha in fechas_combinadas:
        sp_hoy = sp500_en_fecha(sp500_df, fecha) or set()
        ru_hoy = sp500_en_fecha(russell_df, fecha) or set()
        union = sp_hoy | ru_hoy
        filas.append({"date": fecha, "tickers": ",".join(sorted(union))})
    return pd.DataFrame(filas).set_index("date")


def _renombrar_ultima_salida(prefijo, antes_de_llamar):
    """
    guardar_resultados() (backtest_expandido.py) genera su propio
    timestamp internamente con datetime.now() -- NO acepta ni expone
    ese timestamp al llamador. Reconstruir el nombre a partir de un
    datetime.now() capturado por fuera, ANTES de llamarla, es fragil:
    son dos llamadas a datetime.now() en instantes distintos (de por
    medio, os.makedirs() y la escritura de los DataFrames), y aunque
    el hueco sea de milisegundos puede cruzar la frontera de segundo
    -- el formato "%Y%m%d_%H%M%S" no lleva fraccion. Riesgo real, no
    hipotetico, señalado por Javier antes de lanzar la ejecución
    (23/08/2026).

    En vez de eso: localiza por glob el fichero mas reciente de OUT_DIR
    con el patron esperado, filtrando a los creados DESPUES de
    `antes_de_llamar` (un datetime.now() capturado justo antes de
    llamar a guardar_resultados() -- se usa solo como cota inferior de
    sanidad para excluir ficheros antiguos, nunca como parte del
    nombre). Si no aparece ninguno, falla con excepcion explicita en
    vez de saltarse la copia en silencio.
    """
    import glob
    for tipo in ["trades", "capital", "metricas"]:
        patron = f"{OUT_DIR}/expandido_{tipo}_*.csv"
        candidatos = [
            c for c in glob.glob(patron)
            if datetime.fromtimestamp(os.path.getmtime(c)) >= antes_de_llamar
        ]
        if not candidatos:
            raise RuntimeError(
                f"No se encontró ningún fichero '{patron}' creado después de "
                f"{antes_de_llamar} -- guardar_resultados() no generó la "
                f"salida esperada para '{tipo}' (o trades/curva_capital/"
                f"metricas estaba vacío para este run). Revisar antes de "
                f"continuar -- no hay copia silenciosa que valga."
            )
        origen = max(candidatos, key=os.path.getmtime)
        destino = f"{OUT_DIR}/fase7a_{prefijo}_{tipo}.csv"
        if os.path.exists(destino):
            raise RuntimeError(
                f"{destino} ya existe -- me niego a sobrescribirlo sin "
                f"confirmacion explicita. Revisar a mano."
            )
        shutil.copy2(origen, destino)
        print(f"  Copiado (original intacto en {origen}) -> {destino}")


def clasificar_trades_por_indice(trades, sp500_df):
    """
    Para cada trade del run combinado, clasifica "S&P500" o
    "RUSSELL_ONLY" segun si el simbolo pertenecia al S&P500 real en la
    fecha de ENTRADA de ESE trade concreto (no una lista estatica --
    un ticker puede haber estado en ambos indices en momentos
    distintos de la ventana, ver punto 7 del diseño aprobado).
    """
    clasificacion = []
    for t in trades:
        miembros_sp500 = sp500_en_fecha(sp500_df, t["fecha_entrada"]) or set()
        clasificacion.append(
            "SP500" if t["symbol"] in miembros_sp500 else "RUSSELL_ONLY"
        )
    return clasificacion


def revisar_candidatos_rename(trades, russell_df):
    """
    Punto 8 del diseño: para cada candidato a cambio de ticker de la
    Parte 1, busca trades del ticker ANTIGUO y comprueba si la salida
    ocurre justo en la fecha de transicion del snapshot Russell (señal
    de posible corte artificial) en vez de en una fecha de stop
    ordinaria. NO corrige nada -- solo reporta, tal como pidio Javier.

    Nota estructural (ya explicada en el diseño, se confirma aqui de
    forma empirica): la gestion de posiciones abiertas del motor NO
    consulta la composicion en ningun momento -- solo las entradas
    nuevas se filtran por ella. Por construccion, un cambio de ticker
    Russell no puede cerrar una posicion ya abierta.
    """
    fechas_transicion = set(russell_df.index)
    hallazgos = []
    for ticker_viejo, ticker_nuevo in CANDIDATOS_RENAME:
        trades_viejo = [t for t in trades if t["symbol"] == ticker_viejo]
        for t in trades_viejo:
            fecha_salida = pd.Timestamp(t["fecha_salida"])
            cerca_de_transicion = any(
                abs((fecha_salida - ft).days) <= 5 for ft in fechas_transicion
            )
            hallazgos.append({
                "ticker_viejo": ticker_viejo, "ticker_nuevo": ticker_nuevo,
                "fecha_entrada": t["fecha_entrada"], "fecha_salida": t["fecha_salida"],
                "resultado": t["resultado"], "pnl": t["pnl"],
                "salida_cerca_de_transicion_russell": cerca_de_transicion,
            })
    return hallazgos


def main():
    print("=" * 60)
    print("  FASE 7A -- Parte 2: S&P500 vs S&P500+Russell1000 real")
    print(f"  Ventana: {START_DATE} -> {END_DATE}")
    print("=" * 60)

    sp500_df = cargar_composicion_sp500_updated()
    russell_df = pd.read_csv(RUSSELL_COMP_FILE, index_col=0, parse_dates=True)

    universo_sp500 = universo_en_ventana(sp500_df, START_DATE, END_DATE)
    universo_russell = universo_en_ventana(russell_df, START_DATE, END_DATE)
    universo_combinado = sorted(set(universo_sp500) | set(universo_russell))

    print(f"\n  Universo S&P500 (ventana)     : {len(universo_sp500)} tickers")
    print(f"  Universo Russell1000 (ventana): {len(universo_russell)} tickers")
    print(f"  Universo combinado            : {len(universo_combinado)} tickers")

    # --------------------------------------------------
    # Descarga UNA VEZ, se reutiliza para los dos runs -- garantiza
    # precios identicos para cualquier ticker compartido entre ambos
    # backtests (ver punto 5 del diseño).
    # --------------------------------------------------
    datos_combinado = descargar_datos(universo_combinado, START_DATE, END_DATE)
    if not datos_combinado:
        print("ERROR: no se pudieron cargar datos.")
        return

    datos_sp500 = {t: df for t, df in datos_combinado.items() if t in set(universo_sp500)}

    eurusd = obtener_datos_cached("EURUSD=X", START_DATE, END_DATE)
    if eurusd is None:
        print("\n  AVISO: no se pudo cargar EURUSD=X — el backtest sigue en "
              "USD sin desglose en EUR (fail-safe, sin cambio de comportamiento).")

    comp_sp500_ventana = _filtrar_comp_df_ventana(sp500_df, START_DATE, END_DATE)
    comp_combinada = construir_composicion_combinada(sp500_df, russell_df, START_DATE, END_DATE)

    # Sobreescribe temporalmente los atributos del modulo SOLO para que
    # imprimir_informe() muestre las fechas correctas de esta ventana
    # -- no se toca backtest_expandido.py.
    bt.START_DATE, bt.END_DATE = START_DATE, END_DATE

    # --------------------------------------------------
    # RUN 1 -- S&P500 solo
    # --------------------------------------------------
    print("\n" + "=" * 60)
    print("  RUN 1/2 -- S&P500 solo")
    print("=" * 60)
    trades_sp, curva_sp, capital_final_sp = ejecutar_backtest(
        datos_sp500, composicion_df=comp_sp500_ventana, eurusd=eurusd)
    capital_inicial_sp = capital_inicial_usd_para_reporte(datos_sp500, eurusd)
    metricas_sp = calcular_metricas(
        trades_sp, curva_sp, capital_final_sp, capital_inicial_usd=capital_inicial_sp)
    imprimir_informe(metricas_sp)
    antes_sp = datetime.now()
    guardar_resultados(trades_sp, curva_sp, metricas_sp)
    _renombrar_ultima_salida("sp500solo", antes_sp)

    # --------------------------------------------------
    # RUN 2 -- S&P500 + Russell1000 (composicion real punto-en-el-tiempo)
    # --------------------------------------------------
    print("\n" + "=" * 60)
    print("  RUN 2/2 -- S&P500 + Russell1000 (composición real)")
    print("=" * 60)
    trades_comb, curva_comb, capital_final_comb = ejecutar_backtest(
        datos_combinado, composicion_df=comp_combinada, eurusd=eurusd)
    capital_inicial_comb = capital_inicial_usd_para_reporte(datos_combinado, eurusd)
    metricas_comb = calcular_metricas(
        trades_comb, curva_comb, capital_final_comb, capital_inicial_usd=capital_inicial_comb)
    imprimir_informe(metricas_comb)
    antes_comb = datetime.now()
    guardar_resultados(trades_comb, curva_comb, metricas_comb)
    _renombrar_ultima_salida("combinado", antes_comb)

    # --------------------------------------------------
    # Desglose Russell-only vs S&P500 dentro del run combinado
    # (clasificacion dinamica por fecha real de cada trade)
    # --------------------------------------------------
    clasificacion = clasificar_trades_por_indice(trades_comb, sp500_df)
    df_trades_comb = pd.DataFrame(trades_comb)
    df_trades_comb["origen"] = clasificacion
    df_trades_comb.to_csv(f"{OUT_DIR}/fase7a_combinado_trades_clasificados.csv", index=False)

    n_sp500 = clasificacion.count("SP500")
    n_russell = clasificacion.count("RUSSELL_ONLY")
    print(f"\n  Desglose combinado: {n_sp500} trades S&P500, {n_russell} trades Russell-only")

    # --------------------------------------------------
    # Chequeo de los 9 candidatos a rename (punto 8 del diseño)
    # --------------------------------------------------
    hallazgos_rename = revisar_candidatos_rename(trades_comb, russell_df)
    pd.DataFrame(hallazgos_rename).to_csv(
        f"{OUT_DIR}/fase7a_chequeo_renames.csv", index=False)
    sospechosos = [h for h in hallazgos_rename if h["salida_cerca_de_transicion_russell"]]
    print(f"\n  Chequeo renames: {len(hallazgos_rename)} trades de tickers antiguos "
          f"encontrados, {len(sospechosos)} con salida cerca de una fecha de "
          f"transición Russell (revisar a mano si >0).")

    print("\nFASE 7A -- Parte 2 completada.")


if __name__ == "__main__":
    main()
