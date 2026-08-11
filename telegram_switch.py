"""
telegram_switch.py — PROYECTO_LIBERTAD_2045

"Interruptor" remoto por Telegram para reiniciar IB Gateway a distancia,
sin necesidad de ProBook ni SSH (ver incidentes IB Gateway 10-11/08/2026,
sección 12 del contexto — diagnóstico por consola de rescate / VNC).

AISLAMIENTO DELIBERADO: proceso independiente del bot de trading. No
importa nada de libertad2045.py, logger.py, config.py, telegram.py ni de
ningún otro módulo del motor — ni siquiera el logger.py del bot (usa su
propio fichero de log, logs/telegram_switch.log, en texto plano). Si este
proceso se cae o tiene un bug, el bot de trading no se entera y sigue
funcionando exactamente igual. Y al revés: nada de lo que hace este
proceso puede alcanzar el código de trading.

ÚNICO PRIVILEGIO: un comando systemctl concreto y HARDCODEADO —
    systemctl --user restart ibgateway.service
— nunca construido a partir de texto libre del mensaje de Telegram
recibido. No hay ningún otro camino de ejecución en este fichero.

Comando reconocido, solo desde el chat_id autorizado (comparación exacta
contra TELEGRAM_CHAT_ID):
    /restart_gateway

Cualquier otro texto se ignora en silencio (sin responder, sin log —
evita ruido). Un /restart_gateway desde un chat_id distinto se ignora
igual (sin responder) pero SÍ se loguea como intento no autorizado.

Variables de entorno usadas (ya existen en .env — no se añade ninguna
variable nueva):
    TELEGRAM_TOKEN    : token del bot de Telegram
    TELEGRAM_CHAT_ID  : chat_id autorizado (canal 1:1 con Javier)

Ejecutar como servicio systemd de usuario — ver
install/telegram_switch.service (plantilla lista para copiar a
~/.config/systemd/user/ en el VPS).
"""

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

# --------------------------------------------------
# Configuración
# --------------------------------------------------

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise SystemExit(
        "telegram_switch: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID no configurados "
        "en el entorno — abortando arranque (fail-safe: sin credenciales "
        "no se levanta el interruptor)."
    )

API_BASE = f"https://api.telegram.org/bot{TOKEN}"

COMANDO             = "/restart_gateway"
SERVICIO_GATEWAY    = "ibgateway.service"   # hardcodeado — nunca desde el mensaje
RATE_LIMIT_SECONDS  = 60
VERIFICACION_DELAY  = 35                     # segundos antes del 2º mensaje (pedido: 30-40s)
POLL_TIMEOUT        = 30                     # long-polling de getUpdates

_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_FILE    = _PROJECT_DIR / "logs" / "telegram_switch.log"

_ultimo_restart_monotonic = 0.0
_rate_limit_lock = threading.Lock()


# --------------------------------------------------
# Logging propio — fichero de texto plano separado de logger.py (que usa
# CSV para eventos de trading). No comparte formato ni destino con el bot.
# --------------------------------------------------

def _log(evento: str) -> None:
    linea = f"{datetime.now().isoformat(timespec='seconds')} | {evento}"
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception as e:
        print(f"telegram_switch: error escribiendo log: {e}")
    print(linea)


def _enviar_mensaje(texto: str) -> None:
    """Envío mínimo y autocontenido — deliberadamente NO reutiliza
    telegram.py del bot de trading (aislamiento total, ver docstring)."""
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            data={"chat_id": CHAT_ID, "text": texto},
            timeout=10,
        )
    except Exception as e:
        _log(f"ERROR enviando mensaje a Telegram: {e}")


# --------------------------------------------------
# Único punto de ejecución de comandos del sistema. Todo hardcodeado,
# sin shell=True, sin interpolar texto del mensaje recibido.
# --------------------------------------------------

def _ejecutar_restart() -> None:
    subprocess.run(
        ["systemctl", "--user", "restart", SERVICIO_GATEWAY],
        capture_output=True, text=True, timeout=30, check=False,
    )


def _estado_servicio() -> str:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICIO_GATEWAY],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return r.stdout.strip() or "desconocido"
    except Exception as e:
        return f"error ({e})"


def _puerto_4001_en_listen() -> bool:
    """Equivalente a `ss -tln` buscando *:4001 en LISTEN — mismo comando
    que Javier usa a mano para verificar un incidente."""
    try:
        r = subprocess.run(
            ["ss", "-tln"], capture_output=True, text=True, timeout=10, check=False,
        )
        for linea in r.stdout.splitlines():
            partes = linea.split()
            if len(partes) >= 4 and partes[0] == "LISTEN":
                local_addr = partes[3]
                if local_addr.rsplit(":", 1)[-1] == "4001":
                    return True
        return False
    except Exception as e:
        _log(f"ERROR comprobando puerto 4001 con ss -tln: {e}")
        return False


def _verificacion_diferida() -> None:
    time.sleep(VERIFICACION_DELAY)
    estado = _estado_servicio()
    puerto = _puerto_4001_en_listen()
    ok     = estado == "active" and puerto
    icono  = "✅" if ok else "⚠️"
    mensaje = (
        f"{icono} IB Gateway — resultado tras {VERIFICACION_DELAY}s:\n"
        f"  systemctl --user status: {estado}\n"
        f"  puerto 4001 LISTEN: {'sí' if puerto else 'no'}"
    )
    if not ok:
        mensaje += (
            "\nSi sigue sin LISTEN, puede hacer falta aprobar el 2FA en la "
            "app de IBKR o un rescate manual (consola de rescate / VNC)."
        )
    _enviar_mensaje(mensaje)
    _log(f"VERIFICACION | estado={estado} | puerto_4001_listen={puerto} | "
         f"resultado={'OK' if ok else 'REVISAR'}")


# --------------------------------------------------
# Procesamiento de mensajes entrantes
# --------------------------------------------------

def _procesar_mensaje(mensaje: dict) -> None:
    global _ultimo_restart_monotonic

    chat_id_msg = str(mensaje.get("chat", {}).get("id", ""))
    texto = (mensaje.get("text") or "").strip()

    # Tolerar el sufijo @NombreDelBot que Telegram añade en algunos
    # contextos (grupos) — la comparación sigue siendo EXACTA contra el
    # comando completo, no una búsqueda de subcadena.
    comando_base = texto.split("@")[0] if texto else ""
    if comando_base != COMANDO:
        return  # cualquier otro mensaje: ignorado en silencio, sin log (evita ruido)

    if chat_id_msg != CHAT_ID:
        _log(f"INTENTO NO AUTORIZADO | chat_id={chat_id_msg} | texto={texto!r}")
        return  # se ignora, no se responde

    with _rate_limit_lock:
        ahora = time.monotonic()
        transcurrido = ahora - _ultimo_restart_monotonic
        if transcurrido < RATE_LIMIT_SECONDS:
            _log(f"RATE_LIMIT | comando repetido a los {transcurrido:.1f}s "
                 f"(umbral {RATE_LIMIT_SECONDS}s) — ignorado, sin restart")
            _enviar_mensaje(
                f"⏳ Ya se procesó /restart_gateway hace {transcurrido:.0f}s "
                f"— ignorando repetición (umbral {RATE_LIMIT_SECONDS}s)."
            )
            return
        _ultimo_restart_monotonic = ahora

    _log(f"RESTART ACEPTADO | chat_id={chat_id_msg}")
    _ejecutar_restart()
    _enviar_mensaje(
        "🔄 Restart de IB Gateway lanzado (systemctl --user restart "
        "ibgateway.service). Puede hacer falta aprobar el 2FA en la app "
        "de IBKR si aparece. Aviso con el resultado en unos segundos…"
    )
    threading.Thread(target=_verificacion_diferida, daemon=True).start()


# --------------------------------------------------
# Bucle principal — long-polling de getUpdates, sin webhook ni certificado
# --------------------------------------------------

def _long_polling_loop() -> None:
    offset = None
    _log("telegram_switch: arrancado, escuchando /restart_gateway por long-polling")
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(
                f"{API_BASE}/getUpdates", params=params, timeout=POLL_TIMEOUT + 10
            )
            resp.raise_for_status()
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                mensaje = update.get("message")
                if mensaje:
                    _procesar_mensaje(mensaje)
        except Exception as e:
            _log(f"ERROR en el bucle de long-polling: {e} — reintentando en 5s")
            time.sleep(5)


if __name__ == "__main__":
    _long_polling_loop()
