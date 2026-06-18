import os
from datetime import date

from ib_insync import *

from logger import log_event
from position_size import calcular_trailing_stop


# --------------------------------------------------
# Parámetros — sincronizados con experimento 27
# --------------------------------------------------

MAX_POSITIONS           = 10   # Posiciones simultáneas máximas (exp. 10 confirmado)  # Ver también config.py — MAX_POSITIONS
MAX_POSICIONES_ARRANQUE = int(os.getenv("MAX_POSICIONES_ARRANQUE", str(MAX_POSITIONS)))

# Trailing stop dinámico B1 (idéntico al backtest_expandido.py)
ATR_PERIOD     = 14
ATR_MULTIPLIER = 3.1   # fallback si percentil no disponible
B1_MULT_MIN    = 2.2
B1_MULT_MAX    = 4.0
B1_VENTANA     = 252   # ventana rolling para ATR percentil
TRAILING_FACTOR = 0.75  # Aprobado en Experimento 40-ter (stress test 3/3 crisis)  # Ver también config.py — TRAILING_FACTOR


# --------------------------------------------------
# Evaluación de stops por precio de cierre (exp. 27)
# --------------------------------------------------

def evaluar_stops_por_cierre(ib, capital_peak_file="capital_peak.txt", datos=None, mode="PAPER"):
    """
    Implementación de la Palanca 2B — salida por cierre de sesión.

    El sistema ejecuta a las 22:10 CET, cuando el mercado USA ya ha cerrado.
    Esto permite evaluar el precio de CIERRE del día antes de decidir si
    mantener o cerrar cada posición — eliminando salidas por ruido intradiario.

    Lógica:
        Para cada posición abierta:
            1. Leer el precio de cierre del día desde IBKR
            2. Leer el stop loss actual de la orden GTC asociada
            3. Si close <= stop → cancelar orden GTC y cerrar con MKT
            4. Si close > stop  → mantener posición (trailing stop sigue activo)

    Retorna lista de símbolos cerrados en este ciclo.
    """

    cerrados = []

    try:
        positions = ib.positions()

        if not positions:
            log_event("INFO", "Sin posiciones abiertas — evaluación de stops omitida")
            return cerrados
 # Solicitar TODOS los open orders a IBKR, incluyendo los de sesiones
        # anteriores (GTC colocados antes de un reinicio de TWS/Gateway).
        ib.reqAllOpenOrders()
        ib.sleep(1)
        # Mapa symbol → orden GTC de stop loss
        stops_gtc = {}
        for trade in ib.trades():
            if (trade.order.orderType in ("STP", "TRAIL") and
                    trade.order.action == "SELL" and
                    trade.order.tif == "GTC"):
                symbol = trade.contract.symbol
                if symbol in stops_gtc:
                    precio_exist = getattr(stops_gtc[symbol].order, "auxPrice", 0) or 0
                    precio_nuevo = getattr(trade.order, "auxPrice", 0) or 0
                    log_event("CRITICAL",
                              f"STOP GTC DUPLICADO: {symbol} — "
                              f"órdenes {stops_gtc[symbol].order.orderId} ({precio_exist:.2f}) "
                              f"y {trade.order.orderId} ({precio_nuevo:.2f}) — "
                              f"conservando precio mayor",
                              symbol=symbol)
                    try:
                        from telegram import send_telegram_critical
                        send_telegram_critical(
                            f"🔴 LIBERTAD_2045 — Stop GTC duplicado: {symbol} | "
                            f"Órdenes {stops_gtc[symbol].order.orderId} y {trade.order.orderId}. "
                            f"Revisar manualmente."
                        )
                    except Exception:
                        pass
                    if precio_nuevo > precio_exist:
                        stops_gtc[symbol] = trade
                else:
                    stops_gtc[symbol] = trade

        for pos in positions:

            if pos.position == 0:
                continue

            symbol = pos.contract.symbol

            # Posición corta — no contemplada en la estrategia
            # Alerta crítica y saltar sin tocar la posición
            if pos.position < 0:
                log_event("ERROR",
                          f"POSICIÓN CORTA DETECTADA | {symbol} | "
                          f"{pos.position} acc — estrategia solo opera largo. "
                          f"Intervención manual requerida.",
                          symbol=symbol)
                try:
                    from telegram import send_telegram_critical
                    send_telegram_critical(
                        f"🔴 LIBERTAD_2045 — POSICIÓN CORTA: {symbol} "
                        f"({pos.position} acc). El sistema NO cierra cortos. "
                        f"Cerrar manualmente en IBKR inmediatamente."
                    )
                except Exception:
                    pass
                continue  # No tocar — SELL empeoraría el corto

            # Obtener precio de cierre del día
            try:
                bars = ib.reqHistoricalData(
                    pos.contract,
                    endDateTime="",
                    durationStr="2 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    keepUpToDate=False
                )

                if not bars:
                    log_event("WARN", f"Sin datos de cierre para {symbol} — stop no evaluado",
                              symbol=symbol)
                    continue

                bar_date = bars[-1].date
                if hasattr(bar_date, "date"):
                    bar_date = bar_date.date()
                antiguedad = (date.today() - bar_date).days
                if antiguedad > 5:
                    log_event("WARN",
                              f"Datos de {symbol} con {antiguedad}d de antigüedad "
                              f"(última barra: {bar_date}) — stop no evaluado",
                              symbol=symbol)
                    continue

                precio_cierre = bars[-1].close

            except Exception as e:
                log_event("ERROR", f"Error obteniendo cierre de {symbol}: {e}", symbol=symbol)
                continue

            # Obtener nivel de stop actual
            stop_level = None

            if symbol in stops_gtc:
                trade_stop = stops_gtc[symbol]
                if hasattr(trade_stop.order, "auxPrice") and trade_stop.order.auxPrice:
                    stop_level = trade_stop.order.auxPrice
                elif hasattr(trade_stop.order, "trailStopPrice"):
                    stop_level = trade_stop.order.trailStopPrice

            if stop_level is None:
                log_event("WARN", f"No se encontró stop GTC para {symbol} — evaluación omitida",
                          symbol=symbol)
                try:
                    from telegram import send_telegram
                    send_telegram(f"ALERTA CRITICA LIBERTAD_2045 - {symbol} sin stop GTC activo. Posicion abierta sin proteccion. Revisar inmediatamente.")
                except Exception:
                    pass
                continue

            # ── Trailing stop dinámico B1 ─────────────────────────────────
            # Actualiza el stop GTC en IBKR si el nuevo nivel es más alto.
            # Modificación in-place (mismo orderId) para evitar ventana sin protección.
            df_sym = (datos or {}).get(symbol)
            if df_sym is not None:
                nuevo_stop, mult = calcular_trailing_stop(df_sym)
            else:
                # Fallback: descarga propia (watchdog, relanzo aislado)
                from data_loader import obtener_datos
                df_sym = obtener_datos(ib, symbol)
                nuevo_stop, mult = calcular_trailing_stop(df_sym) if df_sym is not None else (None, None)

            if nuevo_stop is not None and nuevo_stop > stop_level:
                try:
                    trade_stop = stops_gtc[symbol]
                    trade_stop.order.auxPrice = nuevo_stop
                    if mode in ("PAPER", "LIVE"):
                        ib.placeOrder(trade_stop.contract, trade_stop.order)
                        ib.sleep(0.5)
                        log_event("INFO",
                                  f"Trailing stop actualizado | {symbol} | "
                                  f"{stop_level:.2f} → {nuevo_stop:.2f} | mult={mult}",
                                  symbol=symbol)
                    else:
                        log_event("SIM", f"Orden simulada — no enviada a IBKR", symbol=symbol)
                    stop_level = nuevo_stop
                except Exception as e:
                    log_event("WARN",
                              f"Error actualizando trailing stop de {symbol}: {e}",
                              symbol=symbol)

            log_event("INFO",
                      f"Evaluando stop | {symbol} | "
                      f"cierre={precio_cierre:.2f} | stop={stop_level:.2f}",
                      symbol=symbol)

            # Evaluación: ¿el cierre toca el stop?
            if precio_cierre <= stop_level:

                log_event("INFO",
                          f"STOP ACTIVADO por cierre | {symbol} | "
                          f"cierre={precio_cierre:.2f} <= stop={stop_level:.2f}",
                          symbol=symbol)

                # Cancelar orden GTC existente
                try:
                    if mode in ("PAPER", "LIVE"):
                        ib.cancelOrder(stops_gtc[symbol].order)
                        ib.sleep(1)
                    else:
                        log_event("SIM", f"CancelOrder simulado — GTC no tocado", symbol=symbol)
                except Exception as e:
                    log_event("WARN", f"Error cancelando GTC de {symbol}: {e}", symbol=symbol)

                # Cerrar posición con orden MKT
                try:
                    contrato = pos.contract
                    shares   = int(abs(pos.position))

                    # Verificar que la posición sigue siendo larga antes de cerrar
                    # Si pos.position <= 0, no enviar SELL — sería crear o agravar un corto
                    posicion_actual = next(
                        (p for p in ib.positions() if p.contract.symbol == symbol),
                        None
                    )
                    if posicion_actual is None or posicion_actual.position <= 0:
                        log_event("WARN",
                                  f"SELL omitido — {symbol} ya no tiene posición larga "
                                  f"(position={posicion_actual.position if posicion_actual else 'N/A'})",
                                  symbol=symbol)
                        continue

                    orden_cierre = MarketOrder("SELL", shares)
                    orden_cierre.tif = "DAY"

                    if mode in ("PAPER", "LIVE"):
                        trade = ib.placeOrder(contrato, orden_cierre)
                        ib.sleep(2)

                        estado_cierre = trade.orderStatus.status

                        log_event("INFO",
                                  f"Orden MKT enviada | {symbol} | {shares} acc. | "
                                  f"estado={estado_cierre}",
                                  symbol=symbol)

                        if estado_cierre in ("Cancelled", "Inactive"):
                            # La orden fue rechazada — la posición sigue abierta.
                            # Recolocar un stop GTC de emergencia para no dejarla sin protección.
                            log_event("ERROR",
                                      f"Orden MKT RECHAZADA ({estado_cierre}) | {symbol} | "
                                      f"posición sigue abierta — recolocando stop GTC de emergencia",
                                      symbol=symbol)
                            try:
                                from telegram import send_telegram_critical
                                send_telegram_critical(
                                    f"🔴 LIBERTAD_2045 — {symbol}: orden de cierre RECHAZADA "
                                    f"({estado_cierre}). Posición sigue abierta. "
                                    f"Stop GTC de emergencia recolocado automáticamente."
                                )
                            except Exception:
                                pass
                            # Recolocar stop GTC con el nivel anterior
                            try:
                                contrato_em = pos.contract
                                contrato_em.exchange = "SMART"
                                if ib.qualifyContracts(contrato_em):
                                    stop_em = Order()
                                    stop_em.action        = "SELL"
                                    stop_em.orderType     = "STP"
                                    stop_em.totalQuantity = int(abs(pos.position))
                                    stop_em.auxPrice      = stop_level
                                    stop_em.tif           = "GTC"
                                    stop_em.transmit      = True
                                    if mode in ("PAPER", "LIVE"):
                                        ib.placeOrder(contrato_em, stop_em)
                                        ib.sleep(1)
                                        log_event("INFO",
                                                  f"Stop GTC de emergencia recolocado | {symbol} | "
                                                  f"stop={stop_level:.2f}",
                                                  symbol=symbol)
                                    else:
                                        log_event("SIM", f"Orden simulada — no enviada a IBKR", symbol=symbol)
                            except Exception as e_em:
                                log_event("ERROR",
                                          f"Error recolocando stop de emergencia para {symbol}: {e_em}",
                                          symbol=symbol)
                            # NO añadir a cerrados — la posición sigue abierta
                        else:
                            cerrados.append(symbol)
                    else:
                        log_event("SIM", f"Orden simulada — no enviada a IBKR", symbol=symbol)
                        cerrados.append(symbol)

                except Exception as e:
                    log_event("ERROR", f"Error cerrando posición {symbol}: {e}", symbol=symbol)

            else:
                log_event("INFO",
                          f"Posición mantenida | {symbol} | "
                          f"cierre={precio_cierre:.2f} > stop={stop_level:.2f}",
                          symbol=symbol)

    except Exception as e:
        log_event("ERROR", f"Error en evaluación de stops por cierre: {e}")

    if cerrados:
        log_event("INFO", f"Posiciones cerradas por cierre: {len(cerrados)} → {cerrados}")

    return cerrados


# --------------------------------------------------
# Posiciones y señales
# --------------------------------------------------

def obtener_posiciones_abiertas(ib):
    """
    Devuelve una lista de símbolos con posición abierta o con
    orden de entrada pendiente de ejecución.

    Incluye ambos casos para evitar entradas duplicadas:
        - Posiciones ya ejecutadas (aparecen en ib.positions())
        - Órdenes BUY pendientes aún no ejecutadas (en ib.openOrders())
          que no aparecerían en ib.positions() hasta que se ejecuten
    """

    simbolos_ocupados = set()

    # -- Posiciones abiertas --
    try:
        positions = ib.positions()

        for p in positions:
            if p.position != 0:
                simbolos_ocupados.add(p.contract.symbol)

    except Exception as e:
        log_event("ERROR", f"Error leyendo posiciones abiertas: {e}")

    # -- Órdenes de compra pendientes --
    try:
        for trade in ib.openTrades():
            if trade.order.action == "BUY":
                simbolos_ocupados.add(trade.contract.symbol)

    except Exception as e:
        log_event("ERROR", f"Error leyendo órdenes pendientes: {e}")

    simbolos = list(simbolos_ocupados)

    log_event("INFO", f"Posiciones ocupadas: {len(simbolos)} → {simbolos}")

    return simbolos


def filtrar_senales(signals, open_positions):
    """
    Filtra las señales para:
        1. Evitar entradas en activos ya ocupados (posición o BUY pendiente)
        2. Respetar el límite máximo de posiciones simultáneas (MAX_POSITIONS)

    Las señales llegan ya ordenadas por score descendente desde el orquestador.
    Se devuelven las mejores señales disponibles hasta cubrir los slots libres.
    """

    if len(open_positions) == 0:
        available_slots = MAX_POSICIONES_ARRANQUE
        log_event("INFO", f"Portfolio vacío — arranque gradual: máx {MAX_POSICIONES_ARRANQUE} entradas este ciclo")
    else:
        available_slots = MAX_POSITIONS - len(open_positions)

    if available_slots <= 0:
        log_event("INFO", f"Portfolio lleno ({MAX_POSITIONS}/{MAX_POSITIONS}). "
                           "No se ejecutan nuevas entradas.")
        return []

    filtered = []

    for signal in signals:

        symbol = signal["symbol"]

        if symbol in open_positions:
            continue

        filtered.append(signal)

    resultado = filtered[:available_slots]

    log_event("INFO", f"Señales tras filtro: {len(resultado)} "
                       f"(slots libres: {available_slots})")

    return resultado
