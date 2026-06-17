import os
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

from ib_insync import MarketOrder
from conexion_ib import conectar_ib, desconectar_ib
from data_loader import obtener_datos
from signal_engine import detectar_senal
from position_size import calcular_posicion
from portfolio_manager import obtener_posiciones_abiertas, filtrar_senales, evaluar_stops_por_cierre
from trade_executor import ejecutar_trade
from order_manager import cancelar_ordenes_pendientes
from logger import log_event, limpiar_logs_antiguos
from telegram import send_telegram, send_telegram_critical
from universe_sp500 import SP500
from risk_guardian import risk_check
from process_guard import acquire_lock, release_lock
from rebalance import rebalancear, resumen_texto as rebalance_resumen
import dashboard as _dashboard
from github_publisher import publicar_dashboard


# --------------------------------------------------
# Modo de operación: SIM | PAPER | LIVE
# Nunca hardcodeado — siempre desde variable de entorno
# Valor por defecto: SIM (el más seguro)
# --------------------------------------------------

MODE = os.getenv("TRADING_MODE", "SIM")


_PROJECT_DIR   = Path(__file__).resolve().parent
FILLS_IDS_FILE = _PROJECT_DIR / "logged_exec_ids.txt"
_LAST_RUN_FILE = _PROJECT_DIR / "last_run.txt"


def _escribir_last_run():
    try:
        _LAST_RUN_FILE.write_text(datetime.now().isoformat())
    except Exception as e:
        log_event("WARN", f"No se pudo escribir last_run.txt: {e}")


def _cargar_datos_posiciones(ib, symbols: list) -> dict:
    """
    Descarga DataFrames de IBKR para los símbolos con posición abierta.
    Una sola descarga por símbolo — compartida entre stops, rebalanceo y trailing.
    """
    from data_loader import obtener_datos
    datos = {}
    for symbol in symbols:
        try:
            df = obtener_datos(ib, symbol)
            if df is not None and len(df) >= 20:
                datos[symbol] = df
            else:
                log_event("WARN", f"_cargar_datos_posiciones: datos insuficientes para {symbol}",
                          symbol=symbol)
        except Exception as e:
            log_event("ERROR", f"_cargar_datos_posiciones: error en {symbol}: {e}",
                      symbol=symbol)
    return datos


def registrar_fills_recientes(ib):
    """
    Detecta fills de BUY STOP (BOT) y SELL (SLD) ocurridos desde el ciclo anterior.

    BOT → TRADE_FILLED con precio real de ejecución.
    SLD → TRADE_SOLD con precio real de ejecución.

    Consolida fills parciales del mismo símbolo en un único registro usando
    precio promedio ponderado. Deduplicación por execId.
    """
    try:
        ib.reqExecutions()
        ib.sleep(1)

        logged_ids = set()
        if FILLS_IDS_FILE.exists():
            logged_ids = set(FILLS_IDS_FILE.read_text().strip().splitlines())

        # Agrupar fills por símbolo y lado — consolida parciales
        fills_bot = {}
        fills_sld = {}
        nuevos_ids = []

        for fill in ib.fills():
            exec_id = fill.execution.execId
            if exec_id in logged_ids:
                continue
            side = fill.execution.side
            if side not in ("BOT", "SLD"):
                continue

            symbol = fill.contract.symbol
            precio = fill.execution.price
            qty    = int(fill.execution.shares)

            bucket = fills_bot if side == "BOT" else fills_sld
            if symbol not in bucket:
                bucket[symbol] = {
                    "total_qty":    0,
                    "precio_sum":   0.0,
                    "exec_ids":     [],
                    "contract":     fill.contract,
                }
            bucket[symbol]["total_qty"]  += qty
            bucket[symbol]["precio_sum"] += precio * qty
            bucket[symbol]["exec_ids"].append(exec_id)

        # Buscar stops GTC activos para recuperar el precio de stop (solo BOT)
        ib.reqAllOpenOrders()
        ib.sleep(1)
        stops_gtc = {}
        for trade in ib.trades():
            if (trade.order.orderType in ("STP", "TRAIL")
                    and trade.order.action == "SELL"
                    and trade.order.tif == "GTC"):
                stops_gtc[trade.contract.symbol] = getattr(
                    trade.order, "auxPrice", None
                )

        # Registrar fills de compra (BOT) → TRADE_FILLED
        for symbol, datos in fills_bot.items():
            total_qty   = datos["total_qty"]
            precio_prom = round(datos["precio_sum"] / total_qty, 2)
            stop_price  = stops_gtc.get(symbol, "")

            log_event(
                "TRADE_FILLED",
                f"Fill BUY STOP confirmado | precio_real={precio_prom:.2f}"
                + (f" | fills_parciales={len(datos['exec_ids'])}"
                   if len(datos["exec_ids"]) > 1 else ""),
                symbol=symbol,
                shares=total_qty,
                entry=precio_prom,
                stop=stop_price if stop_price else "",
            )
            nuevos_ids.extend(datos["exec_ids"])

        # Registrar fills de venta (SLD) → TRADE_SOLD
        for symbol, datos in fills_sld.items():
            total_qty   = datos["total_qty"]
            precio_prom = round(datos["precio_sum"] / total_qty, 2)

            log_event(
                "TRADE_SOLD",
                f"Fill SELL confirmado | precio_real={precio_prom:.2f}",
                symbol=symbol,
                shares=total_qty,
                entry=precio_prom,
            )
            nuevos_ids.extend(datos["exec_ids"])

        if nuevos_ids:
            todas = list(logged_ids) + nuevos_ids
            FILLS_IDS_FILE.write_text("\n".join(todas[-1000:]))
            log_event("INFO", f"Fills nuevos registrados: "
                               f"{len(fills_bot)} compras, {len(fills_sld)} ventas "
                               f"({len(nuevos_ids)} ejecuciones parciales)")

    except Exception as e:
        log_event("WARN", f"registrar_fills_recientes: {e}")


def git_backup(capital: float | None) -> tuple[bool, str]:
    """Commit y push del estado del proyecto tras el ciclo noche."""
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    capital_str = f"{capital:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if capital else "N/D"
    msg = f"Ciclo noche {fecha} — Capital: {capital_str}"

    def run(args):
        return subprocess.run(
            args, cwd=_PROJECT_DIR, capture_output=True, text=True, timeout=30
        )

    # Add selectivo: solo código fuente. Evita subir accidentalmente
    # archivos de estado en tiempo real o sensibles no cubiertos por .gitignore.
    _archivos = (
        list(_PROJECT_DIR.glob("*.py")) +
        list(_PROJECT_DIR.glob("*.md")) +
        list(_PROJECT_DIR.glob("*.sh")) +
        [_PROJECT_DIR / ".gitignore"]
    )
    _nombres = [str(f.relative_to(_PROJECT_DIR)) for f in _archivos if f.exists()]
    if _nombres:
        run(["git", "add", "--"] + _nombres)

    status = run(["git", "status", "--porcelain"])
    if not status.stdout.strip():
        return True, "Git ya estaba al día (sin cambios)"

    result = run(["git", "commit", "-m", msg])
    if result.returncode != 0:
        return False, f"git commit falló: {result.stderr.strip()}"

    result = run(["git", "push", "origin", "main"])
    if result.returncode != 0:
        return False, f"git push falló: {result.stderr.strip()}"

    return True, msg


def obtener_capital(ib):
    """
    Lee el capital real de la cuenta desde IBKR.
    Devuelve el NetLiquidation como float, o None si no está disponible.
    """

    try:
        account = ib.accountSummary()

        for item in account:
            if item.tag == "NetLiquidation":
                return float(item.value)

    except Exception as e:
        log_event("ERROR", f"No se pudo leer el capital de IBKR: {e}")

    return None


def main():

    acquire_lock()

    start_time = datetime.now()

    # --------------------------------------------------
    # Limpieza de logs antiguos
    # --------------------------------------------------

    limpiar_logs_antiguos()

    log_event("INFO", f"SYSTEM_START | Modo: {MODE}")

    ib = None

    try:

        # --------------------------------------------------
        # Validación coherencia TRADING_MODE / IBKR_PORT
        # LIVE → puerto 4001 (cuenta real). PAPER → puerto 4002 (cuenta paper).
        # Detiene el sistema si la combinación es incoherente — evita operar LIVE
        # con logs PAPER o enviar órdenes reales desde modo PAPER.
        # --------------------------------------------------

        _port = int(os.getenv("IBKR_PORT", "4002"))
        if MODE == "LIVE" and _port != 4001:
            msg = f"CONFIGURACIÓN INVÁLIDA: TRADING_MODE=LIVE pero IBKR_PORT={_port} (esperado 4001)"
            log_event("CRITICAL", msg)
            send_telegram_critical(f"🔴 {msg}")
            return
        if MODE == "PAPER" and _port == 4001:
            msg = f"CONFIGURACIÓN INVÁLIDA: TRADING_MODE=PAPER pero IBKR_PORT=4001 (cuenta LIVE real)"
            log_event("CRITICAL", msg)
            send_telegram_critical(f"🔴 {msg}")
            return

        # --------------------------------------------------
        # Conexión con Interactive Brokers
        # --------------------------------------------------

        try:
            ib = conectar_ib()
        except ConnectionError as e:
            log_event("ERROR", f"Conexión IBKR fallida: {e}")
            send_telegram_critical(f"⚠️ LIBERTAD_2045: conexión IBKR fallida ({e})")
            log_event("ERROR", "C1: IB caído — stops y rebalanceo no ejecutables sin conexión activa")
            _escribir_last_run()
            return

        if not ib.isConnected():
            log_event("ERROR", "Sin conexión IBKR tras connect() — abortando")
            _escribir_last_run()
            return

        registrar_fills_recientes(ib)


        # --------------------------------------------------
        # Leer capital real de la cuenta
        # --------------------------------------------------

        capital = obtener_capital(ib)

        if capital is None:
            log_event("WARN", "Capital no disponible — stops y rebalanceo corren igualmente")
            send_telegram_critical("⚠️ LIBERTAD_2045: no se pudo leer el capital de IBKR")
            try:
                posiciones_cerradas = evaluar_stops_por_cierre(ib, mode=MODE)
            except Exception as e:
                log_event("ERROR", f"C1: stops fallaron sin capital: {e}")
            try:
                rebalancear(ib, 0, mode=MODE)
            except Exception as e:
                log_event("ERROR", f"C1: rebalanceo falló sin capital: {e}")
            _escribir_last_run()
            return

        log_event("INFO", f"Capital disponible: {capital:.2f}")


        # --------------------------------------------------
        # Limpiar órdenes de entrada pendientes
        # --------------------------------------------------

        cancelar_ordenes_pendientes(ib)


        # --------------------------------------------------
        # Evaluar stops por precio de cierre (Palanca 2B — exp. 27)
        # El mercado USA ya ha cerrado cuando ejecutamos a las 22:10 CET.
        # Si el cierre del día está por debajo del stop → cerrar posición.
        # Esto elimina salidas por ruido intradiario y replica el backtest.
        # Se ejecuta ANTES del Risk Guardian para proteger posiciones abiertas
        # incluso en estado de drawdown máximo (alineado con backtest).
        # --------------------------------------------------

        # Descargar datos una sola vez para todas las posiciones abiertas
        # Compartido entre stops, rebalanceo y trailing — fuente única IBKR
        ib.reqPositions()
        ib.sleep(1)
        symbols_abiertos = [p.contract.symbol for p in ib.positions() if p.position > 0]
        datos_cartera = _cargar_datos_posiciones(ib, symbols_abiertos)

        posiciones_cerradas = evaluar_stops_por_cierre(ib, datos=datos_cartera, mode=MODE)

        if posiciones_cerradas:
            log_event("INFO",
                      f"Stops por cierre activados: {len(posiciones_cerradas)} → "
                      f"{posiciones_cerradas}")


        # --------------------------------------------------
        # Detectar y cerrar cortos involuntarios (H-3)
        # Un corto involuntario implica doble SELL previo — ningún otro módulo
        # los cierra automáticamente, y en LIVE implican pérdida potencial ilimitada.
        # --------------------------------------------------

        for pos in ib.positions():
            if pos.position < 0:
                symbol = pos.contract.symbol
                shares = int(abs(pos.position))
                log_event("ERROR",
                          f"CORTO INVOLUNTARIO DETECTADO — cerrando {symbol} {shares} acc",
                          symbol=symbol)
                send_telegram_critical(
                    f"🔴 LIBERTAD_2045 — CORTO INVOLUNTARIO: {symbol} "
                    f"({pos.position} acc). Cerrando automáticamente."
                )
                try:
                    contrato = pos.contract
                    contrato.exchange = "SMART"
                    if ib.qualifyContracts(contrato) and MODE in ("PAPER", "LIVE"):
                        orden_cierre = MarketOrder("BUY", shares)
                        orden_cierre.tif = "DAY"
                        ib.placeOrder(contrato, orden_cierre)
                        ib.sleep(2)
                        log_event("INFO",
                                  f"Orden BUY enviada para cerrar corto de {symbol}",
                                  symbol=symbol)
                except Exception as e:
                    log_event("ERROR", f"Error cerrando corto de {symbol}: {e}",
                              symbol=symbol)


        # --------------------------------------------------
        # Rebalanceo de posiciones existentes
        # Se ejecuta ANTES del Risk Guardian: ajusta posiciones abiertas
        # independientemente del estado de riesgo.
        # --------------------------------------------------

        decisiones_rebalanceo = rebalancear(ib, capital, mode=MODE, datos=datos_cartera)


        # --------------------------------------------------
        # Risk Guardian — gate exclusivo para nuevas entradas
        # Stops y rebalanceo ya ejecutados. Si falla: heartbeat + return.
        # --------------------------------------------------

        if not risk_check(ib):
            log_event("WARN", "Risk Guardian bloqueó nuevas entradas — gestión de posiciones completada")
            send_telegram_critical("⚠️ LIBERTAD_2045 — Risk Guardian: nuevas entradas bloqueadas. Stops y rebalanceo activos.")
            _escribir_last_run()
            log_event("INFO", "last_run.txt actualizado — RG bloqueó entradas pero ciclo completado")
            return


        # --------------------------------------------------
        # Obtener posiciones abiertas tras evaluación de stops y rebalanceo
        # --------------------------------------------------

        open_positions = obtener_posiciones_abiertas(ib)


        # --------------------------------------------------
        # V3 FIX: Verificar datos de posiciones abiertas ANTES del escaneo
        # Usa datos_cartera ya cargado — evita llamadas extra a IBKR
        # --------------------------------------------------
        try:
            for sym_pos in symbols_abiertos:
                if sym_pos not in datos_cartera or len(datos_cartera[sym_pos]) < 20:
                    log_event("WARN",
                              f"FALLO DE DATOS en posicion abierta: {sym_pos}",
                              symbol=sym_pos)
                    try:
                        send_telegram(f"WARNING LIBERTAD_2045 - Sin datos para {sym_pos} (posicion abierta). Stop no evaluado este ciclo.")
                    except Exception:
                        pass
        except Exception as e:
            log_event("WARN", f"V3: no se pudieron verificar posiciones: {e}")

        # Escaneo del universo
        # --------------------------------------------------

        signals       = []
        total_signals = 0
        fallos_datos  = 0

        UMBRAL_FALLOS = 0.30  # Alerta si más del 30% del universo falla

        for symbol in SP500:

            try:

                df = obtener_datos(ib, symbol)

                if df is None:
                    fallos_datos += 1
                    continue

                if len(df) < 200:
                    fallos_datos += 1
                    continue

                if not detectar_senal(df):
                    continue

                last = df.iloc[-1]

                if last.ATR <= 0:
                    continue

                # Mejora 3: score compuesto — bounce sobre SMA50 + pendiente
                # de la SMA200 en los últimos 5 días, ambos normalizados por ATR.
                # Prioriza activos donde la tendencia de largo plazo está acelerando.
                _sma200_5d    = df.iloc[-6]["SMA200"] if len(df) >= 6 else float("nan")
                _sma200_slope = (
                    (last.SMA200 - _sma200_5d) / last.ATR
                    if not pd.isna(_sma200_5d) else 0.0
                )
                score = (last.close - last.SMA50) / last.ATR + _sma200_slope

                signals.append({
                    "symbol": symbol,
                    "score":  score,
                    "df":     df
                })

            except Exception as e:
                fallos_datos += 1
                log_event("ERROR", f"Error escaneando {symbol}: {e}")
                continue

        total_signals  = len(signals)
        pct_fallos     = fallos_datos / len(SP500) if SP500 else 0

        log_event("INFO", f"Escaneo completado: {total_signals} señales detectadas "
                           f"sobre {len(SP500)} activos | fallos datos: {fallos_datos} "
                           f"({pct_fallos:.1%})")

        # --------------------------------------------------
        # Alerta crítica si los fallos de datos superan el umbral
        # Indica un problema con la fuente de datos (IBKR timeout,
        # mantenimiento, desconexión parcial). El sistema continúa
        # pero el operador debe revisar.
        # --------------------------------------------------

        if pct_fallos > UMBRAL_FALLOS:
            log_event("WARN", f"ALERTA DATOS: {pct_fallos:.1%} del universo sin datos "
                               f"({fallos_datos}/{len(SP500)} activos fallaron)")
            send_telegram_critical(
                f"⚠️ LIBERTAD_2045 — ALERTA DE DATOS\n\n"
                f"El {pct_fallos:.1%} del universo no devolvió datos válidos "
                f"({fallos_datos}/{len(SP500)} activos).\n"
                f"Posible problema con la conexión a IBKR o los datos históricos.\n"
                f"Revisar logs para más detalle."
            )


        # --------------------------------------------------
        # Ranking de señales por score descendente
        # --------------------------------------------------

        signals = sorted(signals, key=lambda x: x["score"], reverse=True)


        # --------------------------------------------------
        # Filtrar por portfolio
        # --------------------------------------------------

        signals = filtrar_senales(signals, open_positions)


        # --------------------------------------------------
        # Ejecutar trades
        # capital_restante decrece conforme se comprometen posiciones,
        # evitando que el sizing acumulado supere el capital disponible.
        # --------------------------------------------------

        trades_executed  = 0
        capital_restante = capital

        for signal in signals:

            symbol = signal["symbol"]
            df     = signal["df"]

            try:

                shares, stop_distance, atr = calcular_posicion(df, capital_restante)

                if shares <= 0:
                    log_event("INFO", "Posición descartada: shares = 0", symbol=symbol)
                    continue

                # Coste estimado: precio BUY STOP (máximo del día + buffer) × acciones
                buy_stop_price  = round(df.iloc[-1].high + 0.05, 2)
                coste_estimado  = shares * buy_stop_price

                if coste_estimado > capital_restante:
                    log_event("INFO",
                              f"Capital insuficiente para {symbol} "
                              f"({coste_estimado:.2f} > restante {capital_restante:.2f}) — skip",
                              symbol=symbol)
                    continue

                ejecutar_trade(
                    ib,
                    symbol,
                    df,
                    shares,
                    stop_distance,
                    mode=MODE
                )

                capital_restante -= coste_estimado
                trades_executed  += 1

            except Exception as e:
                log_event("ERROR", f"Error ejecutando trade {symbol}: {e}")
                continue


        # --------------------------------------------------
        # Heartbeat + registro de éxito
        # last_run.txt  : watchdog heartbeat — solo ciclo completo sin bloqueos RG
        # last_success.txt : confirmación explícita de ciclo con escaneo y ejecución
        # --------------------------------------------------

        ts = datetime.now().isoformat()
        (_PROJECT_DIR / "last_run.txt").write_text(ts)
        (_PROJECT_DIR / "last_success.txt").write_text(ts)


        # --------------------------------------------------
        # Reporte final
        # --------------------------------------------------

        runtime = int((datetime.now() - start_time).total_seconds())

        stops_texto = (f"Stops por cierre    : {len(posiciones_cerradas)} "
                       f"({', '.join(posiciones_cerradas) if posiciones_cerradas else 'ninguno'})\n"
                       if posiciones_cerradas else
                       f"Stops por cierre    : 0\n")

        message = (
            f"⚙️ LIBERTAD_2045\n\n"
            f"Modo                : {MODE}\n"
            f"Capital             : {capital:.2f}\n"
            f"Posiciones abiertas : {len(open_positions)}\n"
            f"{stops_texto}"
            f"{rebalance_resumen(decisiones_rebalanceo)}\n"
            f"Señales detectadas  : {total_signals}\n"
            f"Señales filtradas   : {len(signals)}\n"
            f"Trades ejecutados   : {trades_executed}\n"
            f"Fallos de datos     : {fallos_datos}/{len(SP500)} ({pct_fallos:.1%})\n"
            f"Tiempo ejecución    : {runtime}s\n\n"
            f"Estado: OK"
        )

        send_telegram(message)

        log_event("INFO", "SYSTEM_END")


        # --------------------------------------------------
        # Regenerar dashboard HTML
        # Se ejecuta con IB aún conectado para que leer_cartera()
        # pueda obtener posiciones en vivo (clientId=7).
        # El try aísla errores del dashboard del ciclo principal.
        # --------------------------------------------------

        try:
            _dashboard.main()
            log_event("INFO", "Dashboard regenerado")

            # Publicar en GitHub Pages
            ok_gh, msg_gh = publicar_dashboard()
            if ok_gh:
                log_event("INFO", f"GitHub Pages actualizado: {msg_gh}")
            else:
                log_event("WARN", f"GitHub Pages no actualizado: {msg_gh}")
        except Exception as e_dash:
            log_event("WARN", f"Dashboard no regenerado: {e_dash}")

        try:
            ok_git, msg_git = git_backup(capital)
            if ok_git:
                log_event("INFO", f"Git backup: {msg_git}")
            else:
                log_event("WARN", f"Git backup falló: {msg_git}")
        except Exception as e_git:
            log_event("WARN", f"Git backup no completado: {e_git}")

        # --------------------------------------------------
        # Reprogramar RTC para el próximo ciclo
        # Garantiza que el wakeup automático esté configurado
        # incluso si el usuario apaga manualmente antes del
        # timer systemd (trading-night-shutdown.timer, 22:20).
        # --------------------------------------------------
        try:
            result = subprocess.run(
                ["sudo", "/usr/local/bin/trading-boot-rtcwake.sh"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                log_event("INFO", "RTC reprogramado para próximo ciclo")
            else:
                log_event("WARN", f"RTC no reprogramado: {result.stderr.strip()}")
        except Exception as e_rtc:
            log_event("WARN", f"RTC reprogram error: {e_rtc}")


    except Exception as e:

        log_event("CRITICAL", f"SYSTEM FAILURE: {e}")
        send_telegram_critical(f"🔥 LIBERTAD_2045 fallo crítico:\n{e}")


    finally:

        if ib:
            try:
                desconectar_ib(ib)
            except Exception:
                pass

        release_lock()


if __name__ == "__main__":
    main()