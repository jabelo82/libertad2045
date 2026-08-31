import csv
import os
from datetime import datetime, timedelta
from pathlib import Path


# --------------------------------------------------
# Configuración de retención de logs
# Por defecto: 90 días
# --------------------------------------------------

LOG_DIR          = str(Path(__file__).resolve().parent / "logs")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "90"))

# --------------------------------------------------
# Deduplicación compartida de exec_ids de IBKR (fills)
#
# Un mismo fill puede llegar a más de un punto del código que lo procesa:
#   - registrar_fills_recientes() (libertad2045.py) via ib.fills() genérico,
#     ciclo tras ciclo.
#   - el camino de AMPLIAR con fill inmediato (rebalance.py), que ve el
#     fill en el propio trade.fills nada más colocar la orden MKT.
# ib_insync rellena ib.fills()/ib.trades() en connect() con un backfill de
# reqCompletedOrders()+reqExecutions() SIN filtro de tiempo (ver docstring
# de rebalance.py::_verificar_ejecucion_pendiente) — el mismo fill puede
# reaparecer en un ciclo posterior. Este fichero compartido es la única
# fuente de verdad de "este exec_id ya generó un TRADE_FILLED", para que
# ningún fill real se cuente dos veces en el KPI "Trades ejecutados"
# (hallazgo 31/08/2026).
# --------------------------------------------------

FILLS_IDS_FILE = Path(__file__).resolve().parent / "logged_exec_ids.txt"


def leer_exec_ids_registrados() -> set:
    """Exec_ids de fills que ya generaron un evento TRADE_FILLED, de
    cualquier origen (registrar_fills_recientes o AMPLIAR inmediato)."""
    if FILLS_IDS_FILE.exists():
        return set(FILLS_IDS_FILE.read_text().strip().splitlines())
    return set()


def exec_ids_pendientes_de_registrar(candidatos) -> list:
    """De una lista de exec_ids candidatos, devuelve los que TODAVÍA no
    están marcados como ya contados. Lista vacía == todos ya se contaron
    en otro punto del código — no volver a loguear TRADE_FILLED para ellos."""
    existentes = leer_exec_ids_registrados()
    return [e for e in candidatos if e not in existentes]


def registrar_exec_ids(nuevos_ids, existentes=None) -> None:
    """Marca exec_ids como ya contados, fusionando con lo que ya hubiera en
    el fichero compartido. Cap a los últimos 10000 (mismo criterio que
    siempre usó registrar_fills_recientes() antes de este fichero
    compartido)."""
    if not nuevos_ids:
        return
    if existentes is None:
        existentes = leer_exec_ids_registrados()
    todas = list(existentes) + list(nuevos_ids)
    try:
        FILLS_IDS_FILE.write_text("\n".join(todas[-10000:]))
    except Exception as e:
        log_event("ERROR", f"registrar_exec_ids: fallo persistiendo logged_exec_ids.txt: {e}")


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