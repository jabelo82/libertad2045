"""
tests/test_libertad2045_reentrada_riesgo.py

Test de integración para el cableado de verificar_riesgo_entrada() dentro
del bucle de entradas nuevas en libertad2045.py — CRÍTICA #2 de la
auditoría 07/08/2026 (LIBERTAD2045_Auditoria_20260807): Risk Guardian
evaluado una sola vez ante el escaneo de ~500 símbolos.

Caso central pedido por Javier: risk_check() da OK al principio del ciclo,
pero el estado de la cuenta cambia (drawdown o apalancamiento) ANTES de
una entrada concreta más adelante en el bucle de señales — esa entrada
concreta debe omitirse (sin ejecutar_trade), mientras el resto de señales
del ciclo se evalúan y ejecutan con normalidad.

verificar_riesgo_entrada se sustituye por un mock controlado (su propia
lógica pura ya está cubierta en tests/test_risk_guardian_reentrada.py) —
este fichero prueba el CABLEADO en libertad2045.py: que se llama justo
antes de cada ejecutar_trade() individual, no solo una vez al principio.

Mismo patrón de stubs pesados que tests/test_dashboard_rg_bloqueo.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_libertad2045_reentrada_riesgo.py -v
"""

import datetime as _dt_real
import os
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Bootstrap: stubs mínimos para importar libertad2045 sin I/O de red.
# ---------------------------------------------------------------------------

_ib_stub = types.ModuleType("ib_insync")
for _name in ("IB", "MarketOrder", "Trade", "OrderStatus", "Stock", "Order"):
    setattr(_ib_stub, _name, MagicMock)
sys.modules["ib_insync"] = _ib_stub

_logger_stub = types.ModuleType("logger")
_logger_stub.log_event = MagicMock()
_logger_stub.limpiar_logs_antiguos = MagicMock()
sys.modules["logger"] = _logger_stub

_telegram_stub = types.ModuleType("telegram")
_telegram_stub.send_telegram = MagicMock()
_telegram_stub.send_telegram_critical = MagicMock()
sys.modules["telegram"] = _telegram_stub

_stub_attrs = {
    "conexion_ib":        ["conectar_ib", "desconectar_ib"],
    "data_loader":        ["obtener_datos"],
    "signal_engine":      ["detectar_senal"],
    "position_size":      ["calcular_posicion"],
    "portfolio_manager":  ["obtener_posiciones_abiertas", "filtrar_senales",
                           "evaluar_stops_por_cierre"],
    "trade_executor":     ["ejecutar_trade"],
    "order_manager":      ["cancelar_ordenes_pendientes"],
    "risk_guardian":      ["risk_check", "verificar_riesgo_entrada"],
    "process_guard":      ["acquire_lock", "release_lock"],
    "rebalance":          ["rebalancear", "resumen_texto", "reconciliar_stops_gtc"],
    "github_publisher":   ["publicar_dashboard"],
}
for _mod_name, _attrs in _stub_attrs.items():
    _m = types.ModuleType(_mod_name)
    for _attr in _attrs:
        setattr(_m, _attr, MagicMock())
    sys.modules[_mod_name] = _m

_sp500_stub = types.ModuleType("universe_sp500")
_sp500_stub.SP500 = []
sys.modules["universe_sp500"] = _sp500_stub

_dash_stub = types.ModuleType("dashboard")
_dash_stub.main = MagicMock()
sys.modules["dashboard"] = _dash_stub

sys.modules.pop("libertad2045", None)
import libertad2045  # noqa: E402

# Otros ficheros de test importan rebalance/risk_guardian reales — no dejar
# el stub puesto para el resto de la sesión de pytest.
sys.modules.pop("rebalance", None)
sys.modules.pop("risk_guardian", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ib_mock() -> MagicMock:
    """IB conectado sin posiciones abiertas."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []
    return ib


def _make_df(score_offset: float) -> pd.DataFrame:
    """
    DataFrame mínimo (200 filas) que satisface el filtro `len(df) < 200`
    del escaneo y produce un score determinista — (close-SMA50)/ATR, con
    SMA200 constante (pendiente 0) — para controlar el orden de las
    señales sin depender de detectar_senal() real (mockeado a True).
    """
    n = 200
    return pd.DataFrame({
        "close":  [100.0 + score_offset] * n,
        "SMA50":  [100.0] * n,
        "SMA200": [90.0] * n,
        "ATR":    [1.0] * n,
        "high":   [105.0] * n,
    })


class _FakeDatetime(_dt_real.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt_real.datetime(2026, 6, 15, 22, 10)  # lunes, no festivo NYSE


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestReentradaRiesgoEnBucleDeSenales(unittest.TestCase):
    """
    risk_check() OK al principio del ciclo; el estado cambia (drawdown o
    apalancamiento) antes de una entrada concreta más adelante en el
    bucle — esa entrada se omite, el resto del ciclo continúa normal.
    """

    def setUp(self):
        os.environ["TRADING_MODE"] = "SIM"
        os.environ["IBKR_PORT"]    = "4002"
        os.environ["MAQUINA"]      = "VPS"  # evita el intento de RTC reprogram

    def tearDown(self):
        os.environ.pop("IBKR_PORT", None)
        os.environ.pop("MAQUINA", None)

    def test_entrada_omitida_por_veredicto_de_riesgo_obsoleto_resto_continua(self):
        ib_mock = _make_ib_mock()

        # Tres señales, orden de score AAA(3) > BBB(2) > CCC(1) tras el
        # sorted() descendente de libertad2045.py — mismo orden en el que
        # se llamará a verificar_riesgo_entrada() y, si procede,
        # ejecutar_trade().
        dfs = {
            "AAA": _make_df(3.0),
            "BBB": _make_df(2.0),
            "CCC": _make_df(1.0),
        }

        mock_ejecutar_trade = MagicMock()

        # AAA: riesgo OK -> ejecuta.
        # BBB: el estado cambió durante el escaneo (drawdown obsoleto) -> omite.
        # CCC: riesgo OK de nuevo -> el ciclo sigue con normalidad.
        mock_verificar_riesgo = MagicMock(side_effect=[
            (True,  "OK"),
            (False, "drawdown 12.00% > límite 10.00% (pico 10000.00€ | actual 8800.00€)"),
            (True,  "OK"),
        ])

        with ExitStack() as stack:
            stack.enter_context(patch.object(libertad2045, "datetime", _FakeDatetime))
            stack.enter_context(patch.object(libertad2045, "acquire_lock"))
            stack.enter_context(patch.object(libertad2045, "release_lock"))
            stack.enter_context(patch.object(libertad2045, "limpiar_logs_antiguos"))
            stack.enter_context(patch.object(libertad2045, "log_event"))
            mock_send_telegram = stack.enter_context(
                patch.object(libertad2045, "send_telegram")
            )
            stack.enter_context(patch.object(libertad2045, "send_telegram_critical"))
            stack.enter_context(patch.object(libertad2045, "conectar_ib",
                                             return_value=ib_mock))
            stack.enter_context(patch.object(libertad2045, "desconectar_ib"))
            stack.enter_context(patch.object(libertad2045, "registrar_fills_recientes",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "obtener_capital",
                                             return_value=10000.0))
            stack.enter_context(patch.object(libertad2045, "cancelar_ordenes_pendientes"))
            stack.enter_context(patch.object(libertad2045, "reconciliar_stops_gtc",
                                             return_value=0))
            stack.enter_context(patch.object(libertad2045, "evaluar_stops_por_cierre",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalancear",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalance_resumen",
                                             return_value=""))
            # risk_check() OK al principio del ciclo — el veredicto obsoleto
            # que verificar_riesgo_entrada() debe corregir más adelante.
            stack.enter_context(patch.object(libertad2045, "risk_check",
                                             return_value=(True, "OK")))
            stack.enter_context(patch.object(libertad2045, "obtener_posiciones_abiertas",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "filtrar_senales",
                                             side_effect=lambda signals, open_positions: signals))
            stack.enter_context(patch.object(libertad2045, "SP500", list(dfs.keys())))
            stack.enter_context(patch.object(libertad2045, "obtener_datos",
                                             side_effect=lambda ib, symbol: dfs[symbol]))
            stack.enter_context(patch.object(libertad2045, "detectar_senal",
                                             return_value=True))
            stack.enter_context(patch.object(libertad2045, "calcular_posicion",
                                             return_value=(10, 1.0, 1.0)))
            stack.enter_context(patch.object(libertad2045, "verificar_riesgo_entrada",
                                             mock_verificar_riesgo))
            stack.enter_context(patch.object(libertad2045, "ejecutar_trade",
                                             mock_ejecutar_trade))
            stack.enter_context(patch.object(libertad2045, "_escribir_last_run"))
            stack.enter_context(patch.object(libertad2045, "git_backup",
                                             return_value=(True, "ok")))
            mock_dash = stack.enter_context(patch.object(libertad2045, "_dashboard"))
            stack.enter_context(patch.object(libertad2045, "publicar_dashboard",
                                             return_value=(True, "ok")))
            # _PROJECT_DIR aislado: el ciclo completo escribe last_run.txt/
            # last_success.txt como paso final — no debe tocar el repo real.
            tmp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(tmp_dir.cleanup)
            stack.enter_context(patch.object(libertad2045, "_PROJECT_DIR", Path(tmp_dir.name)))
            stack.enter_context(patch("subprocess.run",
                                      return_value=MagicMock(returncode=0, stderr="")))

            libertad2045.main()

        # --- ejecutar_trade: AAA y CCC sí, BBB no ---
        ejecutados = [c.args[1] for c in mock_ejecutar_trade.call_args_list]
        self.assertEqual(
            sorted(ejecutados), ["AAA", "CCC"],
            "AAA y CCC deben ejecutarse (riesgo OK); BBB debe omitirse "
            "(veredicto de riesgo obsoleto detectado en el re-check)",
        )
        self.assertNotIn("BBB", ejecutados)

        # --- verificar_riesgo_entrada llamado una vez por señal candidata ---
        self.assertEqual(mock_verificar_riesgo.call_count, 3)

        # --- Telegram no crítico avisando de la entrada omitida ---
        avisos_bbb = [
            c.args[0] for c in mock_send_telegram.call_args_list
            if "BBB" in c.args[0] and "omitida" in c.args[0]
        ]
        self.assertTrue(
            avisos_bbb,
            "Debe enviarse un aviso Telegram no crítico de la entrada omitida para BBB",
        )

        # --- El dashboard se genera igualmente al final del ciclo ---
        mock_dash.main.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
