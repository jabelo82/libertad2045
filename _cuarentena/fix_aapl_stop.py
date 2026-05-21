"""
fix_aapl_stop.py — Diagnóstico y reparación del stop GTC de AAPL

Pasos:
    1. Conecta a IBKR y comprueba si AAPL tiene stop GTC activo
    2. Si no lo tiene, calcula el precio de stop con la misma fórmula
       que el sistema (avgCost - ATR × multiplicador dinámico B1)
    3. Coloca la orden GTC de SELL STP en IBKR

Uso:
    export TRADING_MODE=PAPER   # o LIVE
    python fix_aapl_stop.py
"""

import os
import sys

from ib_insync import IB, Stock, Order

from data_loader import obtener_datos
from position_size import _obtener_multiplicador

SYMBOL = "AAPL"
MODE   = os.getenv("TRADING_MODE", "PAPER")

# Puerto según modo (misma lógica que conexion_ib.py)
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4002"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "2"))   # clientId distinto al sistema


def main():
    print(f"[fix_aapl_stop] Modo: {MODE} | {IBKR_HOST}:{IBKR_PORT}")

    ib = IB()
    ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=10)

    if not ib.isConnected():
        print("ERROR: No se pudo conectar a IBKR")
        sys.exit(1)

    print("Conectado a IBKR")

    try:

        # --------------------------------------------------
        # PASO 1 — Buscar posición AAPL
        # --------------------------------------------------

        positions = ib.positions()
        aapl_pos  = None

        for p in positions:
            if p.contract.symbol == SYMBOL and p.position != 0:
                aapl_pos = p
                break

        if aapl_pos is None:
            print(f"INFO: No hay posición abierta en {SYMBOL}. Nada que hacer.")
            return

        shares   = int(abs(aapl_pos.position))
        avg_cost = aapl_pos.avgCost
        print(f"Posición encontrada: {shares} acc. | avgCost={avg_cost:.2f}")

        # --------------------------------------------------
        # PASO 2 — Verificar si ya existe stop GTC
        # Solicita TODOS los open orders (incluyendo sesiones anteriores)
        # --------------------------------------------------

        ib.reqAllOpenOrders()
        ib.sleep(1)

        stop_existente = None
        for trade in ib.openTrades():
            if (trade.contract.symbol == SYMBOL and
                    trade.order.action == "SELL" and
                    trade.order.orderType in ("STP", "TRAIL") and
                    trade.order.tif == "GTC"):
                stop_existente = trade
                break

        if stop_existente is not None:
            nivel = stop_existente.order.auxPrice
            print(f"OK: Stop GTC ya existe para {SYMBOL} — nivel={nivel:.2f}")
            print("No es necesario hacer nada.")
            return

        # --------------------------------------------------
        # PASO 3 — Calcular precio de stop
        # Usa la misma fórmula que el sistema: avgCost - ATR × multiplicador B1
        # --------------------------------------------------

        print(f"WARN: No se encontró stop GTC para {SYMBOL}. Calculando...")

        df = obtener_datos(ib, SYMBOL)

        if df is None or df.empty:
            print("ERROR: No se pudieron obtener datos de AAPL para calcular el stop")
            sys.exit(1)

        atr          = df["ATR"].iloc[-1]
        multiplicador = _obtener_multiplicador(df)
        stop_price   = round(avg_cost - atr * multiplicador, 2)

        if stop_price <= 0:
            print(f"ERROR: Stop price calculado inválido ({stop_price}). Abortando.")
            sys.exit(1)

        current_close = df["close"].iloc[-1]
        print(f"ATR={atr:.4f} | Multiplicador={multiplicador} | "
              f"Stop calculado={stop_price:.2f} | Cierre actual={current_close:.2f}")

        if stop_price >= current_close:
            print(f"WARN: Stop ({stop_price:.2f}) >= cierre actual ({current_close:.2f}). "
                  "La posición ya habría tocado el stop. Revisa manualmente.")
            sys.exit(1)

        # --------------------------------------------------
        # PASO 4 — Colocar orden GTC
        # --------------------------------------------------

        if MODE == "SIM":
            print(f"[SIM] Se colocaría: SELL STP {shares} {SYMBOL} @ {stop_price:.2f} GTC")
            return

        contrato = Stock(SYMBOL, "SMART", "USD")
        ib.qualifyContracts(contrato)

        orden_stop = Order()
        orden_stop.action    = "SELL"
        orden_stop.orderType = "STP"
        orden_stop.totalQuantity = shares
        orden_stop.auxPrice  = stop_price
        orden_stop.tif       = "GTC"
        orden_stop.transmit  = True

        trade = ib.placeOrder(contrato, orden_stop)
        ib.sleep(2)

        status = trade.orderStatus.status
        print(f"Orden enviada: SELL STP {shares} {SYMBOL} @ {stop_price:.2f} GTC "
              f"| Estado={status}")

        if status in ("Submitted", "PreSubmitted", "Filled"):
            print(f"OK: Stop GTC colocado correctamente para {SYMBOL}")
        else:
            print(f"WARN: Estado inesperado '{status}'. Verifica en TWS.")

    finally:
        ib.disconnect()
        print("Desconectado de IBKR")


if __name__ == "__main__":
    main()
