"""
tests/test_watchdog_relanzar_maquina.py

Tests del fix del hallazgo ALTA #1 (auditoría 07/08/2026): relanzar_bot()
(watchdog.py) no comprobaba MAQUINA=="VPS" antes de aplicar el guard de
"shutdown de mediodía" -- una premisa (apagado físico a las 12:10) que
solo es cierta en la Torre retirada, deshabilitada desde el cutover del
03/07/2026, y falsa en el VPS (nunca se apaga). Sin el guard, en el VPS
el bloqueo se disparaba cada día laborable dejando la cartera sin
relanzamiento LIVE ese día entero.

Investigación previa (ver conversación) confirmó además, con dos fuentes
de infraestructura independientes (INFRAESTRUCTURA_IBG.md,
diagnostico_rtc_29jun.md), que la CONDICIÓN tenía un segundo bug: cubría
12:05-12:59 (55 min) en vez de los 12:05-12:09 (5 min) que ya describía
correctamente el propio comentario original -- el shutdown real de la
Torre dispara EXACTAMENTE a las 12:10, no en cualquier momento de la hora.

Dos fixes en el mismo cambio:
    1. Guard MAQUINA=="VPS" -- mismo patrón que check_rtc() (línea ~227).
    2. Ventana acotada a 5 <= minute < 10 (antes: minute >= 5, sin límite
       superior).

Cubre:
    a) VPS + LIVE + 12:07 (dentro de la ventana vieja y la nueva) -->
       YA NO bloquea -- aísla el fix #1 (guard de máquina) sin mezclar
       con el fix #2 (ventana).
    b) Sin MAQUINA (Torre/desconocida) + LIVE + 12:07 --> SIGUE
       bloqueando, igual que antes del fix -- confirma que el fix #1 no
       cambia nada fuera del VPS.
    c) Sin MAQUINA + LIVE + 12:30 (dentro de la ventana vieja de 55 min,
       fuera de la nueva de 5 min) --> YA NO bloquea, incluso sin tocar
       el guard de máquina -- aísla el fix #2 (ventana) del fix #1.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_watchdog_relanzar_maquina.py -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime as _real_datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# watchdog.py no importa ib_insync/telegram/rebalance a nivel de módulo
# (todo inline, dentro de las funciones que los necesitan) -- un
# `import watchdog` normal es seguro aquí sin stubs adicionales. Los
# puntos que SÍ harían I/O real dentro de relanzar_bot() (Telegram vía
# _send(), subprocess.Popen, time.sleep) se parchean por test.
import watchdog


def _fixed_datetime(fixed: _real_datetime):
    """Subclase de datetime cuyo .now() siempre devuelve `fixed` --
    todo lo demás (strftime, etc.) se hereda intacto de la clase real."""
    class _Fake(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    return _Fake


class TestRelanzarBotGuardMediodia(unittest.TestCase):

    def _preparar_entorno(self, tmp_path):
        """PROJECT_DIR apuntando a un directorio temporal con logs/ ya
        creado -- evita escribir bajo la ruta real del proyecto y evita
        que falte el directorio si el test llega a abrir el log."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    # -- (a) VPS -- ya no bloquea -----------------------------------------

    def test_a_vps_no_bloquea_dentro_de_la_ventana(self):
        """12:07, MAQUINA=VPS, LIVE -- dentro de la ventana vieja Y la
        nueva: si NO bloquea, es exclusivamente por el guard de máquina."""
        with tempfile.TemporaryDirectory() as tmp:
            self._preparar_entorno(Path(tmp))

            fake_proc = MagicMock()
            fake_proc.pid = 12345
            fake_proc.poll.return_value = None  # sigue vivo -- no "crash temprano"

            with patch.dict(os.environ, {"MAQUINA": "VPS", "TRADING_MODE": "LIVE"}), \
                 patch.object(watchdog, "datetime",
                               _fixed_datetime(_real_datetime(2026, 8, 24, 12, 7, 0))), \
                 patch.object(watchdog, "PROJECT_DIR", Path(tmp)), \
                 patch.object(watchdog, "_mercado_usa_abierto", return_value=False), \
                 patch.object(watchdog, "_send") as mock_send, \
                 patch.object(watchdog.subprocess, "Popen", return_value=fake_proc) as mock_popen, \
                 patch.object(watchdog.time, "sleep") as mock_time_sleep:
                ok, msg = watchdog.relanzar_bot()

        self.assertTrue(ok)
        self.assertNotIn("omitido", msg)
        self.assertNotIn("mediodía", msg)
        # Prueba positiva de que de verdad llegó a relanzar, no solo que
        # el mensaje no contiene esas palabras por casualidad.
        mock_popen.assert_called_once()
        mock_time_sleep.assert_called_once_with(30)
        # Nunca se avisa de bloqueo -- porque nunca se bloqueó.
        for llamada in mock_send.call_args_list:
            aviso = llamada.args[0] if llamada.args else ""
            self.assertNotIn("mediodía", aviso)

    # -- (b) Sin MAQUINA -- sigue bloqueando (sin cambios) ----------------

    def test_b_sin_maquina_sigue_bloqueando_dentro_de_la_ventana(self):
        """12:07, sin MAQUINA (Torre/desconocida), LIVE -- mismo
        comportamiento que antes del fix: sigue bloqueando."""
        with patch.dict(os.environ, {"TRADING_MODE": "LIVE"}, clear=False):
            os.environ.pop("MAQUINA", None)
            with patch.object(watchdog, "datetime",
                               _fixed_datetime(_real_datetime(2026, 8, 24, 12, 7, 0))), \
                 patch.object(watchdog, "_mercado_usa_abierto", return_value=False), \
                 patch.object(watchdog, "_send") as mock_send, \
                 patch.object(watchdog.subprocess, "Popen") as mock_popen:
                ok, msg = watchdog.relanzar_bot()

        self.assertFalse(ok)
        self.assertEqual(
            msg, "Relaunch omitido — shutdown de mediodía inminente en modo LIVE")
        mock_popen.assert_not_called()
        mock_send.assert_called_once()
        aviso = mock_send.call_args.args[0]
        self.assertIn("NO relanzado a mediodía", aviso)

    # -- (c) Sin MAQUINA, fuera de la ventana corregida -- ya no bloquea --

    def test_c_sin_maquina_ya_no_bloquea_fuera_de_la_ventana_corregida(self):
        """12:30, sin MAQUINA, LIVE -- dentro de la ventana VIEJA (55 min,
        12:05-12:59) pero fuera de la NUEVA (12:05-12:09): si YA NO
        bloquea pese a no tener MAQUINA=VPS, es exclusivamente por el
        fix de la ventana (punto 2), no por el guard de máquina."""
        with tempfile.TemporaryDirectory() as tmp:
            self._preparar_entorno(Path(tmp))

            fake_proc = MagicMock()
            fake_proc.pid = 54321
            fake_proc.poll.return_value = None

            with patch.dict(os.environ, {"TRADING_MODE": "LIVE"}, clear=False):
                os.environ.pop("MAQUINA", None)
                with patch.object(watchdog, "datetime",
                                   _fixed_datetime(_real_datetime(2026, 8, 24, 12, 30, 0))), \
                     patch.object(watchdog, "PROJECT_DIR", Path(tmp)), \
                     patch.object(watchdog, "_mercado_usa_abierto", return_value=False), \
                     patch.object(watchdog, "_send") as mock_send, \
                     patch.object(watchdog.subprocess, "Popen", return_value=fake_proc) as mock_popen, \
                     patch.object(watchdog.time, "sleep") as mock_time_sleep:
                    ok, msg = watchdog.relanzar_bot()

        self.assertTrue(ok)
        self.assertNotIn("omitido", msg)
        mock_popen.assert_called_once()
        mock_time_sleep.assert_called_once_with(30)
        for llamada in mock_send.call_args_list:
            aviso = llamada.args[0] if llamada.args else ""
            self.assertNotIn("mediodía", aviso)


if __name__ == "__main__":
    unittest.main()
