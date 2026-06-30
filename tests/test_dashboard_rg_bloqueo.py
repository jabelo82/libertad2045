"""
tests/test_dashboard_rg_bloqueo.py

Regresión: verifica que el dashboard se genera incluso cuando el Risk Guardian
bloquea nuevas entradas por apalancamiento > 1,00×.

Bug reproducido el 30/06/2026: el `return` en la rama de bloqueo RG
(libertad2045.py:457) saltaba por encima del bloque de generación del dashboard
(líneas 675-686), dejando dashboard.html del día anterior sin actualizar.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_dashboard_rg_bloqueo.py -v
"""

import os
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap: insertar raíz del proyecto en sys.path
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Stubs mínimos para permitir la importación de libertad2045 sin I/O de red.
# Deben instalarse ANTES del import para que los `from X import Y` de nivel
# de módulo se resuelvan contra los stubs.
# ---------------------------------------------------------------------------

# ib_insync — necesita MarketOrder como nombre en el módulo
_ib_stub = types.ModuleType("ib_insync")
for _name in ("IB", "MarketOrder", "Trade", "OrderStatus", "Stock", "Order"):
    setattr(_ib_stub, _name, MagicMock)
sys.modules["ib_insync"] = _ib_stub

# logger
_logger_stub = types.ModuleType("logger")
_logger_stub.log_event = MagicMock()
_logger_stub.limpiar_logs_antiguos = MagicMock()
sys.modules["logger"] = _logger_stub

# telegram
_telegram_stub = types.ModuleType("telegram")
_telegram_stub.send_telegram = MagicMock()
_telegram_stub.send_telegram_critical = MagicMock()
sys.modules["telegram"] = _telegram_stub

# Módulos internos con los símbolos que libertad2045 importa directamente
_stub_attrs = {
    "conexion_ib":        ["conectar_ib", "desconectar_ib"],
    "data_loader":        ["obtener_datos"],
    "signal_engine":      ["detectar_senal"],
    "position_size":      ["calcular_posicion"],
    "portfolio_manager":  ["obtener_posiciones_abiertas", "filtrar_senales",
                           "evaluar_stops_por_cierre"],
    "trade_executor":     ["ejecutar_trade"],
    "order_manager":      ["cancelar_ordenes_pendientes"],
    "risk_guardian":      ["risk_check"],
    "process_guard":      ["acquire_lock", "release_lock"],
    "rebalance":          ["rebalancear", "resumen_texto", "reconciliar_stops_gtc"],
    "github_publisher":   ["publicar_dashboard"],
}
for _mod_name, _attrs in _stub_attrs.items():
    _m = types.ModuleType(_mod_name)
    for _attr in _attrs:
        setattr(_m, _attr, MagicMock())
    sys.modules[_mod_name] = _m

# universe_sp500 — SP500 vacío para que el escaneo no haga nada
_sp500_stub = types.ModuleType("universe_sp500")
_sp500_stub.SP500 = []
sys.modules["universe_sp500"] = _sp500_stub

# dashboard — módulo real mockeado; libertad2045 lo importa como `_dashboard`
_dash_stub = types.ModuleType("dashboard")
_dash_stub.main = MagicMock()
sys.modules["dashboard"] = _dash_stub

# Asegurar reimportación limpia de libertad2045 en caso de sesión pytest larga
sys.modules.pop("libertad2045", None)
import libertad2045  # noqa: E402

# rebalance lo importa test_gtc_dedup.py directamente (from rebalance import ...).
# Eliminar el stub del sys.modules para que ese test encuentre el módulo real.
# libertad2045 ya tiene los nombres ligados en su namespace — no se rompe nada.
sys.modules.pop("rebalance", None)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_ib_mock() -> MagicMock:
    """IB conectado sin posiciones abiertas."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []
    return ib


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDashboardConRGBloqueado(unittest.TestCase):
    """El dashboard debe generarse aunque el Risk Guardian bloquee entradas."""

    def setUp(self):
        # SIM mode evita las validaciones de puerto LIVE/PAPER
        os.environ.setdefault("TRADING_MODE", "SIM")
        os.environ["IBKR_PORT"] = "4002"

    def tearDown(self):
        os.environ.pop("IBKR_PORT", None)

    def test_dashboard_se_genera_cuando_rg_bloquea_apalancamiento(self):
        """
        Apalancamiento 1,08× > 1,00× → risk_check devuelve False → RG bloquea.
        El dashboard DEBE generarse igualmente como paso terminal del ciclo.

        Regresión 30/06/2026: libertad2045.py:457 hacía `return` antes de
        llegar al bloque del dashboard (líneas 675-686).
        """
        ib_mock = _make_ib_mock()

        with ExitStack() as stack:
            stack.enter_context(patch.object(libertad2045, "acquire_lock"))
            stack.enter_context(patch.object(libertad2045, "release_lock"))
            stack.enter_context(patch.object(libertad2045, "limpiar_logs_antiguos"))
            stack.enter_context(patch.object(libertad2045, "log_event"))
            stack.enter_context(patch.object(libertad2045, "send_telegram"))
            stack.enter_context(patch.object(libertad2045, "send_telegram_critical"))
            stack.enter_context(patch.object(libertad2045, "conectar_ib",
                                             return_value=ib_mock))
            stack.enter_context(patch.object(libertad2045, "desconectar_ib"))
            stack.enter_context(patch.object(libertad2045, "registrar_fills_recientes"))
            stack.enter_context(patch.object(libertad2045, "obtener_capital",
                                             return_value=7762.0))
            stack.enter_context(patch.object(libertad2045, "cancelar_ordenes_pendientes"))
            stack.enter_context(patch.object(libertad2045, "reconciliar_stops_gtc",
                                             return_value=0))
            stack.enter_context(patch.object(libertad2045, "evaluar_stops_por_cierre",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalancear",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalance_resumen",
                                             return_value=""))
            # risk_check devuelve False → simula apalancamiento 1,08× > 1,00×
            stack.enter_context(patch.object(libertad2045, "risk_check",
                                             return_value=False))
            stack.enter_context(patch.object(libertad2045, "_escribir_last_run"))
            mock_dash = stack.enter_context(
                patch.object(libertad2045, "_dashboard")
            )
            stack.enter_context(patch.object(libertad2045, "publicar_dashboard",
                                             return_value=(True, "ok")))
            stack.enter_context(patch("subprocess.run",
                                      return_value=MagicMock(returncode=0,
                                                             stderr="")))

            libertad2045.main()

        mock_dash.main.assert_called_once(), (
            "El dashboard debe generarse aunque el Risk Guardian bloquee entradas"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
