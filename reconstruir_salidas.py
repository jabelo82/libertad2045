#!/usr/bin/env python3
"""
reconstruir_salidas.py — Reconstruye precios de salida para trades cerrados
que no tienen TRADE_SOLD en los logs.

Algoritmo:
  1. Lee todos los logs LIBERTAD_*.csv en orden cronológico.
  2. Por símbolo: detecta entrada (Orden enviada), seguimiento (Posiciones
     ocupadas) y salidas reales (TRADE_SOLD, STOP ACTIVADO por cierre).
  3. Fecha de cierre = primera ausencia en "Posiciones ocupadas" tras haber
     estado presente.
  4. Precio de salida por prioridad:
       a) TRADE_SOLD ya en logs      → skip (no tocar)
       b) STOP ACTIVADO por cierre   → usa ese precio (precio real)
       c) yfinance cierre del día    → precio aproximado
  5. Escribe línea TRADE_SOLD en el CSV del día de cierre para los casos b y c.
     No sobreescribe TRADE_SOLD existentes.

Posiciones actualmente abiertas (excluidas): BG, AAPL, DE, CBOE, JCI, INCY
"""

import argparse
import csv
import glob
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Argumentos ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Reconstruye TRADE_SOLD para trades cerrados sin precio de salida.")
parser.add_argument("--dry-run", action="store_true",
                    help="Imprime lo que haría sin escribir nada en los CSVs.")
args = parser.parse_args()
DRY_RUN = args.dry_run

if DRY_RUN:
    print("[reconstruir] *** MODO DRY-RUN — no se escribirá nada ***")
    print()

# ── Configuración ─────────────────────────────────────────────────────────────

PROJECT_DIR         = Path(__file__).resolve().parent
LOG_DIR             = PROJECT_DIR / "logs"
POSICIONES_ABIERTAS = {"BG", "AAPL", "DE", "CBOE", "JCI", "INCY"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def leer_archivos_ordenados():
    return sorted(glob.glob(str(LOG_DIR / "LIBERTAD_*.csv")))


def fecha_de_archivo(ruta):
    m = re.search(r"LIBERTAD_(\d{4}-\d{2}-\d{2})\.csv", ruta)
    return m.group(1) if m else None


def parsear_posiciones(event_str):
    """Extrae set de símbolos de 'Posiciones ocupadas: N → [SYM, ...]'."""
    m = re.search(r"\[([^\]]*)\]", event_str)
    if not m:
        return set()
    return {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip().strip("'\"")}


# ── Paso 1: Leer todos los logs ───────────────────────────────────────────────

entradas          = {}   # sym → {ts, fecha, shares, entry, stop}
trade_sold_logs   = {}   # sym → {ts, precio}  — TRADE_SOLD ya presentes
stop_activado     = {}   # sym → {fecha, precio}
posiciones_por_dia = {}  # fecha YYYY-MM-DD → set de símbolos

archivos = leer_archivos_ordenados()
print(f"[reconstruir] Leyendo {len(archivos)} archivos de log...")

for ruta in archivos:
    fecha_archivo = fecha_de_archivo(ruta)
    try:
        with open(ruta, encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 3:
                    continue
                ts     = row[0].strip()
                level  = row[1].strip()
                event  = row[2].strip()
                symbol = row[3].strip() if len(row) > 3 else ""
                shares = row[5].strip() if len(row) > 5 else ""
                entry  = row[6].strip() if len(row) > 6 else ""
                stop   = row[7].strip() if len(row) > 7 else ""

                # Entradas: primera "Orden enviada" por símbolo
                if level == "TRADE" and "Orden enviada" in event and symbol:
                    if symbol not in entradas:
                        entradas[symbol] = {
                            "ts":     ts,
                            "fecha":  ts[:10],
                            "shares": shares,
                            "entry":  entry,
                            "stop":   stop,
                        }

                # Salidas reales ya registradas: TRADE_SOLD
                if level == "TRADE_SOLD" and symbol:
                    if symbol not in trade_sold_logs:
                        precio = None
                        m = re.search(r"precio_real=([\d.]+)", event)
                        if m:
                            precio = float(m.group(1))
                        elif entry:
                            try:
                                precio = float(entry)
                            except ValueError:
                                pass
                        if precio is not None:
                            trade_sold_logs[symbol] = {"ts": ts, "precio": precio}

                # Salidas reales: STOP ACTIVADO por cierre
                if level == "INFO" and "STOP ACTIVADO por cierre" in event and symbol:
                    if symbol not in stop_activado:
                        m = re.search(r"cierre=([\d.]+)", event)
                        if m:
                            stop_activado[symbol] = {
                                "fecha":  ts[:10],
                                "precio": float(m.group(1)),
                            }

                # Seguimiento de posiciones por día
                if level == "INFO" and "Posiciones ocupadas" in event and fecha_archivo:
                    syms = parsear_posiciones(event)
                    if syms:
                        posiciones_por_dia.setdefault(fecha_archivo, set()).update(syms)

    except Exception as e:
        print(f"[reconstruir] WARN: error leyendo {ruta}: {e}")

# ── Paso 2: Determinar fecha de cierre por símbolo ────────────────────────────

fechas_ordenadas = sorted(posiciones_por_dia.keys())

primera_ausencia = {}  # sym → fecha YYYY-MM-DD de cierre

for sym in entradas:
    if sym in POSICIONES_ABIERTAS:
        continue

    dias_presente = [f for f in fechas_ordenadas if sym in posiciones_por_dia[f]]
    if not dias_presente:
        # BUY STOP enviado pero nunca aparece en Posiciones ocupadas → no ejecutado
        print(f"[reconstruir]   {sym:8s} SKIP  — BUY STOP no ejecutado (nunca en Posiciones ocupadas)")
        continue

    ultima_presencia = dias_presente[-1]
    idx = fechas_ordenadas.index(ultima_presencia)

    for f in fechas_ordenadas[idx + 1:]:
        if sym not in posiciones_por_dia.get(f, set()):
            primera_ausencia[sym] = f
            break
    else:
        # No hay log posterior — usar día siguiente como aproximación
        try:
            dt = datetime.strptime(ultima_presencia, "%Y-%m-%d")
            primera_ausencia[sym] = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            pass

# ── Paso 3: Precio de cierre vía yfinance ─────────────────────────────────────

try:
    import pandas as pd
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False
    print("[reconstruir] WARN: yfinance no disponible — precios aproximados no obtenibles")

# Caché de histórico descargado en batch (una sola petición para todos los símbolos)
_historico_cache: "pd.DataFrame | None" = None


def cargar_historico_batch(simbolos: list, fecha_min: str, fecha_max: str):
    """Descarga histórico de TODOS los símbolos en UNA sola petición yfinance."""
    global _historico_cache
    if not YFINANCE_OK or not simbolos:
        _historico_cache = None
        return
    try:
        dt_fin = (datetime.strptime(fecha_max, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d")
        print(f"[reconstruir] Descargando histórico batch: {len(simbolos)} símbolos "
              f"({fecha_min} → {dt_fin})...")
        df = yf.download(simbolos, start=fecha_min, end=dt_fin,
                         progress=False, auto_adjust=True)
        if df.empty:
            print("[reconstruir] WARN: histórico batch vacío")
            _historico_cache = None
            return
        # Extraer columna Close; con un solo ticker squeeze a Series, con varios es DataFrame
        close = df["Close"]
        if hasattr(close, "squeeze") and len(simbolos) == 1:
            close = close.squeeze().to_frame(name=simbolos[0])
        _historico_cache = close
        print(f"[reconstruir] Histórico cargado: {len(_historico_cache)} sesiones, "
              f"{list(_historico_cache.columns)[:5]}{'...' if len(_historico_cache.columns) > 5 else ''}")
    except Exception as e:
        print(f"[reconstruir] WARN: yfinance batch error: {e}")
        _historico_cache = None


def obtener_precio_cierre(sym: str, fecha_str: str) -> "float | None":
    """Precio de cierre de `sym` en o inmediatamente después de `fecha_str`,
    usando el histórico batch pre-descargado."""
    if _historico_cache is None or sym not in _historico_cache.columns:
        return None
    try:
        ts = pd.Timestamp(fecha_str)
        col = _historico_cache[sym]
        col = col[col.index >= ts].dropna()
        if col.empty:
            return None
        return round(float(col.iloc[0]), 2)
    except Exception as e:
        print(f"[reconstruir] WARN: precio {sym} {fecha_str}: {e}")
        return None


# ── Paso 4: Construir lista de salidas a escribir ─────────────────────────────

a_escribir            = []   # {sym, fecha_cierre, precio, fuente, shares, entry}
n_ya_tienen_real      = 0
n_stop_activado       = 0
n_yfinance            = 0
n_sin_precio          = 0

cerrados = [sym for sym in entradas if sym not in POSICIONES_ABIERTAS]
print(f"[reconstruir] Símbolos cerrados detectados: {len(cerrados)} → {sorted(cerrados)}")

# Descarga batch única para todos los símbolos que necesitarán precio yfinance
necesitan_yfinance = [
    sym for sym in cerrados
    if sym not in trade_sold_logs
    and sym not in stop_activado
    and sym in primera_ausencia
]
if necesitan_yfinance:
    fechas_cierre = [primera_ausencia[s] for s in necesitan_yfinance]
    cargar_historico_batch(
        necesitan_yfinance,
        fecha_min=min(fechas_cierre),
        fecha_max=max(fechas_cierre),
    )

for sym in sorted(cerrados):
    datos = entradas[sym]

    # a) Ya tiene TRADE_SOLD → no tocar
    if sym in trade_sold_logs:
        print(f"[reconstruir]   {sym:8s} SKIP  — TRADE_SOLD ya existe "
              f"(precio={trade_sold_logs[sym]['precio']})")
        n_ya_tienen_real += 1
        continue

    fecha_cierre = primera_ausencia.get(sym)

    # b) STOP ACTIVADO → precio real, escribir TRADE_SOLD si no existe aún
    if sym in stop_activado:
        precio   = stop_activado[sym]["precio"]
        fecha_c  = stop_activado[sym]["fecha"] or fecha_cierre
        if not fecha_c:
            print(f"[reconstruir]   {sym:8s} SKIP  — STOP ACTIVADO sin fecha de cierre")
            n_sin_precio += 1
            continue
        a_escribir.append({
            "sym":    sym, "fecha_cierre": fecha_c,
            "precio": precio, "fuente": "STOP_ACTIVADO",
            "shares": datos["shares"], "entry": datos["entry"],
        })
        print(f"[reconstruir]   {sym:8s} REAL  — STOP_ACTIVADO precio={precio} fecha={fecha_c}")
        n_stop_activado += 1
        continue

    # c) yfinance
    if not fecha_cierre:
        print(f"[reconstruir]   {sym:8s} SKIP  — sin fecha de cierre determinable")
        n_sin_precio += 1
        continue

    precio = obtener_precio_cierre(sym, fecha_cierre)
    if precio is None:
        print(f"[reconstruir]   {sym:8s} SKIP  — yfinance sin datos para {fecha_cierre}")
        n_sin_precio += 1
        continue

    a_escribir.append({
        "sym":    sym, "fecha_cierre": fecha_cierre,
        "precio": precio, "fuente": "yfinance_aprox",
        "shares": datos["shares"], "entry": datos["entry"],
    })
    print(f"[reconstruir]   {sym:8s} APROX — yfinance precio={precio} fecha={fecha_cierre}")
    n_yfinance += 1

# ── Paso 5: Escribir líneas TRADE_SOLD en los CSVs ────────────────────────────

n_escritos = 0

for rec in a_escribir:
    fecha_cierre = rec["fecha_cierre"]
    sym          = rec["sym"]
    csv_path     = LOG_DIR / f"LIBERTAD_{fecha_cierre}.csv"

    # Verificar que no exista ya TRADE_SOLD para este símbolo en ese archivo
    if csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for linea in f:
                partes = linea.split(",")
                if (len(partes) > 3
                        and partes[1].strip() == "TRADE_SOLD"
                        and partes[3].strip() == sym):
                    print(f"[reconstruir]   {sym:8s} SKIP escritura — "
                          f"TRADE_SOLD ya presente en {csv_path.name}")
                    break
            else:
                pass  # no hay duplicado → continuar a escritura

    precio  = rec["precio"]
    shares  = rec["shares"]
    fuente  = rec["fuente"]
    ts_line = f"{fecha_cierre} 23:59:00"
    evento  = f"Fill SELL reconstruido | precio_aprox={precio}"

    linea = f"{ts_line},TRADE_SOLD,{evento},{sym},,{shares},{precio},\n"

    if DRY_RUN:
        print(f"[reconstruir] DRY  {sym:8s} → {csv_path.name}  "
              f"precio={precio}  fuente={fuente}  línea: {linea.rstrip()}")
    else:
        with open(csv_path, "a", encoding="utf-8") as f:
            f.write(linea)
        print(f"[reconstruir] ✓ escrito {sym} → {csv_path.name} "
              f"precio={precio} fuente={fuente}")
    n_escritos += 1

# ── Resumen ───────────────────────────────────────────────────────────────────

print()
modo = "DRY-RUN (nada escrito)" if DRY_RUN else "REAL"
print("=" * 60)
print(f"  Modo                         : {modo}")
print(f"  Trades cerrados detectados   : {len(cerrados)}")
print(f"  Ya tenían TRADE_SOLD en logs : {n_ya_tienen_real}")
print(f"  Precio real (STOP_ACTIVADO)  : {n_stop_activado}")
print(f"  Precio aproximado (yfinance) : {n_yfinance}")
print(f"  Sin precio (skipped)         : {n_sin_precio}")
print(f"  Líneas {'que se escribirían' if DRY_RUN else 'escritas'} en CSVs : {n_escritos}")
print("=" * 60)
