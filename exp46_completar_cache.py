"""
LIBERTAD_2045 — Exp.46 (retomado) — Completar caché del diferencial Russell 1000
==================================================================================

Script de UNA SOLA TANDA para la campaña multi-día acordada con Javier el
22/08/2026: cada vez que haya VPN conectada (o simplemente haya pasado
tiempo desde el último bloqueo), se ejecuta este script UNA VEZ. Descarga
solo lo que falta (nunca vuelve a tocar lo ya cacheado), y se PARA SOLO
si detecta que Yahoo sigue bloqueando — para no repetir el error del
02/08/2026 (comerse horas de bloqueo intentando a ciegas).

Uso:
    source venv/bin/activate
    python exp46_completar_cache.py

Progreso persistente en logs/exp46_completar_cache_progreso.csv
(una fila por intento, todas las tandas — permite ver el historial
completo de la campaña sin depender de la memoria de una sesión).
"""

import csv
import os
import time
from datetime import datetime
from pathlib import Path

from data_manager import DATA_DIR, _ruta_cache, _descargar

START = "2006-01-01"
END   = "2025-12-31"

UNIVERSO_FILE = "russell1000_diferencial_filtrado.txt"
PROGRESO_FILE = "logs/exp46_completar_cache_progreso.csv"

DELAY_ENTRE_DESCARGAS = 3          # segundos, entre descargas exitosas
CIRCUIT_BREAKER_FALLOS = 5         # si N consecutivos fallan por rate limit, PARAR la tanda
MIN_BARRAS = 200                   # umbral usado por el backtest (200 barras mínimo)


def cargar_universo():
    with open(UNIVERSO_FILE) as f:
        return [l.strip() for l in f if l.strip()]


def ya_cacheado(symbol):
    ruta = _ruta_cache(symbol, START, END)
    return ruta.exists() and ruta.stat().st_size > 100


def registrar_progreso(symbol, resultado, detalle=""):
    nuevo = not os.path.exists(PROGRESO_FILE)
    os.makedirs("logs", exist_ok=True)
    with open(PROGRESO_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(["timestamp", "symbol", "resultado", "detalle"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), symbol, resultado, detalle])


def main():
    universo = cargar_universo()
    pendientes = [s for s in universo if not ya_cacheado(s)]

    print(f"Universo Russell 1000 diferencial filtrado : {len(universo)}")
    print(f"Ya cacheados (se saltan)                    : {len(universo) - len(pendientes)}")
    print(f"Pendientes esta tanda                        : {len(pendientes)}")
    print(f"Circuit breaker                              : parar tras {CIRCUIT_BREAKER_FALLOS} fallos consecutivos por rate limit\n")

    if not pendientes:
        print("Nada pendiente — cobertura ya al 100% del universo filtrado.")
        return

    exitos = 0
    fallos_rate_limit = 0
    fallos_otro = 0
    fallos_consecutivos = 0

    for i, symbol in enumerate(pendientes, 1):

        df = _descargar(symbol, START, END)

        if df is not None and not df.empty and len(df) >= MIN_BARRAS:
            ruta = _ruta_cache(symbol, START, END)
            df.to_csv(ruta)
            exitos += 1
            fallos_consecutivos = 0
            print(f"  [{i:3d}/{len(pendientes)}] {symbol:12s} → OK ({len(df)} barras)")
            registrar_progreso(symbol, "OK", f"{len(df)} barras")
            time.sleep(DELAY_ENTRE_DESCARGAS)
            continue

        if df is not None and not df.empty and len(df) < MIN_BARRAS:
            # Datos reales pero historial insuficiente (ticker joven / reciclado)
            # — no es un fallo de descarga, es un motivo permanente. Igualmente
            # se guarda en caché para no re-intentarlo cada tanda.
            ruta = _ruta_cache(symbol, START, END)
            df.to_csv(ruta)
            fallos_otro += 1
            fallos_consecutivos = 0
            print(f"  [{i:3d}/{len(pendientes)}] {symbol:12s} → datos insuficientes ({len(df)} barras, permanente, no reintentar)")
            registrar_progreso(symbol, "INSUFICIENTE", f"{len(df)} barras")
            time.sleep(DELAY_ENTRE_DESCARGAS)
            continue

        # df is None -> _descargar ya agotó sus 3 reintentos internos con backoff
        fallos_rate_limit += 1
        fallos_consecutivos += 1
        print(f"  [{i:3d}/{len(pendientes)}] {symbol:12s} → FALLO (rate limit tras reintentos)")
        registrar_progreso(symbol, "RATE_LIMIT", "")

        if fallos_consecutivos >= CIRCUIT_BREAKER_FALLOS:
            print(f"\n⚠ CIRCUIT BREAKER: {fallos_consecutivos} fallos consecutivos por rate limit.")
            print("  Yahoo sigue bloqueando esta IP/sesión — PARANDO la tanda para no repetir")
            print("  el bloqueo de horas del 02/08/2026. Reintentar más tarde (otro día, u")
            print("  otra IP/salida VPN).")
            break

    restantes = len(pendientes) - exitos - fallos_otro - fallos_rate_limit
    print(f"\n--- Resumen de esta tanda ---")
    print(f"  Éxitos                    : {exitos}")
    print(f"  Insuficientes (permanente): {fallos_otro}")
    print(f"  Rate limit (reintentar)   : {fallos_rate_limit}")
    print(f"  No intentados (parada por circuit breaker o fin de lista): {restantes}")

    total_cacheado_ahora = len(universo) - (len(pendientes) - exitos - fallos_otro)
    print(f"\nCobertura acumulada tras esta tanda: {total_cacheado_ahora}/{len(universo)} "
          f"({total_cacheado_ahora/len(universo)*100:.1f}%)")


if __name__ == "__main__":
    main()
