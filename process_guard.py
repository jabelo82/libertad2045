import os
import sys

from logger import log_event


# --------------------------------------------------
# Ruta absoluta del lockfile
# Usar /tmp garantiza que cron y ejecución manual
# siempre apuntan al mismo archivo,
# independientemente del directorio de trabajo
# --------------------------------------------------

LOCK_FILE = "/tmp/libertad2045.lock"


def acquire_lock():
    """
    Intenta adquirir el lock del proceso.

    Si el lockfile ya existe, significa que hay una instancia
    activa del sistema. En ese caso se registra el evento y
    se termina el proceso de forma limpia.

    Si no existe, crea el lockfile y permite continuar.
    """

    if os.path.exists(LOCK_FILE):
        log_event("WARN", "Ejecución bloqueada: otra instancia ya está activa "
                           f"(lockfile: {LOCK_FILE})")
        print("LIBERTAD_2045 ya está en ejecución. Abortando.")
        sys.exit(0)

    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))

        log_event("INFO", f"Lock adquirido (PID: {os.getpid()})")

    except Exception as e:
        log_event("ERROR", f"No se pudo crear el lockfile: {e}")
        raise


def release_lock():
    """
    Libera el lock del proceso eliminando el lockfile.

    Llamado siempre desde el bloque finally del orquestador,
    garantizando que el lock se libera aunque el sistema falle.
    """

    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            log_event("INFO", "Lock liberado")

    except Exception as e:
        log_event("ERROR", f"No se pudo eliminar el lockfile: {e}")