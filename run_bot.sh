#!/bin/bash

# --------------------------------------------------
# LIBERTAD_2045 — Script de arranque
#
# Activa el entorno virtual, carga las variables
# de entorno y ejecuta el motor de trading principal.
#
# Ejecutado por cron a las 22:10 diariamente:
#   10 22 * * * /bin/bash /home/jabelopc/Escritorio/PROYECTO_LIBERTAD_2045/run_bot.sh
# --------------------------------------------------

PROJECT_DIR="/home/jabelopc/Escritorio/PROYECTO_LIBERTAD_2045"

# Cargar variables de entorno desde .env
set -a && source "$PROJECT_DIR/.env" && set +a

LOCKFILE="/tmp/libertad2045.lock"
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"

# --------------------------------------------------
# Protección doble contra ejecución simultánea
# (el process_guard.py también lo verifica desde Python)
# --------------------------------------------------

if [ -f "$LOCKFILE" ]; then
    echo "LIBERTAD_2045 ya está ejecutándose. Abortando."
    exit 1
fi

# --------------------------------------------------
# Cambiar al directorio del proyecto
# --------------------------------------------------

cd "$PROJECT_DIR" || {
    echo "ERROR: no se puede acceder a $PROJECT_DIR"
    exit 1
}

# --------------------------------------------------
# Activar entorno virtual
# --------------------------------------------------

source "$VENV_ACTIVATE" || {
    echo "ERROR: no se puede activar el entorno virtual"
    exit 1
}

# --------------------------------------------------
# Ejecutar el motor de trading
# --------------------------------------------------

python "$PROJECT_DIR/libertad2045.py"
