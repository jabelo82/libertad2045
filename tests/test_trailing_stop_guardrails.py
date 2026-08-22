"""
tests/test_trailing_stop_guardrails.py

Tests unitarios de calcular_trailing_stop() (position_size.py) tras la
RETIRADA (22/08/2026) de las guardias anti-outlier ATR_SALTO y
STOP_DEMASIADO_CERCA, añadidas el 05-07/08/2026 tras el incidente ANET
y revertidas después del Experimento 47 (backtest de 21 años,
backtest_exp47_guardias_trailing.py): la variante SIN guardias (A) dio
mejores resultados en las 4 métricas que la variante CON guardias (B, la
que estaba en producción) — PF 2,6071 vs 2,4342, capital 8.888.418€ vs
6.984.283€, DD 10,4% vs 12,7%. Ver comentario de cabecera de
position_size.py para el análisis completo, incluido el caso real MRNA
(20/08/2026) donde ATR_SALTO bloqueó una actualización que habría sido
mejor que la corrección manual.

Este archivo probaba explícitamente el comportamiento de ambas guardias
(descarte del ciclo + alerta). Con las guardias retiradas, esos
escenarios ya no descartan nada — calcular_trailing_stop() debe seguir
calculando el stop normalmente incluso ante un salto de ATR o un stop
que quede cerca del cierre. Se ELIMINARON:
    - TestGuardiaATRSalto (probaba el descarte por salto de ATR, que ya
      no existe) y TestGuardiaStopDemasiadoCerca (ídem, descarte por
      stop pegado al precio) — ambas verificaban un `return (None, None)`
      + llamada a `_alertar_anomalia_trailing` que ya no ocurre.
    - TestAlertaNuncaRompeElCalculo — probaba que un fallo de logging/
      telegram durante una alerta de guardia no rompía el cálculo;
      calcular_trailing_stop() ya no dispara ninguna alerta, así que el
      escenario no aplica. La propiedad "una alerta que falla nunca debe
      propagar" se sigue cubriendo donde sigue siendo relevante: en
      tests/test_stop_vivo_guard.py, sobre verificar_margen_stop_vivo()
      (que no se ha tocado y sigue usando _alertar_anomalia_trailing()).
Se CONSERVA TestCasoNormal (el cálculo normal no cambia) y se AÑADEN dos
casos que antes disparaban una guardia y ahora deben calcular el stop
igual que cualquier otro ciclo, sin descartarlo ni alertar.

Ejecutar desde la raíz del proyecto:
    venv/bin/python -m pytest tests/test_trailing_stop_guardrails.py -v
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


class TestSinGuardias(unittest.TestCase):
    """
    Escenarios que, con las guardias todavía activas, descartaban el ciclo
    (ver historial en el docstring del módulo). Ahora deben calcular el
    stop con la fórmula directa B1, sin descartar nada ni alertar.
    """

    @patch("position_size.log_event")
    def test_salto_extremo_de_atr_ya_no_descarta_el_ciclo(self, mock_log):
        # Mismo salto de ATR (8.0 -> 30.0, x3.75) que antes disparaba ATR_SALTO
        atr_series = [8.0] * 13 + [30.0]
        percentil = 0.5
        df = _df(atr_series, high=200.0, close=190.0, percentil=percentil)

        nuevo_stop, mult = calcular_trailing_stop(df)

        mult_esperado = round((B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil) * TRAILING_FACTOR, 2)
        stop_esperado = round(200.0 - 30.0 * mult_esperado, 2)

        self.assertEqual(mult, mult_esperado)
        self.assertEqual(nuevo_stop, stop_esperado)
        mock_log.assert_not_called()

    @patch("position_size.log_event")
    def test_stop_pegado_al_cierre_ya_no_descarta_el_ciclo(self, mock_log):
        # Reconstrucción del caso ANET 05/08: high anómalo que antes empujaba
        # el stop a quedar pegado al cierre y disparaba STOP_DEMASIADO_CERCA.
        atr_val = 10.9
        atr_series = [10.5] * 13 + [atr_val]
        high_anomalo = 214.89
        percentil = 0.95
        df = _df(atr_series, high=high_anomalo, close=197.31, percentil=percentil)

        nuevo_stop, mult = calcular_trailing_stop(df, symbol="ANET")

        mult_esperado = round((B1_MULT_MAX - (B1_MULT_MAX - B1_MULT_MIN) * percentil) * TRAILING_FACTOR, 2)
        stop_esperado = round(high_anomalo - atr_val * mult_esperado, 2)

        self.assertEqual(mult, mult_esperado)
        self.assertEqual(nuevo_stop, stop_esperado)
        mock_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
