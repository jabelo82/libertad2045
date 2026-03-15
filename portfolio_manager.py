from ib_insync import *

from logger import log_event


MAX_POSITIONS = 5


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
        open_orders = ib.openOrders()

        for order in open_orders:
            if order.action == "BUY":
                # Intentar obtener el símbolo del contrato asociado a la orden
                symbol = getattr(order, "symbol", None)
                if symbol:
                    simbolos_ocupados.add(symbol)

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