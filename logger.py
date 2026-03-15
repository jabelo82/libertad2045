import csv
import os
from datetime import datetime, timedelta
from pathlib import Path


# --------------------------------------------------
# Configuración de retención de logs
# Por defecto: 90 días
# --------------------------------------------------

LOG_DIR          = "logs"
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "90"))


def log_event(level, event, symbol="", score="", shares="", entry="", stop=""):
    """
    Registra un evento en el archivo CSV diario de logs.

    Formato del archivo: logs/LIBERTAD_YYYY-MM-DD.csv
    Cada archivo cubre un día de operación.

    Parámetros:
        level  : INFO | WARN | ERROR | CRITICAL | TRADE | SIM
        event  : descripción del evento
        symbol : símbolo del activo (opcional)
        score  : score de la señal (opcional)
        shares : número de acciones (opcional)
        entry  : precio de entrada (opcional)
        stop   : precio de stop-loss (opcional)
    """

    os.makedirs(LOG_DIR, exist_ok=True)

    date      = datetime.now().strftime("%Y-%m-%d")
    file_path = f"{LOG_DIR}/LIBERTAD_{date}.csv"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row     = [timestamp, level, event, symbol, score, shares, entry, stop]
    new_file = not os.path.exists(file_path)

    try:
        with open(file_path, "a", newline="") as f:
            writer = csv.writer(f)

            if new_file:
                writer.writerow([
                    "timestamp", "level", "event",
                    "symbol", "score", "shares", "entry", "stop"
                ])

            writer.writerow(row)

    except Exception as e:
        # El logger no debe romper el sistema bajo ninguna circunstancia
        print(f"[LOGGER ERROR] No se pudo escribir en el log: {e}")


def limpiar_logs_antiguos():
    """
    Elimina archivos de log con más de LOG_RETENTION_DAYS días.

    Llamar una vez por ciclo desde el orquestador, al inicio,
    para mantener el directorio de logs limpio a lo largo del tiempo.
    """

    if not os.path.exists(LOG_DIR):
        return

    limite = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    eliminados = 0

    for archivo in Path(LOG_DIR).glob("LIBERTAD_*.csv"):

        try:
            # Extraer la fecha del nombre del archivo: LIBERTAD_YYYY-MM-DD.csv
            fecha_str = archivo.stem.replace("LIBERTAD_", "")
            fecha     = datetime.strptime(fecha_str, "%Y-%m-%d")

            if fecha < limite:
                archivo.unlink()
                eliminados += 1

        except Exception:
            # Si no se puede parsear o eliminar, ignorar silenciosamente
            continue

    if eliminados > 0:
        log_event("INFO", f"Limpieza de logs: {eliminados} archivos eliminados "
                           f"(retención: {LOG_RETENTION_DAYS} días)")