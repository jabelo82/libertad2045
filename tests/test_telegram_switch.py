"""
tests/test_telegram_switch.py

Tests unitarios para telegram_switch.py — el interruptor remoto por
Telegram que reinicia IB Gateway.

Este módulo es deliberadamente independiente del bot de trading (no
importa ib_insync, logger.py, config.py, ni ningún módulo del motor), así
que este fichero de test tampoco necesita el bootstrap de stubs pesado de
tests/test_gtc_dedup.py — solo variables de entorno mínimas antes del
import (telegram_switch.py aborta con SystemExit si faltan credenciales).

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_telegram_switch.py -v
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token-no-real")
os.environ.setdefault("TELEGRAM_CHAT_ID", "5206910099")

import telegram_switch as ts

AUTORIZADO    = ts.CHAT_ID
NO_AUTORIZADO = "999999999"


def _make_mensaje(texto: str, chat_id: str = AUTORIZADO) -> dict:
    return {"chat": {"id": int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id},
            "text": texto}


class TestProcesarMensajeSeguridad(unittest.TestCase):
    """Cobertura de los requisitos de seguridad: comando exacto, origen
    autorizado, y que el comando ejecutado nunca se construye a partir de
    texto libre."""

    def setUp(self):
        ts._ultimo_restart_monotonic = 0.0

    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch.object(ts, "_ejecutar_restart")
    def test_ignora_mensaje_sin_comando(self, mock_restart, mock_enviar, mock_log):
        ts._procesar_mensaje(_make_mensaje("hola, ¿cómo va todo?"))
        mock_restart.assert_not_called()
        mock_enviar.assert_not_called()
        mock_log.assert_not_called()  # sin ruido para mensajes irrelevantes

    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch.object(ts, "_ejecutar_restart")
    def test_comando_desde_chat_no_autorizado_no_ejecuta_ni_responde(
            self, mock_restart, mock_enviar, mock_log):
        ts._procesar_mensaje(_make_mensaje(ts.COMANDO, chat_id=NO_AUTORIZADO))
        mock_restart.assert_not_called()
        mock_enviar.assert_not_called()
        mock_log.assert_called_once()
        self.assertIn("NO AUTORIZADO", mock_log.call_args[0][0])
        self.assertIn(NO_AUTORIZADO, mock_log.call_args[0][0])

    @patch("telegram_switch.threading.Thread")
    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch("telegram_switch.subprocess.run")
    def test_comando_valido_ejecuta_exactamente_el_systemctl_hardcodeado(
            self, mock_run, mock_enviar, mock_log, mock_thread):
        ts._procesar_mensaje(_make_mensaje(ts.COMANDO))

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["systemctl", "--user", "restart", "ibgateway.service"])
        self.assertNotIn("shell", kwargs)  # nunca shell=True
        mock_enviar.assert_called_once()
        self.assertIn("Restart de IB Gateway lanzado", mock_enviar.call_args[0][0])
        mock_thread.assert_called_once()  # dispara la verificación diferida

    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch("telegram_switch.subprocess.run")
    def test_texto_con_intento_de_inyeccion_no_coincide_con_el_comando(
            self, mock_run, mock_enviar, mock_log):
        """Un intento de inyección tipo `/restart_gateway; rm -rf /` no es
        una coincidencia EXACTA del comando -- se ignora igual que
        cualquier otro texto, sin ejecutar nada."""
        ts._procesar_mensaje(_make_mensaje("/restart_gateway; rm -rf /"))
        mock_run.assert_not_called()
        mock_enviar.assert_not_called()

    @patch("telegram_switch.threading.Thread")
    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch("telegram_switch.subprocess.run")
    def test_comando_con_sufijo_arroba_bot_se_reconoce(
            self, mock_run, mock_enviar, mock_log, mock_thread):
        ts._procesar_mensaje(_make_mensaje(f"{ts.COMANDO}@MiBotDePrueba"))
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        self.assertEqual(args[0], ["systemctl", "--user", "restart", "ibgateway.service"])


class TestRateLimit(unittest.TestCase):

    def setUp(self):
        ts._ultimo_restart_monotonic = 0.0

    @patch("telegram_switch.threading.Thread")
    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch("telegram_switch.subprocess.run")
    def test_repeticion_dentro_de_60s_no_relanza_restart(
            self, mock_run, mock_enviar, mock_log, mock_thread):
        ts._procesar_mensaje(_make_mensaje(ts.COMANDO))
        mock_run.reset_mock()
        mock_enviar.reset_mock()

        ts._procesar_mensaje(_make_mensaje(ts.COMANDO))  # doble tap inmediato

        mock_run.assert_not_called()
        mock_enviar.assert_called_once()
        self.assertIn("ignorando repetición", mock_enviar.call_args[0][0])

    @patch("telegram_switch.threading.Thread")
    @patch.object(ts, "_log")
    @patch.object(ts, "_enviar_mensaje")
    @patch("telegram_switch.subprocess.run")
    def test_repeticion_tras_ventana_si_relanza_restart(
            self, mock_run, mock_enviar, mock_log, mock_thread):
        # Simula que el último restart aceptado fue hace más de 60s.
        ts._ultimo_restart_monotonic = time.monotonic() - (ts.RATE_LIMIT_SECONDS + 1)

        ts._procesar_mensaje(_make_mensaje(ts.COMANDO))

        mock_run.assert_called_once()


class TestPuerto4001EnListen(unittest.TestCase):

    @patch("telegram_switch.subprocess.run")
    def test_detecta_4001_en_listen(self, mock_run):
        mock_run.return_value = MagicMock(stdout=(
            "State   Recv-Q Send-Q   Local Address:Port    Peer Address:Port\n"
            "LISTEN  0      128          127.0.0.1:4001         0.0.0.0:*\n"
        ))
        self.assertTrue(ts._puerto_4001_en_listen())

    @patch("telegram_switch.subprocess.run")
    def test_sin_4001_devuelve_false(self, mock_run):
        mock_run.return_value = MagicMock(stdout=(
            "State   Recv-Q Send-Q   Local Address:Port    Peer Address:Port\n"
            "LISTEN  0      128          127.0.0.1:4002         0.0.0.0:*\n"
        ))
        self.assertFalse(ts._puerto_4001_en_listen())

    @patch("telegram_switch.subprocess.run")
    def test_no_confunde_puerto_40010_con_4001(self, mock_run):
        """Un puerto que empieza igual (40010) no debe dar falso positivo
        de 4001 -- ver incidentes reales donde el puerto exacto importa
        (4001 LIVE vs 4002 PAPER)."""
        mock_run.return_value = MagicMock(stdout=(
            "State   Recv-Q Send-Q   Local Address:Port    Peer Address:Port\n"
            "LISTEN  0      128          127.0.0.1:40010        0.0.0.0:*\n"
        ))
        self.assertFalse(ts._puerto_4001_en_listen())

    @patch("telegram_switch.subprocess.run")
    def test_error_en_ss_devuelve_false_sin_propagar(self, mock_run):
        mock_run.side_effect = Exception("ss no disponible")
        self.assertFalse(ts._puerto_4001_en_listen())


if __name__ == "__main__":
    unittest.main(verbosity=2)
