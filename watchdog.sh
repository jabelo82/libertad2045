#!/bin/bash
PROJECT_DIR="${PROJECT_DIR:-/home/jabelo/PROYECTO_LIBERTAD_2045}"
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"
set -a && source "$PROJECT_DIR/.env" && set +a
source "$VENV_ACTIVATE" || { echo "ERROR: venv no disponible"; exit 1; }
cd "$PROJECT_DIR" || exit 1
timeout 300 python "$PROJECT_DIR/watchdog.py"
