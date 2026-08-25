"""
tests/test_stop_vivo_guard.py

Tests unitarios de la guardia de frescura temporal del trailing stop
(incidente ANET 05/08/2026, segunda causa): entre el cierre oficial usado
por calcular_trailing_stop() al INICIO del ciclo y el placeOrder() real
del stop pueden pasar varios minutos (13-14 min en el incidente real) —
tiempo suficiente para que el margen de seguridad se desplome aunque el
cálculo, en su momento, fuera correcto.

Se verificó que el reordenamiento del ciclo (stops de cartera antes del
escaneo de 500 símbolos) NO es la causa: esa fase ya se ejecuta antes del
escaneo desde el 21/05/2026 — el desfase ocurre DENTRO de la propia fase
de gestión de cartera. El fix aplicado es la alternativa "precio fresco
justo antes de transmitir" (ver 00_LIBERTAD2045_CONTEXT.txt):

    - position_size.verificar_margen_stop_vivo(): guardia pura, reaplica
      el umbral de STOP_DEMASIADO_CERCA contra un precio VIVO.
    - portfolio_manager.obtener_precio_vivo(): snapshot acotado en el
      tiempo (nunca bloqueante sin límite) pedido justo antes de
      placeOrder().
    - portfolio_manager.evaluar_stops_por_cierre(): usa ambas para abortar
      la transmisión (conservando el stop GTC vigente) si el margen se ha
      colapsado en el instante real de envío.

Cubre:
    - verificar_margen_stop_vivo: caso con margen sano, caso colapsado
      (reproduce el patrón del desfase real), fail-open sin precio vivo /
      sin ATR, nunca propaga excepciones.
    - obtener_precio_vivo: devuelve el precio cuando el snapshot llega a
      tiempo, devuelve None acotado en el tiempo si no llega (nunca se
      cuelga), siempre cancela la suscripción, nunca propaga excepciones.
    - Integración en evaluar_stops_por_cierre: con margen vivo colapsado
      NO se transmite (placeOrder no llamado, stop GTC vigente
      conservado); con margen vivo sano se transmite igual que antes.

Ejecutar desde la raíz del proyecto:
    venv/bin/python -m pytest tests/test_stop_vivo_guard.py -v
"""

import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# Import limpio — ver nota en test_trailing_stop_guardrails.py sobre
# módulos stubeados por test_gtc_dedup.py si se ejecuta antes en la misma
# sesión de pytest.
# Nota sobre patch targets: usamos @patch.object(modulo, "atributo") con la
# referencia de módulo capturada aquí (no @patch("modulo.atributo") por
# string). pytest importa TODOS los archivos de test durante la colección
# antes de ejecutar ningún test; si un archivo posterior (p.ej.
# test_trailing_stop_guardrails.py) vuelve a popear y reimportar
# "position_size", sys.modules["position_size"] termina apuntando a OTRA
# instancia de módulo distinta de la que usan las funciones que probamos
# aquí. Un patch por string resuelto en sys.modules en el momento del test
# parchearía esa otra instancia y el parche no tendría efecto real —
# exactamente el fallo (y la llamada de red real a Telegram) que motivó
# este comentario.
for _mod in ("position_size", "portfolio_manager"):
    sys.modules.pop(_mod, None)

import position_size
import portfolio_manager
from position_size import verificar_margen_stop_vivo, MIN_STOP_DISTANCE_ATR
from portfolio_manager import obtener_precio_vivo, evaluar_stops_por_cierre


# ---------------------------------------------------------------------------
# verificar_margen_stop_vivo (position_size.py) — guardia pura
# ---------------------------------------------------------------------------

class TestVerificarMargenStopVivo(unittest.TestCase):

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_margen_sano_no_bloquea(self, mock_alerta):
        # precio vivo lejos del stop, margen >> 1×ATR
        ok = verificar_margen_stop_vivo(precio_vivo=199.5, stop_price=181.36, atr=8.0)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_margen_colapsado_por_desfase_bloquea(self, mock_alerta):
        # Reproduce el patrón real: el precio se movió entre el cálculo
        # (cierre usado por calcular_trailing_stop) y la transmisión real.
        ok = verificar_margen_stop_vivo(precio_vivo=182.0, stop_price=181.36,
                                         atr=8.0, symbol="ANET")
        self.assertFalse(ok)
        mock_alerta.assert_called_once()
        self.assertEqual(mock_alerta.call_args.args[0], "STOP_DEMASIADO_CERCA_VIVO")
        self.assertEqual(mock_alerta.call_args.kwargs.get("symbol"), "ANET")

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_margen_exactamente_en_el_umbral_no_bloquea(self, mock_alerta):
        # distancia == min_mult × atr → no dispara (umbral es "<", no "<=")
        atr = 8.0
        stop = 100.0
        precio_vivo = stop + MIN_STOP_DISTANCE_ATR * atr
        ok = verificar_margen_stop_vivo(precio_vivo, stop, atr)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_sin_precio_vivo_no_bloquea_fail_open(self, mock_alerta):
        ok = verificar_margen_stop_vivo(precio_vivo=None, stop_price=181.36, atr=8.0)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_precio_vivo_nan_no_bloquea_fail_open(self, mock_alerta):
        ok = verificar_margen_stop_vivo(precio_vivo=float("nan"), stop_price=181.36, atr=8.0)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_precio_vivo_no_positivo_no_bloquea_fail_open(self, mock_alerta):
        ok = verificar_margen_stop_vivo(precio_vivo=0.0, stop_price=181.36, atr=8.0)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_sin_atr_no_bloquea_fail_open(self, mock_alerta):
        ok = verificar_margen_stop_vivo(precio_vivo=182.0, stop_price=181.36, atr=None)
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    @patch.object(position_size, "_alertar_anomalia_trailing")
    def test_atr_nan_no_bloquea_fail_open(self, mock_alerta):
        ok = verificar_margen_stop_vivo(precio_vivo=182.0, stop_price=181.36, atr=float("nan"))
        self.assertTrue(ok)
        mock_alerta.assert_not_called()

    def test_fallo_interno_nunca_propaga(self):
        # stop_price no numérico → la resta lanza TypeError internamente;
        # la guardia debe absorberlo y no bloquear (fail-open), nunca
        # interrumpir el ciclo real de stops.
        ok = verificar_margen_stop_vivo(precio_vivo=182.0, stop_price="no-numerico", atr=8.0)
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# obtener_precio_vivo (portfolio_manager.py) — snapshot acotado en el tiempo
# ---------------------------------------------------------------------------

def _make_ib_con_ticker(precio_final, ticks_hasta_precio=0):
    """
    IB mock cuyo ticker.marketPrice() devuelve NaN durante
    `ticks_hasta_precio` llamadas y luego `precio_final` de forma estable.
    ib.sleep() es un no-op (MagicMock) — no introduce espera real.
    """
    ticker = MagicMock()
    valores = [float("nan")] * ticks_hasta_precio + [precio_final] * 100
    ticker.marketPrice.side_effect = valores

    ib = MagicMock()
    ib.reqMktData.return_value = ticker
    return ib, ticker


class TestObtenerPrecioVivo(unittest.TestCase):

    def test_precio_disponible_de_inmediato(self):
        ib, ticker = _make_ib_con_ticker(precio_final=182.0)
        contrato = MagicMock()

        precio = obtener_precio_vivo(ib, contrato, symbol="TEST", timeout=1.0)

        self.assertEqual(precio, 182.0)
        # Snapshot con éxito → TWS ya cerró la suscripción sola
        # (tickSnapshotEnd). cancelMktData() sería redundante y solo
        # generaría ruido ("Error 300") — no debe llamarse.
        ib.cancelMktData.assert_not_called()

    def test_precio_llega_tras_varios_sondeos(self):
        ib, ticker = _make_ib_con_ticker(precio_final=182.0, ticks_hasta_precio=3)
        contrato = MagicMock()

        precio = obtener_precio_vivo(ib, contrato, symbol="TEST", timeout=1.0)

        self.assertEqual(precio, 182.0)
        ib.cancelMktData.assert_not_called()

    def test_sin_datos_a_tiempo_devuelve_none_acotado(self):
        # marketPrice() nunca deja de ser NaN — el snapshot no llega nunca.
        # Con timeout pequeño, la función debe volver en tiempo acotado
        # (no colgarse) y devolver None.
        ib, ticker = _make_ib_con_ticker(precio_final=float("nan"))
        contrato = MagicMock()

        t0 = time.monotonic()
        precio = obtener_precio_vivo(ib, contrato, symbol="TEST", timeout=0.3)
        elapsed = time.monotonic() - t0

        self.assertIsNone(precio)
        self.assertLess(elapsed, 2.0)   # margen amplio sobre el timeout de 0.3s
        # Sin precio a tiempo, el snapshot puede seguir vivo en TWS —
        # aquí sí conviene cancelar explícitamente.
        ib.cancelMktData.assert_called_once_with(contrato)

    def test_excepcion_en_reqmktdata_devuelve_none_y_no_propaga(self):
        ib = MagicMock()
        ib.reqMktData.side_effect = Exception("Gateway desconectado")
        contrato = MagicMock()

        precio = obtener_precio_vivo(ib, contrato, symbol="TEST", timeout=0.3)

        self.assertIsNone(precio)
        # cancelMktData se intenta igualmente (limpieza best-effort)
        ib.cancelMktData.assert_called_once_with(contrato)

    def test_excepcion_en_cancelmktdata_no_propaga(self):
        # Camino donde SÍ se llama a cancelMktData (timeout, sin precio) y
        # esa llamada falla — no debe propagar ni impedir devolver None.
        ib, ticker = _make_ib_con_ticker(precio_final=float("nan"))
        ib.cancelMktData.side_effect = Exception("ya cancelado")
        contrato = MagicMock()

        precio = obtener_precio_vivo(ib, contrato, symbol="TEST", timeout=0.3)

        self.assertIsNone(precio)
        ib.cancelMktData.assert_called_once_with(contrato)


# ---------------------------------------------------------------------------
# Integración: evaluar_stops_por_cierre() respeta la guardia vivo
# ---------------------------------------------------------------------------

def _df_trailing(atr=8.0, high=200.0, close=200.0, percentil=0.5, n=14):
    """DataFrame mínimo para forzar un trailing stop calculable y real."""
    return pd.DataFrame({
        "ATR":           [atr] * n,
        "ATR_PERCENTIL": [percentil] * n,
        "high":          [high] * n,
        "close":         [close] * n,
    })


def _make_posicion_con_stop(symbol="TEST", shares=10, stop_actual=150.0, order_id=42):
    pos = MagicMock()
    pos.position = shares
    pos.contract.symbol = symbol

    trade_stop = MagicMock()
    trade_stop.contract = pos.contract
    trade_stop.order.orderType     = "STP"
    trade_stop.order.action        = "SELL"
    trade_stop.order.tif           = "GTC"
    trade_stop.order.auxPrice      = stop_actual
    trade_stop.order.totalQuantity = shares
    trade_stop.order.orderId       = order_id
    # Stop GTC ya activo — necesario desde el fix del Hallazgo MEDIA #6
    # (auditoría 07/08/2026): detectar_stops_gtc_duplicados() ahora filtra
    # por orderStatus.status, y un MagicMock sin configurar no coincide
    # con ningún estado "vivo".
    trade_stop.orderStatus.status  = "PreSubmitted"

    ib = MagicMock()
    ib.positions.return_value = [pos]
    ib.trades.return_value    = [trade_stop]

    return ib, pos, trade_stop


class TestEvaluarStopsPorCierreConGuardiaVivo(unittest.TestCase):

    def setUp(self):
        # nuevo_stop real calculado por calcular_trailing_stop() con estos
        # datos: mult = (4.0-(4.0-2.2)*0.5)*0.75 = 2.33 → 200 - 8*2.33 = 181.36
        self.df = _df_trailing(atr=8.0, high=200.0, close=200.0, percentil=0.5)
        self.nuevo_stop_esperado = 181.36

    @patch.object(portfolio_manager, "obtener_precio_vivo")
    def test_margen_vivo_colapsado_no_transmite_y_conserva_stop_vigente(self, mock_precio_vivo):
        ib, pos, trade_stop = _make_posicion_con_stop(stop_actual=150.0)
        # Precio vivo pegado al nuevo stop calculado — simula que el precio
        # se movió durante el desfase cálculo→transmisión.
        mock_precio_vivo.return_value = 182.0

        cerrados = evaluar_stops_por_cierre(ib, datos={"TEST": self.df}, mode="PAPER")

        self.assertEqual(cerrados, [])
        ib.placeOrder.assert_not_called()
        # El stop GTC en IBKR queda tal cual — nunca se tocó auxPrice
        self.assertEqual(trade_stop.order.auxPrice, 150.0)
        mock_precio_vivo.assert_called_once_with(ib, trade_stop.contract, symbol="TEST")

    @patch.object(portfolio_manager, "obtener_precio_vivo")
    def test_margen_vivo_sano_transmite_igual_que_antes(self, mock_precio_vivo):
        ib, pos, trade_stop = _make_posicion_con_stop(stop_actual=150.0)
        # Precio vivo con margen amplio respecto al nuevo stop.
        mock_precio_vivo.return_value = 199.5

        cerrados = evaluar_stops_por_cierre(ib, datos={"TEST": self.df}, mode="PAPER")

        self.assertEqual(cerrados, [])
        ib.placeOrder.assert_called_once_with(trade_stop.contract, trade_stop.order)
        self.assertEqual(trade_stop.order.auxPrice, self.nuevo_stop_esperado)

    @patch.object(portfolio_manager, "obtener_precio_vivo")
    def test_sin_precio_vivo_disponible_transmite_igual_que_antes(self, mock_precio_vivo):
        # Snapshot no disponible (timeout, error) → fail-open, comportamiento
        # idéntico al que había antes de esta guardia.
        mock_precio_vivo.return_value = None
        ib, pos, trade_stop = _make_posicion_con_stop(stop_actual=150.0)

        cerrados = evaluar_stops_por_cierre(ib, datos={"TEST": self.df}, mode="PAPER")

        self.assertEqual(cerrados, [])
        ib.placeOrder.assert_called_once_with(trade_stop.contract, trade_stop.order)
        self.assertEqual(trade_stop.order.auxPrice, self.nuevo_stop_esperado)

    @patch.object(portfolio_manager, "obtener_precio_vivo")
    def test_modo_sim_no_pide_precio_vivo(self, mock_precio_vivo):
        # En SIM no se transmite nada a IBKR — no tiene sentido gastar un
        # snapshot en vivo. Comportamiento previo a este fix, preservado.
        ib, pos, trade_stop = _make_posicion_con_stop(stop_actual=150.0)

        cerrados = evaluar_stops_por_cierre(ib, datos={"TEST": self.df}, mode="SIM")

        self.assertEqual(cerrados, [])
        mock_precio_vivo.assert_not_called()
        ib.placeOrder.assert_not_called()
        # En SIM el nivel se sigue simulando como actualizado en memoria
        # (igual que antes de este fix), pero nunca se llama a placeOrder.
        self.assertEqual(trade_stop.order.auxPrice, self.nuevo_stop_esperado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
