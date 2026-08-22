"""
tests/test_pending_rebalance_reconciliacion.py

Tests unitarios para la reconciliación de pending_rebalance.json (H-4).

Cubre el fix del incidente real DVN (10-11/08/2026, ver sección 12 del
contexto): una entrada AMPLIAR arrastrada de una fase/sesión anterior se
daba por resuelta porque una posición NUEVA e independiente en el mismo
símbolo coincidía por casualidad con shares_esperadas, sin ninguna
relación con la orden original. El fix verifica contra el order_id real
de IBKR (_verificar_ejecucion_pendiente) antes de dar una entrada por
resuelta, y descarta como huérfana cualquier entrada cuyo order_id no
tenga rastro alguno (ni fill, ni orden activa) tras un umbral de días.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_pending_rebalance_reconciliacion.py -v
"""

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap mínimo: evitar importar ib_insync real ni conexión de red
# (mismo patrón que tests/test_gtc_dedup.py)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    # verificar_apalancamiento_ampliar (CRÍTICA #1, auditoría 07/08/2026):
    # stub permisivo — no es objeto de este fichero de test, AMPLIAR debe
    # comportarse igual que antes de ese fix salvo que un test concreto
    # lo sobreescriba.
    m.verificar_apalancamiento_ampliar = MagicMock(return_value=(True, "OK", 0.0))
    sys.modules[mod] = m

import rebalance
from rebalance import (
    _reconciliar_pendientes,
    _verificar_ejecucion_pendiente,
    _guardar_pendiente_ampliar,
    _guardar_pendiente_reducir,
    _leer_pendientes,
    PENDING_ORPHAN_THRESHOLD_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fill(order_id: int, symbol: str = "DVN", side: str = "BOT") -> MagicMock:
    fill = MagicMock()
    fill.execution.orderId = order_id
    fill.execution.side = side
    fill.execution.shares = 8
    fill.execution.price = 45.48
    fill.execution.execId = f"exec-{order_id}"
    fill.contract.symbol = symbol
    return fill


def _make_trade(order_id: int, symbol: str = "DVN", status: str = "Submitted") -> MagicMock:
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.orderStatus.status = status
    trade.contract.symbol = symbol
    return trade


def _make_position(symbol: str, qty: int) -> MagicMock:
    pos = MagicMock()
    pos.contract.symbol = symbol
    pos.position = qty
    return pos


def _make_ib(positions=None, fills=None, trades=None) -> MagicMock:
    ib = MagicMock()
    ib.positions.return_value = positions or []
    ib.fills.return_value = fills or []
    ib.trades.return_value = trades or []
    return ib


def _timestamp_hace(dias: int) -> str:
    return (datetime.now() - timedelta(days=dias)).isoformat()


# ---------------------------------------------------------------------------
# Tests: _verificar_ejecucion_pendiente
# ---------------------------------------------------------------------------

class TestVerificarEjecucionPendiente(unittest.TestCase):

    def test_order_id_none_es_no_verificable(self):
        ib = _make_ib()
        self.assertEqual(_verificar_ejecucion_pendiente(ib, None, "DVN"), "NO_VERIFICABLE")

    def test_fill_real_para_ese_order_id_es_confirmada(self):
        ib = _make_ib(fills=[_make_fill(555, "DVN")])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "CONFIRMADA")

    def test_fill_de_otro_order_id_no_confirma(self):
        """Un fill real de OTRO order_id (p.ej. una entrada nueva del escáner)
        no debe confirmar una entrada pendiente que rastrea otro order_id."""
        ib = _make_ib(fills=[_make_fill(999, "DVN")], trades=[])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "HUERFANA")

    def test_orden_abierta_sin_fill_es_pendiente_activa(self):
        ib = _make_ib(trades=[_make_trade(555, "DVN", status="Submitted")])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "PENDIENTE_ACTIVA")

    def test_trade_filled_sin_estar_en_fills_tambien_confirma(self):
        ib = _make_ib(trades=[_make_trade(555, "DVN", status="Filled")])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "CONFIRMADA")

    def test_trade_cancelado_es_huerfana(self):
        ib = _make_ib(trades=[_make_trade(555, "DVN", status="Cancelled")])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "HUERFANA")

    def test_sin_rastro_en_fills_ni_trades_es_huerfana(self):
        ib = _make_ib(fills=[], trades=[])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "HUERFANA")

    def test_error_leyendo_fills_es_no_verificable(self):
        ib = MagicMock()
        ib.fills.side_effect = Exception("boom")
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "NO_VERIFICABLE")

    def test_sin_rastro_local_dispara_reqexecutions_explicito(self):
        """Si no hay rastro en la caché local (fills/trades), debe forzarse
        una consulta explícita a reqExecutions() antes de concluir huérfana
        -- por si el Gateway se reinició entre sesiones y la caché local no
        llegó a recibir ese historial en el backfill automático de connect()."""
        ib = _make_ib(fills=[], trades=[])
        _verificar_ejecucion_pendiente(ib, 555, "DVN")
        ib.reqExecutions.assert_called_once()

    def test_reqexecutions_explicito_encuentra_fill_devuelve_confirmada(self):
        """Si la consulta explícita repuebla ib.fills() con la ejecución
        real (que la caché local todavía no tenía), se confirma -- no se
        descarta como huérfana solo porque el primer vistazo local estaba
        vacío."""
        fills = []
        ib = _make_ib(trades=[])
        ib.fills.return_value = fills  # override: `fills or []` en _make_ib
                                        # crearía una lista NUEVA con [] vacío

        def _reqexecutions_efecto(execFilter):
            fills.append(_make_fill(555, "DVN"))

        ib.reqExecutions.side_effect = _reqexecutions_efecto

        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "CONFIRMADA")

    def test_reqexecutions_explicito_usa_filtro_acotado_en_dias_y_simbolo(self):
        ib = _make_ib(fills=[], trades=[])
        _verificar_ejecucion_pendiente(ib, 555, "DVN")

        # ib_insync.ExecutionFilter está stubbeado como MagicMock en este
        # fichero de test -- Mock(**kwargs) aplica los kwargs como
        # atributos reales de la instancia (configure_mock), así que
        # podemos comprobar el filtro realmente pasado a reqExecutions()
        # sin depender de la clase real de ib_insync.
        (filtro,), _ = ib.reqExecutions.call_args
        self.assertEqual(filtro.symbol, "DVN")
        self.assertTrue(filtro.time)  # se pasó alguna fecha de corte

    def test_reqexecutions_explicito_sin_rastro_sigue_siendo_huerfana(self):
        ib = _make_ib(fills=[], trades=[])  # reqExecutions no añade nada
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "HUERFANA")
        ib.reqExecutions.assert_called_once()

    def test_error_en_reqexecutions_explicito_es_no_verificable(self):
        ib = _make_ib(fills=[], trades=[])
        ib.reqExecutions.side_effect = Exception("timeout de red")
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "NO_VERIFICABLE")

    def test_trade_cancelado_no_dispara_reqexecutions_explicito(self):
        """Un estado terminal ya confirmado (Cancelled) por IBKR en
        ib.trades() es autoritativo -- no hace falta gastar la consulta
        adicional."""
        ib = _make_ib(trades=[_make_trade(555, "DVN", status="Cancelled")])
        self.assertEqual(_verificar_ejecucion_pendiente(ib, 555, "DVN"), "HUERFANA")
        ib.reqExecutions.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _reconciliar_pendientes — caso real DVN y comportamiento general
# ---------------------------------------------------------------------------

class TestReconciliarPendientes(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._pending_file = Path(self._tmpdir.name) / "pending_rebalance.json"
        self._patcher = patch.object(rebalance, "_PENDING_REBALANCE_FILE", self._pending_file)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_modo_sim_no_reconcilia_ni_lee_posiciones(self):
        _guardar_pendiente_ampliar("DVN", 8, 8, order_id=555)
        ib = _make_ib()
        resultado = _reconciliar_pendientes(ib, mode="SIM")
        self.assertEqual(resultado, set())
        ib.positions.assert_not_called()

    def test_sin_pendientes_devuelve_vacio(self):
        ib = _make_ib()
        resultado = _reconciliar_pendientes(ib, mode="LIVE")
        self.assertEqual(resultado, set())

    def test_reproduce_incidente_dvn_no_confunde_posicion_no_relacionada(self):
        """
        Caso real: AMPLIAR de DVN arrastrado varios días (order_id=555, de
        una fase/sesión anterior), y una posición DVN de 8 acciones que
        existe de verdad en la cuenta pero que viene de una entrada NUEVA e
        independiente del escáner (order_id=999, no relacionado).

        El fix NO debe dar la entrada por resuelta solo porque la cantidad
        coincide -- debe verificar el order_id, no encontrar rastro de 555
        ni en fills() ni en trades(), y tras superar el umbral de días
        descartarla como huérfana (no dejarla viva para siempre tampoco).

        Timestamp fijado a 5 días (>= PENDING_ORPHAN_THRESHOLD_DAYS=3, pero
        < 7) para aislar la verificación de ESTE fix del purgado genérico
        de 7 días que ya existía antes en _leer_pendientes() (mecanismo
        previo, más burdo, que no se ha tocado y sigue actuando como red
        de seguridad adicional -- el incidente real de DVN llevaba 9 días,
        pero probarlo con 9 aquí dispararía primero ese purgado genérico
        en vez de ejercitar la verificación por order_id que es el objeto
        de este test).
        """
        _guardar_pendiente_ampliar("DVN", 8, 8, order_id=555)
        pendientes = _leer_pendientes()
        pendientes["DVN"]["timestamp"] = _timestamp_hace(5)
        rebalance._guardar_pendientes(pendientes)

        # Posición real de 8 acciones -- coincide con shares_esperadas,
        # pero NO tiene ninguna relación con order_id=555 (fills/trades
        # vacíos para ese id; la orden 555 es de otra sesión/cuenta).
        ib = _make_ib(
            positions=[_make_position("DVN", 8)],
            fills=[],
            trades=[],
        )

        with patch.object(rebalance, "send_telegram") as mock_telegram:
            resultado = _reconciliar_pendientes(ib, mode="LIVE")

        # No se confunde con "resuelta": no queda en pendientes activos
        # (fue descartada por huérfana), y el JSON queda limpio.
        self.assertNotIn("DVN", resultado)
        self.assertEqual(_leer_pendientes(), {})
        mock_telegram.assert_called_once()
        aviso = mock_telegram.call_args[0][0]
        self.assertIn("DVN", aviso)
        self.assertIn("555", aviso)

    def test_no_declara_huerfana_antes_del_umbral(self):
        """La misma situación de arriba pero recién creada (1 día) no debe
        descartarse todavía -- solo tras superar PENDING_ORPHAN_THRESHOLD_DAYS."""
        _guardar_pendiente_ampliar("DVN", 8, 8, order_id=555)
        pendientes = _leer_pendientes()
        pendientes["DVN"]["timestamp"] = _timestamp_hace(1)
        rebalance._guardar_pendientes(pendientes)

        ib = _make_ib(positions=[_make_position("DVN", 8)], fills=[], trades=[])

        with patch.object(rebalance, "send_telegram") as mock_telegram:
            resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertIn("DVN", resultado)
        self.assertIn("DVN", _leer_pendientes())
        mock_telegram.assert_not_called()

    def test_umbral_es_configurable_por_defecto_3_dias(self):
        self.assertEqual(PENDING_ORPHAN_THRESHOLD_DAYS, 3)

    def test_ampliar_confirmado_por_order_id_borra_entrada(self):
        _guardar_pendiente_ampliar("DVN", 8, 8, order_id=555)
        ib = _make_ib(
            positions=[_make_position("DVN", 8)],
            fills=[_make_fill(555, "DVN", side="BOT")],
        )

        resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertNotIn("DVN", resultado)
        self.assertEqual(_leer_pendientes(), {})

    def test_reducir_confirmado_actualiza_stop_gtc(self):
        _guardar_pendiente_reducir("DVN", 4, -4, order_id=777)
        ib = _make_ib(
            positions=[_make_position("DVN", 4)],
            fills=[_make_fill(777, "DVN", side="SLD")],
        )

        with patch.object(rebalance, "_actualizar_stop_tras_reduccion") as mock_stop:
            resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertNotIn("DVN", resultado)
        self.assertEqual(_leer_pendientes(), {})
        mock_stop.assert_called_once_with(ib, "DVN", 4)

    def test_orden_activa_no_se_confunde_con_posicion_coincidente(self):
        """Si la orden sigue viva en IBKR (Submitted), una posición que
        coincide en cantidad tampoco debe confundirse con un fill -- sigue
        pendiente, sin descartarla ni darla por resuelta."""
        _guardar_pendiente_ampliar("DVN", 8, 8, order_id=555)
        ib = _make_ib(
            positions=[_make_position("DVN", 8)],
            fills=[],
            trades=[_make_trade(555, "DVN", status="Submitted")],
        )

        with patch.object(rebalance, "send_telegram") as mock_telegram:
            resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertIn("DVN", resultado)
        self.assertIn("DVN", _leer_pendientes())
        mock_telegram.assert_not_called()

    def test_legacy_sin_order_id_usa_fallback_de_cantidad(self):
        """Entradas guardadas antes de este fix (sin order_id) mantienen el
        comportamiento previo: comparación de símbolo/cantidad."""
        pendientes = {
            "DVN": {
                "accion": "AMPLIAR",
                "shares_esperadas": 8,
                "shares_delta": 8,
                "timestamp": _timestamp_hace(1),
                # sin "order_id" -- entrada legacy
            }
        }
        rebalance._guardar_pendientes(pendientes)

        ib = _make_ib(positions=[_make_position("DVN", 8)])
        resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertNotIn("DVN", resultado)
        self.assertEqual(_leer_pendientes(), {})

    def test_legacy_sin_order_id_sigue_pendiente_si_cantidad_insuficiente(self):
        pendientes = {
            "DVN": {
                "accion": "AMPLIAR",
                "shares_esperadas": 8,
                "shares_delta": 8,
                "timestamp": _timestamp_hace(1),
            }
        }
        rebalance._guardar_pendientes(pendientes)

        ib = _make_ib(positions=[_make_position("DVN", 3)])
        resultado = _reconciliar_pendientes(ib, mode="LIVE")

        self.assertIn("DVN", resultado)
        self.assertIn("DVN", _leer_pendientes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
