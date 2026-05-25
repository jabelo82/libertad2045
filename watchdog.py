"""
LIBERTAD_2045 — Watchdog de monitorización mejorado
=====================================================
Ejecutado por cron a las 12:00 (L-V):
    0 12 * * 1-5 /bin/bash watchdog.sh

Comprobaciones en orden:
    1. Heartbeat — el bot corrió en las últimas 25h
    2. IB Gateway — conexión activa con IBKR
    3. Órdenes GTC — stops activos y sin cancelaciones inesperadas
    4. RTC wakeup — alarma programada para el próximo ciclo
    5. Relanzar — si el bot no corrió, relanzar automáticamente
"""

import os
import subprocess
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR    = Path(os.getenv("PROJECT_DIR", "/home/jabelo/PROYECTO_LIBERTAD_2045"))
HEARTBEAT_FILE = PROJECT_DIR / "last_run.txt"
VENV_PYTHON    = PROJECT_DIR / "venv" / "bin" / "python"
BOT_SCRIPT     = PROJECT_DIR / "libertad2045.py"
RTC_WAKEALARM  = Path("/sys/class/rtc/rtc0/wakealarm")

IBKR_HOST          = os.getenv("IBKR_HOST",  "127.0.0.1")
IBKR_PORT          = int(os.getenv("IBKR_PORT", "4002"))
WATCHDOG_CLIENT_ID = 8

sys.path.insert(0, str(PROJECT_DIR))


def _send(msg, critico=False):
    try:
        from telegram import send_telegram, send_telegram_critical
        if critico:
            send_telegram_critical(msg)
        else:
            send_telegram(msg)
    except Exception as e:
        print(f"[WARN] Telegram no disponible: {e}")


def _prev_business_day(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def check_heartbeat():
    if not HEARTBEAT_FILE.exists():
        return False, "Archivo heartbeat no encontrado", None

    try:
        last_run = datetime.fromisoformat(HEARTBEAT_FILE.read_text().strip())
    except (ValueError, OSError):
        last_run = datetime.fromtimestamp(HEARTBEAT_FILE.stat().st_mtime)

    now     = datetime.now()
    weekday = now.date().weekday()  # 0=lun … 6=dom

    # Fin de semana: cron no ejecuta, pero por si acaso
    if weekday >= 5:
        horas = int((now - last_run).total_seconds()) // 3600
        return True, f"Fin de semana — sin ciclo (última ejecución hace {horas}h)", 0

    diff    = now - last_run
    horas   = int(diff.total_seconds()) // 3600
    minutos = int(diff.total_seconds() % 3600) // 60

    prev_bday = _prev_business_day(now.date())
    if last_run.date() >= prev_bday:
        return True, f"Última ejecución hace {horas}h {minutos}m", horas
    return (
        False,
        f"Bot sin ejecutar desde {last_run.date()} (esperado desde {prev_bday}) — {horas}h {minutos}m de silencio",
        horas,
    )


def check_ibkr():
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=WATCHDOG_CLIENT_ID, timeout=10)
        if ib.isConnected():
            return True, f"IB Gateway activo ({IBKR_HOST}:{IBKR_PORT})", ib
        return False, "IB Gateway no responde tras connect()", None
    except Exception as e:
        return False, f"IB Gateway inaccesible: {e}", None


def check_ordenes_gtc(ib):
    if ib is None:
        return False, "Sin conexión IBKR — no se puede verificar", {}
    try:
        trades         = ib.trades()
        gtc_activas    = []
        gtc_canceladas = []
        for t in trades:
            if t.order.tif == "GTC":
                info = {
                    "symbol": t.contract.symbol,
                    "accion": t.order.action,
                    "qty":    t.order.totalQuantity,
                    "precio": getattr(t.order, "auxPrice", getattr(t.order, "lmtPrice", "?")),
                    "estado": t.orderStatus.status
                }
                if t.orderStatus.status in ("Cancelled", "Inactive"):
                    gtc_canceladas.append(info)
                elif t.orderStatus.status in ("PreSubmitted", "Submitted"):
                    gtc_activas.append(info)
        detalle = {"gtc_activas": len(gtc_activas), "gtc_canceladas": len(gtc_canceladas), "canceladas": gtc_canceladas}
        if gtc_canceladas:
            simbolos = [o["symbol"] for o in gtc_canceladas]
            return False, f"{len(gtc_canceladas)} orden(es) GTC cancelada(s): {simbolos}", detalle
        return True, f"{len(gtc_activas)} órdenes GTC activas", detalle
    except Exception as e:
        return False, f"Error verificando GTC: {e}", {}


def check_rtc():
    try:
        wakealarm = RTC_WAKEALARM.read_text().strip()
        if not wakealarm or wakealarm == "0":
            resultado = subprocess.run(["sudo", "/usr/local/bin/trading-boot-rtcwake.sh"], capture_output=True, text=True, timeout=15)
            if resultado.returncode == 0:
                wakealarm = RTC_WAKEALARM.read_text().strip()
                wake_dt   = datetime.fromtimestamp(int(wakealarm))
                return True, f"RTC estaba vacío — reprogramado para {wake_dt.strftime('%d/%m %H:%M')}"
            return False, f"RTC vacío y no se pudo reprogramar: {resultado.stderr}"
        wake_dt = datetime.fromtimestamp(int(wakealarm))
        return True, f"RTC programado para {wake_dt.strftime('%d/%m %H:%M')}"
    except Exception as e:
        return False, f"Error verificando RTC: {e}"


def relanzar_bot():
    try:
        env = os.environ.copy()
        env["TRADING_MODE"]      = "PAPER"
        env["FORCE_HOUR_BYPASS"] = "true"
        log_path = PROJECT_DIR / "logs" / f"watchdog_relaunch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(log_path, "w") as log_file:
            proc = subprocess.Popen([str(VENV_PYTHON), str(BOT_SCRIPT)], cwd=str(PROJECT_DIR), env=env, stdout=log_file, stderr=log_file)
        return True, f"Bot relanzado (PID {proc.pid}) — log: {log_path.name}"
    except Exception as e:
        return False, f"Error relanzando el bot: {e}"


def main():
    print(f"\n{'='*55}")
    print(f"  LIBERTAD_2045 — Watchdog  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}\n")

    resultados    = {}
    alertas       = []
    ib            = None
    bot_relanzado = False

    ok, msg, horas_silencio = check_heartbeat()
    resultados["heartbeat"] = (ok, msg)
    print(f"[{'OK' if ok else 'FAIL'}] Heartbeat: {msg}")
    if not ok:
        alertas.append(f"🔴 Heartbeat: {msg}")

    ok_ib, msg_ib, ib = check_ibkr()
    resultados["ibkr"] = (ok_ib, msg_ib)
    print(f"[{'OK' if ok_ib else 'FAIL'}] IBKR: {msg_ib}")
    if not ok_ib:
        alertas.append(f"🔴 IB Gateway: {msg_ib}")

    ok_gtc, msg_gtc, detalle_gtc = check_ordenes_gtc(ib)
    resultados["gtc"] = (ok_gtc, msg_gtc)
    print(f"[{'OK' if ok_gtc else 'FAIL'}] GTC: {msg_gtc}")
    if not ok_gtc and ib is not None:
        alertas.append(f"🔴 Órdenes GTC: {msg_gtc}")
        for o in detalle_gtc.get("canceladas", []):
            alertas.append(f"   ⚠️  {o['symbol']} — {o['accion']} {o['qty']} @ {o['precio']} [{o['estado']}]")

    ok_rtc, msg_rtc = check_rtc()
    resultados["rtc"] = (ok_rtc, msg_rtc)
    print(f"[{'OK' if ok_rtc else 'FAIL'}] RTC: {msg_rtc}")
    if not ok_rtc:
        alertas.append(f"🔴 RTC Wakeup: {msg_rtc}")

    if not resultados["heartbeat"][0]:
        print("\n[ACCIÓN] Bot no ejecutado — relanzando...")
        ok_rl, msg_rl = relanzar_bot()
        bot_relanzado = ok_rl
        print(f"[{'OK' if ok_rl else 'FAIL'}] Relaunch: {msg_rl}")
        if ok_rl:
            alertas.append(f"🔄 Bot relanzado automáticamente: {msg_rl}")
        else:
            alertas.append(f"🔴 Relaunch fallido: {msg_rl}")

    if ib and ib.isConnected():
        try:
            ib.disconnect()
        except Exception:
            pass

    todo_ok   = all(v[0] for v in resultados.values())
    icono_hb  = "✅" if resultados["heartbeat"][0] else "❌"
    icono_ib  = "✅" if resultados["ibkr"][0]      else "❌"
    icono_gtc = "✅" if resultados["gtc"][0]        else "❌"
    icono_rtc = "✅" if resultados["rtc"][0]        else "❌"

    informe = (
        f"🔍 LIBERTAD_2045 — Watchdog 12:00\n\n"
        f"{icono_hb} Heartbeat    : {resultados['heartbeat'][1]}\n"
        f"{icono_ib} IB Gateway   : {resultados['ibkr'][1]}\n"
        f"{icono_gtc} Stops GTC    : {resultados['gtc'][1]}\n"
        f"{icono_rtc} RTC wakeup   : {resultados['rtc'][1]}\n"
    )
    if bot_relanzado:
        informe += f"\n🔄 Bot relanzado automáticamente\n"
    informe += f"\nEstado: {'✅ Todo OK' if todo_ok and not bot_relanzado else '⚠️ Revisar alertas'}"

    print(f"\n{informe}")
    _send(informe)

    if alertas:
        alerta_msg = "⚠️ LIBERTAD_2045 — WATCHDOG ALERTA\n\n" + "\n".join(alertas)
        _send(alerta_msg, critico=True)
        print(f"\n[ALERTA CRÍTICA ENVIADA]")

    print(f"\n{'='*55}\n")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    sys.exit(main())
