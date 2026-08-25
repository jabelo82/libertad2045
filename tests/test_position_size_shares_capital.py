"""
tests/test_position_size_shares_capital.py

Tests para el fix del Hallazgo MEDIA #5 (auditoría 07/08/2026):
shares_capital, en calcular_posicion() (position_size.py), dividía
max_position_value entre df["close"] (el cierre del día del escaneo) en
vez del precio de entrada real (el buy-stop, high + ENTRY_BUFFER) — la
posición real podía superar MAX_POSITION_PCT (25%) porque siempre se
compra al buy-stop, nunca al cierre.

Escenario KO (24/08/2026): reproduce el ejemplo numérico de la
investigación de Fase 1 — close=91,99 $, high=92,49 $, ATR14=1,3357 $
(ATR%=1,45%, uno de los pocos casos reales del universo donde
shares_capital puede ganar a shares_risk; ver exp46_fase2_perfil_riesgo.csv
— solo el 1,2% del S&P500 tiene ATR% bajo el umbral necesario). El
capital (7.400 $) se eligió para caer justo en el punto donde el error
de redondeo por int() ya no absorbe el gap close/buy-stop — con
capitales "redondos" más simples (p. ej. 40.000 $) el propio floor()
esconde el problema por casualidad, como ya se documentó en la
investigación.

El slippage mediano real (+0,65%) aplicado aquí es el observado en las
23 entradas LIVE reales de agosto 2026 (fill real vs. cierre usado en el
sizing) — no el máximo (4,77%, un salto de mercado, no previsible por
diseño y fuera del alcance de un fix de sizing).

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_position_size_shares_capital.py -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Otros ficheros de test (test_rebalance_ampliar_leverage.py y hermanos)
# sustituyen sys.modules["position_size"] por un stub con
# calcular_posicion = MagicMock(return_value=(0, 0, 0)) para aislar
# rebalance.py de IBKR. pytest ejecuta todo el árbol de tests en el mismo
# proceso, así que ese stub puede seguir en sys.modules cuando este fichero
# se importa — hay que descartarlo explícitamente para obtener aquí el
# position_size.py real, no el stub. Mismo patrón que
# sys.modules.pop("rebalance", None) en esos ficheros.
sys.modules.pop("position_size", None)
sys.modules.pop("logger", None)

from position_size import ENTRY_BUFFER, calcular_posicion  # noqa: E402


def _df_ko_24_08_2026() -> pd.DataFrame:
    """
    Réplica mínima de la fila que calcular_posicion() lee de data_loader
    para KO el 24/08/2026 (datos reales, IBKR get_price_history):
    close=91,989998 $, high=92,489998 $, ATR14=1,335714 $.
    ATR_PERCENTIL=1.0 fuerza el multiplicador mínimo (B1_MULT_MIN=2,2,
    volatilidad en máximos históricos) — es el caso que más aprieta
    stop_distance contra el umbral de 3,4% del precio bajo el cual
    shares_capital puede ganarle a shares_risk (ver investigación Fase 1).
    Las filas anteriores son relleno — calcular_posicion() solo lee
    .iloc[-1].
    """
    n = 25
    return pd.DataFrame({
        "close":         [91.0] * (n - 1) + [91.989998],
        "high":          [91.5] * (n - 1) + [92.489998],
        "ATR":           [1.3] * (n - 1) + [1.335714],
        "ATR_PERCENTIL": [0.5] * (n - 1) + [1.0],
    })


def _df_atr_tipico() -> pd.DataFrame:
    """
    Escenario de control con ATR% representativo de la mediana del
    universo (2,577%, ver exp46_fase2_perfil_riesgo.csv) — stop_distance
    queda muy por encima del umbral de 3,4%, así que shares_risk debe
    ganar tanto antes como después del fix. Confirma que el fix no toca
    el camino ya validado (RISK_PERCENT) cuando shares_capital no es la
    rama vinculante.
    """
    n = 25
    return pd.DataFrame({
        "close":         [100.0] * (n - 1) + [100.0],
        "high":          [100.0] * (n - 1) + [101.5],
        "ATR":           [2.5] * (n - 1) + [2.5],
        "ATR_PERCENTIL": [0.5] * (n - 1) + [1.0],
    })


class TestSharesCapitalPrecioEntrada(unittest.TestCase):

    # ------------------------------------------------------------
    # Caso KO: shares_capital gana — el caso que expone el bug
    # ------------------------------------------------------------

    def test_ko_shares_capital_usa_high_mas_buffer_no_close(self):
        df = _df_ko_24_08_2026()
        capital = 7400.0

        shares, stop_distance, atr = calcular_posicion(df, capital)

        close = df["close"].iloc[-1]
        high = df["high"].iloc[-1]
        buy_stop = high + ENTRY_BUFFER

        # shares_capital (19, dividiendo por high+buffer) debe ganar a
        # shares_risk (20 con mult=2.2) — confirma que este test ejercita
        # la rama que el hallazgo describe, no shares_risk.
        shares_capital_esperado = int((capital * 0.25) / buy_stop)
        self.assertEqual(shares, shares_capital_esperado)
        self.assertEqual(shares, 19)

        # El shares_capital ANTIGUO (dividiendo por close) habría dado 20 —
        # uno más. Si esta aserción empezara a fallar, comprobar que nadie
        # ha revertido el fix a dividir por close.
        shares_capital_antiguo = int((capital * 0.25) / close)
        self.assertEqual(shares_capital_antiguo, 20)
        self.assertNotEqual(shares, shares_capital_antiguo)

    def test_ko_coste_real_con_slippage_mediano_queda_dentro_del_25pct(self):
        """
        Aplica el slippage mediano real observado en agosto 2026
        (fill real vs. cierre usado en el sizing, +0,65%) sobre el
        resultado YA CORREGIDO de calcular_posicion() y confirma que el
        coste real de la posición se mantiene ≤ 25% del capital.

        No se cubre el máximo observado (+4,77%, CSCO 04/08/2026): es un
        salto de mercado, no algo que un fix de sizing pueda prever —
        el fix solo puede cerrar el margen ya conocido en el momento del
        cálculo (high + buffer), no la volatilidad del día siguiente.
        """
        df = _df_ko_24_08_2026()
        capital = 7400.0
        max_position_value = capital * 0.25

        shares, _, _ = calcular_posicion(df, capital)

        close = df["close"].iloc[-1]
        SLIPPAGE_MEDIANO_REAL = 0.0065  # mediana real, 23 entradas LIVE ago-2026
        fill_real_estimado = close * (1 + SLIPPAGE_MEDIANO_REAL)

        coste_real = shares * fill_real_estimado
        self.assertLessEqual(coste_real, max_position_value)
        self.assertLessEqual(coste_real / capital, 0.25)

    def test_ko_shares_capital_antiguo_con_slippage_mediano_habria_superado_25pct(self):
        """
        Control negativo: reproduce a mano la fórmula ANTIGUA
        (shares_capital = int(max_position_value / close)) para demostrar
        que el bug era real — con el slippage mediano real de este mes,
        el coste ya superaba el 25% incluso sin llegar al caso extremo.
        No ejercita código de producción; es la prueba de que el fix
        corrige algo que de verdad ocurría.
        """
        df = _df_ko_24_08_2026()
        capital = 7400.0
        max_position_value = capital * 0.25

        close = df["close"].iloc[-1]
        shares_capital_antiguo = int(max_position_value / close)

        SLIPPAGE_MEDIANO_REAL = 0.0065
        fill_real_estimado = close * (1 + SLIPPAGE_MEDIANO_REAL)
        coste_real_antiguo = shares_capital_antiguo * fill_real_estimado

        self.assertGreater(coste_real_antiguo, max_position_value)
        self.assertGreater(coste_real_antiguo / capital, 0.25)

    # ------------------------------------------------------------
    # Caso de control: ATR% típico — shares_risk gana, fix no debe
    # cambiar nada
    # ------------------------------------------------------------

    def test_atr_tipico_shares_risk_gana_y_no_cambia_con_el_fix(self):
        df = _df_atr_tipico()
        capital = 40000.0

        shares, stop_distance, atr = calcular_posicion(df, capital)

        risk_amount = capital * 0.0085
        shares_risk_esperado = int(risk_amount / stop_distance)
        close = df["close"].iloc[-1]
        high = df["high"].iloc[-1]
        shares_capital_nuevo = int((capital * 0.25) / (high + ENTRY_BUFFER))
        shares_capital_antiguo = int((capital * 0.25) / close)

        # shares_risk debe ser la rama vinculante en ambos casos —
        # confirma que el escenario ejercita el camino no afectado.
        self.assertLess(shares_risk_esperado, shares_capital_nuevo)
        self.assertLess(shares_risk_esperado, shares_capital_antiguo)
        self.assertEqual(shares, shares_risk_esperado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
