"""
tests/test_reemplazo_stop_gtc_confirmado.py

Tests de la confirmación ACTIVA (Fase 2, fix del hallazgo ALTA #2 de la
auditoría 07/08/2026 -- "confirmación de órdenes por sondeo de tiempo
fijo, sin eventos de error IBKR") en _reemplazar_stop_gtc(), vía
"cantidad cambia" (crea un stop GTC nuevo y cancela el antiguo).

Antes de este fix: ib.sleep(1) fijo tras colocar el stop nuevo, seguido
de una lista de EXCLUSIÓN ("Inactive","Cancelled","Rejected") que trataba
cualquier otro estado -- incluidos los genuinamente pendientes
(PendingSubmit/ApiPending, ni terminales ni confirmados) -- como éxito
sin más verificación, y ENTONCES cancelaba el stop antiguo. Era el
patrón de mayor riesgo real de todo el catálogo investigado: un falso
positivo de "nuevo stop aceptado" seguido de cancelar el único stop que
sí protegía la posición podía dejarla sin ningún stop real.

Fix: espera ACTIVA (bucle de sondeo con ib.sleep(), nunca un sleep fijo)
a un estado realmente confirmado (Trade.isActive() -- PreSubmitted o
Submitted, el reposo normal de una STP GTC ya aceptada -- o Filled),
vigilando Rejected/Inactive de forma explícita para cortar de inmediato
(ninguno de los dos vive en isDone()/isActive() de ib_insync 0.9.86).
Solo si se confirma se cancela el stop antiguo, con la misma espera
activa (timeout más corto: el peor caso ahí es un duplicado inofensivo,
no una posición desprotegida -- reconciliar_stops_gtc() ya limpia
duplicados en un ciclo posterior).

Cubre los 5 desenlaces del diseño aprobado:
    a) Confirmación rápida (happy path).
    b) Rechazo explícito del stop nuevo -- corte inmediato, sin agotar
       el timeout.
    c) Timeout sin respuesta del stop nuevo -- nunca confirma ni rechaza.
    d) Caso límite (Nota A del diseño): el stop ANTIGUO se dispara de
       verdad (Filled) durante la espera de su propia cancelación.
    e) Fallo al confirmar la cancelación del antiguo, con el nuevo YA
       confirmado protegiendo -- WARN, nunca Telegram crítico, éxito.

Los timeouts de producción (8s/5s) se sobreescriben con valores pequeños
vía patch.object(rebalance, ...) para que la suite sea rápida. ib.sleep()
está mockeado como no-op (mismo patrón que
test_stop_vivo_guard.py::TestObtenerPrecioVivo) -- el tiempo real
transcurrido en los escenarios de timeout queda acotado por el propio
valor de timeout parcheado, no por el sondeo en sí.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_reemplazo_stop_gtc_confirmado.py -v
"""

import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Bootstrap mínimo: evitar importar ib_insync real ni conexión de red
# (mismo patrón que tests/test_gtc_dedup.py y
# tests/test_pending_rebalance_reconciliacion.py)
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
    # verificar_apalancamiento_ampliar (CRÍTICA #1, auditoría 07/08/2026):
    # stub permisivo -- no es objeto de este fichero de test.
    m.verificar_apalancamiento_ampliar = MagicMock(return_value=(True, "OK", 0.0))
    sys.modules[mod] = m

# rebalance.py hace `from logger import log_event` a NIVEL DE MÓDULO (no
# inline) -- si otro fichero de test ya importó "rebalance" antes que
# este en la misma sesión de pytest (la colección importa TODOS los
# ficheros antes de ejecutar ningún test), esa referencia quedaría
# congelada al logger de aquel otro fichero, no al de este. Mismo patrón
# defensivo que test_rebalance_ampliar_leverage.py: forzar una
# reimportación limpia bajo los stubs de ESTE fichero antes de usar
# rebalance.
sys.modules.pop("rebalance", None)
import rebalance
from rebalance import _reemplazar_stop_gtc

# NOTA sobre send_telegram_critical: rebalance.py lo importa INLINE
# (`from telegram import send_telegram_critical`, dentro de la función,
# no a nivel de módulo) -- se resuelve contra sys.modules["telegram"] en
# el momento de la llamada, no en el de importar rebalance. Verificado
# empíricamente: al menos dos ficheros de test posteriores en orden
# alfabético (test_risk_guardian_*.py, que lo re-stubean con SU PROPIO
# Mock, y test_trailing_stop_guardrails.py, que lo despoja de
# sys.modules a propósito para forzar el módulo real -- ver su propio
# comentario) cambian sys.modules["telegram"] durante la fase de
# colección de pytest, ANTES de que se ejecute ningún test -- afecta a
# TODA la sesión, no solo a los tests que corren después de esos
# ficheros. Guardar una referencia propia a "telegram" (como hace
# _make_ib con logger en otros ficheros) no es fiable aquí por eso: en
# los tests que necesitan verificar send_telegram_critical se parchea
# directamente por string ("telegram.send_telegram_critical"), que
# unittest.mock resuelve de nuevo en el momento del test, no en el de
# la colección -- inmune a qué fichero se coleccionó último.


# ---------------------------------------------------------------------------
# Helpers para construir trades falsos (mismo patrón que test_gtc_dedup.py)
# ---------------------------------------------------------------------------

def _make_trade(symbol: str, precio: float, qty: int,
                 order_id: int = 1, status: str = "Submitted") -> MagicMock:
    trade = MagicMock()
    trade.contract.symbol     = symbol
    trade.order.orderType     = "STP"
    trade.order.action        = "SELL"
    trade.order.tif           = "GTC"
    trade.order.auxPrice      = precio
    trade.order.totalQuantity = qty
    trade.order.orderId       = order_id
    trade.orderStatus.status  = status
    return trade


def _make_ib(trades: list) -> MagicMock:
    """IB mock cuyo .trades() devuelve la lista dada. ib.sleep() queda
    como MagicMock no-op por defecto -- no introduce espera real."""
    ib = MagicMock()
    ib.trades.return_value = trades
    ib.isConnected.return_value = True
    return ib


class TestReemplazoStopGtcConfirmacionActiva(unittest.TestCase):

    # -- (a) Confirmación rápida (happy path) --------------------------

    def test_a_confirmacion_rapida_happy_path(self):
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443,
                                status="PreSubmitted")
        ib = _make_ib([stop_ant])
        nuevo_trade = _make_trade("TRV", 306.18, 6, order_id=99001,
                                   status="PreSubmitted")
        ib.placeOrder.return_value = nuevo_trade
        # Cancelación confirmada al instante -- transición realista.
        ib.cancelOrder.side_effect = lambda order: setattr(
            stop_ant.orderStatus, "status", "Cancelled")
        contrato = MagicMock()

        with patch.object(rebalance, "REPLACE_STOP_CONFIRM_TIMEOUT", 5.0), \
             patch.object(rebalance, "REPLACE_STOP_CANCEL_TIMEOUT", 5.0), \
             patch("telegram.send_telegram_critical") as mock_critical:
            resultado = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)

        self.assertTrue(resultado)
        ib.placeOrder.assert_called_once()
        ib.cancelOrder.assert_called_once_with(stop_ant.order)
        mock_critical.assert_not_called()

    # -- (b) Rechazo explícito -- corte inmediato, sin agotar el timeout --

    def test_b_rechazo_explicito_corta_sin_agotar_timeout(self):
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443,
                                status="PreSubmitted")
        ib = _make_ib([stop_ant])
        nuevo_trade = _make_trade("TRV", 306.18, 6, order_id=99002,
                                   status="Rejected")
        ib.placeOrder.return_value = nuevo_trade
        contrato = MagicMock()

        with patch.object(rebalance, "REPLACE_STOP_CONFIRM_TIMEOUT", 8.0), \
             patch("telegram.send_telegram_critical") as mock_critical:
            t0 = time.monotonic()
            resultado = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)
            elapsed = time.monotonic() - t0

        self.assertFalse(resultado)
        # Corte inmediato: el rechazo ya estaba en la primera lectura,
        # nunca llega a sondear -- mucho antes de agotar los 8s.
        self.assertLess(elapsed, 0.5)
        ib.sleep.assert_not_called()
        # El stop antiguo se conserva -- nunca se cancela.
        ib.cancelOrder.assert_not_called()
        mock_critical.assert_called_once()
        aviso = mock_critical.call_args[0][0]
        self.assertIn("TRV", aviso)
        self.assertIn("rechazado", aviso)

    # -- (c) Timeout sin respuesta -- nunca confirma ni rechaza -----------

    def test_c_timeout_sin_respuesta_no_confirma(self):
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443,
                                status="PreSubmitted")
        ib = _make_ib([stop_ant])
        # Se queda en PendingSubmit para siempre -- ActiveStates de
        # ib_insync, pero deliberadamente NO cuenta como confirmado aquí
        # (ver _ESTADOS_STOP_NUEVO_CONFIRMADO en rebalance.py).
        nuevo_trade = _make_trade("TRV", 306.18, 6, order_id=99003,
                                   status="PendingSubmit")
        ib.placeOrder.return_value = nuevo_trade
        contrato = MagicMock()

        with patch.object(rebalance, "REPLACE_STOP_CONFIRM_TIMEOUT", 0.15), \
             patch.object(rebalance, "REPLACE_STOP_POLL_INTERVAL", 0.02), \
             patch("telegram.send_telegram_critical") as mock_critical:
            t0 = time.monotonic()
            resultado = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)
            elapsed = time.monotonic() - t0

        self.assertFalse(resultado)
        self.assertGreaterEqual(elapsed, 0.15)
        self.assertLess(elapsed, 2.0)          # acotado -- nunca se cuelga
        ib.cancelOrder.assert_not_called()
        mock_critical.assert_called_once()
        aviso = mock_critical.call_args[0][0]
        self.assertIn("sin confirmar", aviso)

    # -- (d) Nota A: el stop viejo se dispara durante la espera de cancel -

    def test_d_stop_antiguo_se_dispara_durante_espera_cancelacion(self):
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443,
                                status="PreSubmitted")
        ib = _make_ib([stop_ant])
        nuevo_trade = _make_trade("TRV", 306.18, 6, order_id=99004,
                                   status="PreSubmitted")
        ib.placeOrder.return_value = nuevo_trade
        # cancelOrder() se envía, pero el mercado ya cruzó el nivel
        # antiguo -- la orden termina en Filled, no en Cancelled.
        ib.cancelOrder.side_effect = lambda order: setattr(
            stop_ant.orderStatus, "status", "Filled")
        contrato = MagicMock()

        with patch.object(rebalance, "REPLACE_STOP_CONFIRM_TIMEOUT", 5.0), \
             patch.object(rebalance, "REPLACE_STOP_CANCEL_TIMEOUT", 5.0), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch("telegram.send_telegram_critical") as mock_critical:
            resultado = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)

        # Desenlace correcto -- la posición ya se cerró por el stop
        # antiguo -- no es un fallo del reemplazo en sí.
        self.assertTrue(resultado)
        ib.cancelOrder.assert_called_once_with(stop_ant.order)
        mock_critical.assert_not_called()
        mensajes = [c.args[1] for c in mock_log.call_args_list]
        self.assertTrue(any("huérfano" in m for m in mensajes),
                         f"ningún log menciona el stop nuevo huérfano: {mensajes}")

    # -- (e) Fallo al confirmar la cancelación -- WARN, no crítico --------

    def test_e_fallo_confirmar_cancelacion_no_es_critico(self):
        stop_ant = _make_trade("TRV", 305.63, 4, order_id=13443,
                                status="PreSubmitted")
        ib = _make_ib([stop_ant])
        nuevo_trade = _make_trade("TRV", 306.18, 6, order_id=99005,
                                   status="PreSubmitted")
        ib.placeOrder.return_value = nuevo_trade
        # cancelOrder() no hace nada -- el status del antiguo se queda
        # congelado en PreSubmitted para siempre (nunca Cancelled/Filled).

        contrato = MagicMock()

        with patch.object(rebalance, "REPLACE_STOP_CONFIRM_TIMEOUT", 5.0), \
             patch.object(rebalance, "REPLACE_STOP_CANCEL_TIMEOUT", 0.15), \
             patch.object(rebalance, "REPLACE_STOP_POLL_INTERVAL", 0.02), \
             patch.object(rebalance, "log_event") as mock_log, \
             patch("telegram.send_telegram_critical") as mock_critical:
            resultado = _reemplazar_stop_gtc(ib, "TRV", contrato, 6, 306.18, stop_ant)

        # El nuevo stop YA protege la posición -- éxito, pese a no poder
        # confirmar la cancelación del antiguo.
        self.assertTrue(resultado)
        ib.cancelOrder.assert_called_once_with(stop_ant.order)
        # Clave del desenlace (e): nunca escala a Telegram crítico -- el
        # peor caso es un stop duplicado, no una posición desprotegida.
        mock_critical.assert_not_called()
        mensajes = [c.args[1] for c in mock_log.call_args_list]
        self.assertTrue(any("reconciliar_stops_gtc" in m for m in mensajes),
                         f"ningún log menciona la red de reconciliar_stops_gtc: {mensajes}")


if __name__ == "__main__":
    unittest.main()
