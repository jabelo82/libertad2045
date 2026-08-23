"""
Tests — conversión de CAPITAL_INICIAL y APORTACION_ANUAL a USD al tipo
de cambio EUR/USD real (backtest_expandido.py)
========================================================================

Iniciativa "backtest más fiel a la realidad", Fase 6 (22/08/2026).

Hallazgo previo: CAPITAL_INICIAL=4000.0 y APORTACION_ANUAL=4000.0 se
sumaban directamente a la variable "capital" del motor, que
funcionalmente es USD (financia compras a last_price, en USD). En la
realidad Javier aporta 4.000€ REALES cada año a la cuenta IBKR, que se
convierten a dólares al tipo de cambio de ESE día concreto antes de
poder comprar nada — el motor lo ignoraba, tratando 4.000€ como si
fueran 4.000$ fijos, sin importar el año ni el EUR/USD real.

Esto es DISTINTO de la capa de reporte de la Fase 5 (convierte el
resultado final a EUR para mostrarlo, sin tocar el sizing). Este
cambio SÍ afecta al comportamiento real del motor: el capital
disponible cada año para dimensionar posiciones pasa a depender del
EUR/USD real de ese momento — ver convertir_aportacion_a_usd() y el
aviso en el docstring de ejecutar_backtest().

Cinco niveles de test:
  1. Unitario — convertir_eur_a_usd() (inversa de convertir_a_eur(),
     Fase 5).
  2. Unitario — convertir_aportacion_a_usd() (fail-safe incluido).
  3. Integración: ejecutar_backtest() de extremo a extremo, con y sin
     `eurusd`, para el capital inicial y la aportación anual.
  4. Unitario — calcular_posicion(): confirma que la conversión SÍ
     puede cambiar el nº de acciones que dimensiona una entrada (no es
     un cambio puramente de reporte, a diferencia de la Fase 5).
  5. calcular_metricas(capital_inicial_usd=...) — "Opción A" aprobada
     por Javier (22/08/2026) para el hallazgo de retorno_total/
     "capital_inicial" calculados contra la constante CAPITAL_INICIAL
     en vez del capital real convertido. Incluye
     capital_inicial_usd_para_reporte(), el helper que cierra el
     riesgo de desincronización entre lo que usó el motor por dentro
     y lo que calcula el caller por fuera.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import pytest

from backtest_expandido import (
    convertir_eur_a_usd,
    convertir_aportacion_a_usd,
    capital_inicial_usd_para_reporte,
    calcular_posicion,
    calcular_metricas,
    ejecutar_backtest,
    CAPITAL_INICIAL,
    APORTACION_ANUAL,
)

from test_backtest_gap_stops import _fila_plana, _construir_escenario


# ============================================================
# 1. UNITARIOS — convertir_eur_a_usd()
# ============================================================

class TestConvertirEurAUsd:

    def test_caso_verificable_a_mano(self):
        """Caso pedido explícitamente: EUR/USD=1.20 -> 4000€ = 4800$,
        no 4000$ directos."""
        assert convertir_eur_a_usd(4000.0, 1.20) == pytest.approx(4800.0)

    def test_rate_uno_no_cambia_el_monto(self):
        assert convertir_eur_a_usd(4000.0, 1.0) == pytest.approx(4000.0)

    def test_es_la_inversa_exacta_de_convertir_a_eur(self):
        from backtest_expandido import convertir_a_eur
        eur = 4000.0
        usd = convertir_eur_a_usd(eur, 1.1678)
        assert convertir_a_eur(usd, 1.1678) == pytest.approx(eur)

    def test_rate_cero_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_eur_a_usd(4000.0, 0.0))

    def test_rate_none_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_eur_a_usd(4000.0, None))

    def test_rate_nan_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_eur_a_usd(4000.0, float("nan")))

    def test_rate_negativo_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_eur_a_usd(4000.0, -1.0))


# ============================================================
# 2. UNITARIOS — convertir_aportacion_a_usd()
# ============================================================

class TestConvertirAportacionAUsd:

    def _eurusd(self):
        fechas = pd.DatetimeIndex(["2019-12-30", "2019-12-31",
                                    "2020-01-02", "2020-01-03"])
        return pd.DataFrame({"Close": [1.10, 1.10, 1.25, 1.25]}, index=fechas)

    def test_eurusd_none_devuelve_monto_sin_convertir_fail_safe(self):
        """
        Requisito #3 del encargo: sin `eurusd`, comportamiento IDÉNTICO
        al actual (4000.0 como capital nativo, sin convertir).
        """
        assert convertir_aportacion_a_usd(4000.0, None, pd.Timestamp("2020-01-02")) == 4000.0

    def test_convierte_al_tipo_de_cambio_de_la_fecha_exacta(self):
        eurusd = self._eurusd()
        # Día de la aportación de 2020: rate=1.25, NO el rate de 2019
        # (1.10) ni ningún valor fijo.
        resultado = convertir_aportacion_a_usd(
            APORTACION_ANUAL, eurusd, pd.Timestamp("2020-01-02"))
        assert resultado == pytest.approx(4000.0 * 1.25)

    def test_fechas_distintas_dan_conversiones_distintas(self):
        """
        Verifica explícitamente que NO se usa un tipo de cambio fijo
        para todas las aportaciones — cada año debe usar el suyo.
        """
        eurusd = self._eurusd()
        aportacion_2019 = convertir_aportacion_a_usd(
            APORTACION_ANUAL, eurusd, pd.Timestamp("2019-12-31"))
        aportacion_2020 = convertir_aportacion_a_usd(
            APORTACION_ANUAL, eurusd, pd.Timestamp("2020-01-02"))
        assert aportacion_2019 == pytest.approx(4000.0 * 1.10)
        assert aportacion_2020 == pytest.approx(4000.0 * 1.25)
        assert aportacion_2019 != pytest.approx(aportacion_2020)

    def test_sin_tipo_de_cambio_disponible_esa_fecha_cae_a_monto_sin_convertir(self):
        """
        Fail-safe cuando `eurusd` está presente pero no cubre la fecha
        pedida (NaN de obtener_tipo_cambio) — nunca debe bloquear el
        backtest ni inventar un tipo de cambio.
        """
        eurusd = self._eurusd()
        resultado = convertir_aportacion_a_usd(
            APORTACION_ANUAL, eurusd, pd.Timestamp("2000-01-01"))
        assert resultado == 4000.0


# ============================================================
# 3. INTEGRACIÓN — ejecutar_backtest(): capital inicial y aportación
# ============================================================

def _escenario_dos_anios_sin_señal():
    """
    4 días sintéticos que cruzan un año (2019->2020), sin ningún
    ticker con historial suficiente para disparar detectar_senal()
    (exige i>=200; aquí i llega como mucho a 3) — aísla por completo
    la mecánica de capital inicial / aportación anual del resto del
    motor (señales, stops, comisiones, slippage).
    """
    fechas = pd.DatetimeIndex(["2019-12-30", "2019-12-31",
                                "2020-01-02", "2020-01-03"])
    df = pd.DataFrame([_fila_plana() for _ in fechas], index=fechas)
    return {"TEST": df}, fechas


class TestEjecutarBacktestCapitalInicial:

    def test_sin_eurusd_capital_inicial_identico_al_actual(self):
        """Requisito #3: sin `eurusd`, CAPITAL_INICIAL se usa tal cual
        — comportamiento idéntico al de antes de la Fase 6."""
        datos, fechas = _escenario_dos_anios_sin_señal()
        trades, curva, capital_final = ejecutar_backtest(datos)
        assert curva[0]["capital"] == pytest.approx(CAPITAL_INICIAL)

    def test_con_eurusd_capital_inicial_se_convierte_al_rate_del_dia_1(self):
        """Caso verificable a mano: EUR/USD=1.20 el primer día -> el
        motor arranca con 4800$, no 4000$."""
        datos, fechas = _escenario_dos_anios_sin_señal()
        eurusd = pd.DataFrame({"Close": [1.20] * len(fechas)}, index=fechas)
        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)
        assert curva[0]["capital"] == pytest.approx(4800.0)

    def test_sin_tipo_de_cambio_para_el_dia_1_cae_a_capital_sin_convertir(self):
        """
        `eurusd` presente pero sin cobertura para la fecha de inicio
        (la serie empieza DESPUÉS del día 1 del backtest) — fail-safe
        por fecha, no solo por `eurusd is None`.
        """
        datos, fechas = _escenario_dos_anios_sin_señal()
        eurusd = pd.DataFrame({"Close": [1.25, 1.25]}, index=fechas[2:])
        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)
        assert curva[0]["capital"] == pytest.approx(CAPITAL_INICIAL)


class TestEjecutarBacktestAportacionAnual:

    def _eurusd_dos_tipos(self, fechas):
        # 1.10 en 2019, 1.25 en 2020 — deliberadamente distintos para
        # confirmar que cada aportación usa el tipo de SU año.
        return pd.DataFrame(
            {"Close": [1.10, 1.10, 1.25, 1.25]}, index=fechas)

    def test_sin_eurusd_aportacion_identica_a_la_actual(self):
        """Requisito #3, mismo patrón para la aportación anual."""
        datos, fechas = _escenario_dos_anios_sin_señal()
        trades, curva, capital_final = ejecutar_backtest(datos)

        fila_2019 = next(f for f in curva if f["fecha"] == fechas[0])
        fila_2020 = next(f for f in curva if f["fecha"] == fechas[2])

        assert fila_2019["capital"] == pytest.approx(4000.0)
        assert fila_2020["capital"] == pytest.approx(8000.0)  # 4000 + 4000

    def test_aportacion_usa_el_tipo_de_cambio_de_su_propio_año(self):
        """
        Caso central del encargo: capital inicial a 1,10 (4400$),
        aportación de 2020 a 1,25 (5000$) -> capital tras la
        aportación = 9400$. Si el motor reutilizara el tipo de cambio
        del día 1 (1,10) para la aportación, daría 8800$ en vez de
        9400$ — este test distingue explícitamente los dos casos.
        """
        datos, fechas = _escenario_dos_anios_sin_señal()
        eurusd = self._eurusd_dos_tipos(fechas)
        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)

        fila_2019 = next(f for f in curva if f["fecha"] == fechas[0])
        fila_2020 = next(f for f in curva if f["fecha"] == fechas[2])

        assert fila_2019["capital"] == pytest.approx(4000.0 * 1.10)   # 4400.0
        assert fila_2020["capital"] == pytest.approx(
            4000.0 * 1.10 + 4000.0 * 1.25)                             # 9400.0
        assert fila_2020["capital"] != pytest.approx(8800.0)           # rate erróneo (fijo al del día 1)

    def test_sin_tipo_de_cambio_disponible_ninguna_conversion_cae_a_monto_sin_convertir(self):
        """
        Fail-safe por fecha (no por `eurusd is None`): la serie de
        `eurusd` solo cubre el ÚLTIMO día del escenario, así que
        obtener_tipo_cambio() (asof — solo rellena hacia ADELANTE
        desde datos ya existentes) devuelve NaN tanto para el día 1
        como para la fecha de la aportación, al no haber ningún dato
        de tipo de cambio en o antes de ninguna de las dos. Ambas
        conversiones deben caer a su monto EUR sin convertir — mismo
        resultado que sin `eurusd` en absoluto.
        """
        datos, fechas = _escenario_dos_anios_sin_señal()
        eurusd = pd.DataFrame({"Close": [1.30]}, index=fechas[3:])
        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)

        fila_2020 = next(f for f in curva if f["fecha"] == fechas[2])
        assert fila_2020["capital"] == pytest.approx(4000.0 + 4000.0)  # 8000.0


# ============================================================
# 4. UNITARIO — calcular_posicion(): la conversión SÍ cambia el
#    dimensionado (no es puramente de reporte, a diferencia de la
#    Fase 5)
# ============================================================

class TestSizingSensibleAlCapitalConvertido:

    def test_mas_capital_por_conversion_puede_pedir_mas_acciones(self):
        """
        Mismo día, mismo activo, mismo ATR — la ÚNICA variable es el
        capital de entrada (4000$ sin convertir vs 4800$ convertidos a
        EUR/USD=1.20, el caso verificable a mano del encargo). El
        stop_distance no cambia; el nº de acciones sí.
        """
        fechas = pd.bdate_range(start="2020-01-02", periods=1)
        df = pd.DataFrame([{
            "Open": 200.0, "High": 200.0, "Low": 200.0, "Close": 200.0,
            "Volume": 1_000_000, "SMA50": 195.0, "ATR": 4.0,
        }], index=fechas)

        capital_sin_convertir = 4000.0
        capital_convertido    = convertir_eur_a_usd(4000.0, 1.20)  # 4800.0

        shares_sin, sd_sin, _ = calcular_posicion(df, 0, capital_sin_convertir)
        shares_con, sd_con, _ = calcular_posicion(df, 0, capital_convertido)

        assert sd_sin == sd_con  # el riesgo por acción no cambia
        assert shares_sin == 2
        assert shares_con == 3
        assert shares_con > shares_sin  # la conversión SÍ altera el sizing real


# ============================================================
# 5. calcular_metricas(capital_inicial_usd=...) — "Opción A"
#    (22/08/2026, aprobada por Javier)
# ============================================================

class TestCalcularMetricasCapitalInicialUsd:
    """
    El hallazgo que quedó documentado como pendiente en el primer
    diff de la Fase 6: retorno_total y "capital_inicial" se calculaban
    contra la CONSTANTE CAPITAL_INICIAL (4000.0), no contra el capital
    en USD que el motor usó realmente el día 1 tras la conversión de
    esta misma Fase. Inofensivo mientras nadie active la conversión;
    roto de forma latente en cuanto se activa. Diseño aprobado
    ("Opción A"): calcular_metricas() gana un parámetro opcional
    `capital_inicial_usd`, retrocompatible (None -> comportamiento
    idéntico al de siempre).
    """

    def _trade_minimo(self, pnl):
        return {
            "symbol": "TEST", "clase": "ACCION",
            "fecha_entrada": pd.Timestamp("2020-01-02"),
            "fecha_salida": pd.Timestamp("2020-01-03"),
            "entrada": 100.0, "salida": 100.0 + pnl, "shares": 1,
            "comision": 0.0, "pnl": pnl,
            "resultado": "WIN" if pnl >= 0 else "LOSS",
            "capital": 0.0,
        }

    def test_retrocompatible_sin_capital_inicial_usd_usa_la_constante(self):
        """Requisito explícito: sin pasar el parámetro nuevo, el
        resultado debe ser IDÉNTICO al de antes de este cambio."""
        trades = [self._trade_minimo(1000.0)]
        curva  = [{"fecha": pd.Timestamp("2020-01-02"), "capital": CAPITAL_INICIAL}]

        metricas = calcular_metricas(trades, curva, capital_final=5000.0)

        assert metricas["capital_inicial"] == CAPITAL_INICIAL
        assert metricas["retorno_total"] == pytest.approx(
            (5000.0 - CAPITAL_INICIAL) / CAPITAL_INICIAL)

    def test_con_capital_inicial_usd_explicito_retorno_contra_el_valor_real(self):
        """
        Caso central del encargo: capital_final en USD ya convertido
        por la Fase 6 (el motor arrancó con 4800$, no 4000$, porque
        EUR/USD=1,20 el día 1). retorno_total debe salir calculado
        contra esos 4800$ reales -- el caso que hasta este commit
        estaba roto de forma latente.
        """
        capital_inicial_real = 4800.0  # 4000€ * 1.20 (Fase 6)
        capital_final         = 9600.0  # se dobló

        trades = [self._trade_minimo(4800.0)]
        curva  = [{"fecha": pd.Timestamp("2020-01-02"), "capital": capital_inicial_real}]

        metricas = calcular_metricas(
            trades, curva, capital_final, capital_inicial_usd=capital_inicial_real)

        assert metricas["capital_inicial"] == capital_inicial_real
        assert metricas["retorno_total"] == pytest.approx(1.0)  # (9600-4800)/4800
        # La cifra ANTES de este fix (contra la constante 4000.0
        # EUR-nominal) habría sido distinta -- y equivocada.
        assert metricas["retorno_total"] != pytest.approx(
            (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL)


class TestCapitalInicialUsdParaReporte:
    """capital_inicial_usd_para_reporte() — el helper que el caller
    externo usa para no duplicar la fórmula de conversión."""

    def test_eurusd_none_devuelve_none_fail_safe(self):
        datos, fechas = _escenario_dos_anios_sin_señal()
        assert capital_inicial_usd_para_reporte(datos, None) is None

    def test_datos_vacio_devuelve_none_fail_safe(self):
        assert capital_inicial_usd_para_reporte({}, pd.DataFrame({"Close": [1.2]})) is None

    def test_coincide_con_el_capital_que_uso_el_motor_por_dentro(self):
        """
        Cierra el riesgo de desincronización señalado en el diseño de
        la Opción A: el valor que calcula este helper desde FUERA debe
        ser exactamente el mismo que ejecutar_backtest() usó por
        DENTRO como capital de partida.
        """
        datos, fechas = _escenario_dos_anios_sin_señal()
        eurusd = pd.DataFrame({"Close": [1.20] * len(fechas)}, index=fechas)

        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)
        capital_inicial_usd = capital_inicial_usd_para_reporte(datos, eurusd)

        assert capital_inicial_usd == pytest.approx(4800.0)
        assert capital_inicial_usd == pytest.approx(curva[0]["capital"])


class TestPipelineCompletoRetornoTotalConFase6:

    def test_retorno_total_end_to_end_usa_el_capital_inicial_real_no_la_constante(self):
        """
        Pipeline real de extremo a extremo (no valores inventados a
        mano): ejecutar_backtest() con `eurusd` activo, un trade real,
        capital_inicial_usd calculado con el helper de reporte, y
        calcular_metricas() con ese valor. Confirma que retorno_total
        coincide con la fórmula correcta (contra el capital real) y
        NO con la fórmula antigua (contra la constante 4000.0).
        """
        datos  = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        fechas = datos["TEST"].index
        eurusd = pd.DataFrame({"Close": [1.20] * len(fechas)}, index=fechas)

        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)
        capital_inicial_usd = capital_inicial_usd_para_reporte(datos, eurusd)
        assert capital_inicial_usd == pytest.approx(4800.0)

        metricas = calcular_metricas(
            trades, curva, capital_final, capital_inicial_usd=capital_inicial_usd)

        retorno_correcto  = (capital_final - capital_inicial_usd) / capital_inicial_usd
        retorno_equivocado = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL

        assert metricas["retorno_total"] == pytest.approx(retorno_correcto)
        assert metricas["retorno_total"] != pytest.approx(retorno_equivocado)
        assert metricas["capital_inicial"] == pytest.approx(capital_inicial_usd)
