import os
from datetime import datetime
from pathlib import Path

from conexion_ib import conectar_ib, desconectar_ib
from data_loader import obtener_datos
from signal_engine import detectar_senal
from position_size import calcular_posicion
from portfolio_manager import obtener_posiciones_abiertas, filtrar_senales
from trade_executor import ejecutar_trade
from order_manager import cancelar_ordenes_pendientes
from logger import log_event, limpiar_logs_antiguos
from telegram import send_telegram, send_telegram_critical
from universe_sp500 import SP500
from risk_guardian import risk_check
from process_guard import acquire_lock, release_lock


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
    # Se ejecuta una vez por ciclo, al inicio
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
        # Obtener posiciones abiertas y órdenes pendientes
        # --------------------------------------------------

        open_positions = obtener_posiciones_abiertas(ib)


        # --------------------------------------------------
        # Escaneo del universo
        # --------------------------------------------------

        signals = []

        for symbol in SP500:

            try:

                df = obtener_datos(ib, symbol)

                if df is None:
                    continue

                if len(df) < 200:
                    continue

                if not detectar_senal(df):
                    continue

                last = df.iloc[-1]

                if last.ATR <= 0:
                    continue

                score = (last.close - last.SMA50) / last.ATR

                signals.append({
                    "symbol": symbol,
                    "score":  score,
                    "df":     df
                })

            except Exception as e:
                log_event("ERROR", f"Error escaneando {symbol}: {e}")
                continue

        total_signals = len(signals)

        log_event("INFO", f"Escaneo completado: {total_signals} señales detectadas "
                           f"sobre {len(SP500)} activos")


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

        message = (
            f"⚙️ LIBERTAD_2045\n\n"
            f"Modo                : {MODE}\n"
            f"Capital             : {capital:.2f}\n"
            f"Posiciones abiertas : {len(open_positions)}\n"
            f"Señales detectadas  : {total_signals}\n"
            f"Señales filtradas   : {len(signals)}\n"
            f"Trades ejecutados   : {trades_executed}\n"
            f"Tiempo ejecución    : {runtime}s\n\n"
            f"Estado: OK"
        )

        send_telegram(message)

        log_event("INFO", "SYSTEM_END")


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
