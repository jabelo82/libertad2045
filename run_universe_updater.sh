#!/bin/bash
# run_universe_updater.sh — Actualiza universe_sp500.py semanalmente
# Ejecutado por cron los lunes a las 21:00 (1 h antes del bot)

PROJECT_DIR="/home/jabelo/PROYECTO_LIBERTAD_2045"
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"

set -a && source "$PROJECT_DIR/.env" && set +a

cd "$PROJECT_DIR" || { echo "ERROR: directorio no accesible"; exit 1; }

source "$VENV_ACTIVATE" || { echo "ERROR: venv no disponible"; exit 1; }

python "$PROJECT_DIR/universe_updater.py"
