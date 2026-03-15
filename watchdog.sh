#!/bin/bash

# --------------------------------------------------
# LIBERTAD_2045 — Watchdog de monitorización
#
# Comprueba que el sistema se ejecutó en las últimas
# 25 horas. Si no, envía alerta por Telegram.
#
# Ejecutado por cron a las 12:00 diariamente:
#   0 12 * * * /bin/bash /home/jabelopc/Escritorio/PROYECTO_LIBERTAD_2045/watchdog.sh
# --------------------------------------------------

PROJECT_DIR="/home/jabelopc/Escritorio/PROYECTO_LIBERTAD_2045"
HEARTBEAT_FILE="$PROJECT_DIR/last_run.txt"
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"

# 25 horas en segundos
MAX_SILENCE=90000

# --------------------------------------------------
# Verificar que el archivo de heartbeat existe
# --------------------------------------------------

if [ ! -f "$HEARTBEAT_FILE" ]; then
    echo "LIBERTAD_2045: heartbeat no encontrado — el sistema nunca se ha ejecutado"

    source "$VENV_ACTIVATE"

    cd "$PROJECT_DIR" || exit 1

    python - <<EOF
from telegram import send_telegram
send_telegram("⚠️ LIBERTAD_2045 — Watchdog: archivo heartbeat no encontrado. El sistema puede no haberse ejecutado nunca.")
EOF

    exit 1
fi

# --------------------------------------------------
# Calcular tiempo desde la última ejecución
# --------------------------------------------------

LAST_RUN=$(stat -c %Y "$HEARTBEAT_FILE")
NOW=$(date +%s)
DIFF=$((NOW - LAST_RUN))

echo "Última ejecución: hace $((DIFF / 3600))h $((DIFF % 3600 / 60))m"

# --------------------------------------------------
# Alertar si el silencio supera el umbral
# --------------------------------------------------

if [ "$DIFF" -gt "$MAX_SILENCE" ]; then

    HORAS=$((DIFF / 3600))

    echo "ALERTA: el sistema no se ha ejecutado en ${HORAS}h"

    source "$VENV_ACTIVATE"

    cd "$PROJECT_DIR" || exit 1

    python - <<EOF
from telegram import send_telegram
send_telegram("⚠️ LIBERTAD_2045 — Watchdog: el sistema no se ha ejecutado en más de ${HORAS} horas. Verificar cron y estado del servidor.")
EOF

else
    echo "Sistema operativo — última ejecución dentro del umbral"
fi