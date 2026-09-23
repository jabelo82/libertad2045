"""
Test — v8 (Parte G, investigación racha LIVE 09-10/09/2026): la salida
por stop debe evaluarse contra el Low intradía (SALIDA_POR_CIERRE=False,
default del módulo desde v11, 23/09/2026), no solo contra el Close del
día (v7, SALIDA_POR_CIERRE=True -- el default antiguo, antes de v11).

Motivo: Parte D.2 de la investigación verificó con 6/6 trades ganadores
reales de LIVE que v7 (Close-only) deja correr posiciones que en
producción (orden STP GTC real, se dispara por toque intradía) ya se
habrían cerrado — inflando sistemáticamente el tamaño de los
ganadores. En 2/6 casos reales (DVN, WFC) v7 ni siquiera habría
cerrado la posición ese día porque el cierre quedó por encima del stop
aunque el mínimo del día lo hubiera cruzado.

Escenario: mismo patrón de calentamiento que test_backtest_paridad_riesgo.py
(200 días de tendencia + pullback día 198 + señal día 200 + entrada
día 201, ATR fijo, ATR_PERCENTIL ausente -> obtener_multiplicador()
usa siempre el fallback ATR_MULTIPLIER=3,1, determinista).

Verificado ejecutando el motor real (no a mano, igual que exige el
docstring de test_backtest_paridad_riesgo.py): con este patrón de
calentamiento, el propio día de entrada (día 201: Open=50,5 High=52,0
Low=50,0 Close=51,5) ya genera la divergencia por sí solo — el primer
recálculo de trailing usa el High de ESE MISMO día (52,0) y sube el
stop a 50,8375, por encima del Low del propio día (50,0) pero por
debajo de su Close (51,5). Días 202-205 (subida sostenida) solo sirven
para que, si la posición sobrevive (SALIDA_POR_CIERRE=True), termine
cerrándose mucho más tarde y a un precio mejor — cuantificando la
misma inflación de ganadores que Parte D.2 encontró con datos reales.

Se verifica explícitamente que el test FALLA (produce un resultado
distinto, más favorable) contra el motor con SALIDA_POR_CIERRE=True
(comportamiento de v7, el default antiguo del módulo antes de v11) y
PASA con SALIDA_POR_CIERRE=False (v8, default del módulo desde v11) —
no es tautológico. La prueba de que esto último ya no necesita
monkeypatch alguno vive en
tests/test_backtest_v11_defaults.py::TestComportamientoPorDefectoSinMonkeypatch.
"""

import pandas as pd
import pytest

import backtest_expandido as bt
from backtest_expandido import ejecutar_backtest


N_WARMUP           = 200
ATR_FIJO           = 0.5
PRECIO_BASE        = 50.0
SMA50_FIJO         = 48.0
SMA200_INICIO      = 45.0
SMA200_INCREMENTO  = 0.01


def _fila(**overrides):
    fila = {
        "Open": PRECIO_BASE, "High": PRECIO_BASE, "Low": PRECIO_BASE,
        "Close": PRECIO_BASE, "Volume": 1_000_000,
        "SMA50": SMA50_FIJO, "ATR": ATR_FIJO,
    }
    fila.update(overrides)
    return fila


def _construir_ticker(dias_extra: list) -> pd.DataFrame:
    """Mismo patrón de calentamiento que test_backtest_paridad_riesgo.py
    (200 días alcistas + pullback día 198 + señal día 200 + entrada
    día 201 sin gap)."""
    n_total = N_WARMUP + 2 + len(dias_extra)
    fechas  = pd.bdate_range(start="2016-01-04", periods=n_total)

    filas = []
    for i in range(N_WARMUP):
        filas.append(_fila(SMA200=SMA200_INICIO + i * SMA200_INCREMENTO))

    filas[198] = _fila(
        Open=44.0, High=45.0, Low=43.0, Close=44.0,
        SMA200=SMA200_INICIO + 198 * SMA200_INCREMENTO,
    )
    filas.append(_fila(   # día 200 -- señal
        Open=50.0, High=51.0, Low=49.0, Close=50.0,
        SMA200=SMA200_INICIO + N_WARMUP * SMA200_INCREMENTO,
    ))
    filas.append(_fila(   # día 201 -- entrada (High=52,0 / Low=50,0: el propio
                           # día de entrada es el que genera la divergencia)
        Open=50.5, High=52.0, Low=50.0, Close=51.5,
        SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
    ))

    for k, fila_extra in enumerate(dias_extra):
        fila = dict(fila_extra)
        fila.setdefault("SMA200", SMA200_INICIO + (N_WARMUP + 2 + k) * SMA200_INCREMENTO)
        filas.append(_fila(**fila))

    return pd.DataFrame(filas, index=fechas)


def _escenario_toque_intradia():
    """
    Días 202-205: subida sostenida -- solo relevante para el caso
    SALIDA_POR_CIERRE=True (v7), donde la posición sobrevive al día de
    entrada y sigue corriendo. Bajo SALIDA_POR_CIERRE=False (v8) estos
    días son irrelevantes: la posición ya se cerró el día 201.
    """
    return {"WICK": _construir_ticker([
        dict(Open=51.5, High=52.0, Low=51.3, Close=51.8),   # día 202
        dict(Open=51.8, High=53.0, Low=51.5, Close=52.8),   # día 203
        dict(Open=52.8, High=54.0, Low=52.5, Close=53.5),   # día 204
        dict(Open=53.5, High=54.2, Low=53.0, Close=53.8),   # día 205
        dict(Open=53.8, High=55.0, Low=53.5, Close=54.8),   # día 206 -- fin de datos
    ])}


class TestSalidaPorLowIntradia:

    def test_v7_close_only_sobrevive_al_toque_del_dia_de_entrada(self, monkeypatch):
        """
        Comportamiento de v7 (SALIDA_POR_CIERRE=True, el default
        antiguo del módulo antes de v11) -- reproduce el problema: el propio día 201 ya tiene
        un Low (50,0) por debajo del stop recién actualizado (50,8375,
        calculado con el High de ese mismo día), pero como el Close
        (51,5) se mantiene por encima, la posición NO se cierra. Sigue
        corriendo y termina en un precio mucho mejor (forzada al cierre
        de los datos, OPEN→CLOSE, ganancia grande).
        """
        monkeypatch.setattr(bt, "SALIDA_POR_CIERRE", True)
        datos = _escenario_toque_intradia()
        fecha_entrada = datos["WICK"].index[201]

        trades, _, _ = ejecutar_backtest(datos)
        wick_trade = next(t for t in trades if t["symbol"] == "WICK")

        assert wick_trade["fecha_salida"] != fecha_entrada
        assert wick_trade["pnl"] > 50   # ganancia grande, calibrado (~73)

    def test_v8_low_intradia_cierra_el_mismo_dia_de_entrada(self, monkeypatch):
        """
        v8 (SALIDA_POR_CIERRE=False): el mismo toque (Low=50,0 <= stop
        actualizado 50,8375 con el High de ese día) SÍ cierra la
        posición, el mismo día de entrada -- al nivel del stop
        (calcular_precio_salida_stop, sin gap real ya que Open=50,5 <=
        stop -> ejecuta al Open, no al Low ni al stop teórico exacto).
        Resultado: pérdida pequeña, no la ganancia grande de v7.
        """
        monkeypatch.setattr(bt, "SALIDA_POR_CIERRE", False)
        datos = _escenario_toque_intradia()
        fecha_entrada = datos["WICK"].index[201]

        trades, _, _ = ejecutar_backtest(datos)
        wick_trade = next(t for t in trades if t["symbol"] == "WICK")

        assert wick_trade["fecha_salida"] == fecha_entrada
        assert wick_trade["pnl"] < 0   # pérdida pequeña, calibrado (~-13)

    def test_v8_produce_un_resultado_peor_que_v7_para_el_mismo_escenario(self, monkeypatch):
        """
        La comparación directa que motiva Parte G: mismo escenario,
        mismos datos -- el único cambio es la palanca. v8 corta la
        posición de inmediato (pérdida pequeña) donde v7 la deja correr
        hasta una ganancia grande -- cuantifica la misma inflación de
        ganadores verificada con datos reales en Parte D.2, aquí de
        forma determinista y reproducible.
        """
        monkeypatch.setattr(bt, "SALIDA_POR_CIERRE", True)
        trades_v7, _, _ = ejecutar_backtest(_escenario_toque_intradia())
        pnl_v7 = next(t for t in trades_v7 if t["symbol"] == "WICK")["pnl"]

        monkeypatch.setattr(bt, "SALIDA_POR_CIERRE", False)
        trades_v8, _, _ = ejecutar_backtest(_escenario_toque_intradia())
        pnl_v8 = next(t for t in trades_v8 if t["symbol"] == "WICK")["pnl"]

        assert pnl_v8 < pnl_v7
        assert pnl_v7 > 0
        assert pnl_v8 < 0
