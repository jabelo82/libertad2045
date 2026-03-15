import os
import time

import requests


# --------------------------------------------------
# Credenciales desde variables de entorno
# Nunca hardcodeadas en el código
#
# Establecer en el entorno antes de ejecutar:
#   export TELEGRAM_TOKEN   = <token del bot>
#   export TELEGRAM_CHAT_ID = <chat id>
# --------------------------------------------------

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEOUT     = 10    # Segundos de timeout por intento
MAX_RETRIES = 3     # Reintentos para mensajes críticos
RETRY_DELAY = 3     # Segundos entre reintentos


def send_telegram(message):
    """
    Envía un mensaje por Telegram.

    Fallo silencioso: si el envío no funciona, el sistema
    continúa operando con normalidad. Las notificaciones
    son informativas, no bloqueantes.
    """

    if not TOKEN or not CHAT_ID:
        print("Telegram no configurado — mensaje no enviado")
        return

    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}

    try:
        response = requests.post(url, data=data, timeout=TIMEOUT)

        if response.status_code != 200:
            print(f"Telegram: error en el envío ({response.status_code})")

    except Exception as e:
        print(f"Telegram: excepción al enviar mensaje: {e}")


def send_telegram_critical(message):
    """
    Envía un mensaje crítico por Telegram con reintentos automáticos.

    Usar exclusivamente para alertas de máxima prioridad:
        - Fallos críticos del sistema
        - Activación del drawdown máximo
        - Pérdida de conexión con IBKR

    Reintenta hasta MAX_RETRIES veces antes de rendirse.
    Registra cada intento fallido por consola.
    """

    if not TOKEN or not CHAT_ID:
        print("Telegram no configurado — mensaje crítico no enviado")
        return

    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}

    for intento in range(1, MAX_RETRIES + 1):

        try:
            response = requests.post(url, data=data, timeout=TIMEOUT)

            if response.status_code == 200:
                return  # Enviado con éxito

            print(f"Telegram crítico: intento {intento}/{MAX_RETRIES} "
                  f"fallido ({response.status_code})")

        except Exception as e:
            print(f"Telegram crítico: intento {intento}/{MAX_RETRIES} "
                  f"excepción: {e}")

        if intento < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    print(f"Telegram crítico: no se pudo enviar tras {MAX_RETRIES} intentos")