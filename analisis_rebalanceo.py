"""
Análisis del impacto del rebalanceo en producción.
Lee todos los CSV de logs y reconstruye:
  1. Eventos de rebalanceo ejecutados (AMPLIAR / REDUCIR)
  2. Outcome de cada posición rebalanceada (stop activado / cerrada por señal / abierta)
  3. Evolución del stop GTC antes y después del rebalanceo
  4. Frecuencia y distribución de ajustes
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"


# ── Helpers ──────────────────────────────────────────────────────────────────

def leer_todos_los_logs():
    filas = []
    for f in sorted(LOG_DIR.glob("LIBERTAD_*.csv")):
        with open(f, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    row["_dt"] = datetime.fromisoformat(row["timestamp"])
                    row["_date"] = row["_dt"].date()
                    filas.append(row)
                except (ValueError, KeyError):
                    continue
    filas.sort(key=lambda r: r["_dt"])
    return filas


def paresear_shares(event_str):
    """Extrae el número de shares de strings como 'BUY 484 acc.'"""
    import re
    m = re.search(r"BUY (\d+) acc|qty=(\d+)|actual=(\d+)", event_str)
    if m:
        return int(next(g for g in m.groups() if g))
    return None


def parsear_stop(event_str):
    """Extrae el precio del stop de 'stop=287.80'"""
    import re
    m = re.search(r"stop=([\d.]+)", event_str)
    return float(m.group(1)) if m else None


def parsear_shares_y_stop(event_str):
    """Para 'Nuevo stop GTC | qty=386 | stop=367.52' devuelve (shares, stop)"""
    import re
    qty = re.search(r"qty=(\d+)", event_str)
    stp = re.search(r"stop=([\d.]+)", event_str)
    return (int(qty.group(1)) if qty else None,
            float(stp.group(1)) if stp else None)


# ── Análisis ─────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  ANÁLISIS DE REBALANCEO — LIBERTAD_2045")
    print(f"  Logs: {sorted(LOG_DIR.glob('LIBERTAD_*.csv'))[0].name} → "
          f"{sorted(LOG_DIR.glob('LIBERTAD_*.csv'))[-1].name}")
    print("="*60)

    filas = leer_todos_los_logs()
    print(f"\nTotal eventos en logs: {len(filas)}")

    # ── 1. Recopilar todos los eventos relevantes ─────────────────────────

    ampliar_encolados   = []   # BUY encolado para apertura (pendiente)
    ampliar_ejecutados  = []   # BUY confirmado como ejecutado/filled
    reducir_eventos     = []   # SELL de reducción
    stops_gtc           = []   # Actualizaciones de stop GTC (post-rebalanceo)
    trades_filled       = []   # TRADE_FILLED (nuevas entradas)
    trades_sold         = []   # TRADE_SOLD / cierres
    eval_ok             = []   # Posiciones dentro del umbral (OK)
    eval_ampliar        = []   # Decisiones AMPLIAR (antes de ejecutar)
    eval_reducir        = []   # Decisiones REDUCIR

    for r in filas:
        ev = r.get("event", "")
        sym = r.get("symbol", "").strip()
        dt  = r["_dt"]

        if "AMPLIAR encolado para apertura" in ev:
            ampliar_encolados.append({"dt": dt, "symbol": sym, "event": ev, "row": r})
        elif "AMPLIAR ejecutado" in ev or ("AMPLIAR" in ev and "filled" in ev.lower() and "NO ejecutado" not in ev):
            ampliar_ejecutados.append({"dt": dt, "symbol": sym, "event": ev, "row": r})
        elif "Nuevo stop GTC" in ev and sym:
            qty, stp = parsear_shares_y_stop(ev)
            stops_gtc.append({"dt": dt, "symbol": sym, "qty": qty, "stop": stp, "event": ev})
        elif r.get("level") == "TRADE_FILLED":
            stops_gtc_entry = parsear_stop(ev)
            trades_filled.append({"dt": dt, "symbol": sym, "event": ev,
                                   "shares": r.get("shares"), "entry": r.get("entry")})
        elif r.get("level") in ("TRADE_SOLD", "STOP_ACTIVADO"):
            trades_sold.append({"dt": dt, "symbol": sym, "event": ev,
                                  "level": r.get("level"), "row": r})
        elif "acción=AMPLIAR" in ev:
            eval_ampliar.append({"dt": dt, "symbol": sym, "event": ev})
        elif "acción=REDUCIR" in ev:
            eval_reducir.append({"dt": dt, "symbol": sym, "event": ev})
        elif "acción=OK" in ev:
            eval_ok.append({"dt": dt, "symbol": sym, "event": ev})

    # ── 2. Resumen global de actividad de rebalanceo ──────────────────────

    print("\n── ACTIVIDAD DE REBALANCEO ──────────────────────────────────")

    # Contar decisiones únicas por ciclo (un ciclo = una fecha)
    ciclos_con_rebalanceo = set()
    total_eval = len(eval_ampliar) + len(eval_reducir) + len(eval_ok)
    for e in eval_ampliar + eval_reducir:
        ciclos_con_rebalanceo.add(e["dt"].date())

    fechas_unicas = sorted({r["_date"] for r in filas})
    dias_habiles  = len(fechas_unicas)

    print(f"  Días de operación en logs    : {dias_habiles}")
    print(f"  Evaluaciones de posición     : {total_eval}")
    print(f"  Decisiones AMPLIAR           : {len(eval_ampliar)}")
    print(f"  Decisiones REDUCIR           : {len(eval_reducir)}")
    print(f"  Posiciones dentro del umbral : {len(eval_ok)}")
    print(f"  Ciclos con algún ajuste      : {len(ciclos_con_rebalanceo)}")
    print(f"  AMPLIARs encolados apertura  : {len(ampliar_encolados)}")
    print(f"  AMPLIARs ejecutados (filled) : {len(ampliar_ejecutados)}")

    if eval_ampliar or eval_reducir:
        total_decisiones = len(eval_ampliar) + len(eval_reducir) + len(eval_ok)
        pct_ajuste = (len(eval_ampliar) + len(eval_reducir)) / total_decisiones * 100
        print(f"  Tasa de ajuste               : {pct_ajuste:.1f}% de evaluaciones")

    # ── 3. Frecuencia por símbolo ─────────────────────────────────────────

    print("\n── SÍMBOLOS MÁS REBALANCEADOS ───────────────────────────────")

    por_simbolo = defaultdict(lambda: {"AMPLIAR": 0, "REDUCIR": 0, "OK": 0, "stops": []})
    for e in eval_ampliar:
        por_simbolo[e["symbol"]]["AMPLIAR"] += 1
    for e in eval_reducir:
        por_simbolo[e["symbol"]]["REDUCIR"] += 1
    for e in eval_ok:
        por_simbolo[e["symbol"]]["OK"] += 1
    for s in stops_gtc:
        if s["stop"]:
            por_simbolo[s["symbol"]]["stops"].append((s["dt"], s["stop"]))

    simbolos_ajustados = {s: d for s, d in por_simbolo.items()
                          if d["AMPLIAR"] + d["REDUCIR"] > 0}
    print(f"  {'Símbolo':<8} {'AMPLIAR':>8} {'REDUCIR':>8} {'OK':>6}  Stops GTC actualizados")
    print(f"  {'-'*7:<8} {'-'*7:>8} {'-'*7:>8} {'-'*5:>6}  {'─'*22}")
    for sym, d in sorted(simbolos_ajustados.items(),
                          key=lambda x: x[1]["AMPLIAR"] + x[1]["REDUCIR"], reverse=True):
        print(f"  {sym:<8} {d['AMPLIAR']:>8} {d['REDUCIR']:>8} {d['OK']:>6}  {len(d['stops'])} actualizaciones stop")

    # ── 4. Evolución de stops GTC por símbolo ────────────────────────────

    print("\n── EVOLUCIÓN DEL STOP GTC (post-rebalanceo) ─────────────────")
    print("  Muestra si los stops suben (trailing correcto) o bajan (bug)")

    for sym in sorted(simbolos_ajustados.keys()):
        stops_sym = sorted(por_simbolo[sym]["stops"], key=lambda x: x[0])
        if len(stops_sym) < 2:
            continue
        precios = [s[1] for s in stops_sym]
        subidas  = sum(1 for a, b in zip(precios, precios[1:]) if b > a)
        bajadas  = sum(1 for a, b in zip(precios, precios[1:]) if b < a)
        iguales  = sum(1 for a, b in zip(precios, precios[1:]) if b == a)
        delta    = precios[-1] - precios[0]
        print(f"  {sym:<6}: {precios[0]:.2f} → {precios[-1]:.2f} "
              f"(Δ{delta:+.2f}) | ↑{subidas} ↓{bajadas} ={iguales} movimientos")

    # ── 5. Outcome de posiciones rebalanceadas ────────────────────────────

    print("\n── OUTCOME DE POSICIONES REBALANCEADAS ──────────────────────")
    print("  Para cada símbolo ajustado: ¿cómo terminó?")

    cierres_por_sym = defaultdict(list)
    for t in trades_sold:
        cierres_por_sym[t["symbol"]].append(t)

    for sym in sorted(simbolos_ajustados.keys()):
        ampliar_sym = [e for e in eval_ampliar if e["symbol"] == sym]
        reducir_sym = [e for e in eval_reducir if e["symbol"] == sym]
        cierres_sym = cierres_por_sym.get(sym, [])

        if not cierres_sym:
            estado = "ABIERTA (sin cierre registrado en logs)"
        else:
            tipos = [c["level"] for c in cierres_sym]
            fechas = [c["dt"].strftime("%Y-%m-%d") for c in cierres_sym]
            estado = f"CERRADA — {', '.join(set(tipos))} ({', '.join(fechas)})"

        print(f"  {sym:<6}: {len(ampliar_sym)}A/{len(reducir_sym)}R → {estado}")

    # ── 6. Ratio cierre por stop vs señal en posiciones rebalanceadas ─────

    print("\n── RATIO STOP vs SEÑAL (posiciones con al menos 1 ajuste) ──")

    por_stop   = sum(1 for t in trades_sold
                     if t["symbol"] in simbolos_ajustados
                     and t["level"] == "STOP_ACTIVADO")
    por_senal  = sum(1 for t in trades_sold
                     if t["symbol"] in simbolos_ajustados
                     and t["level"] == "TRADE_SOLD")
    sin_ajuste_cerradas = [t for t in trades_sold
                            if t["symbol"] not in simbolos_ajustados]

    print(f"  Posiciones rebalanceadas cerradas por stop   : {por_stop}")
    print(f"  Posiciones rebalanceadas cerradas por señal  : {por_senal}")
    print(f"  Posiciones NO rebalanceadas cerradas (total) : {len(sin_ajuste_cerradas)}")

    # ── 7. AMPLIARs que resultaron en cierre por stop ─────────────────────

    print("\n── AMPLIAR → ¿CERRADA POR STOP DESPUÉS? ─────────────────────")
    print("  (indica si ampliamos justo antes de que el precio cayera)")

    for sym in sorted(simbolos_ajustados.keys()):
        ampliar_sym = sorted([e for e in eval_ampliar if e["symbol"] == sym],
                              key=lambda x: x["dt"])
        if not ampliar_sym:
            continue
        stops_post = [t for t in trades_sold
                       if t["symbol"] == sym and t["level"] == "STOP_ACTIVADO"]
        if stops_post:
            ultimo_ampliar = ampliar_sym[-1]["dt"]
            primer_stop    = min(stops_post, key=lambda x: x["dt"])["dt"]
            dias_entre = (primer_stop.date() - ultimo_ampliar.date()).days
            print(f"  {sym:<6}: último AMPLIAR {ultimo_ampliar.strftime('%Y-%m-%d')} → "
                  f"stop activado {primer_stop.strftime('%Y-%m-%d')} ({dias_entre}d después)")
        else:
            print(f"  {sym:<6}: {len(ampliar_sym)} AMPLIAR(s) — sin stop activado registrado")

    # ── 8. Conclusión ─────────────────────────────────────────────────────

    print("\n── CONCLUSIÓN ───────────────────────────────────────────────")

    n_ajustados = len(simbolos_ajustados)
    n_total_sym = len(por_simbolo)
    pct = n_ajustados / n_total_sym * 100 if n_total_sym else 0

    print(f"  {n_ajustados} de {n_total_sym} símbolos evaluados ({pct:.0f}%) recibieron al menos 1 ajuste.")
    print(f"  Tasa de ajuste por evaluación: "
          f"{(len(eval_ampliar)+len(eval_reducir))/(len(eval_ampliar)+len(eval_reducir)+len(eval_ok))*100:.1f}%"
          if (eval_ampliar or eval_reducir or eval_ok) else "")

    print()
    print("  LIMITACIÓN: los logs cubren solo ~2 meses de paper trading")
    print("  con capital inflado (>1M€ hasta 10/06). Los datos reales")
    print("  de LIVE (capital ~8K€) son solo desde 10/06/2026 (9 ciclos).")
    print()


if __name__ == "__main__":
    main()
