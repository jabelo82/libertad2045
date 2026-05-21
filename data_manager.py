"""
LIBERTAD_2045 — Data Manager
==============================
Gestiona la caché local de datos históricos de Yahoo Finance.

Descarga los datos una sola vez y los guarda en disco.
Los backtests posteriores los leen directamente sin tocar Yahoo.

Estructura de la caché:
    data/
        AAPL_2015-01-01_2025-12-31.csv
        MSFT_2015-01-01_2025-12-31.csv
        ...

Uso desde backtest:
    from data_manager import obtener_datos_cached

Uso directo para descargar/actualizar toda la caché:
    python data_manager.py
"""

import os
import time
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf


# --------------------------------------------------
# Configuración
# --------------------------------------------------

DATA_DIR       = "data"
DELAY          = 2      # Segundos entre descargas
MAX_REINTENTOS = 3


# --------------------------------------------------
# Funciones principales
# --------------------------------------------------

def _ruta_cache(symbol, start, end):
    """
    Devuelve la ruta del archivo CSV para un símbolo y período.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    symbol_safe = symbol.replace("-", "_").replace(" ", "_")
    return Path(DATA_DIR) / f"{symbol_safe}_{start}_{end}.csv"


def _descargar(symbol, start, end):
    """
    Descarga datos de Yahoo Finance con reintentos.
    Devuelve un DataFrame o None si falla.
    """
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            df = yf.download(symbol, start=start, end=end,
                             progress=False, auto_adjust=True)

            if df.empty:
                return None

            # Aplanar MultiIndex si existe
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.loc[:, ~df.columns.duplicated()]

            return df

        except Exception as e:
            if "RateLimit" in str(e) or "Too Many" in str(e):
                espera = intento * 30
                print(f"    Rate limit — esperando {espera}s...")
                time.sleep(espera)
            else:
                print(f"    ERROR: {e}")
                return None

    return None


def obtener_datos_cached(symbol, start, end, forzar_descarga=False):
    """
    Devuelve datos históricos del símbolo.

    1. Si existe en caché y no se fuerza descarga → lee del disco
    2. Si no existe o se fuerza → descarga de Yahoo y guarda en disco

    Parámetros:
        symbol           : ticker del activo
        start            : fecha inicio (YYYY-MM-DD)
        end              : fecha fin (YYYY-MM-DD)
        forzar_descarga  : si True, ignora la caché y descarga de nuevo

    Retorna DataFrame o None si no hay datos.
    """
    ruta = _ruta_cache(symbol, start, end)

    # Leer desde caché si existe
    if ruta.exists() and not forzar_descarga:
        try:
            df = pd.read_csv(ruta, index_col=0, parse_dates=True)
            if not df.empty:
                return df
        except Exception:
            pass  # Si falla la lectura, descargamos de nuevo

    # Descargar y guardar
    df = _descargar(symbol, start, end)

    if df is not None and not df.empty:
        df.to_csv(ruta)

    return df


def construir_cache(universe, start, end, forzar=False):
    """
    Descarga y guarda en disco todos los activos del universo.

    Usar una vez para poblar la caché completa.
    Los backtests posteriores serán instantáneos.

    Parámetros:
        universe : lista de tickers
        start    : fecha inicio
        end      : fecha fin
        forzar   : si True, re-descarga aunque ya existan
    """
    total     = len(universe)
    nuevos    = 0
    cacheados = 0
    errores   = []

    print(f"\nConstruyendo caché de datos...")
    print(f"Universo : {total} activos")
    print(f"Período  : {start} → {end}")
    print(f"Directorio: {os.path.abspath(DATA_DIR)}\n")

    for i, symbol in enumerate(universe, 1):

        ruta = _ruta_cache(symbol, start, end)

        # Si ya existe en caché y no se fuerza, saltar
        if ruta.exists() and not forzar:
            cacheados += 1
            print(f"  [{i:3d}/{total}] {symbol:12s} → caché ✓")
            continue

        # Descargar
        df = _descargar(symbol, start, end)

        if df is not None and not df.empty:
            df.to_csv(ruta)
            nuevos += 1
            print(f"  [{i:3d}/{total}] {symbol:12s} → {len(df)} barras descargadas")
        else:
            errores.append(symbol)
            print(f"  [{i:3d}/{total}] {symbol:12s} → ERROR")

        time.sleep(DELAY)

    print(f"\nResumen:")
    print(f"  Ya en caché   : {cacheados}")
    print(f"  Descargados   : {nuevos}")
    print(f"  Errores       : {len(errores)}")
    if errores:
        print(f"  Tickers error : {errores}")

    tamaño = sum(f.stat().st_size for f in Path(DATA_DIR).glob("*.csv"))
    print(f"  Tamaño total  : {tamaño / 1024 / 1024:.1f} MB")
    print(f"\nCaché lista. Los próximos backtests serán instantáneos.")


def info_cache(start, end):
    """
    Muestra información sobre la caché actual.
    """
    archivos = list(Path(DATA_DIR).glob(f"*_{start}_{end}.csv"))

    if not archivos:
        print(f"No hay datos en caché para {start} → {end}")
        return

    tamaño = sum(f.stat().st_size for f in archivos)

    print(f"\nCaché actual ({start} → {end}):")
    print(f"  Activos en caché : {len(archivos)}")
    print(f"  Tamaño total     : {tamaño / 1024 / 1024:.1f} MB")
    print(f"  Directorio       : {os.path.abspath(DATA_DIR)}")


def limpiar_cache(start=None, end=None):
    """
    Elimina archivos de caché.
    Si se especifica start/end, elimina solo ese período.
    Si no, elimina toda la caché.
    """
    if start and end:
        archivos = list(Path(DATA_DIR).glob(f"*_{start}_{end}.csv"))
    else:
        archivos = list(Path(DATA_DIR).glob("*.csv"))

    for f in archivos:
        f.unlink()

    print(f"Eliminados {len(archivos)} archivos de caché.")


# --------------------------------------------------
# Ejecución directa: construir caché completa
# --------------------------------------------------

if __name__ == "__main__":

    from backtest_expandido import SP500, START_DATE, END_DATE

    UNIVERSE_COMPLETO = SP500

    print("=" * 50)
    print("  LIBERTAD_2045 — Data Manager")
    print("=" * 50)

    info_cache(START_DATE, END_DATE)

    print(f"\n¿Construir caché para {len(UNIVERSE_COMPLETO)} activos ({START_DATE} → {END_DATE})?")
    respuesta = input("Escribe 'si' para continuar: ").strip().lower()

    if respuesta == "si":
        construir_cache(UNIVERSE_COMPLETO, START_DATE, END_DATE)
    else:
        print("Operación cancelada.")