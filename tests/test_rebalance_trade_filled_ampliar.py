"""
tests/test_rebalance_trade_filled_ampliar.py

Tests de integración para el fix del hallazgo KPI "Trades ejecutados"
(31/08/2026) en el cableado de rebalance.py::rebalancear():

  - AMPLIAR con fill inmediato (MKT, estado=="Filled" en el propio ciclo)
    debe loguear level="TRADE_FILLED" (no "TRADE") y registrar el/los
    exec_id(s) del fill en el fichero compartido (logger.registrar_exec_ids),
    para que dashboard.py lo cuente como ejecución confirmada y
    registrar_fills_recientes() no lo cuente una segunda vez si ib.fills()
    lo vuelve a ver en un ciclo posterior.

  - REDUCIR con fill inmediato NO cambia: sigue logueando level="TRADE"
    sin registrar exec_id -- el precio en ese evento es una referencia
    (precio_entrada_ampliar/precio de cierre), no el fill real, así que
    renombrarlo a TRADE_FILLED degradaría leer_precios_salida() en
    dashboard.py (tomaría esa referencia como precio de salida en vez de
    esperar al TRADE_SOLD real de registrar_fills_recientes()).

Mismo patrón de stubs que tests/test_rebalance_ampliar_leverage.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_rebalance_trade_filled_ampliar.py -v
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
# (mismo patrón que tests/test_gtc_dedup.py y test_rebalance_ampliar_leverage.py)
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
    m.leer_exec_ids_registrados = MagicMock(return_value=set())
    m.exec_ids_pendientes_de_registrar = MagicMock(side_effect=lambda ids: list(ids))
    m.registrar_exec_ids = MagicMock()
    m.send_telegram = MagicMock()
    m.send_telegram_critical = MagicMock()
    m.obtener_datos = MagicMock(return_value=None)
    m.calcular_trailing_stop = MagicMock(return_value=(None, None))
    m.calcular_posicion = MagicMock(return_value=(0, 0, 0))
    m.MAX_POSITION_PCT = 0.20
    m.ENTRY_BUFFER = 0.05
    m.verificar_apalancamiento_ampliar = MagicMock(return_value=(True, "OK", 0.0))
    sys.modules[mod] = m

sys.modules.pop("rebalance", None)

import rebalance  # noqa: E402 — debe importarse después de los stubs
from rebalance import DecisionRebalanceo, rebalancear  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (mismos que test_rebalance_ampliar_leverage.py, + fill inmediato)
# ---------------------------------------------------------------------------

def _make_position(symbol: str, qty: int) -> MagicMock:
    pos = MagicMock()
    pos.contract.symbol = symbol
    pos.position = qty
    pos.avgCost = 0
    return pos


def _make_stop_gtc_trade(symbol: str, qty: int) -> MagicMock:
    trade = MagicMock()
    trade.contract.symbol = symbol
    trade.order.orderType = "STP"
    trade.order.action = "SELL"
    trade.order.tif = "GTC"
    trade.order.totalQuantity = qty
    trade.order.auxPrice = 90.0
    trade.orderStatus.status = "PreSubmitted"
    return trade


def _make_fill(exec_id: str) -> MagicMock:
    fill = MagicMock()
    fill.execution.execId = exec_id
    return fill


def _make_trade_ajuste_filled(exec_ids=("EXEC-1",), filled=10) -> MagicMock:
    """Orden MKT ya confirmada Filled en el propio ciclo, con trade.fills
    poblado -- el caso que motiva el fix (AMPLIAR/REDUCIR con fill inmediato)."""
    trade_ajuste = MagicMock()
    trade_ajuste.orderStatus.status = "Filled"
    trade_ajuste.orderStatus.filled = filled
    trade_ajuste.order.orderId = 4242
    trade_ajuste.fills = [_make_fill(e) for e in exec_ids]
    return trade_ajuste


def _make_ib(symbol: str, qty: int) -> MagicMock:
    ib = MagicMock()
    ib.positions.return_value = [_make_position(symbol, qty)]
    ib.trades.return_value = [_make_stop_gtc_trade(symbol, qty)]
    ib.openTrades.return_value = []
    ib.qualifyContracts.return_value = True
    ib.placeOrder.return_value = _make_trade_ajuste_filled()
    return ib


_SYMBOL = "TST"
_PRECIO = 100.0
_CAPITAL = 10000.0
_HIGH_DUMMY = 100.5
_DF_DUMMY = pd.DataFrame({"close": [100.0] * 25, "high": [_HIGH_DUMMY] * 25, "ATR": [1.0] * 25})


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


class TestTradeFilledAmpliarReducir(unittest.TestCase):

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
    # AMPLIAR con fill inmediato: TRADE_FILLED + exec_id registrado
    # ------------------------------------------------------------

    def test_ampliar_fill_inmediato_loguea_trade_filled_y_registra_exec_id(self):
        ib = _make_ib(_SYMBOL, 100)
        ib.placeOrder.return_value = _make_trade_ajuste_filled(
            exec_ids=("EXEC-AMPLIAR-1",), filled=10
        )

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "send_telegram"), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch.object(rebalance, "exec_ids_pendientes_de_registrar",
                           return_value=["EXEC-AMPLIAR-1"]) as mock_pendientes, \
             patch.object(rebalance, "registrar_exec_ids") as mock_registrar:

            decisiones = rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        self.assertEqual(len(decisiones), 1)
        self.assertTrue(decisiones[0].ejecutado)

        niveles_y_textos = [(c.args[0], c.args[1]) for c in mock_log.call_args_list]
        trade_filled = [t for lvl, t in niveles_y_textos if lvl == "TRADE_FILLED"]
        trade_sin_confirmar = [t for lvl, t in niveles_y_textos if lvl == "TRADE"]

        self.assertEqual(len(trade_filled), 1,
                          f"debe haber exactamente un TRADE_FILLED: {niveles_y_textos}")
        self.assertIn("Rebalanceo AMPLIAR ejecutado", trade_filled[0])
        self.assertEqual(trade_sin_confirmar, [],
                          "el AMPLIAR con fill inmediato ya NO debe loguear level=TRADE")

        mock_pendientes.assert_called_once_with(["EXEC-AMPLIAR-1"])
        mock_registrar.assert_called_once_with(["EXEC-AMPLIAR-1"])

    def test_ampliar_ya_contado_no_duplica_trade_filled(self):
        """Si el exec_id ya está en logged_exec_ids.txt (p.ej. lo vio
        registrar_fills_recientes() antes por cualquier motivo), no se
        loguea un TRADE_FILLED repetido para el mismo fill real."""
        ib = _make_ib(_SYMBOL, 100)
        ib.placeOrder.return_value = _make_trade_ajuste_filled(
            exec_ids=("EXEC-YA-CONTADO",), filled=10
        )

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "send_telegram"), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch.object(rebalance, "exec_ids_pendientes_de_registrar",
                           return_value=[]) as mock_pendientes, \
             patch.object(rebalance, "registrar_exec_ids") as mock_registrar:

            rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        niveles = [c.args[0] for c in mock_log.call_args_list]
        self.assertNotIn("TRADE_FILLED", niveles,
                          "exec_id ya contado -- no debe generar un segundo TRADE_FILLED")
        mock_pendientes.assert_called_once_with(["EXEC-YA-CONTADO"])
        mock_registrar.assert_not_called()

    def test_ampliar_filled_sin_fills_poblados_cae_a_fallback_logueando_igual(self):
        """Caso borde: estado=="Filled" pero trade.fills todavía vacío
        (ib_insync no lo ha poblado). No se puede deduplicar por exec_id,
        pero el evento no debe perderse -- se loguea TRADE_FILLED igual,
        con un WARN visible para depurar."""
        ib = _make_ib(_SYMBOL, 100)
        trade_ajuste = _make_trade_ajuste_filled(exec_ids=(), filled=10)
        trade_ajuste.fills = []
        ib.placeOrder.return_value = trade_ajuste

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_ampliar()), \
             patch.object(rebalance, "send_telegram"), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch.object(rebalance, "registrar_exec_ids") as mock_registrar:

            rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        niveles_y_textos = [(c.args[0], c.args[1]) for c in mock_log.call_args_list]
        self.assertTrue(any(lvl == "TRADE_FILLED" for lvl, _ in niveles_y_textos),
                         f"el evento no debe perderse aunque no se pueda deduplicar: {niveles_y_textos}")
        self.assertTrue(any(lvl == "WARN" and "sin exec_ids" in txt
                             for lvl, txt in niveles_y_textos),
                         f"debe quedar un WARN visible para depurar: {niveles_y_textos}")
        mock_registrar.assert_not_called()

    # ------------------------------------------------------------
    # REDUCIR con fill inmediato: sin cambios (regresión)
    # ------------------------------------------------------------

    def test_reducir_fill_inmediato_sigue_logueando_trade_sin_registrar_exec_id(self):
        ib = _make_ib(_SYMBOL, 100)
        ib.placeOrder.return_value = _make_trade_ajuste_filled(
            exec_ids=("EXEC-REDUCIR-1",), filled=10
        )

        with patch.object(rebalance, "evaluar_posicion", return_value=_decision_reducir()), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch.object(rebalance, "registrar_exec_ids") as mock_registrar, \
             patch.object(rebalance, "exec_ids_pendientes_de_registrar") as mock_pendientes:

            decisiones = rebalancear(ib, _CAPITAL, mode="PAPER", datos={_SYMBOL: _DF_DUMMY})

        self.assertEqual(len(decisiones), 1)
        niveles_y_textos = [(c.args[0], c.args[1]) for c in mock_log.call_args_list]
        trade_lines = [t for lvl, t in niveles_y_textos if lvl == "TRADE"]

        self.assertEqual(len(trade_lines), 1,
                          f"REDUCIR con fill inmediato debe seguir logueando level=TRADE: {niveles_y_textos}")
        self.assertIn("Rebalanceo REDUCIR ejecutado", trade_lines[0])
        self.assertFalse(any(lvl == "TRADE_FILLED" for lvl, _ in niveles_y_textos),
                          "REDUCIR NO debe renombrarse a TRADE_FILLED (protege leer_precios_salida())")
        mock_registrar.assert_not_called()
        mock_pendientes.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
