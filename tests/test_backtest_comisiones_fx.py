"""
Tests — comisiones IBKR y coste de FX (backtest_expandido.py)
========================================================================

Iniciativa "backtest más fiel a la realidad", Fase 5 (22/08/2026).

Diagnóstico previo (confirmado por búsqueda exhaustiva en el código, no
supuesto): backtest_expandido.py no modelaba NINGUNA comisión ni NINGÚN
coste/conversión de divisa — trataba implícitamente 1 USD = 1 EUR
durante los 21 años del backtest, mientras que producción real sí opera
una cuenta EUR sobre acciones USD (dashboard.py::
_obtener_tipo_cambio_mercado(), EURUSD=X vía yfinance).

Diseño aprobado por Javier (22/08/2026) antes de escribir código:
  - Comisión IBKR: fija, ~1$/pata (entrada+salida = 2 patas por trade),
    calibrada con la reconciliación real del 21/08/2026.
  - FX: "capital dual" — el motor SIGUE dimensionando en USD sin tocar
    esa lógica (parámetro `eurusd` opcional, None por defecto =
    comportamiento idéntico a v4/Exp.48). Cuando se pasa, cada trade se
    enriquece con pnl_eur/pnl_trading_eur/efecto_fx_eur — el PnL de
    trading en USD convertido a EUR, separado del efecto puro del
    movimiento del tipo de cambio. curva_capital gana capital_eur
    (mark-to-market día a día).

Tres niveles de test:
  1. Unitario — comisión: calcular_comision_total().
  2. Unitario — FX: convertir_a_eur(), calcular_pnl_eur(),
     obtener_tipo_cambio().
  3. Integración: ejecutar_backtest() de extremo a extremo, con y sin
     el parámetro `eurusd`, para confirmar que el motor los usa de
     verdad y que el comportamiento sin `eurusd` es idéntico al de
     antes de esta Fase (salvo la comisión, que SIEMPRE se aplica).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import pytest

from backtest_expandido import (
    calcular_comision_total,
    convertir_a_eur,
    calcular_pnl_eur,
    obtener_tipo_cambio,
    ejecutar_backtest,
    COMISION_IBKR,
)

from test_backtest_gap_stops import (
    N_WARMUP,
    _construir_escenario,
)


# ============================================================
# 1. UNITARIOS — comisión
# ============================================================

class TestCalcularComisionTotal:

    def test_dos_patas_por_defecto(self):
        """Entrada + salida = 2 patas, ~1$ cada una (calibración real)."""
        assert calcular_comision_total() == pytest.approx(2 * COMISION_IBKR)

    def test_comision_por_pata_parametrizable(self):
        assert calcular_comision_total(comision_por_pata=2.5) == pytest.approx(5.0)


# ============================================================
# 2. UNITARIOS — FX
# ============================================================

class TestConvertirAEur:

    def test_conversion_basica(self):
        # rate = USD por 1 EUR -> EUR = USD / rate
        assert convertir_a_eur(1100.0, 1.0) == pytest.approx(1100.0)
        assert convertir_a_eur(1000.0, 1.20) == pytest.approx(833.333, abs=0.01)

    def test_rate_cero_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_a_eur(1000.0, 0.0))

    def test_rate_none_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_a_eur(1000.0, None))

    def test_rate_nan_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_a_eur(1000.0, float("nan")))

    def test_rate_negativo_devuelve_nan_fail_safe(self):
        assert pd.isna(convertir_a_eur(1000.0, -1.0))


class TestCalcularPnlEur:

    def test_caso_verificable_a_mano_movimiento_significativo(self):
        """
        Caso pedido explícitamente: el EUR/USD se mueve de forma
        significativa entre entrada y salida de una posición larga.

        Posición de 1.000$, gana 100$ en USD, tipo de cambio pasa de
        1,20 (entrada) a 1,00 (salida) — el EUR se debilita, los mismos
        dólares valen más euros al salir:
            valor_entrada_eur = 1000/1,20 =  833,33€
            valor_salida_eur  = 1100/1,00 = 1100,00€
            pnl_total_eur     = 266,67€
            pnl_trading_eur   = 100/1,00  = 100,00€
            efecto_fx_eur     = 266,67 - 100,00 = 166,67€
        """
        pnl_total_eur, pnl_trading_eur, efecto_fx_eur = calcular_pnl_eur(
            entry_value_usd=1000.0, pnl_usd=100.0,
            rate_entrada=1.20, rate_salida=1.00,
        )
        assert pnl_total_eur == pytest.approx(266.67, abs=0.01)
        assert pnl_trading_eur == pytest.approx(100.00, abs=0.01)
        assert efecto_fx_eur == pytest.approx(166.67, abs=0.01)
        # La descomposición debe cuadrar exactamente por construcción.
        assert pnl_total_eur == pytest.approx(pnl_trading_eur + efecto_fx_eur, abs=1e-9)

    def test_sin_movimiento_fx_efecto_es_cero(self):
        """Si el tipo de cambio no se mueve, todo el PnL en EUR es de
        trading — el efecto FX debe ser exactamente cero."""
        pnl_total_eur, pnl_trading_eur, efecto_fx_eur = calcular_pnl_eur(
            entry_value_usd=1000.0, pnl_usd=-50.0,
            rate_entrada=1.10, rate_salida=1.10,
        )
        assert efecto_fx_eur == pytest.approx(0.0, abs=1e-9)
        assert pnl_total_eur == pytest.approx(pnl_trading_eur, abs=1e-9)

    def test_rate_entrada_invalido_devuelve_nan_fail_safe(self):
        resultado = calcular_pnl_eur(1000.0, 100.0, 0.0, 1.0)
        assert all(pd.isna(v) for v in resultado)

    def test_rate_salida_invalido_devuelve_nan_fail_safe(self):
        resultado = calcular_pnl_eur(1000.0, 100.0, 1.0, float("nan"))
        assert all(pd.isna(v) for v in resultado)


class TestObtenerTipoCambio:

    def _eurusd_sintetico(self):
        fechas = pd.bdate_range(start="2016-01-04", periods=10)
        return pd.DataFrame({"Close": [1.10] * 5 + [1.05] * 5}, index=fechas)

    def test_busca_tipo_de_cambio_exacto(self):
        eurusd = self._eurusd_sintetico()
        rate = obtener_tipo_cambio(eurusd, eurusd.index[7])
        assert rate == pytest.approx(1.05)

    def test_asof_rellena_hacia_adelante_en_huecos(self):
        eurusd = self._eurusd_sintetico()
        # Fecha entre dos filas cacheadas -> debe usar la anterior (asof)
        fecha_hueco = eurusd.index[4] + pd.Timedelta(hours=12)
        rate = obtener_tipo_cambio(eurusd, fecha_hueco)
        assert rate == pytest.approx(1.10)

    def test_eurusd_none_devuelve_nan_fail_safe(self):
        assert pd.isna(obtener_tipo_cambio(None, pd.Timestamp("2020-01-01")))

    def test_eurusd_vacio_devuelve_nan_fail_safe(self):
        assert pd.isna(obtener_tipo_cambio(pd.DataFrame(), pd.Timestamp("2020-01-01")))

    def test_fecha_anterior_a_toda_la_serie_devuelve_nan(self):
        eurusd = self._eurusd_sintetico()
        rate = obtener_tipo_cambio(eurusd, pd.Timestamp("2000-01-01"))
        assert pd.isna(rate)


# ============================================================
# 3. INTEGRACIÓN — ejecutar_backtest() de extremo a extremo
# ============================================================

class TestEjecutarBacktestComision:

    def test_comision_se_aplica_siempre_sin_eurusd(self):
        """
        La comisión SIEMPRE se aplica, independientemente de si se pasa
        `eurusd` o no — es un coste real de ejecución, no una capa de
        reporte opcional (a diferencia del desglose en EUR).
        """
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 1
        trade = trades[0]
        assert trade["comision"] == pytest.approx(2 * COMISION_IBKR)
        # pnl teórico sin comisión: (194.38-205.05)*2 = -21.34
        # con comisión: -21.34 - 2.0 = -23.34
        assert trade["pnl"] == pytest.approx(-23.34, abs=0.01)
        # Sin eurusd, no debe haber campos de EUR en absoluto.
        assert "pnl_eur" not in trade
        assert "pnl_trading_eur" not in trade
        assert "efecto_fx_eur" not in trade

    def test_sin_eurusd_curva_capital_no_tiene_columna_eur(self):
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        trades, curva, capital_final = ejecutar_backtest(datos)
        assert all("capital_eur" not in fila for fila in curva)


class TestEjecutarBacktestFx:

    def _eurusd_con_salto(self, fechas):
        """
        1,20 hasta el día de entrada (inclusive), 1,00 desde el día
        siguiente hasta el final — reproduce el caso verificable a mano
        (movimiento significativo de EUR/USD entre entrada y salida).
        """
        fecha_entrada = fechas[N_WARMUP + 1]
        valores = [1.20 if f <= fecha_entrada else 1.00 for f in fechas]
        return pd.DataFrame({"Close": valores}, index=fechas)

    def test_desglose_eur_coincide_con_calculo_esperado(self):
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        fechas = datos["TEST"].index
        eurusd = self._eurusd_con_salto(fechas)

        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)

        assert len(trades) == 1
        trade = trades[0]

        # entry_value_usd = 205.05 * 2 = 410.10
        # pnl (con comisión) = -23.34
        # rate_entrada=1.20, rate_salida=1.00
        assert trade["pnl_trading_eur"] == pytest.approx(-23.34, abs=0.01)
        assert trade["efecto_fx_eur"] == pytest.approx(68.35, abs=0.01)
        assert trade["pnl_eur"] == pytest.approx(45.01, abs=0.01)
        # La descomposición debe cuadrar (con el margen del redondeo a 2 decimales).
        assert trade["pnl_eur"] == pytest.approx(
            trade["pnl_trading_eur"] + trade["efecto_fx_eur"], abs=0.02)

    def test_curva_capital_incluye_capital_eur_mark_to_market(self):
        datos = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        fechas = datos["TEST"].index
        eurusd = self._eurusd_con_salto(fechas)

        trades, curva, capital_final = ejecutar_backtest(datos, eurusd=eurusd)

        assert all("capital_eur" in fila for fila in curva)
        # Verifica una fila conocida: el día de entrada, capital sigue en
        # CAPITAL_INICIAL (4000.0, sin trades cerrados aún), rate=1.20.
        fila_entrada = next(f for f in curva if f["fecha"] == fechas[N_WARMUP + 1])
        assert fila_entrada["capital_eur"] == pytest.approx(4000.0 / 1.20, abs=0.01)

    def test_resultado_en_usd_identico_con_o_sin_eurusd(self):
        """
        El parámetro `eurusd` es puramente una capa de reporte — el
        resultado en USD (entrada, salida, pnl, capital) debe ser
        EXACTAMENTE igual con o sin él.
        """
        datos1 = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        datos2 = _construir_escenario(gap_en_entrada=False, gap_en_salida=False)
        fechas = datos2["TEST"].index
        eurusd = self._eurusd_con_salto(fechas)

        trades_sin, _, capital_sin = ejecutar_backtest(datos1)
        trades_con, _, capital_con = ejecutar_backtest(datos2, eurusd=eurusd)

        assert capital_sin == pytest.approx(capital_con)
        assert trades_sin[0]["pnl"] == pytest.approx(trades_con[0]["pnl"])
        assert trades_sin[0]["entrada"] == pytest.approx(trades_con[0]["entrada"])
        assert trades_sin[0]["salida"] == pytest.approx(trades_con[0]["salida"])
