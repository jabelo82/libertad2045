"""
tests/test_backtest_shares_capital_entrada_real.py

Fix del quinto de los "5 fixes combinados" de v10 (16/09/2026): la
divergencia de paridad backtest/producción abierta explícitamente por
el commit fdf3479 (Hallazgo MEDIA #5, auditoría 07/08/2026) --
position_size.py::calcular_posicion() (producción) ya se corrigió para
dividir shares_capital entre el precio de entrada real (buy-stop =
high + BUFFER) en vez del cierre del día del escaneo, pero
backtest_expandido.py::calcular_posicion() reimplementa shares_capital
de forma independiente y COMPARTÍA el mismo error sin corregir --
"requiere su propio fix y su propia revalidación de 20 años... fuera
del alcance de ese hallazgo" (fdf3479). Ahora se porta el mismo fix.

backtest_expandido.py ya usa exactamente el mismo buy_stop
(`round(señal["high"] + BUFFER, 4)`, BUFFER=0.05, línea ~1415) para
ejecutar la entrada real al día siguiente -- shares_capital debe usar
esa MISMA referencia de precio, no bar["Close"], para ser coherente con
el precio al que la orden realmente se ejecutaría.

Escenario KO idéntico (en shape) al de test_position_size_shares_capital.py
(producción) -- mismos parámetros RISK_PERCENT/MAX_POSITION_PCT del
motor de backtest (RISK_PERCENT=0.0085, MAX_POSITION_PCT=0.25, iguales
a producción).

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_backtest_shares_capital_entrada_real.py -v
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_expandido as be  # noqa: E402


def _df_ko_backtest() -> pd.DataFrame:
    """
    Réplica del escenario KO de producción (close=91,99$, high=92,49$,
    ATR14=1,335714$) en el formato de columnas que usa
    backtest_expandido.py (Close/High/ATR/ATR_PERCENTIL, mayúsculas).
    calcular_posicion(df, i, capital) lee df.iloc[i] -- basta con que
    la fila `i` (la última) tenga los valores reales; el resto es
    relleno para que ATR_PERCENTIL no sea NaN por ventana incompleta.
    """
    n = 30
    return pd.DataFrame({
        "Close":         [91.0] * (n - 1) + [91.989998],
        "High":          [91.5] * (n - 1) + [92.489998],
        "Low":           [90.5] * (n - 1) + [91.0],
        "ATR":           [1.3] * (n - 1) + [1.335714],
        "ATR_PERCENTIL": [0.5] * (n - 1) + [1.0],
    })


def _df_atr_tipico_backtest() -> pd.DataFrame:
    n = 30
    return pd.DataFrame({
        "Close":         [100.0] * (n - 1) + [100.0],
        "High":          [100.0] * (n - 1) + [101.5],
        "Low":           [99.0] * (n - 1) + [99.5],
        "ATR":           [2.5] * (n - 1) + [2.5],
        "ATR_PERCENTIL": [0.5] * (n - 1) + [1.0],
    })


class TestSharesCapitalEntradaRealBacktest(unittest.TestCase):

    def test_ko_shares_capital_usa_high_mas_buffer_no_close(self):
        df = _df_ko_backtest()
        capital = 7400.0
        i = len(df) - 1

        shares, stop_distance, atr = be.calcular_posicion(df, i, capital)

        close = df["Close"].iloc[i]
        high = df["High"].iloc[i]
        buy_stop = high + be.BUFFER

        shares_capital_esperado = int((capital * be.MAX_POSITION_PCT) / buy_stop)
        self.assertEqual(shares, shares_capital_esperado)
        self.assertEqual(shares, 19)

        shares_capital_antiguo = int((capital * be.MAX_POSITION_PCT) / close)
        self.assertEqual(shares_capital_antiguo, 20)
        self.assertNotEqual(shares, shares_capital_antiguo)

    def test_ko_coste_real_al_buy_stop_queda_dentro_del_25pct(self):
        df = _df_ko_backtest()
        capital = 7400.0
        i = len(df) - 1
        max_position_value = capital * be.MAX_POSITION_PCT

        shares, _, _ = be.calcular_posicion(df, i, capital)

        buy_stop = df["High"].iloc[i] + be.BUFFER
        coste_real = shares * buy_stop
        self.assertLessEqual(coste_real, max_position_value)
        self.assertLessEqual(coste_real / capital, 0.25)

    def test_ko_shares_capital_antiguo_al_buy_stop_habria_superado_25pct(self):
        """
        Control negativo: reproduce a mano la fórmula ANTIGUA
        (dividir por Close) para confirmar que, ejecutada al buy_stop
        real (lo que de verdad ocurre un día después en el motor), el
        coste ya superaba el 25% -- el bug era real, no hipotético.
        """
        df = _df_ko_backtest()
        capital = 7400.0
        i = len(df) - 1
        max_position_value = capital * be.MAX_POSITION_PCT

        close = df["Close"].iloc[i]
        shares_capital_antiguo = int(max_position_value / close)

        buy_stop = df["High"].iloc[i] + be.BUFFER
        coste_real_antiguo = shares_capital_antiguo * buy_stop

        self.assertGreater(coste_real_antiguo, max_position_value)
        self.assertGreater(coste_real_antiguo / capital, 0.25)

    def test_atr_tipico_shares_risk_gana_y_no_cambia_con_el_fix(self):
        df = _df_atr_tipico_backtest()
        capital = 40000.0
        i = len(df) - 1

        shares, stop_distance, atr = be.calcular_posicion(df, i, capital)

        risk_amount = capital * be.RISK_PERCENT
        shares_risk_esperado = int(risk_amount / stop_distance)
        close = df["Close"].iloc[i]
        high = df["High"].iloc[i]
        shares_capital_nuevo = int((capital * be.MAX_POSITION_PCT) / (high + be.BUFFER))
        shares_capital_antiguo = int((capital * be.MAX_POSITION_PCT) / close)

        self.assertLess(shares_risk_esperado, shares_capital_nuevo)
        self.assertLess(shares_risk_esperado, shares_capital_antiguo)
        self.assertEqual(shares, shares_risk_esperado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
