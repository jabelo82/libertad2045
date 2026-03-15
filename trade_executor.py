from ib_insync import Stock, Order

from logger import log_event


def ejecutar_trade(ib, symbol, df, shares, stop_distance, buffer=0.05, mode="SIM"):
    """
    Ejecuta una operación de entrada con stop-loss adjunto.

    Estructura de la operación:
        - BUY STOP  → entrada por ruptura del máximo del día anterior + buffer
        - SELL STOP → stop-loss automático a distancia ATR × multiplicador

    Modos:
        SIM   → solo imprime la operación, no envía nada a IBKR
        PAPER → envía órdenes a cuenta paper de IBKR
        LIVE  → envía órdenes a cuenta real de IBKR
    """

    # --------------------------------------------------
    # Validaciones previas
    # --------------------------------------------------

    if shares <= 0:
        log_event("WARN", "Trade abortado: shares <= 0", symbol=symbol)
        return

    if df is None or len(df) < 1:
        log_event("WARN", "Trade abortado: DataFrame inválido", symbol=symbol)
        return

    last = df.iloc[-1]
    high = last.high

    buy_stop_price = round(high + buffer, 2)
    stop_loss_price = round(buy_stop_price - stop_distance, 2)

    # Protección crítica: el stop-loss no puede ser cero ni negativo
    if stop_loss_price <= 0:
        log_event("WARN", "Trade abortado: stop_loss_price <= 0", symbol=symbol,
                  entry=buy_stop_price, stop=stop_loss_price)
        return

    # --------------------------------------------------
    # Log y feedback visual
    # --------------------------------------------------

    print(f"[{symbol}] Entrada BUY STOP : {buy_stop_price}")
    print(f"[{symbol}] Stop loss        : {stop_loss_price}")
    print(f"[{symbol}] Acciones         : {shares}")
    print(f"[{symbol}] Modo             : {mode}")

    # --------------------------------------------------
    # Modo SIM: solo registra, no envía órdenes
    # --------------------------------------------------

    if mode == "SIM":
        print(f"[{symbol}] MODO SIMULACIÓN → no se envían órdenes")
        log_event("SIM", "Trade simulado", symbol=symbol, shares=shares,
                  entry=buy_stop_price, stop=stop_loss_price)
        return

    # --------------------------------------------------
    # Modos PAPER y LIVE: envío real de órdenes a IBKR
    # --------------------------------------------------

    contract = Stock(symbol, "SMART", "USD")

    # Verificar que el contrato se resuelve correctamente
    qualified = ib.qualifyContracts(contract)

    if not qualified:
        log_event("ERROR", "Trade abortado: contrato no resuelto en IBKR", symbol=symbol)
        print(f"[{symbol}] ERROR: contrato no resuelto en IBKR")
        return

    # Orden de entrada: BUY STOP (se activa cuando el precio sube hasta buy_stop_price)
    entry = Order()
    entry.action = "BUY"
    entry.orderType = "STP"
    entry.totalQuantity = shares
    entry.auxPrice = buy_stop_price
    entry.tif = "DAY"
    entry.transmit = False          # No transmitir hasta enviar el stop adjunto

    # Enviar la orden de entrada y obtener el orderId real asignado por IBKR
    trade = ib.placeOrder(contract, entry)

    # Dar tiempo a IBKR para asignar el orderId
    ib.sleep(1)

    real_order_id = trade.order.orderId

    if not real_order_id:
        log_event("ERROR", "Trade abortado: IBKR no devolvió orderId para la entrada",
                  symbol=symbol)
        print(f"[{symbol}] ERROR: no se recibió orderId de IBKR")
        return

    # Orden de stop-loss: SELL STOP ligada a la entrada mediante parentId
    stop = Order()
    stop.action = "SELL"
    stop.orderType = "STP"
    stop.totalQuantity = shares
    stop.auxPrice = stop_loss_price
    stop.tif = "GTC"
    stop.parentId = real_order_id   # Vinculada al orderId real, no a un valor fantasma
    stop.transmit = True            # Esta orden transmite el par completo a IBKR

    ib.placeOrder(contract, stop)

    # Dar tiempo a IBKR para procesar el par de órdenes
    ib.sleep(1)

    # --------------------------------------------------
    # Registro en log
    # --------------------------------------------------

    log_event("TRADE", f"Orden enviada a IBKR [{mode}]", symbol=symbol, shares=shares,
              entry=buy_stop_price, stop=stop_loss_price)

    print(f"[{symbol}] Orden enviada a IBKR ({mode})")