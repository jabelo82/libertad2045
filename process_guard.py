import errno
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
    Intenta adquirir el lock del proceso usando creación atómica del lockfile
    (O_CREAT | O_EXCL), eliminando la race condition TOCTOU del patrón
    check-then-create. Si el lockfile ya existe, verifica si el PID es válido
    y elimina el lock stale antes de reintentar.
    """
    while True:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            log_event("INFO", f"Lock adquirido (PID: {os.getpid()})")
            return
        except FileExistsError:
            try:
                pid = int(open(LOCK_FILE).read().strip())
                os.kill(pid, 0)   # señal 0: verifica existencia sin enviar señal real
                # Proceso existe → instancia real activa
                log_event("WARN", f"Ejecución bloqueada: instancia activa (PID {pid}, lockfile: {LOCK_FILE})")
                print(f"LIBERTAD_2045 ya está en ejecución (PID {pid}). Abortando.")
                sys.exit(0)
            except ValueError:
                # PID corrupto en el lockfile — tratar como stale y reintentar
                log_event("WARN", f"Lock stale (PID ilegible) — eliminando {LOCK_FILE}")
                print(f"Lock stale (PID ilegible) eliminado: {LOCK_FILE}")
                os.remove(LOCK_FILE)
            except OSError as e:
                if e.errno == errno.ESRCH:
                    # Proceso no existe → lock stale de un ciclo anterior que crashó
                    log_event("WARN", f"Lock stale detectado (PID desaparecido) — eliminando {LOCK_FILE}")
                    print(f"Lock stale eliminado: {LOCK_FILE}")
                    os.remove(LOCK_FILE)
                else:
                    # EPERM u otro error: el proceso existe pero no tenemos permiso para verificarlo.
                    # Postura conservadora: bloquear para no correr en paralelo.
                    log_event("WARN", f"Ejecución bloqueada: no se pudo verificar PID del lockfile ({e})")
                    print("LIBERTAD_2045 ya está en ejecución (no se pudo verificar PID). Abortando.")
                    sys.exit(0)
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