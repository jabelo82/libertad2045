"""
tests/test_detectar_stops_gtc_duplicados.py

Tests para detectar_stops_gtc_duplicados() (rebalance.py) — el helper
compartido del fix del Hallazgo MEDIA #6 (auditoría 07/08/2026): la
detección de stops GTC duplicados vivía triplicada en
portfolio_manager.py (inline), rebalance.py::_obtener_gtc_stops() y
rebalance.py::reconciliar_stops_gtc(), y dos de las tres copias no
filtraban por trade.orderStatus.status — podían confundir un stop ya
cancelado/ejecutado (que sigue en ib.trades(), la caché de la sesión)
con un duplicado activo real.

Caso TRV (23/06/2026, log real LIBERTAD_2026-06-23.csv): es la prueba
de que el fix cierra una divergencia real, no solo que no rompe nada —
antes de este fix, _obtener_gtc_stops() reportó "STOP GTC DUPLICADO
(rebalance): TRV — órdenes 13443 (305.63) y 17743 (306.18)" en un
ciclo donde reconciliar_stops_gtc() (que sí filtraba por estado) no
vio ningún duplicado ni antes ni después de ese ciclo — consistente
con que 13443 ya no estaba vivo.

Caso XEL/GOOG (26/06/2026 y 16/07/2026, ver 00_LIBERTAD2045_CONTEXT.txt
— incidente GOOG "no es un bug, es la tercera capa de seguridad
funcionando"): ventana normal de reemplazo — dos stops genuinamente
activos y con el mismo precio (una orden nueva sustituyendo a la
anterior por cambio de cantidad). Caso de control: debe seguir
detectándose como duplicado exactamente igual que hoy.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_detectar_stops_gtc_duplicados.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Otros ficheros de test (test_rebalance_ampliar_leverage.py y hermanos)
# sustituyen sys.modules["ib_insync"] y varias de las dependencias de
# rebalance.py por stubs incompletos para aislarlo de IBKR. pytest ejecuta
# todo el árbol de tests en el mismo proceso, así que esos stubs pueden
# seguir en sys.modules cuando este fichero se importa — hay que
# descartarlos explícitamente para obtener aquí el rebalance.py real.
# Mismo patrón que tests/test_position_size_shares_capital.py.
for _mod in ("ib_insync", "data_loader", "logger", "position_size",
             "risk_guardian", "telegram", "rebalance"):
    sys.modules.pop(_mod, None)

from rebalance import detectar_stops_gtc_duplicados  # noqa: E402


def _make_trade(symbol: str, precio: float, qty: int, order_id: int,
                 status: str = "Submitted", order_type: str = "STP",
                 action: str = "SELL", tif: str = "GTC") -> MagicMock:
    trade = MagicMock()
    trade.contract.symbol     = symbol
    trade.order.orderType     = order_type
    trade.order.action        = action
    trade.order.tif           = tif
    trade.order.auxPrice      = precio
    trade.order.totalQuantity = qty
    trade.order.orderId       = order_id
    trade.orderStatus.status  = status
    return trade


class TestDetectarStopsGtcDuplicados(unittest.TestCase):

    # ------------------------------------------------------------
    # Caso real TRV (23/06/2026) — orden en estado terminal mezclada
    # con una activa: el fix debe excluir la terminal.
    # ------------------------------------------------------------

    def test_trv_excluye_orden_en_estado_terminal(self):
        antigua_cancelada = _make_trade("TRV", 305.63, 4, order_id=13443,
                                         status="Cancelled")
        vigente = _make_trade("TRV", 306.18, 6, order_id=17743,
                               status="Submitted")

        resultado = detectar_stops_gtc_duplicados([antigua_cancelada, vigente])

        self.assertIn("TRV", resultado)
        self.assertEqual(len(resultado["TRV"]), 1)
        self.assertIs(resultado["TRV"][0], vigente)

    def test_trv_variante_filled_tambien_se_excluye(self):
        """Filled es igual de terminal que Cancelled/ApiCancelled — no debe
        contar como duplicado activo (ver _ESTADOS_STOP_ANTERIOR_RESUELTO,
        que ya trata Filled como resuelto en rebalance.py)."""
        antigua_filled = _make_trade("TRV", 305.63, 4, order_id=13443,
                                      status="Filled")
        vigente = _make_trade("TRV", 306.18, 6, order_id=17743,
                               status="PreSubmitted")

        resultado = detectar_stops_gtc_duplicados([antigua_filled, vigente])

        self.assertEqual(len(resultado["TRV"]), 1)
        self.assertIs(resultado["TRV"][0], vigente)

    # ------------------------------------------------------------
    # Caso de control XEL/GOOG — dos stops genuinamente activos
    # (ventana normal de reemplazo): debe seguir detectándose igual.
    # ------------------------------------------------------------

    def test_xel_dos_activos_mismo_precio_siguen_detectandose(self):
        orden_antigua = _make_trade("XEL", 79.72, 10, order_id=18257,
                                     status="PreSubmitted")
        orden_nueva = _make_trade("XEL", 79.72, 12, order_id=18808,
                                   status="Submitted")

        resultado = detectar_stops_gtc_duplicados([orden_antigua, orden_nueva])

        self.assertEqual(len(resultado["XEL"]), 2)
        # Empate de precio: se conserva el primero visto (orden_antigua),
        # mismo criterio que ya aplicaban las 3 copias originales.
        self.assertIs(resultado["XEL"][0], orden_antigua)

    def test_goog_dos_activos_distintos_estados_activos(self):
        """PreSubmitted y Submitted son ambos estados 'vivos' (ActiveStates
        de ib_insync) — deben contar igual, no solo un estado concreto."""
        orden_1 = _make_trade("GOOG", 354.38, 1, order_id=22498,
                               status="PreSubmitted")
        orden_2 = _make_trade("GOOG", 354.38, 1, order_id=22519,
                               status="Submitted")

        resultado = detectar_stops_gtc_duplicados([orden_1, orden_2])

        self.assertEqual(len(resultado["GOOG"]), 2)

    # ------------------------------------------------------------
    # Casos generales
    # ------------------------------------------------------------

    def test_sin_stops_devuelve_dict_vacio(self):
        self.assertEqual(detectar_stops_gtc_duplicados([]), {})

    def test_un_solo_stop_no_es_duplicado_pero_aparece_en_el_mapa(self):
        unico = _make_trade("AAPL", 200.0, 5, order_id=1, status="Submitted")
        resultado = detectar_stops_gtc_duplicados([unico])
        self.assertEqual(len(resultado), 1)
        self.assertEqual(len(resultado["AAPL"]), 1)
        self.assertIs(resultado["AAPL"][0], unico)

    def test_tres_duplicados_orden_descendente_por_precio(self):
        bajo  = _make_trade("MSFT", 100.0, 1, order_id=1, status="Submitted")
        alto  = _make_trade("MSFT", 110.0, 1, order_id=2, status="Submitted")
        medio = _make_trade("MSFT", 105.0, 1, order_id=3, status="PreSubmitted")

        resultado = detectar_stops_gtc_duplicados([bajo, alto, medio])

        self.assertEqual([t.order.orderId for t in resultado["MSFT"]], [2, 3, 1])

    def test_ignora_ordenes_que_no_son_stop_gtc_sell(self):
        no_stp    = _make_trade("IBM", 100.0, 1, order_id=1, order_type="LMT")
        no_sell   = _make_trade("IBM", 100.0, 1, order_id=2, action="BUY")
        no_gtc    = _make_trade("IBM", 100.0, 1, order_id=3, tif="DAY")
        no_activa = _make_trade("IBM", 100.0, 1, order_id=4, status="ApiPending")

        resultado = detectar_stops_gtc_duplicados([no_stp, no_sell, no_gtc, no_activa])

        self.assertEqual(resultado, {})

    def test_trail_cuenta_igual_que_stp(self):
        stop_trail = _make_trade("TSLA", 200.0, 1, order_id=1,
                                  order_type="TRAIL", status="Submitted")
        resultado = detectar_stops_gtc_duplicados([stop_trail])
        self.assertIn("TSLA", resultado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
