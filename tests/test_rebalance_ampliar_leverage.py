"""
tests/test_rebalance_ampliar_leverage.py

Tests de integración para el chequeo de apalancamiento antes de AMPLIAR en
rebalance.py — CRÍTICA #1 de la auditoría 07/08/2026
(LIBERTAD2045_Auditoria_20260807): "AMPLIAR nunca verifica apalancamiento".

A diferencia de tests/test_risk_guardian_ampliar.py (que prueba la lógica
pura de verificar_apalancamiento_ampliar()), este fichero prueba el
CABLEADO en rebalance.py: que rebalancear() llama al chequeo solo para
AMPLIAR (accion_orden == "BUY"), respeta su veredicto, y que REDUCIR nunca
pasa por él — reduce exposición, nunca la aumenta.

verificar_apalancamiento_ampliar se sustituye por un mock controlado por
cada test (patch.object(rebalance, "verificar_apalancamiento_ampliar")) —
su propia lógica ya está cubierta en test_risk_guardian_ampliar.py.

Mismo patrón de stubs que tests/test_gtc_dedup.py y
tests/test_pending_rebalance_reconciliacion.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_rebalance_ampliar_leverage.py -v
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Bootstrap mínimo: evitar importar ib_insync real ni conexión de red
# (mismo patrón que tests/test_gtc_dedup.py)
# ---------------------------------------------------------------------------

ib_stub = types.ModuleType("ib_insync")
ib_stub.Order = MagicMock
ib_stub.Stock = MagicMock
ib_stub.ExecutionFilter = MagicMock
for name in ("IB", "MarketOrder", "Trade", "OrderStatus"):
    setattr(ib_stub, name, MagicMock)
sys.modules["ib_insync"] = ib_stub

for mod in ("logger", "telegram", "data_loader", "position_size",
            "conexion_ib", "signal_engine", "trade_executor",
            "order_manager", "universe_sp500", "risk_guardian",
            "process_guard", "dashboard", "github_publisher",
            "portfolio_manager"):
    m = types.ModuleType(mod)
    m.log_event = MagicMock()
    m.send_telegram = MagicMock()
    m.send_telegram_critical = MagicMock()
    m.obtener_datos = MagicMock(return_value=None)
    m.calcular_trailing_stop = MagicMock(return_value=(None, None))
    m.calcular_posicion = MagicMock(return_value=(0, 0, 0))
    m.MAX_POSITION_PCT = 0.20
    m.ENTRY_BUFFER = 0.05
    # Por defecto permisivo — cada test que necesite otro veredicto lo
    # sobreescribe con patch.object(rebalance, "verificar_apalancamiento_ampliar", ...)
    m.verificar_apalancamiento_ampliar = MagicMock(return_value=(True, "OK", 0.0))
    sys.modules[mod] = m

sys.modules.pop("rebalance", None)

import rebalance  # noqa: E402 — debe importarse después de los stubs
from rebalance import DecisionRebalanceo, rebalancear  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(symbol: str, qty: int) -> MagicMock:
    pos = MagicMock()
    pos.contract.symbol = symbol
    pos.position = qty
    pos.avgCost = 0  # desactiva el bloque de break-even, fuera de alcance aquí
    return pos


def _make_stop_gtc_trade(symbol: str, qty: int) -> MagicMock:
    """Stop GTC ya activo para el símbolo — evita el camino de auto-creación
    de stop (fuera de alcance de este fichero)."""
    trade = MagicMock()
    trade.contract.symbol = symbol
    trade.order.orderType = "STP"
    trade.order.action = "SELL"
    trade.order.tif = "GTC"
    trade.order.totalQuantity = qty
    trade.order.auxPrice = 90.0
    return trade


def _make_trade_ajuste(status: str = "Cancelled", filled: float = 0) -> MagicMock:
    """Resultado de ib.placeOrder() — por defecto rechazada/cancelada para no
    entrar en la lógica posterior de reemplazo de stop GTC (fuera de alcance
    aquí; ya cubierta en otros ficheros de test)."""
    trade_ajuste = MagicMock()
    trade_ajuste.orderStatus.status = status
    trade_ajuste.orderStatus.filled = filled
    trade_ajuste.order.orderId = 4242
    return trade_ajuste


def _make_ib(symbol: str, qty: int) -> MagicMock:
    ib = MagicMock()
    ib.positions.return_value = [_make_position(symbol, qty)]
    ib.trades.return_value = [_make_stop_gtc_trade(symbol, qty)]
    ib.openTrades.return_value = []
    ib.qualifyContracts.return_value = True
    ib.placeOrder.return_value = _make_trade_ajuste()
    return ib


_SYMBOL = "TST"
_PRECIO = 100.0
_CAPITAL = 10000.0
# avgCost=0 en _make_position desactiva el bloque de break-even, pero
# atr_actual = df["ATR"].iloc[-1] se evalúa igualmente antes de esa
# comprobación -> necesita un DataFrame real, no un stub de lista.
# "high" es necesario desde el fix del Hallazgo MEDIA #5 (auditoría
# 07/08/2026): rebalancear() calcula precio_entrada_ampliar = high + buffer
# en cuanto df está disponible, antes de llegar al chequeo de apalancamiento.
_HIGH_DUMMY = 100.5
_DF_DUMMY = pd.DataFrame({"close": [100.0] * 25, "high": [_HIGH_DUMMY] * 25, "ATR": [1.0] * 25})
# high + ENTRY_BUFFER (0.05), redondeado a 2 decimales — mismo cálculo que
# rebalancear() aplica a precio_entrada_ampliar.
_PRECIO_ENTRADA_AMPLIAR = round(_HIGH_DUMMY + 0.05, 2)


def _decision_ampliar() -> DecisionRebalanceo:
    return DecisionRebalanceo(
        symbol=_SYMBOL, accion="AMPLIAR",
        shares_actual=100, shares_optimo=110, shares_delta=10,
        valor_actual=10000.0, valor_optimo=11000.0,
        motivo="Infradimensionada (test)",
    )


def _decision_reducir() -> DecisionRebalanceo:
    return DecisionRebalanceo(
        symbol=_SYMBOL, accion="REDUCIR",
        shares_actual=100, shares_optimo=90, shares_delta=-10,
        valor_actual=10000.0, valor_optimo=9000.0,
        motivo="Sobredimensionada (test)",
    )


class TestApalancamientoAmpliarWiring(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        pending_file = Path(self._tmpdir.name) / "pending_rebalance.json"
        self._pending_patcher = patch.object(rebalance, "_PENDING_REBALANCE_FILE", pending_file)
        self._pending_patcher.start()
        self._precio_patcher = patch.object(rebalance, "_precio_cierre_reciente", return_value=_PRECIO)
        self._precio_patcher.start()

    def tearDown(self):
        self._precio_patcher.stop()
        self._pending_patcher.stop()
        self._tmpdir.cleanup()

    # ------------------------------------------------------------
    # AMPLIAR bloqueado por apalancamiento
    # ------------------------------------------------------------

    def test_ampliar_omitido_cuando_apalancamiento_bloqueado(self):
        ib = _make_ib(_SYMBOL, 100)

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "verificar_apalancamiento_ampliar",
                           return_value=(False, "apalancamiento 1.20x > límite 1.00x", 1.20)) as mock_check, \
             patch.object(rebalance, "send_telegram") as mock_telegram:

            decisiones = rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        mock_check.assert_called_once()
        ib.placeOrder.assert_not_called()
        self.assertEqual(len(decisiones), 1)
        self.assertIn("AMPLIAR omitido", decisiones[0].motivo)
        self.assertFalse(decisiones[0].ejecutado)
        mock_telegram.assert_called_once()
        aviso = mock_telegram.call_args[0][0]
        self.assertIn(_SYMBOL, aviso)
        self.assertIn("apalancamiento", aviso)

    # ------------------------------------------------------------
    # AMPLIAR permitido dentro del límite
    # ------------------------------------------------------------

    def test_ampliar_permitido_cuando_dentro_de_limite(self):
        ib = _make_ib(_SYMBOL, 100)

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "verificar_apalancamiento_ampliar",
                           return_value=(True, "OK", 0.95)) as mock_check, \
             patch.object(rebalance, "send_telegram") as mock_telegram:

            decisiones = rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        mock_check.assert_called_once()
        # exposicion_adicional = shares_abs (10) * precio_entrada_ampliar
        # (high + buffer = 100.55), NO shares_abs * precio_cierre (100) —
        # Hallazgo MEDIA #5, ver test_leverage_usa_precio_entrada_no_cierre.
        _, kwargs = mock_check.call_args
        self.assertAlmostEqual(kwargs.get("exposicion_adicional", 0),
                                10 * _PRECIO_ENTRADA_AMPLIAR)

        ib.placeOrder.assert_called_once()
        orden_enviada = ib.placeOrder.call_args[0][1]
        self.assertEqual(orden_enviada.action, "BUY")
        self.assertEqual(len(decisiones), 1)
        self.assertNotIn("AMPLIAR omitido", decisiones[0].motivo)
        # No se omitió por apalancamiento — no hay aviso de bloqueo preventivo
        for llamada in mock_telegram.call_args_list:
            self.assertNotIn("AMPLIAR omitido", llamada[0][0])

    # ------------------------------------------------------------
    # Hallazgo MEDIA #5 (auditoría 07/08/2026): exposicion_adicional debe
    # usar el precio de entrada real (high + buffer), no el cierre reciente
    # — la orden AMPLIAR es MKT y puede ejecutarse horas después, en la
    # siguiente apertura, no al cierre de anoche.
    # ------------------------------------------------------------

    def test_leverage_usa_precio_entrada_no_cierre(self):
        ib = _make_ib(_SYMBOL, 100)

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "verificar_apalancamiento_ampliar",
                           return_value=(True, "OK", 0.95)) as mock_check, \
             patch.object(rebalance, "send_telegram"):

            rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        _, kwargs = mock_check.call_args
        exposicion = kwargs.get("exposicion_adicional", 0)

        # El precio de cierre (_PRECIO=100) sigue siendo el que usa
        # evaluar_posicion() para valorar la posición existente — pero el
        # chequeo de apalancamiento del AMPLIAR debe ignorarlo y usar
        # high+buffer (100.55), no 100.
        self.assertNotAlmostEqual(exposicion, 10 * _PRECIO)
        self.assertAlmostEqual(exposicion, 10 * _PRECIO_ENTRADA_AMPLIAR)

    # ------------------------------------------------------------
    # REDUCIR nunca pasa por este chequeo
    # ------------------------------------------------------------

    def test_reducir_nunca_bloqueado_por_este_check(self):
        ib = _make_ib(_SYMBOL, 100)

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_reducir()), \
             patch.object(rebalance, "verificar_apalancamiento_ampliar",
                           return_value=(False, "apalancamiento 1.20x > límite 1.00x", 1.20)) as mock_check:

            decisiones = rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        mock_check.assert_not_called()
        ib.placeOrder.assert_called_once()
        orden_enviada = ib.placeOrder.call_args[0][1]
        self.assertEqual(orden_enviada.action, "SELL")
        self.assertEqual(len(decisiones), 1)
        self.assertNotIn("AMPLIAR omitido", decisiones[0].motivo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
