import os
import time

from ib_insync import IB

from logger import log_event


# --------------------------------------------------
# Configuración de conexión desde variables de entorno
#
# Puerto por modo:
#   TWS Paper Trading : 7497
#   TWS Live Trading  : 7496
#   IB Gateway Paper  : 4002
#   IB Gateway Live   : 4001
#
# Establecer en el entorno antes de ejecutar:
#   export IBKR_HOST      = 127.0.0.1   (por defecto)
#   export IBKR_PORT      = 7497        (por defecto: Paper)
#   export IBKR_CLIENT_ID = 1           (por defecto)
# --------------------------------------------------

IBKR_HOST      = os.getenv("IBKR_HOST",      "127.0.0.1")
IBKR_PORT      = int(os.getenv("IBKR_PORT",  "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

MAX_RETRIES    = 3       # Número máximo de intentos de conexión
RETRY_DELAY    = 5       # Segundos entre intentos


def conectar_ib():
    """
    Establece conexión con Interactive Brokers con reintentos automáticos.

    Reintenta hasta MAX_RETRIES veces con RETRY_DELAY segundos entre intentos.
    Si todos los intentos fallan, lanza una excepción para que el orquestador
    la capture y detenga el sistema de forma controlada.

    Retorna el objeto IB conectado.
    """

    ib = IB()

    for intento in range(1, MAX_RETRIES + 1):

        try:
            log_event("INFO", f"Conectando a IBKR (intento {intento}/{MAX_RETRIES}) "
                               f"→ {IBKR_HOST}:{IBKR_PORT} clientId={IBKR_CLIENT_ID}")

            ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=10)

            if ib.isConnected():
                log_event("INFO", f"Conexión IBKR establecida en intento {intento}")
                print(f"Conectado a Interactive Brokers ({IBKR_HOST}:{IBKR_PORT})")
                return ib

            else:
                log_event("WARN", f"Intento {intento}: connect() completó sin conexión activa")

        except Exception as e:
            log_event("WARN", f"Intento {intento} fallido: {e}")
            print(f"Intento {intento}/{MAX_RETRIES} fallido: {e}")

        if intento < MAX_RETRIES:
            print(f"Reintentando en {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    # Todos los intentos agotados
    log_event("ERROR", f"No se pudo conectar a IBKR tras {MAX_RETRIES} intentos")
    raise ConnectionError(
        f"IBKR no disponible tras {MAX_RETRIES} intentos "
        f"({IBKR_HOST}:{IBKR_PORT})"
    )


def desconectar_ib(ib):
    """
    Cierra la conexión con Interactive Brokers de forma limpia.
    """

    if ib and ib.isConnected():
        ib.disconnect()
        log_event("INFO", "Conexión IBKR cerrada")
        print("Desconectado de Interactive Brokers")