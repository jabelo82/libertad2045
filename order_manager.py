from ib_insync import *

from logger import log_event


def cancelar_ordenes_pendientes(ib):
    """
    Cancela órdenes de entrada pendientes sin tocar los stop-loss activos.

    Reglas de cancelación:
        - Se cancelan : órdenes DAY (entradas que no se ejecutaron)
        - Se cancelan : órdenes GTC hijo cuyo parentId apunta a una DAY cancelada
                        (evita venta en corto involuntaria si IBKR no las cancela auto)
        - Se protegen : órdenes GTC standalone (parentId == 0), son stop-loss activos

    Esto garantiza que ninguna posición abierta quede sin protección
    después de la limpieza de órdenes.
    """

    ib.reqAllOpenOrders()
    ib.sleep(1)
    open_orders = ib.openOrders()

    if not open_orders:
        print("Sin órdenes pendientes")
        log_event("INFO", "Sin órdenes pendientes para cancelar")
        return

    canceladas = 0
    protegidas = 0

    # Conjunto de orderId de las órdenes DAY pendientes (entradas no ejecutadas).
    # Solo aparecen en openOrders si no están filled — las filled no se tocan.
    day_order_ids = {o.orderId for o in open_orders if o.tif == "DAY"}

    for order in open_orders:

        if order.tif == "GTC":
            parent_id = getattr(order, "parentId", 0) or 0

            if parent_id and parent_id in day_order_ids:
                # GTC hijo de una entrada DAY no ejecutada → cancelar también
                try:
                    ib.cancelOrder(order)
                    canceladas += 1

                    log_event("INFO", "Orden GTC hijo cancelada (entrada DAY no ejecutada)",
                              symbol=getattr(order, "symbol", ""),
                              entry=getattr(order, "auxPrice", ""))

                    print(f"GTC hijo cancelado → ID: {order.orderId} | "
                          f"parentId: {parent_id} | auxPrice: {getattr(order, 'auxPrice', '')}")

                except Exception as e:
                    log_event("ERROR", f"Error cancelando GTC hijo {order.orderId}: {e}")
                    print(f"Error cancelando GTC hijo {order.orderId}: {e}")
            else:
                # GTC standalone: stop-loss de posición abierta → proteger
                protegidas += 1

            continue

        # Cancelar órdenes DAY: entradas que no se ejecutaron
        # Proteger órdenes MKT SELL DAY — son cierres de posición, no entradas.
        # Cancelarlas dejaría posiciones abiertas sin protección ni cierre pendiente.
        if order.action == "SELL" and order.orderType == "MKT":
            protegidas += 1
            log_event("INFO", "Orden MKT SELL DAY protegida (cierre de posición)",
                      symbol=getattr(order, "symbol", ""),
                      entry=getattr(order, "auxPrice", ""))
            continue

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