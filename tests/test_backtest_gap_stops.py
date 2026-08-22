"""
Tests — simulación de gap en entradas y salidas por stop
(backtest_expandido.py)
========================================================================

Iniciativa "backtest más fiel a la realidad", Fase 1 (22/08/2026).

El motor, con SALIDA_POR_CIERRE=True, decidía la salida comparando el
Close del día contra el stop, pero antes de este fix SIEMPRE rellenaba
el precio de salida con el nivel TEÓRICO del stop, nunca con el Open
real — ignorando que un gap bajista en la apertura puede atravesar el
stop antes de que el mercado llegue a cotizar a ese nivel exacto (caso
real: ANET, 05-06/08/2026, stop teórico 196,40, ejecución real 192,545
por gap bajista de apertura a 192,41 — ver sección 13 del contexto).

Las entradas (BUY STOP) tenían el mismo problema, lado contrario: si el
Open del día de entrada ya abría por encima del buy_stop (gap alcista),
el motor seguía usando el buy_stop teórico como precio de entrada, en
vez del Open real (peor precio para el comprador).

Tres niveles de test:
  1. Unitario — salidas : calcular_precio_salida_stop()
  2. Unitario — entradas: calcular_precio_entrada_stop()
  3. Integración: ejecutar_backtest() de extremo a extremo con un
     universo sintético de un solo ticker, variando el gap en la
     entrada y en la salida por separado, para confirmar que el motor
     completo usa de verdad estas funciones (no solo que sean correctas
     en aislamiento).
"""

import pandas as pd
import pytest

from backtest_expandido import (
    calcular_precio_entrada_stop,
    calcular_precio_salida_stop,
    ejecutar_backtest,
)


# ============================================================
# 1. UNITARIOS — calcular_precio_salida_stop()
# ============================================================

class TestCalcularPrecioSalidaStop:

    def test_reproduce_incidente_anet_gap_bajista(self):
        """
        Caso real ANET (05-06/08/2026): stop teórico 196,40, el Open del
        día de ejecución ya abrió en 192,41 (gap bajista overnight,
        cierre anterior 197,31). El precio de ejecución simulado debe
        ser ese Open, no el nivel teórico del stop.
        """
        bar = {"Open": 192.41}
        assert calcular_precio_salida_stop(bar, stop=196.40) == 192.41

    def test_sin_gap_mantiene_precio_teorico_del_stop(self):
        """
        Si el Open sigue por encima del stop, el cruce fue intradía (no
        por gap) — el comportamiento anterior (precio teórico del stop)
        se mantiene intacto. No debe verse afectado por este cambio.
        """
        bar = {"Open": 197.31}
        assert calcular_precio_salida_stop(bar, stop=196.40) == 196.40

    def test_open_exactamente_en_el_stop_usa_el_stop(self):
        """Caso límite: Open == stop → no hay gap real, se usa el nivel."""
        bar = {"Open": 196.40}
        assert calcular_precio_salida_stop(bar, stop=196.40) == 196.40

    def test_gap_alcista_no_afecta_salida_de_stop(self):
        """
        Un gap ALCISTA (Open muy por encima del stop) no cambia nada —
        solo importa si el Open está POR DEBAJO o igual al stop.
        """
        bar = {"Open": 250.0}
        assert calcular_precio_salida_stop(bar, stop=196.40) == 196.40


# ============================================================
# 2. UNITARIOS — calcular_precio_entrada_stop()
# ============================================================

class TestCalcularPrecioEntradaStop:

    def test_gap_alcista_entra_al_open_real_no_al_buy_stop_teorico(self):
        """
        Gap alcista: el Open del día de entrada ya abrió por encima del
        buy_stop (205,05). El precio de entrada simulado debe ser ese
        Open (210,0) — peor precio para el comprador, no el teórico.
        """
        bar_entrada = {"Open": 210.0}
        assert calcular_precio_entrada_stop(bar_entrada, buy_stop=205.05) == 210.0

    def test_sin_gap_mantiene_precio_teorico_del_buy_stop(self):
        """
        Si el Open sigue por debajo del buy_stop, el cruce fue intradía
        (no por gap) — el comportamiento anterior (precio teórico del
        buy_stop) se mantiene intacto.
        """
        bar_entrada = {"Open": 200.5}
        assert calcular_precio_entrada_stop(bar_entrada, buy_stop=205.05) == 205.05

    def test_open_exactamente_en_el_buy_stop_usa_el_buy_stop(self):
        """Caso límite: Open == buy_stop → no hay gap real, se usa el nivel."""
        bar_entrada = {"Open": 205.05}
        assert calcular_precio_entrada_stop(bar_entrada, buy_stop=205.05) == 205.05

    def test_gap_bajista_no_afecta_entrada_por_stop(self):
        """
        Un gap BAJISTA (Open muy por debajo del buy_stop) no cambia
        nada — solo importa si el Open está POR ENCIMA o igual al
        buy_stop.
        """
        bar_entrada = {"Open": 150.0}
        assert calcular_precio_entrada_stop(bar_entrada, buy_stop=205.05) == 205.05


# ============================================================
# 3. INTEGRACIÓN — ejecutar_backtest() de extremo a extremo
# ============================================================

ATR_FIJO = 5.0
CLOSE_BASE = 200.0
SMA50_FIJO = 195.0
SMA200_INICIO = 190.0
SMA200_INCREMENTO = 0.01
N_WARMUP = 200  # índices 0..199 — detectar_senal exige i >= 200


def _fila_plana(**overrides):
    fila = {
        "Open": CLOSE_BASE, "High": CLOSE_BASE, "Low": CLOSE_BASE,
        "Close": CLOSE_BASE, "Volume": 1_000_000,
        "SMA50": SMA50_FIJO, "ATR": ATR_FIJO,
    }
    fila.update(overrides)
    return fila


def _construir_escenario(gap_en_entrada: bool = False, gap_en_salida: bool = False) -> dict:
    """
    Construye un único ticker sintético "TEST" que:
      - Pasa 200 días de calentamiento con tendencia alcista
        (SMA50 > SMA200, SMA200 en ascenso) — requisito de detectar_senal.
      - Día 198: pullback (Close < SMA50 - 0.75*ATR).
      - Día 200 (señal): recuperación (Close > SMA50) — dispara señal.
        buy_stop = High(día 200) + BUFFER = 205,05
      - Día 201 (entrada): High cruza el buy_stop → abre posición.
          gap_en_entrada=True  → Open ya por encima del buy_stop (210,0)
          gap_en_entrada=False → Open por debajo del buy_stop (200,5)
      - Día 202: sube el trailing stop (a 194,38) sin disparar salida.
      - Día 203 (salida): Close cruza el stop —
          gap_en_salida=True  → Open también por debajo del stop (ANET)
          gap_en_salida=False → Open por encima del stop (sin gap)
    """
    fechas = pd.bdate_range(start="2016-01-04", periods=N_WARMUP + 4)

    filas = []
    for i in range(N_WARMUP):
        sma200 = SMA200_INICIO + i * SMA200_INCREMENTO
        filas.append(_fila_plana(SMA200=sma200))

    # Día 198 (índice 198, dentro de la ventana i-3..i-1 del día señal 200):
    # pullback por debajo de SMA50 - 0.75*ATR = 195 - 3.75 = 191.25
    filas[198] = _fila_plana(
        Open=185.0, High=186.0, Low=184.0, Close=185.0,
        SMA200=SMA200_INICIO + 198 * SMA200_INCREMENTO,
    )

    # Día 200 — señal (i == 200, mínimo exigido por el motor)
    filas.append(_fila_plana(
        Open=200.0, High=205.0, Low=198.0, Close=200.0,
        SMA200=SMA200_INICIO + N_WARMUP * SMA200_INCREMENTO,  # 192.00
    ))

    # Día 201 — entrada: buy_stop = High(día 200) + BUFFER = 205.05
    if gap_en_entrada:
        # Gap alcista: Open ya por encima del buy_stop → entra al Open real.
        fila_entrada = _fila_plana(
            Open=210.0, High=212.0, Low=209.0, Close=211.0,
            SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
        )
    else:
        # Sin gap: Open por debajo del buy_stop → precio teórico intacto.
        fila_entrada = _fila_plana(
            Open=200.5, High=206.0, Low=199.0, Close=203.0,
            SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
        )
    filas.append(fila_entrada)

    # Día 202 — sube el trailing stop (High=206 → nuevo_stop≈194.38),
    # sin disparar salida (Close=203 > 194.38)
    filas.append(_fila_plana(
        Open=203.0, High=206.0, Low=202.0, Close=203.0,
        SMA200=SMA200_INICIO + (N_WARMUP + 2) * SMA200_INCREMENTO,
    ))

    # Día 203 — salida: Close(190) cruza el stop (194.38) en los dos casos.
    if gap_en_salida:
        # ANET: Open ya por debajo del stop → ejecución real al Open.
        fila_salida = _fila_plana(
            Open=188.0, High=190.5, Low=187.5, Close=190.0,
            SMA200=SMA200_INICIO + (N_WARMUP + 3) * SMA200_INCREMENTO,
        )
    else:
        # Sin gap: Open sigue por encima del stop → precio teórico intacto.
        fila_salida = _fila_plana(
            Open=196.0, High=196.5, Low=189.5, Close=190.0,
            SMA200=SMA200_INICIO + (N_WARMUP + 3) * SMA200_INCREMENTO,
        )
    filas.append(fila_salida)

    df = pd.DataFrame(filas, index=fechas)
    return {"TEST": df}


class TestEjecutarBacktestGapEnSalida:

    def test_gap_bajista_ejecuta_al_open_no_al_stop_teorico(self):
        """
        Reproduce el caso ANET dentro del motor completo: el trade
        resultante debe salir al Open real (188.0), no al stop
        teórico (194.38). Entrada sin gap, para aislar la variable.
        """
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=True)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "TEST"
        assert trade["entrada"] == pytest.approx(205.05, abs=0.01)
        assert trade["salida"] == pytest.approx(188.0, abs=0.01)
        # El stop teórico (194.38) NO debe ser el precio usado.
        assert trade["salida"] != pytest.approx(194.38, abs=0.01)

    def test_sin_gap_mantiene_comportamiento_anterior_stop_teorico(self):
        """
        Caso de control: el Open NO cruza el stop (solo declive
        intradía). El precio de salida debe seguir siendo el nivel
        teórico del stop — el cambio no debe afectar a este caso.
        """
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "TEST"
        assert trade["entrada"] == pytest.approx(205.05, abs=0.01)
        assert trade["salida"] == pytest.approx(194.38, abs=0.01)


class TestEjecutarBacktestGapEnEntrada:

    def test_gap_alcista_entra_al_open_no_al_buy_stop_teorico(self):
        """
        Gap alcista en el día de entrada: el trade resultante debe
        entrar al Open real (210,0), no al buy_stop teórico (205,05).

        NOTA: no se comprueba aquí el precio de salida — el High de 212
        del propio día de entrada ya eleva el trailing stop ese mismo
        día (la gestión de posición corre también el día en que se
        abre, usando el High de esa jornada), así que el nivel de stop
        resultante no es el mismo que en el escenario sin gap de
        entrada. Eso es correcto y esperado, no un efecto de este fix;
        el comportamiento de salida ya se prueba por separado y de
        forma aislada en TestEjecutarBacktestGapEnSalida.
        """
        datos = _construir_escenario(gap_en_entrada=True, gap_en_salida=False)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "TEST"
        assert trade["entrada"] == pytest.approx(210.0, abs=0.01)
        # El buy_stop teórico (205,05) NO debe ser el precio usado.
        assert trade["entrada"] != pytest.approx(205.05, abs=0.01)

    def test_sin_gap_en_entrada_mantiene_comportamiento_anterior(self):
        """
        Caso de control: el Open del día de entrada NO cruza el
        buy_stop (solo lo alcanza intradía vía High). El precio de
        entrada debe seguir siendo el nivel teórico del buy_stop.
        """
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "TEST"
        assert trade["entrada"] == pytest.approx(205.05, abs=0.01)

    def test_gap_en_entrada_y_en_salida_combinados(self):
        """
        Ambos gaps a la vez (entrada alcista + salida bajista) — confirma
        que las dos funciones actúan de forma independiente sin
        interferir entre sí.
        """
        datos = _construir_escenario(gap_en_entrada=True, gap_en_salida=True)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["entrada"] == pytest.approx(210.0, abs=0.01)
        assert trade["salida"] == pytest.approx(188.0, abs=0.01)
