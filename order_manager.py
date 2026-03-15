from ib_insync import *

from logger import log_event


def cancelar_ordenes_pendientes(ib):
    """
    Cancela órdenes de entrada pendientes sin tocar los stop-loss activos.

    Reglas de cancelación:
        - Se cancelan : órdenes DAY (entradas que no se ejecutaron)
        - Se protegen : órdenes GTC (stop-loss vinculados a posiciones abiertas)

    Esto garantiza que ninguna posición abierta quede sin protección
    después de la limpieza de órdenes.
    """

    open_orders = ib.openOrders()

    if not open_orders:
        print("Sin órdenes pendientes")
        log_event("INFO", "Sin órdenes pendientes para cancelar")
        return

    canceladas = 0
    protegidas = 0

    for order in open_orders:

        # Proteger órdenes GTC: son stop-loss de posiciones abiertas
        if order.tif == "GTC":
            protegidas += 1
            continue

        # Cancelar órdenes DAY: entradas que no se ejecutaron
        try:
            ib.cancelOrder(order)
            canceladas += 1

            log_event("INFO", "Orden DAY cancelada",
                      symbol=getattr(order, "symbol", ""),
                      entry=getattr(order, "auxPrice", ""))

            print(f"Orden cancelada → ID: {order.orderId} | "
                  f"Tipo: {order.orderType} | TIF: {order.tif}")

        except Exception as e:
            log_event("ERROR", f"Error cancelando orden {order.orderId}: {e}")
            print(f"Error cancelando orden {order.orderId}: {e}")

    print(f"Canceladas: {canceladas} | Protegidas (GTC): {protegidas}")
    log_event("INFO", f"Limpieza órdenes: {canceladas} canceladas, {protegidas} protegidas")