#!/bin/bash

# --------------------------------------------------
# LIBERTAD_2045 — Setup del entorno
#
# Recrea el entorno virtual e instala todas las
# dependencias del proyecto desde cero.
#
# Uso:
#   bash setup.sh
#
# Cuándo usarlo:
#   - Primera instalación del proyecto
#   - El venv está roto o corrupto
#   - Después de actualizar Python
#   - Al mover el proyecto a otra máquina
# --------------------------------------------------

PROJECT_DIR="/home/jabelo/PROYECTO_LIBERTAD_2045"

echo ""
echo "=================================================="
echo "  LIBERTAD_2045 — Setup del entorno"
echo "=================================================="
echo ""

# --------------------------------------------------
# Cambiar al directorio del proyecto
# --------------------------------------------------

cd "$PROJECT_DIR" || {
    echo "ERROR: no se puede acceder a $PROJECT_DIR"
    exit 1
}

# --------------------------------------------------
# Eliminar venv anterior si existe
# --------------------------------------------------

if [ -d "venv" ]; then
    echo "Eliminando entorno virtual anterior..."
    rm -rf venv
fi

# --------------------------------------------------
# Crear nuevo entorno virtual
# --------------------------------------------------

echo "Creando entorno virtual..."

python3 -m venv venv --upgrade-deps || {
    echo ""
    echo "ERROR: no se pudo crear el entorno virtual."
    echo "Ejecuta: sudo apt install python3.12-venv"
    exit 1
}

# --------------------------------------------------
# Activar entorno virtual
# --------------------------------------------------

source venv/bin/activate || {
    echo "ERROR: no se pudo activar el entorno virtual"
    exit 1
}

echo "Entorno virtual activado."

# --------------------------------------------------
# Instalar dependencias
# --------------------------------------------------

echo ""
echo "Instalando dependencias..."
echo ""

pip install --quiet \
    pandas \
    numpy \
    yfinance==0.2.54 \
    requests \
    ib_insync

# --------------------------------------------------
# Verificar instalación
# --------------------------------------------------

echo ""
echo "Verificando instalación..."
echo ""

python - << 'EOF'
errores = []

try:
    import pandas
    print(f"  pandas       {pandas.__version__}  ✓")
except ImportError:
    print("  pandas       ✗ ERROR")
    errores.append("pandas")

try:
    import numpy
    print(f"  numpy        {numpy.__version__}  ✓")
except ImportError:
    print("  numpy        ✗ ERROR")
    errores.append("numpy")

try:
    import yfinance
    print(f"  yfinance     {yfinance.__version__}  ✓")
except ImportError:
    print("  yfinance     ✗ ERROR")
    errores.append("yfinance")

try:
    import requests
    print(f"  requests     {requests.__version__}  ✓")
except ImportError:
    print("  requests     ✗ ERROR")
    errores.append("requests")

try:
    import ib_insync
    print(f"  ib_insync    {ib_insync.__version__}  ✓")
except ImportError:
    print("  ib_insync    ✗ ERROR")
    errores.append("ib_insync")

print()
if errores:
    print(f"  ATENCIÓN: {len(errores)} dependencia(s) no instalada(s): {errores}")
else:
    print("  Todas las dependencias instaladas correctamente.")
EOF

# --------------------------------------------------
# Instrucciones finales
# --------------------------------------------------

echo ""
echo "=================================================="
echo "  Setup completado."
echo ""
echo "  Para activar el entorno:"
echo "  source venv/bin/activate"
echo ""
echo "  Para ejecutar el sistema:"
echo "  source .env && python libertad2045.py"
echo "=================================================="
echo ""