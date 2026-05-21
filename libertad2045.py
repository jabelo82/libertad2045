import os
from datetime import datetime
from pathlib import Path

import pandas as pd

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
        # Conexión con Interactive Brokers
        # --------------------------------------------------

        ib = conectar_ib()

        if not ib.isConnected():
            log_event("ERROR", "IBKR connection failed")
            send_telegram_critical("⚠️ LIBERTAD_2045: conexión IBKR fallida")
            return


        # --------------------------------------------------
        # Risk Guardian
        # --------------------------------------------------

        if not risk_check(ib):
            log_event("WARN", "Risk Guardian bloqueó la ejecución")
            send_telegram_critical("⚠️ LIBERTAD_2045 detenido por Risk Guardian")
            return


        # --------------------------------------------------
        # Leer capital real de la cuenta
        # --------------------------------------------------

        capital = obtener_capital(ib)

        if capital is None:
            log_event("ERROR", "No se pudo obtener el capital de la cuenta")
            send_telegram_critical("⚠️ LIBERTAD_2045: no se pudo leer el capital de IBKR")
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
        # --------------------------------------------------

        posiciones_cerradas = evaluar_stops_por_cierre(ib)

        if posiciones_cerradas:
            log_event("INFO",
                      f"Stops por cierre activados: {len(posiciones_cerradas)} → "
                      f"{posiciones_cerradas}")


        # --------------------------------------------------
        # Rebalanceo de posiciones existentes
        # Ajusta el tamaño de cada posición al óptimo actual antes
        # de escanear nuevas entradas, para que el conteo de slots
        # y el capital disponible sean precisos.
        # --------------------------------------------------

        decisiones_rebalanceo = rebalancear(ib, capital, mode=MODE)


        # --------------------------------------------------
        # Obtener posiciones abiertas tras evaluación de stops y rebalanceo
        # --------------------------------------------------

        open_positions = obtener_posiciones_abiertas(ib)


        # --------------------------------------------------
        # V3 FIX: Verificar datos de posiciones abiertas ANTES del escaneo
        # --------------------------------------------------
        try:
            posiciones_abiertas = [p.contract.symbol for p in ib.positions() if p.position > 0]
            for sym_pos in posiciones_abiertas:
                df_pos = obtener_datos(ib, sym_pos)
                if df_pos is None or len(df_pos) < 20:
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
        # --------------------------------------------------

        trades_executed = 0

        for signal in signals:

            symbol = signal["symbol"]
            df     = signal["df"]

            try:

                shares, stop_distance, atr = calcular_posicion(df, capital)

                if shares <= 0:
                    log_event("INFO", "Posición descartada: shares = 0", symbol=symbol)
                    continue

                ejecutar_trade(
                    ib,
                    symbol,
                    df,
                    shares,
                    stop_distance,
                    mode=MODE
                )

                trades_executed += 1

            except Exception as e:
                log_event("ERROR", f"Error ejecutando trade {symbol}: {e}")
                continue


        # --------------------------------------------------
        # Heartbeat
        # --------------------------------------------------

        Path("last_run.txt").write_text(datetime.now().isoformat())


        # --------------------------------------------------
        # Reporte final
        # --------------------------------------------------

        runtime = (datetime.now() - start_time).seconds

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