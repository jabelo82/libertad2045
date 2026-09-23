"""
tests/test_backtest_guardia_dato_corrupto.py

Fix "guardia de datos corruptos >80-90%" -- segundo de los "5 fixes
combinados" de v10 (16/09/2026), motivado directamente por la Parte J
(investigación del drawdown de v9, mismo día): Yahoo Finance sirve, en
fechas puntuales alrededor de fusiones/bajas de bolsa (caso real
verificado: CBE/Cooper Industries, adquirida por Eaton, cerrada
30/11/2012 -- el ticker fue reciclado después para otro instrumento),
barras con precio casi cero o negativo intercaladas con precios reales
del día anterior/siguiente -- no un movimiento de mercado real. Sin
guardia, el motor ejecuta un stop contra ese precio corrupto: el caso
real que lo motivó fue CBE 02/11/2012->21/11/2012, entrada 77,09$,
"salida" 0,02$, -6.475,94$ -- el 97% del peor episodio de drawdown de
v9 (backtest_results/parteJ_drawdown_v9_16-09-2026.md).

Dos niveles de test:
  1. Unitario -- _bar_precio_corrupto(): casos aislados (precio
     negativo, precio casi cero con caída del High también, caída
     normal sin ser corrupta, subida no debe dispararla).
  2. Integración -- ejecutar_backtest() de extremo a extremo,
     reutilizando el patrón de escenario de test_backtest_gap_stops.py
     (mismo warmup/señal/entrada) pero con un día CORRUPTO en vez de
     una salida normal: confirma que el motor completo NO cierra la
     posición contra el precio corrupto, y que si el precio real se
     recupera al día siguiente la posición sigue viva y se cierra más
     tarde a un precio razonable cuando el stop real se cruza de
     verdad.
"""

import pandas as pd
import pytest

from backtest_expandido import ejecutar_backtest


# ============================================================
# 1. UNITARIO -- _bar_precio_corrupto()
# ============================================================

class TestBarPrecioCorrupto:

    def test_precio_negativo_es_corrupto(self):
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 41.0, "High": 42.0, "Low": -0.027, "Close": -0.027})
        assert _bar_precio_corrupto(bar, prev_close=41.99) is True

    def test_precio_casi_cero_con_high_tambien_hundido_es_corrupto(self):
        """Caso real CBE 21/11/2012: Open=High=Low=Close=0.08, prev_close=77.78."""
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 0.08, "High": 0.08, "Low": 0.08, "Close": 0.08})
        assert _bar_precio_corrupto(bar, prev_close=77.78) is True

    def test_caida_normal_no_corrupta(self):
        """
        Una caída real de mercado (incluso severa, -15% intradía) NO debe
        marcarse como corrupta -- el umbral es 80-90%, no cualquier caída.
        """
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 95.0, "High": 96.0, "Low": 83.0, "Close": 85.0})
        assert _bar_precio_corrupto(bar, prev_close=100.0) is False

    def test_crash_real_con_rango_intradia_genuino_no_es_corrupto(self):
        """
        Caso real de test ya existente (TestReduccionDuranteDrawdown,
        test_backtest_paridad_riesgo.py): caída del 94% con variación
        intradía genuina (Open=2, High=3, Low=1, Close=2 sobre un
        cierre previo de 51,5) -- un crash real, no un dato corrupto.
        La guardia NO debe descartarlo -- solo dispara cuando, ADEMÁS
        de la caída severa, la vela es degenerada (rango casi nulo).
        """
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 2.0, "High": 3.0, "Low": 1.0, "Close": 2.0})
        assert _bar_precio_corrupto(bar, prev_close=51.5) is False

    def test_subida_nunca_es_corrupta(self):
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 100.0, "High": 250.0, "Low": 99.0, "Close": 240.0})
        assert _bar_precio_corrupto(bar, prev_close=100.0) is False

    def test_sin_referencia_previa_nunca_bloquea(self):
        """Fail-safe: sin prev_close válido, nunca se marca corrupto."""
        from backtest_expandido import _bar_precio_corrupto
        bar = pd.Series({"Open": 0.01, "High": 0.02, "Low": 0.01, "Close": 0.01})
        assert _bar_precio_corrupto(bar, prev_close=None) is False
        assert _bar_precio_corrupto(bar, prev_close=float("nan")) is False
        assert _bar_precio_corrupto(bar, prev_close=0.0) is False


# ============================================================
# 2. INTEGRACIÓN -- ejecutar_backtest() de extremo a extremo
# ============================================================

ATR_FIJO = 5.0
CLOSE_BASE = 200.0
SMA50_FIJO = 195.0
SMA200_INICIO = 190.0
SMA200_INCREMENTO = 0.01
N_WARMUP = 200


def _fila_plana(**overrides):
    fila = {
        "Open": CLOSE_BASE, "High": CLOSE_BASE, "Low": CLOSE_BASE,
        "Close": CLOSE_BASE, "Volume": 1_000_000,
        "SMA50": SMA50_FIJO, "ATR": ATR_FIJO,
    }
    fila.update(overrides)
    return fila


def _construir_escenario_con_dia_corrupto() -> dict:
    """
    Mismo warmup/señal/entrada que test_backtest_gap_stops.py (entrada
    sin gap, buy_stop=205.05). Día 202 sube el trailing stop a ~194.38
    (igual que allí). A partir de ahí, el guion diverge:

      Día 203 (CORRUPTO): Open=High=Low=Close=0.02 -- reproduce el
        patrón real de CBE 21/11/2012 (precio casi cero, todo el rango
        del día hundido, no solo el cierre). Cierre del día anterior
        (día 202) = 203.0, muy por encima -> caída >80%, debe tratarse
        como dato corrupto.
      Día 204 (recuperación real): Close=203.0, el mismo nivel de antes
        del día corrupto -- confirma que una SUBIDA tras el día corrupto
        no dispara la guardia y el motor vuelve a operar con datos
        reales con normalidad.
      Día 205 (salida real): Close=190.0, cruza el stop real (194.38)
        -- confirma que la posición SÍ puede cerrarse más tarde, a un
        precio razonable, una vez el precio real cruza el stop de
        verdad.
    """
    fechas = pd.bdate_range(start="2016-01-04", periods=N_WARMUP + 6)

    filas = []
    for i in range(N_WARMUP):
        sma200 = SMA200_INICIO + i * SMA200_INCREMENTO
        filas.append(_fila_plana(SMA200=sma200))

    filas[198] = _fila_plana(
        Open=185.0, High=186.0, Low=184.0, Close=185.0,
        SMA200=SMA200_INICIO + 198 * SMA200_INCREMENTO,
    )

    # Día 200 — señal
    filas.append(_fila_plana(
        Open=200.0, High=205.0, Low=198.0, Close=200.0,
        SMA200=SMA200_INICIO + N_WARMUP * SMA200_INCREMENTO,
    ))

    # Día 201 — entrada sin gap (buy_stop = 205.05)
    filas.append(_fila_plana(
        Open=200.5, High=206.0, Low=199.0, Close=203.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
    ))

    # Día 202 — sube el trailing stop a ~194.38, sin disparar salida
    filas.append(_fila_plana(
        Open=203.0, High=206.0, Low=202.0, Close=203.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 2) * SMA200_INCREMENTO,
    ))

    # Día 203 — CORRUPTO (patrón real CBE 21/11/2012)
    filas.append(_fila_plana(
        Open=0.02, High=0.02, Low=0.02, Close=0.02,
        SMA200=SMA200_INICIO + (N_WARMUP + 3) * SMA200_INCREMENTO,
    ))

    # Día 204 — recuperación real, mismo nivel que antes del día corrupto
    filas.append(_fila_plana(
        Open=202.5, High=204.0, Low=201.0, Close=203.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 4) * SMA200_INCREMENTO,
    ))

    # Día 205 — salida real: Close cruza el stop real (194.38)
    filas.append(_fila_plana(
        Open=196.0, High=196.5, Low=189.5, Close=190.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 5) * SMA200_INCREMENTO,
    ))

    df = pd.DataFrame(filas, index=fechas)
    return {"TEST": df}


class TestEjecutarBacktestDiaCorrupto:

    def test_dia_corrupto_no_cierra_la_posicion_contra_precio_basura(self):
        datos = _construir_escenario_con_dia_corrupto()
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1, (
            f"Se esperaba exactamente 1 trade (la posición sobrevive el "
            f"día corrupto y se cierra más tarde, a un precio real) -- "
            f"se obtuvieron {len(trades)}: {trades}"
        )
        trade = trades[0]

        # Ningún trade debe ejecutarse a un precio "basura" -- el ratio
        # salida/entrada de una salida real (incluso una pérdida grande)
        # nunca cae por debajo de un 50%; el precio corrupto (0.02$
        # sobre una entrada de 205.05$) da un ratio de ~0.0001.
        ratio = trade["salida"] / trade["entrada"]
        assert ratio > 0.5, (
            f"El trade se cerró a un precio que parece el dato corrupto "
            f"del día 203 (ratio salida/entrada={ratio:.5f}) -- la "
            f"guardia no está descartando la barra corrupta."
        )

        # El precio de salida debe corresponder al cruce REAL del stop
        # en el día 205 (~194.38, mismo nivel que en
        # test_backtest_gap_stops.py con el mismo escenario de trailing),
        # no al 0.02$ del día corrupto.
        assert trade["salida"] == pytest.approx(194.38, abs=0.5)
        assert trade["pnl"] > -3000  # una pérdida real de este tamaño de
        # posición es del orden de cientos de $, nunca miles por un solo
        # trade en este escenario -- descarta cualquier resto del precio
        # corrupto colándose en el cálculo.


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
