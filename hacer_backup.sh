#!/bin/bash
# Backup completo de LIBERTAD_2045 → GitHub + ProBook
# Ejecutar manualmente cuando quieras guardar el estado:  bash ~/PROYECTO_LIBERTAD_2045/hacer_backup.sh

set -euo pipefail
PROYECTO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FECHA=$(date '+%Y-%m-%d %H:%M')
LOG_TAG="backup-libertad"

echo ""
echo "=============================================="
echo "  BACKUP LIBERTAD_2045 — ${FECHA}"
echo "=============================================="
cd "${PROYECTO_DIR}"

# ─── 1. BACKUP GITHUB (código) ────────────────────────────────────────────────
echo ""
echo "▶ [1/2] Backup GitHub (código)..."

# Añadir todos los archivos relevantes (respeta .gitignore)
git add -A

# Ver qué se va a commitear
CAMBIOS=$(git diff --cached --stat 2>/dev/null | tail -1 || echo "sin cambios staged")
echo "  Cambios: ${CAMBIOS}"

if git diff --cached --quiet 2>/dev/null; then
    echo "  ✓ GitHub ya está al día (sin cambios nuevos)"
else
    git commit -m "Backup automático — ${FECHA}

Estado del sistema:
- Kernel: $(uname -r)
- Paper trading activo
$(git diff --cached --stat | head -10)"

    git push origin main
    echo "  ✓ Push a GitHub completado"
fi

# ─── 2. BACKUP PROBOOK (código + datos, via rsync) ────────────────────────────
echo ""
echo "▶ [2/2] Backup ProBook 192.168.1.146 (código + data)..."

PROBOOK_USER="jabelopc"
PROBOOK_HOST="192.168.1.146"
PROBOOK_DEST="~/BACKUP_LIBERTAD2045"

# Excluir lo mismo que .gitignore más los datos grandes si no hay espacio
# Para el ProBook SÍ incluimos data/ (backup completo)
if ssh -o ConnectTimeout=5 -o BatchMode=yes "${PROBOOK_USER}@${PROBOOK_HOST}" "mkdir -p ${PROBOOK_DEST}" 2>/dev/null; then
    rsync -avz --delete \
        --exclude='.git/' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        --exclude='logs/' \
        --exclude='backtest_results/' \
        --exclude='last_run.txt' \
        --exclude='capital_peak.txt' \
        --exclude='portfolio_cache.json' \
        --exclude='*.tmp' \
        "${PROYECTO_DIR}/" \
        "${PROBOOK_USER}@${PROBOOK_HOST}:${PROBOOK_DEST}/"
    echo "  ✓ Rsync a ProBook completado (incluye data/)"
else
    echo "  ⚠ ProBook no accesible (sin SSH o fuera de red) — sólo backup GitHub"
    logger -t "${LOG_TAG}" "ProBook no accesible, backup sólo en GitHub"
fi

echo ""
echo "=============================================="
echo "  BACKUP COMPLETADO — ${FECHA}"
echo "  GitHub: https://github.com/jabelo82/libertad2045"
echo "  ProBook: ${PROBOOK_USER}@${PROBOOK_HOST}:${PROBOOK_DEST}"
echo "=============================================="
logger -t "${LOG_TAG}" "Backup completado: ${FECHA}"
