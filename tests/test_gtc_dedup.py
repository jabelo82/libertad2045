"""
tests/test_gtc_dedup.py

Tests unitarios para la prevención y reconciliación de stops GTC duplicados.
Cubre la lógica de _hay_stop_gtc_activo(), _reemplazar_stop_gtc() y
reconciliar_stops_gtc() con 0, 1, 2 y 3 stops GTC activos simulados.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_gtc_dedup.py -v
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap mínimo: evitar importar ib_insync real ni conexión de red
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Stub de ib_insync con solo lo necesario
ib_stub = types.ModuleType("ib_insync")
ib_stub.Order = MagicMock
ib_stub.Stock = MagicMock
for name in ("IB", "MarketOrder", "Trade", "OrderStatus"):
    setattr(ib_stub, name, MagicMock)
sys.modules["ib_insync"] = ib_stub

# Stubs de módulos que hacen I/O en importación
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
    sys.modules[mod] = m

import rebalance
from rebalance import _hay_stop_gtc_activo, _reemplazar_stop_gtc, reconciliar_stops_gtc


# ---------------------------------------------------------------------------
# Helpers para construir trades/órdenes falsos
# ---------------------------------------------------------------------------

def _make_trade(symbol: str, precio: float, qty: int,
                order_id: int = 1, status: str = "Submitted") -> MagicMock:
    trade = MagicMock()
    trade.contract.symbol      = symbol
    trade.order.orderType      = "STP"
    trade.order.action         = "SELL"
    trade.order.tif            = "GTC"
    trade.order.auxPrice       = precio
    trade.order.totalQuantity  = qty
    trade.order.orderId        = order_id
    trade.orderStatus.status   = status
    return trade


def _make_ib(trades: list) -> MagicMock:
    """IB mock cuyo .trades() devuelve la lista dada."""
    ib = MagicMock()
    ib.trades.return_value = trades
    ib.isConnected.return_value = True
    return ib


# ---------------------------------------------------------------------------
# Tests: _hay_stop_gtc_activo
# ---------------------------------------------------------------------------

class TestHayStopGtcActivo(unittest.TestCase):

    def test_sin_stops_devuelve_false(self):
        ib = _make_ib([])
        self.assertFalse(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_stop_exacto_devuelve_true(self):
        t = _make_trade("TRV", 305.63, 4, order_id=13443)
        ib = _make_ib([t])
        self.assertTrue(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_precio_diferente_devuelve_false(self):
        t = _make_trade("TRV", 300.00, 4, order_id=13443)
        ib = _make_ib([t])
        self.assertFalse(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_qty_diferente_devuelve_false(self):
        t = _make_trade("TRV", 305.63, 8, order_id=13443)
        ib = _make_ib([t])
        self.assertFalse(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_simbolo_diferente_devuelve_false(self):
        t = _make_trade("AAPL", 305.63, 4, order_id=13443)
        ib = _make_ib([t])
        self.assertFalse(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_stop_cancelled_ignorado(self):
        t = _make_trade("TRV", 305.63, 4, order_id=13443, status="Cancelled")
        ib = _make_ib([t])
        self.assertFalse(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))

    def test_tolerancia_precio_001(self):
        t = _make_trade("TRV", 305.635, 4, order_id=13443)
        ib = _make_ib([t])
        self.assertTrue(_hay_stop_gtc_activo(ib, "TRV", 305.63, 4))


# ---------------------------------------------------------------------------
# Tests: _reemplazar_stop_gtc (vía in-place cuando qty no cambia)
# ---------------------------------------------------------------------------

class TestReemplazarStopGtcInPlace(unittest.TestCase):

    def test_modifica_inplace_cuando_qty_igual(self):
        """Si la cantidad no cambia, se modifica auxPrice in-place, sin crear orden nueva."""
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443)
        contrato = MagicMock()
        ib = _make_ib([stop_ant])
        ib.placeOrder.return_value = MagicMock()

        result = _reemplazar_stop_gtc(ib, "TRV", contrato, 4, 306.18, stop_ant)

        self.assertTrue(result)
        # placeOrder llamado sobre el objeto existente con el nuevo precio
        self.assertEqual(stop_ant.order.auxPrice, 306.18)
        ib.placeOrder.assert_called_once_with(stop_ant.contract, stop_ant.order)
        # cancelOrder NO debe haberse llamado (no hay cancel en vía in-place)
        ib.cancelOrder.assert_not_called()

    def test_stop_price_invalido_retorna_false(self):
        ib = _make_ib([])
        contrato = MagicMock()
        result = _reemplazar_stop_gtc(ib, "TRV", contrato, 4, 0, None)
        self.assertFalse(result)
        ib.placeOrder.assert_not_called()

    def test_guard_idempotencia_no_crea_duplicado(self):
        """Si ya existe un stop idéntico, no se llama a placeOrder."""
        stop_existente = _make_trade("TRV", 306.18, 4, order_id=17743)
        ib = _make_ib([stop_existente])
        contrato = MagicMock()
        # stop_anterior con qty diferente para forzar la vía estándar (no in-place)
        stop_ant = _make_trade("TRV", 305.63, 8, order_id=13443)

        result = _reemplazar_stop_gtc(ib, "TRV", contrato, 4, 306.18, stop_ant)

        self.assertTrue(result)
        ib.placeOrder.assert_not_called()

    def test_crea_nuevo_cuando_qty_cambia_y_no_hay_duplicado(self):
        """Cuando la cantidad cambia y no existe stop idéntico, crea la orden nueva."""
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443)
        ib = _make_ib([stop_ant])
        nuevo_trade = MagicMock()
        nuevo_trade.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = nuevo_trade
        contrato = MagicMock()

        result = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)

        self.assertTrue(result)
        ib.placeOrder.assert_called_once()
        ib.cancelOrder.assert_called_once_with(stop_ant.order)


# ---------------------------------------------------------------------------
# Tests: reconciliar_stops_gtc con 0, 1, 2 y 3 stops activos
# ---------------------------------------------------------------------------

class TestReconciliarStopsGtc(unittest.TestCase):

    def test_cero_stops_no_cancela(self):
        ib = _make_ib([])
        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")
        self.assertEqual(cancelados, 0)
        ib.cancelOrder.assert_not_called()

    def test_un_stop_no_cancela(self):
        t = _make_trade("TRV", 305.63, 4, order_id=13443)
        ib = _make_ib([t])
        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")
        self.assertEqual(cancelados, 0)
        ib.cancelOrder.assert_not_called()

    def test_dos_stops_cancela_el_menor(self):
        """Con 2 stops, cancela el de precio menor, conserva el mayor."""
        t1 = _make_trade("TRV", 305.63, 4, order_id=13443)  # precio menor
        t2 = _make_trade("TRV", 306.18, 4, order_id=17743)  # precio mayor → conservar
        ib = _make_ib([t1, t2])

        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")

        self.assertEqual(cancelados, 1)
        ib.cancelOrder.assert_called_once_with(t1.order)

    def test_tres_stops_cancela_dos_menores(self):
        """Con 3 stops, conserva el mayor y cancela los otros dos."""
        t1 = _make_trade("TRV", 300.00, 4, order_id=11111)
        t2 = _make_trade("TRV", 305.63, 4, order_id=13443)
        t3 = _make_trade("TRV", 306.18, 4, order_id=17743)  # mayor → conservar
        ib = _make_ib([t1, t2, t3])

        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")

        self.assertEqual(cancelados, 2)
        cancelados_ids = {c[0][0].orderId for c in ib.cancelOrder.call_args_list}
        self.assertIn(11111, cancelados_ids)
        self.assertIn(13443, cancelados_ids)
        self.assertNotIn(17743, cancelados_ids)

    def test_sim_no_cancela_pero_cuenta(self):
        """En modo SIM, no se llama cancelOrder pero el contador sube."""
        t1 = _make_trade("TRV", 305.63, 4, order_id=13443)
        t2 = _make_trade("TRV", 306.18, 4, order_id=17743)
        ib = _make_ib([t1, t2])

        cancelados = reconciliar_stops_gtc(ib, mode="SIM")

        self.assertEqual(cancelados, 1)
        ib.cancelOrder.assert_not_called()

    def test_duplicados_en_dos_simbolos_distintos(self):
        """Duplicados en TRV y AAPL se reconcilian independientemente."""
        trv1 = _make_trade("TRV",  305.63, 4, order_id=13443)
        trv2 = _make_trade("TRV",  306.18, 4, order_id=17743)
        aapl1 = _make_trade("AAPL", 180.00, 3, order_id=20001)
        aapl2 = _make_trade("AAPL", 182.50, 3, order_id=20002)  # mayor → conservar
        ib = _make_ib([trv1, trv2, aapl1, aapl2])

        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")

        self.assertEqual(cancelados, 2)
        cancelados_ids = {c[0][0].orderId for c in ib.cancelOrder.call_args_list}
        self.assertIn(13443, cancelados_ids)   # TRV menor
        self.assertIn(20001, cancelados_ids)   # AAPL menor
        self.assertNotIn(17743, cancelados_ids)
        self.assertNotIn(20002, cancelados_ids)

    def test_stops_cancelled_no_cuentan_como_activos(self):
        """Un stop en estado Cancelled no debe participar en la reconciliación."""
        t_activo   = _make_trade("TRV", 305.63, 4, order_id=13443, status="Submitted")
        t_cancelado = _make_trade("TRV", 306.18, 4, order_id=17743, status="Cancelled")
        ib = _make_ib([t_activo, t_cancelado])

        cancelados = reconciliar_stops_gtc(ib, mode="PAPER")

        self.assertEqual(cancelados, 0)
        ib.cancelOrder.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
