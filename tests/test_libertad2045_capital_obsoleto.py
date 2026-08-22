"""
tests/test_libertad2045_capital_obsoleto.py

Test de integración para la relectura de capital tras stops + rebalanceo
en libertad2045.py — CRÍTICA #3 de la auditoría 07/08/2026
(LIBERTAD2045_Auditoria_20260807): capital para entradas nuevas quedaba
obsoleto tras el rebalanceo.

Caso central pedido por Javier: `capital` se lee UNA VEZ al principio del
ciclo. rebalancear() ejecuta un AMPLIAR real que gasta una porción de ese
capital (aquí simulado con una segunda lectura de obtener_capital() con un
valor menor, sin necesidad de reproducir la orden AMPLIAR completa — esa
lógica ya está cubierta en tests/test_rebalance_ampliar_leverage.py). El
capital_restante que dimensiona las entradas nuevas debe reflejar el
capital YA DESCONTADO (la relectura post-rebalanceo), no el original.

Mismo patrón de stubs pesados que tests/test_libertad2045_reentrada_riesgo.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_libertad2045_capital_obsoleto.py -v
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


def _make_df() -> pd.DataFrame:
    """DataFrame mínimo (200 filas) que satisface el filtro `len(df) < 200`
    del escaneo — el score exacto no importa, solo hay una señal."""
    n = 200
    return pd.DataFrame({
        "close":  [103.0] * n,
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

class TestCapitalReleidoTrasRebalanceo(unittest.TestCase):
    """
    Capital leído al principio del ciclo (10.000€), un AMPLIAR real
    ejecutado en rebalancear() que gasta 3.000€ (simulado con una segunda
    lectura de obtener_capital() que refleja ese gasto: 7.000€) —
    capital_restante para las entradas nuevas debe partir de 7.000€, no
    de los 10.000€ originales.
    """

    def setUp(self):
        os.environ["TRADING_MODE"] = "SIM"
        os.environ["IBKR_PORT"]    = "4002"
        os.environ["MAQUINA"]      = "VPS"  # evita el intento de RTC reprogram

    def tearDown(self):
        os.environ.pop("IBKR_PORT", None)
        os.environ.pop("MAQUINA", None)

    def test_capital_restante_parte_de_relectura_post_rebalanceo(self):
        ib_mock = _make_ib_mock()

        df_unica_senal = _make_df()

        # Primera lectura (inicio de ciclo, línea ~354): 10.000€.
        # Segunda lectura (CRÍTICA #3, tras stops+rebalanceo): 7.000€ —
        # simula el AMPLIAR real de 3.000€ ejecutado dentro de rebalancear().
        mock_obtener_capital = MagicMock(side_effect=[10000.0, 7000.0])

        mock_calcular_posicion = MagicMock(return_value=(10, 1.0, 1.0))

        with ExitStack() as stack:
            stack.enter_context(patch.object(libertad2045, "datetime", _FakeDatetime))
            stack.enter_context(patch.object(libertad2045, "acquire_lock"))
            stack.enter_context(patch.object(libertad2045, "release_lock"))
            stack.enter_context(patch.object(libertad2045, "limpiar_logs_antiguos"))
            stack.enter_context(patch.object(libertad2045, "log_event"))
            stack.enter_context(patch.object(libertad2045, "send_telegram"))
            stack.enter_context(patch.object(libertad2045, "send_telegram_critical"))
            stack.enter_context(patch.object(libertad2045, "conectar_ib",
                                             return_value=ib_mock))
            stack.enter_context(patch.object(libertad2045, "desconectar_ib"))
            stack.enter_context(patch.object(libertad2045, "registrar_fills_recientes",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "obtener_capital",
                                             mock_obtener_capital))
            stack.enter_context(patch.object(libertad2045, "cancelar_ordenes_pendientes"))
            stack.enter_context(patch.object(libertad2045, "reconciliar_stops_gtc",
                                             return_value=0))
            stack.enter_context(patch.object(libertad2045, "evaluar_stops_por_cierre",
                                             return_value=[]))
            # rebalancear(): aquí es donde, en producción, se ejecutaría el
            # AMPLIAR real que gasta caja — no hace falta reproducir la
            # orden, solo que exista una llamada entre las dos lecturas de
            # capital (ya cubierto por el side_effect de mock_obtener_capital).
            stack.enter_context(patch.object(libertad2045, "rebalancear",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalance_resumen",
                                             return_value=""))
            stack.enter_context(patch.object(libertad2045, "risk_check",
                                             return_value=(True, "OK")))
            stack.enter_context(patch.object(libertad2045, "obtener_posiciones_abiertas",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "filtrar_senales",
                                             side_effect=lambda signals, open_positions: signals))
            stack.enter_context(patch.object(libertad2045, "SP500", ["AAA"]))
            stack.enter_context(patch.object(libertad2045, "obtener_datos",
                                             return_value=df_unica_senal))
            stack.enter_context(patch.object(libertad2045, "detectar_senal",
                                             return_value=True))
            stack.enter_context(patch.object(libertad2045, "calcular_posicion",
                                             mock_calcular_posicion))
            stack.enter_context(patch.object(libertad2045, "verificar_riesgo_entrada",
                                             return_value=(True, "OK")))
            stack.enter_context(patch.object(libertad2045, "ejecutar_trade"))
            stack.enter_context(patch.object(libertad2045, "_escribir_last_run"))
            stack.enter_context(patch.object(libertad2045, "git_backup",
                                             return_value=(True, "ok")))
            mock_dash = stack.enter_context(patch.object(libertad2045, "_dashboard"))
            stack.enter_context(patch.object(libertad2045, "publicar_dashboard",
                                             return_value=(True, "ok")))
            tmp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(tmp_dir.cleanup)
            stack.enter_context(patch.object(libertad2045, "_PROJECT_DIR", Path(tmp_dir.name)))
            stack.enter_context(patch("subprocess.run",
                                      return_value=MagicMock(returncode=0, stderr="")))

            libertad2045.main()

        # --- obtener_capital se llamó dos veces: inicio de ciclo y tras rebalanceo ---
        self.assertEqual(mock_obtener_capital.call_count, 2,
                          "Debe releerse el capital tras stops+rebalanceo, "
                          "no reutilizar solo la lectura de inicio de ciclo")

        # --- calcular_posicion (dimensiona la entrada) recibió el capital
        # YA DESCONTADO (7.000€ de la relectura), no el original (10.000€) ---
        self.assertEqual(mock_calcular_posicion.call_count, 1)
        capital_usado = mock_calcular_posicion.call_args.args[1]
        self.assertEqual(
            capital_usado, 7000.0,
            "capital_restante para la entrada nueva debe partir de la "
            "relectura post-rebalanceo (7.000€), no del capital de inicio "
            "de ciclo (10.000€) — CRÍTICA #3",
        )

        mock_dash.main.assert_called_once()

    def test_fallback_a_capital_inicial_si_relectura_falla(self):
        """
        Fail-safe (mismo patrón que CRÍTICA #1/#2): si la relectura de
        capital tras rebalanceo devuelve None (fallo de IBKR puntual), el
        ciclo NO se aborta — sigue con la lectura de inicio de ciclo como
        base de capital_restante, con WARN + Telegram no crítico.
        """
        ib_mock = _make_ib_mock()
        df_unica_senal = _make_df()

        # Primera lectura OK (10.000€); segunda lectura (post-rebalanceo) falla.
        mock_obtener_capital = MagicMock(side_effect=[10000.0, None])
        mock_calcular_posicion = MagicMock(return_value=(10, 1.0, 1.0))

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
                                             mock_obtener_capital))
            stack.enter_context(patch.object(libertad2045, "cancelar_ordenes_pendientes"))
            stack.enter_context(patch.object(libertad2045, "reconciliar_stops_gtc",
                                             return_value=0))
            stack.enter_context(patch.object(libertad2045, "evaluar_stops_por_cierre",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalancear",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "rebalance_resumen",
                                             return_value=""))
            stack.enter_context(patch.object(libertad2045, "risk_check",
                                             return_value=(True, "OK")))
            stack.enter_context(patch.object(libertad2045, "obtener_posiciones_abiertas",
                                             return_value=[]))
            stack.enter_context(patch.object(libertad2045, "filtrar_senales",
                                             side_effect=lambda signals, open_positions: signals))
            stack.enter_context(patch.object(libertad2045, "SP500", ["AAA"]))
            stack.enter_context(patch.object(libertad2045, "obtener_datos",
                                             return_value=df_unica_senal))
            stack.enter_context(patch.object(libertad2045, "detectar_senal",
                                             return_value=True))
            stack.enter_context(patch.object(libertad2045, "calcular_posicion",
                                             mock_calcular_posicion))
            stack.enter_context(patch.object(libertad2045, "verificar_riesgo_entrada",
                                             return_value=(True, "OK")))
            stack.enter_context(patch.object(libertad2045, "ejecutar_trade"))
            stack.enter_context(patch.object(libertad2045, "_escribir_last_run"))
            stack.enter_context(patch.object(libertad2045, "git_backup",
                                             return_value=(True, "ok")))
            mock_dash = stack.enter_context(patch.object(libertad2045, "_dashboard"))
            stack.enter_context(patch.object(libertad2045, "publicar_dashboard",
                                             return_value=(True, "ok")))
            tmp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(tmp_dir.cleanup)
            stack.enter_context(patch.object(libertad2045, "_PROJECT_DIR", Path(tmp_dir.name)))
            stack.enter_context(patch("subprocess.run",
                                      return_value=MagicMock(returncode=0, stderr="")))

            libertad2045.main()

        # --- El ciclo continúa: calcular_posicion se llama con el capital
        # de inicio de ciclo (10.000€) como fallback, no se aborta ---
        self.assertEqual(mock_calcular_posicion.call_count, 1)
        capital_usado = mock_calcular_posicion.call_args.args[1]
        self.assertEqual(
            capital_usado, 10000.0,
            "Si la relectura falla, capital_restante debe caer de vuelta "
            "al capital de inicio de ciclo, sin abortar el resto del ciclo",
        )

        # --- Aviso Telegram NO crítico (send_telegram, no send_telegram_critical) ---
        avisos = [
            c.args[0] for c in mock_send_telegram.call_args_list
            if "releer" in c.args[0].lower() or "capital" in c.args[0].lower()
        ]
        self.assertTrue(avisos, "Debe avisarse por Telegram no crítico del fallo de relectura")

        mock_dash.main.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
