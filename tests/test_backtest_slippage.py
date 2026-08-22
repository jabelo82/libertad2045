"""
Tests — aproximación de slippage por liquidez (backtest_expandido.py)
========================================================================

Iniciativa "backtest más fiel a la realidad", Fase 2 (22/08/2026).

Motivo: la comparativa del Exp.46 retomado (Fase 2, Russell 1000)
mostró que el diferencial de ese índice tiene ~4x menos volumen medio
diario que el S&P500 actual (322M$ vs 79M$) — un backtest que ignora
slippage penaliza igual a ambos universos, cuando en la práctica el
más ilíquido sufriría más.

LIMITACIÓN EXPLÍCITA (ver docstring de calcular_slippage()): esto es
una APROXIMACIÓN basada en el % de volumen diario que representa la
orden, no una simulación precisa — el backtest usa datos diarios, no
tick-by-tick ni libro de órdenes real.

Modelo: forma de "ley de raíz cuadrada" (square-root law) de impacto
de mercado — Almgren et al. (2005), Bouchaud/Farmer/Lillo (2008). La
constante de calibración (SLIPPAGE_K) es una elección propia, no
calibrada con datos reales de ejecución de este proyecto.

El slippage se aplica DESPUÉS de la corrección de gap de la Fase 1
(commit 8dfcd4d): primero se determina el precio de mercado del día
(gap-corregido), y el slippage es el coste ADICIONAL de que la propia
orden consuma liquidez alrededor de ese precio de referencia — son dos
efectos distintos (uno exógeno — el gap —, otro causado por el tamaño
de la propia orden).

Tres niveles de test:
  1. Unitario: calcular_slippage() en aislamiento.
  2. Integración — volumen alto: el resultado apenas cambia respecto a
     los tests de la Fase 1 (slippage << tolerancia habitual).
  3. Integración — volumen bajo: slippage significativo, caso numérico
     verificable a mano en ambos lados (entrada y salida).
"""

import pandas as pd
import pytest

from backtest_expandido import (
    calcular_precio_entrada_stop,
    calcular_precio_salida_stop,
    calcular_slippage,
    ejecutar_backtest,
)

from test_backtest_gap_stops import (
    ATR_FIJO,
    N_WARMUP,
    SMA200_INCREMENTO,
    SMA200_INICIO,
    _fila_plana,
)


# ============================================================
# 1. UNITARIOS — calcular_slippage()
# ============================================================

class TestCalcularSlippage:

    def test_volumen_alto_orden_pequena_slippage_minimo(self):
        """
        Orden de 2 acciones sobre 1.000.000 de volumen diario (fracción
        0,0002%) — el slippage debe ser prácticamente cero, coherente
        con "lo que ya asume el backtest hoy" para órdenes pequeñas.
        """
        slippage = calcular_slippage(shares_orden=2, volumen_dia=1_000_000, atr=5.0)
        assert slippage == pytest.approx(0.000707, abs=1e-5)
        assert slippage < 0.01  # despreciable en términos de precio

    def test_volumen_bajo_orden_grande_slippage_significativo(self):
        """
        Caso verificable a mano: orden de 10.000 acciones sobre 100.000
        de volumen diario (10% del volumen) con ATR=5:
            fracción = 10.000 / 100.000 = 0,1
            slippage = 0,10 · 5,0 · sqrt(0,1) = 0,10 · 5,0 · 0,316228
                     = 0,158114
        """
        slippage = calcular_slippage(shares_orden=10_000, volumen_dia=100_000, atr=5.0)
        assert slippage == pytest.approx(0.158114, abs=1e-5)

    def test_escala_con_raiz_cuadrada_no_linealmente(self):
        """
        Cuadruplicar la fracción de volumen debe DUPLICAR el slippage
        (raíz cuadrada), no cuadruplicarlo — confirma la forma
        funcional del modelo, no una proporcionalidad lineal.
        """
        s1 = calcular_slippage(shares_orden=1_000, volumen_dia=100_000, atr=5.0)
        s4 = calcular_slippage(shares_orden=4_000, volumen_dia=100_000, atr=5.0)
        assert s4 == pytest.approx(s1 * 2, rel=1e-6)

    def test_volumen_cero_no_penaliza_fail_safe(self):
        """Sin dato de volumen fiable (0), fail-safe: no penaliza."""
        assert calcular_slippage(shares_orden=100, volumen_dia=0, atr=5.0) == 0.0

    def test_volumen_nan_no_penaliza_fail_safe(self):
        assert calcular_slippage(shares_orden=100, volumen_dia=float("nan"), atr=5.0) == 0.0

    def test_atr_invalido_no_penaliza_fail_safe(self):
        assert calcular_slippage(shares_orden=100, volumen_dia=100_000, atr=0.0) == 0.0
        assert calcular_slippage(shares_orden=100, volumen_dia=100_000, atr=float("nan")) == 0.0

    def test_shares_cero_no_penaliza(self):
        assert calcular_slippage(shares_orden=0, volumen_dia=100_000, atr=5.0) == 0.0


# ============================================================
# 2. INTEGRACIÓN — ejecutar_backtest() de extremo a extremo
# ============================================================
#
# Reutiliza la misma construcción de escenario sintético que la Fase 1
# (tests/test_backtest_gap_stops.py), variando solo el Volumen de los
# días de entrada y salida para aislar el efecto del slippage.

def _construir_escenario_liquidez(volumen_entrada: float, volumen_salida: float) -> dict:
    """
    Mismo escenario base que test_backtest_gap_stops._construir_escenario()
    (sin gaps en ningún lado — Open dentro de los niveles teóricos),
    variando el Volumen del día de entrada y de salida para poder medir
    el efecto aislado del slippage por liquidez.
    """
    fechas = pd.bdate_range(start="2016-01-04", periods=N_WARMUP + 4)

    filas = []
    for i in range(N_WARMUP):
        filas.append(_fila_plana(SMA200=SMA200_INICIO + i * SMA200_INCREMENTO))

    filas[198] = _fila_plana(
        Open=185.0, High=186.0, Low=184.0, Close=185.0,
        SMA200=SMA200_INICIO + 198 * SMA200_INCREMENTO,
    )

    # Día 200 — señal. buy_stop = High + BUFFER = 205,05
    filas.append(_fila_plana(
        Open=200.0, High=205.0, Low=198.0, Close=200.0,
        SMA200=SMA200_INICIO + N_WARMUP * SMA200_INCREMENTO,
    ))

    # Día 201 — entrada, SIN gap (Open=200,5 < buy_stop=205,05)
    filas.append(_fila_plana(
        Open=200.5, High=206.0, Low=199.0, Close=203.0, Volume=volumen_entrada,
        SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
    ))

    # Día 202 — sube el trailing stop a 194,38, sin disparar salida.
    filas.append(_fila_plana(
        Open=203.0, High=206.0, Low=202.0, Close=203.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 2) * SMA200_INCREMENTO,
    ))

    # Día 203 — salida, SIN gap (Open=196 > stop=194,38).
    filas.append(_fila_plana(
        Open=196.0, High=196.5, Low=189.5, Close=190.0, Volume=volumen_salida,
        SMA200=SMA200_INICIO + (N_WARMUP + 3) * SMA200_INCREMENTO,
    ))

    df = pd.DataFrame(filas, index=fechas)
    return {"TEST": df}


class TestEjecutarBacktestSlippageVolumenAlto:

    def test_volumen_alto_apenas_cambia_respecto_a_solo_gap(self):
        """
        Con volumen alto (1.000.000, igual que en los tests de la Fase
        1), el slippage es << 0,01 — el comportamiento debe seguir
        siendo prácticamente el mismo que sin slippage (solo gap):
        entrada ≈ 205,05 teórico, salida ≈ 194,38 teórico.
        """
        datos = _construir_escenario_liquidez(volumen_entrada=1_000_000,
                                               volumen_salida=1_000_000)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        # Tolerancia MUY ajustada — si el slippage fuera significativo
        # aquí, este test fallaría.
        assert trade["entrada"] == pytest.approx(205.05, abs=0.001)
        assert trade["salida"] == pytest.approx(194.38, abs=0.001)


class TestEjecutarBacktestSlippageVolumenBajo:

    def test_volumen_bajo_penaliza_entrada_y_salida_caso_verificable(self):
        """
        Caso numérico verificable a mano: shares=2, Volume=50 en ambos
        días (entrada y salida) — fracción de volumen = 2/50 = 0,04,
        ATR=5:
            slippage = 0,10 · 5,0 · sqrt(0,04) = 0,10 · 5,0 · 0,2 = 0,10

        Entrada: precio teórico 205,05 (sin gap) + slippage 0,10 = 205,15
        Salida : precio teórico 194,38 (sin gap) − slippage 0,10 = 194,28
        """
        datos = _construir_escenario_liquidez(volumen_entrada=50, volumen_salida=50)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["entrada"] == pytest.approx(205.15, abs=0.01)
        assert trade["salida"] == pytest.approx(194.28, abs=0.01)
        # El slippage debe EMPEORAR el precio en ambos lados respecto al
        # nivel teórico (paga más al entrar, cobra menos al salir).
        assert trade["entrada"] > 205.05
        assert trade["salida"] < 194.38

    def test_volumen_bajo_solo_en_entrada_no_afecta_a_la_salida(self):
        """Aislamiento: volumen bajo solo en el día de entrada no debe
        afectar al precio de salida (volumen alto ese día)."""
        datos = _construir_escenario_liquidez(volumen_entrada=50,
                                               volumen_salida=1_000_000)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["entrada"] == pytest.approx(205.15, abs=0.01)
        assert trade["salida"] == pytest.approx(194.38, abs=0.001)
