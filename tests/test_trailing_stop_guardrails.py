"""
tests/test_trailing_stop_guardrails.py

Tests unitarios de las guardias anti-outlier de calcular_trailing_stop()
(position_size.py), añadidas tras el incidente ANET del 05/08/2026:
el stop quedó a 0,91 del cierre porque un "high" de un solo ciclo,
anómalo respecto a la volatilidad reciente, se trasladó sin validar
al cálculo del stop.

Cubre:
    - Caso normal (sin anomalía) → stop calculado como siempre.
    - Guardia 1: salto extremo de ATR entre ciclos consecutivos.
    - Guardia 2: stop resultante a menos de 1×ATR del cierre
                 (reproduce el escenario real del incidente ANET).
    - Ambas guardias no deben interrumpir el cálculo si la alerta
      (log/telegram) falla.

Ejecutar desde la raíz del proyecto:
    venv_torre_roto/bin/python3 -m pytest tests/test_trailing_stop_guardrails.py -v
"""

import sys
import unittest
from unittest.mock import patch

import pandas as pd

# Otros módulos de test (test_gtc_dedup.py) stubean position_size/logger/telegram
# en sys.modules sin restaurarlos — si este archivo se ejecuta después en la
# misma sesión de pytest, un import normal devolvería el stub en vez del
# módulo real. Forzamos un import limpio para probar el código real.
for _mod in ("position_size", "logger", "telegram"):
    sys.modules.pop(_mod, None)

from position_size import calcular_trailing_stop, TRAILING_FACTOR, B1_MULT_MIN, B1_MULT_MAX


def _df(atr_series, high, close, percentil=0.5):
    """
    Construye un DataFrame mínimo compatible con calcular_trailing_stop():
    columnas ATR, ATR_PERCENTIL, high, close. `atr_series` es la serie
    completa de ATR (para poder controlar ATR de hoy y de ayer);
    high/close son los valores del último ciclo.
    """
    n = len(atr_series)
    return pd.DataFrame({
        "ATR":           atr_series,
        "ATR_PERCENTIL": [percentil] * n,
        "high":          [high] * (n - 1) + [high],
        "close":         [close] * (n - 1) + [close],
    })


class TestCasoNormal(unittest.TestCase):

    @patch("position_size.log_event")
    def test_stop_normal_se_calcula_igual_que_antes(self, mock_log):
        # ATR estable ciclo a ciclo, stop a distancia razonable del cierre
        atr_series = [8.0] * 13 + [8.0]
        df = _df(atr_series, high=191.32, close=189.0, percentil=0.5)

        nuevo_stop, mult = calcular_trailing_stop(df)

        mult_esperado = round((B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * 0.5) * TRAILING_FACTOR, 2)
        stop_esperado = round(191.32 - 8.0 * mult_esperado, 2)

        self.assertEqual(mult, mult_esperado)
        self.assertEqual(nuevo_stop, stop_esperado)
        mock_log.assert_not_called()


class TestGuardiaATRSalto(unittest.TestCase):

    @patch("position_size._alertar_anomalia_trailing")
    def test_atr_colapsa_respecto_al_ciclo_anterior_se_descarta(self, mock_alerta):
        # ATR previo 8.0 → ATR hoy 0.96 (colapso ~8x, igual que la hipótesis inicial del incidente)
        atr_series = [8.0] * 13 + [0.96]
        df = _df(atr_series, high=197.31, close=197.31, percentil=0.9)

        nuevo_stop, mult = calcular_trailing_stop(df, symbol="ANET")

        self.assertIsNone(nuevo_stop)
        self.assertIsNone(mult)
        mock_alerta.assert_called_once()
        self.assertEqual(mock_alerta.call_args.args[0], "ATR_SALTO")
        self.assertEqual(mock_alerta.call_args.kwargs.get("symbol"), "ANET")

    @patch("position_size._alertar_anomalia_trailing")
    def test_atr_se_dispara_respecto_al_ciclo_anterior_se_descarta(self, mock_alerta):
        # ATR previo 8.0 → ATR hoy 30.0 (x3.75, por encima del umbral ATR_RATIO_MAX)
        atr_series = [8.0] * 13 + [30.0]
        df = _df(atr_series, high=200.0, close=190.0, percentil=0.5)

        nuevo_stop, mult = calcular_trailing_stop(df)

        self.assertIsNone(nuevo_stop)
        self.assertIsNone(mult)
        mock_alerta.assert_called_once()
        self.assertEqual(mock_alerta.call_args.args[0], "ATR_SALTO")

    @patch("position_size._alertar_anomalia_trailing")
    def test_atr_dentro_de_rango_normal_no_dispara_guardia(self, mock_alerta):
        # Variación moderada día a día (dentro de ATR_RATIO_MIN/MAX) — no debe descartarse
        atr_series = [8.0] * 13 + [9.5]
        df = _df(atr_series, high=191.32, close=189.0, percentil=0.5)

        nuevo_stop, mult = calcular_trailing_stop(df)

        self.assertIsNotNone(nuevo_stop)
        mock_alerta.assert_not_called()


class TestGuardiaStopDemasiadoCerca(unittest.TestCase):

    @patch("position_size._alertar_anomalia_trailing")
    def test_reproduce_incidente_anet_05_08(self, mock_alerta):
        # Reconstrucción aproximada del ciclo real: ATR estable (sin salto),
        # pero un "high" de un solo ciclo muy por encima del rango reciente
        # empuja el stop a quedar pegado al cierre.
        atr_val = 10.9
        atr_series = [10.5] * 13 + [atr_val]
        high_anomalo = 214.89   # ~20 puntos por encima del rango reciente (~165-195)
        close_hoy    = 197.31
        df = _df(atr_series, high=high_anomalo, close=close_hoy, percentil=0.95)

        nuevo_stop, mult = calcular_trailing_stop(df, symbol="ANET")

        self.assertIsNone(nuevo_stop)
        self.assertIsNone(mult)
        mock_alerta.assert_called_once()
        self.assertEqual(mock_alerta.call_args.args[0], "STOP_DEMASIADO_CERCA")
        self.assertEqual(mock_alerta.call_args.kwargs.get("symbol"), "ANET")

    @patch("position_size._alertar_anomalia_trailing")
    def test_stop_con_margen_suficiente_no_dispara_guardia(self, mock_alerta):
        atr_series = [8.0] * 13 + [8.0]
        df = _df(atr_series, high=191.32, close=189.0, percentil=0.5)

        nuevo_stop, mult = calcular_trailing_stop(df)

        self.assertIsNotNone(nuevo_stop)
        mock_alerta.assert_not_called()


class TestAlertaNuncaRompeElCalculo(unittest.TestCase):

    @patch("position_size.log_event", side_effect=Exception("disco lleno"))
    @patch("telegram.send_telegram_critical", side_effect=Exception("sin red"))
    def test_fallos_en_logging_y_telegram_no_propagan(self, mock_tg, mock_log):
        atr_series = [8.0] * 13 + [0.5]
        df = _df(atr_series, high=197.31, close=197.31, percentil=0.9)

        # No debe lanzar excepción aunque logging y telegram fallen
        nuevo_stop, mult = calcular_trailing_stop(df, symbol="ANET")

        self.assertIsNone(nuevo_stop)
        self.assertIsNone(mult)


if __name__ == "__main__":
    unittest.main()
