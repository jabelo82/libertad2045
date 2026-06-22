import json
from pathlib import Path

from ib_insync import *

from logger import log_event


_PROJECT_DIR = Path(__file__).resolve().parent


def _leer_ampliar_pendientes() -> set:
    """
    Devuelve el conjunto de símbolos con acción AMPLIAR pendiente en pending_rebalance.json.
    Las órdenes BUY DAY de rebalanceo AMPLIAR no deben cancelarse — son posiciones en ajuste.
    """
    try:
        ruta = _PROJECT_DIR / "pending_rebalance.json"
        if ruta.exists():
            datos = json.loads(ruta.read_text())
            return {sym for sym, v in datos.items() if v.get("accion") == "AMPLIAR"}
    except Exception:
        pass
    return set()


def cancelar_ordenes_pendientes(ib):
    """
    Cancela órdenes de entrada pendientes sin tocar los stop-loss activos.

    Reglas de cancelación:
        - Se cancelan : órdenes DAY (entradas que no se ejecutaron)
        - Se cancelan : órdenes GTC hijo cuyo parentId apunta a una DAY cancelada
                        (evita venta en corto involuntaria si IBKR no las cancela auto)
        - Se protegen : órdenes GTC standalone (parentId == 0), son stop-loss activos
        - Se protegen : órdenes BUY DAY con símbolo en pending_rebalance AMPLIAR

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

    # Mapa orderId → symbol para logging (Order no tiene .symbol, sí lo tiene Trade)
    _symbol_map = {}
    try:
        for t in ib.openTrades():
            _symbol_map[t.order.orderId] = t.contract.symbol
    except Exception:
        pass

    # Símbolos con AMPLIAR de rebalanceo pendiente — sus BUY DAY no deben cancelarse
    ampliar_pendientes = _leer_ampliar_pendientes()

    # Conjunto de orderId de las órdenes DAY pendientes (entradas no ejecutadas).
    # Solo aparecen en openOrders si no están filled — las filled no se tocan.
    day_order_ids = {o.orderId for o in open_orders if o.tif == "DAY"}

    for order in open_orders:

        sym = _symbol_map.get(order.orderId, "")

        if order.tif == "GTC":
            parent_id = getattr(order, "parentId", 0) or 0

            if parent_id and parent_id in day_order_ids:
                # GTC hijo de una entrada DAY no ejecutada → cancelar también
                try:
                    ib.cancelOrder(order)
                    ib.sleep(1)
                    canceladas += 1

                    log_event("INFO", "Orden GTC hijo cancelada (entrada DAY no ejecutada)",
                              symbol=sym,
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
                      symbol=sym,
                      entry=getattr(order, "auxPrice", ""))
            continue

        # Proteger BUY DAY con AMPLIAR de rebalanceo pendiente —
        # cancelarla eliminaría el ajuste de tamaño que el rebalanceo encoló.
        if order.action == "BUY" and sym in ampliar_pendientes:
            protegidas += 1
            log_event("INFO",
                      f"Orden BUY DAY protegida (AMPLIAR pendiente en pending_rebalance)",
                      symbol=sym)
            print(f"BUY DAY protegido (AMPLIAR pendiente) → {sym} | ID: {order.orderId}")
            continue

        try:
            ib.cancelOrder(order)
            ib.sleep(1)
            canceladas += 1

            log_event("INFO", "Orden DAY cancelada",
                      symbol=sym,
                      entry=getattr(order, "auxPrice", ""))

            print(f"Orden cancelada → ID: {order.orderId} | "
                  f"Tipo: {order.orderType} | TIF: {order.tif}")

        except Exception as e:
            log_event("ERROR", f"Error cancelando orden {order.orderId}: {e}")
            print(f"Error cancelando orden {order.orderId}: {e}")

    print(f"Canceladas: {canceladas} | Protegidas (GTC): {protegidas}")
    log_event("INFO", f"Limpieza órdenes: {canceladas} canceladas, {protegidas} protegidas")