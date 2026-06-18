"""
rebalance.py — PROYECTO_LIBERTAD_2045

Rebalanceo dinámico de posiciones abiertas.

Ejecutado desde libertad2045.py tras evaluar_stops_por_cierre() y antes
del escaneo de nuevas señales. Usa la misma conexión IB ya establecida
por el orquestador — no crea ninguna conexión propia.

Lógica de decisión por posición:
    1. Obtener shares actuales y precio de cierre más reciente
    2. Calcular shares óptimos con calcular_posicion() (idéntico a entradas)
    3. Medir desviación relativa: (actual - óptimo) / óptimo
       · desviación > +REBALANCE_THRESHOLD  → sobredimensionada → REDUCIR a óptimo
       · desviación < -REBALANCE_THRESHOLD  → infradimensionada → AMPLIAR a óptimo
    4. Protección adicional: si valor_posición > MAX_POSITION_PCT × capital
       → REDUCIR aunque la desviación no supere el umbral (límite de concentración)
    5. Tras ejecutar el ajuste, cancelar stop GTC anterior y colocar uno nuevo
       para la cantidad actualizada (stop calculado con ATR actual)

No genera nuevas entradas ni evalúa señales. No llama a risk_check —
ese control lo hace el orquestador antes de invocar este módulo.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
from ib_insync import Order, Stock

from data_loader import obtener_datos
from logger import log_event
from position_size import MAX_POSITION_PCT, calcular_posicion, calcular_trailing_stop
from telegram import send_telegram


# --------------------------------------------------
# Parámetros — configurables desde .env
# --------------------------------------------------

# Desviación relativa mínima para disparar un ajuste (0.25 = 25 %)
REBALANCE_THRESHOLD = float(os.getenv("REBALANCE_THRESHOLD", "0.25"))  # Ver también config.py — REBALANCE_THRESHOLD

# Delta mínimo de acciones para ejecutar el ajuste.
# Evita micro-operaciones que generarían comisiones sin beneficio real.
REBALANCE_MIN_SHARES = int(os.getenv("REBALANCE_MIN_SHARES", "5"))  # Ver también config.py — REBALANCE_MIN_SHARES


# --------------------------------------------------
# H-4: Archivo de estado para órdenes AMPLIAR pendientes
# Persiste entre ciclos para detectar fills de apertura y actualizar el stop GTC.
# --------------------------------------------------

_PROJECT_DIR            = Path(__file__).resolve().parent
_PENDING_REBALANCE_FILE = _PROJECT_DIR / "pending_rebalance.json"


def _leer_pendientes() -> dict:
    """Lee pending_rebalance.json. Devuelve {} si no existe o está corrupto.
    Elimina automáticamente entradas con más de 7 días sin confirmarse."""
    try:
        if _PENDING_REBALANCE_FILE.exists():
            pendientes = json.loads(_PENDING_REBALANCE_FILE.read_text())
            stale = []
            for sym, entrada in list(pendientes.items()):
                ts_str = entrada.get("timestamp")
                if not ts_str:
                    continue
                try:
                    age_days = (datetime.now() - datetime.fromisoformat(ts_str)).days
                except Exception:
                    continue
                if age_days > 7:
                    stale.append((sym, entrada.get("accion", "AMPLIAR"), age_days))
            if stale:
                for sym, accion, age_days in stale:
                    log_event("ERROR",
                              f"pending_rebalance: {accion} de {sym} lleva {age_days}d "
                              f"sin confirmarse — eliminando entrada stale",
                              symbol=sym)
                    try:
                        from telegram import send_telegram_critical
                        send_telegram_critical(
                            f"🔴 LIBERTAD_2045 — pending_rebalance: {accion} {sym} lleva "
                            f"{age_days}d sin confirmar. Verificar manualmente en IBKR."
                        )
                    except Exception:
                        pass
                    del pendientes[sym]
                _guardar_pendientes(pendientes)
            return pendientes
    except Exception as e:
        log_event("WARN", f"pending_rebalance: error leyendo archivo: {e}")
    return {}


def _guardar_pendientes(pendientes: dict) -> None:
    try:
        _PENDING_REBALANCE_FILE.write_text(json.dumps(pendientes, indent=2))
    except Exception as e:
        log_event("WARN", f"pending_rebalance: error guardando archivo: {e}")


def _guardar_pendiente_ampliar(symbol: str, shares_esperadas: int, shares_delta: int) -> None:
    pendientes = _leer_pendientes()
    pendientes[symbol] = {
        "accion":           "AMPLIAR",
        "shares_esperadas": shares_esperadas,
        "shares_delta":     shares_delta,
        "timestamp":        datetime.now().isoformat(),
    }
    _guardar_pendientes(pendientes)
    log_event("INFO",
              f"pending_rebalance: AMPLIAR guardado para {symbol} "
              f"(shares_esperadas={shares_esperadas})",
              symbol=symbol)


def _guardar_pendiente_reducir(symbol: str, shares_esperadas: int, shares_delta: int) -> None:
    pendientes = _leer_pendientes()
    pendientes[symbol] = {
        "accion":           "REDUCIR",
        "shares_esperadas": shares_esperadas,
        "shares_delta":     shares_delta,
        "timestamp":        datetime.now().isoformat(),
    }
    _guardar_pendientes(pendientes)
    log_event("INFO",
              f"pending_rebalance: REDUCIR guardado para {symbol} "
              f"(shares_esperadas={shares_esperadas})",
              symbol=symbol)


def _eliminar_pendiente_ampliar(symbol: str) -> None:
    pendientes = _leer_pendientes()
    if symbol in pendientes:
        del pendientes[symbol]
        _guardar_pendientes(pendientes)


# --------------------------------------------------
# Estructura de decisión
# --------------------------------------------------

@dataclass
class DecisionRebalanceo:
    """Resultado de la evaluación de una posición."""
    symbol:        str
    accion:        str    # 'AMPLIAR' | 'REDUCIR' | 'OK' | 'ERROR'
    shares_actual: int
    shares_optimo: int
    shares_delta:  int    # positivo = compra, negativo = venta
    valor_actual:  float
    valor_optimo:  float
    motivo:        str
    ejecutado:     bool = False


# --------------------------------------------------
# Helpers internos
# --------------------------------------------------

def _obtener_gtc_stops(ib) -> dict:
    """
    Devuelve un mapa {symbol: trade} con los stops GTC activos.
    Usa el mismo patrón que evaluar_stops_por_cierre() en portfolio_manager.
    """
    ib.reqAllOpenOrders()
    ib.sleep(2)

    stops = {}
    for trade in ib.trades():
        if (trade.order.orderType in ("STP", "TRAIL")
                and trade.order.action == "SELL"
                and trade.order.tif == "GTC"):
            symbol = trade.contract.symbol
            if symbol in stops:
                precio_exist = getattr(stops[symbol].order, "auxPrice", 0) or 0
                precio_nuevo = getattr(trade.order, "auxPrice", 0) or 0
                log_event("CRITICAL",
                          f"STOP GTC DUPLICADO (rebalance): {symbol} — "
                          f"órdenes {stops[symbol].order.orderId} ({precio_exist:.2f}) "
                          f"y {trade.order.orderId} ({precio_nuevo:.2f}) — "
                          f"conservando precio mayor",
                          symbol=symbol)
                try:
                    from telegram import send_telegram_critical
                    send_telegram_critical(
                        f"🔴 LIBERTAD_2045 — Stop GTC duplicado: {symbol} | "
                        f"Órdenes {stops[symbol].order.orderId} y {trade.order.orderId}. "
                        f"Revisar manualmente."
                    )
                except Exception:
                    pass
                if precio_nuevo > precio_exist:
                    stops[symbol] = trade
            else:
                stops[symbol] = trade

    return stops


def _precio_cierre_reciente(ib, symbol: str) -> Optional[float]:
    """
    Devuelve el precio de cierre más reciente para el símbolo.
    Misma llamada que usa evaluar_stops_por_cierre().
    """
    try:
        contract = Stock(symbol, "SMART", "USD")
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            keepUpToDate=False,
        )
        if bars:
            bar_date = bars[-1].date
            if hasattr(bar_date, "date"):
                bar_date = bar_date.date()
            antiguedad = (datetime.now().date() - bar_date).days
            if antiguedad > 5:
                log_event("WARN",
                          f"Rebalanceo: datos de {symbol} con {antiguedad}d de antigüedad "
                          f"(última barra: {bar_date}) — precio ignorado",
                          symbol=symbol)
                return None
            return bars[-1].close
    except Exception as e:
        log_event("ERROR", f"Rebalanceo: error obteniendo precio de {symbol}: {e}",
                  symbol=symbol)
    return None


def _reemplazar_stop_gtc(
    ib,
    symbol: str,
    contrato,
    shares_nuevas: int,
    stop_price: float,
    stop_anterior,
) -> bool:
    """
    Coloca el nuevo stop GTC y, solo si tiene éxito, cancela el anterior.
    Retorna True si el reemplazo se completó sin errores.

    Orden de operaciones (H-8 fix): validar → colocar nuevo → cancelar viejo.
    Nunca cancelar el stop existente si el nuevo no es válido o falla.
    """
    # Validar precio antes de tocar nada
    if not (stop_price and stop_price > 0):
        log_event("ERROR",
                  f"Rebalanceo: stop_price inválido ({stop_price}) para {symbol} "
                  f"— stop GTC anterior conservado sin cambios",
                  symbol=symbol)
        return False

    # Colocar stop GTC nuevo PRIMERO
    try:
        nuevo_stop = Order()
        nuevo_stop.action        = "SELL"
        nuevo_stop.orderType     = "STP"
        nuevo_stop.totalQuantity = shares_nuevas
        nuevo_stop.auxPrice      = stop_price
        nuevo_stop.tif           = "GTC"
        nuevo_stop.transmit      = True

        trade_nuevo = ib.placeOrder(contrato, nuevo_stop)
        ib.sleep(1)

        estado = trade_nuevo.orderStatus.status if trade_nuevo else "Unknown"
        if estado in ("Inactive", "Cancelled", "Rejected"):
            log_event("ERROR",
                      f"Rebalanceo: nuevo stop GTC de {symbol} rechazado "
                      f"(estado={estado}) — stop anterior conservado sin cambios",
                      symbol=symbol)
            try:
                from telegram import send_telegram_critical
                send_telegram_critical(
                    f"🔴 LIBERTAD_2045 — Stop GTC rechazado: {symbol} | "
                    f"stop={stop_price:.2f} | estado={estado}"
                )
            except Exception:
                pass
            return False

        log_event("INFO",
                  f"Nuevo stop GTC | qty={shares_nuevas} | stop={stop_price:.2f}",
                  symbol=symbol, shares=shares_nuevas, stop=stop_price)

    except Exception as e:
        log_event("ERROR",
                  f"Rebalanceo: error colocando nuevo stop GTC de {symbol}: {e} "
                  f"— stop GTC anterior conservado sin cambios",
                  symbol=symbol)
        return False

    # Cancelar el stop anterior solo si el nuevo se colocó sin errores
    if stop_anterior is not None:
        try:
            ib.cancelOrder(stop_anterior.order)
            ib.sleep(1)
            log_event("INFO", f"Stop GTC anterior cancelado", symbol=symbol)
        except Exception as e:
            log_event("WARN",
                      f"Rebalanceo: error cancelando stop GTC anterior de {symbol}: {e}",
                      symbol=symbol)

    return True


# --------------------------------------------------
# Lógica de evaluación — función pura (sin llamadas IBKR)
# --------------------------------------------------

def evaluar_posicion(
    symbol:        str,
    shares_actual: int,
    precio:        float,
    capital:       float,
    df,
) -> DecisionRebalanceo:
    """
    Determina si una posición debe ajustarse y calcula el delta de acciones.

    Pura: recibe todos los datos ya obtenidos, no hace ninguna llamada a IBKR.
    Permite verificar la lógica sin conexión de mercado.
    """
    valor_actual = shares_actual * precio

    shares_optimo, _stop_dist, _atr = calcular_posicion(df, capital)
    valor_optimo = shares_optimo * df["close"].iloc[-1] if shares_optimo > 0 else 0.0

    # --------------------------------------------------
    # Protección MAX_POSITION_PCT (límite duro de concentración)
    # Actúa aunque la desviación relativa no supere el umbral.
    # --------------------------------------------------

    limite_valor = capital * MAX_POSITION_PCT
    if valor_actual > limite_valor:
        shares_limite = int(limite_valor / precio)
        delta = shares_limite - shares_actual  # siempre negativo aquí

        if abs(delta) >= REBALANCE_MIN_SHARES:
            return DecisionRebalanceo(
                symbol=symbol,
                accion="REDUCIR",
                shares_actual=shares_actual,
                shares_optimo=shares_limite,
                shares_delta=delta,
                valor_actual=valor_actual,
                valor_optimo=shares_limite * precio,
                motivo=(
                    f"Supera MAX_POSITION_PCT "
                    f"({valor_actual / capital:.1%} > {MAX_POSITION_PCT:.0%})"
                ),
            )

    # --------------------------------------------------
    # calcular_posicion devolvió 0: datos insuficientes → sin acción
    # (no cerrar por este motivo — eso lo hace evaluar_stops_por_cierre)
    # --------------------------------------------------

    if shares_optimo == 0:
        return DecisionRebalanceo(
            symbol=symbol,
            accion="OK",
            shares_actual=shares_actual,
            shares_optimo=0,
            shares_delta=0,
            valor_actual=valor_actual,
            valor_optimo=0.0,
            motivo="calcular_posicion devolvió 0 — datos insuficientes",
        )

    # --------------------------------------------------
    # Desviación relativa respecto al óptimo
    # positivo = sobredimensionada, negativo = infradimensionada
    # --------------------------------------------------

    desviacion = (shares_actual - shares_optimo) / shares_optimo

    if desviacion > REBALANCE_THRESHOLD:
        delta = shares_optimo - shares_actual  # negativo: hay que vender
        if abs(delta) >= REBALANCE_MIN_SHARES:
            return DecisionRebalanceo(
                symbol=symbol,
                accion="REDUCIR",
                shares_actual=shares_actual,
                shares_optimo=shares_optimo,
                shares_delta=delta,
                valor_actual=valor_actual,
                valor_optimo=valor_optimo,
                motivo=(
                    f"Sobredimensionada +{desviacion:.1%} "
                    f"(actual={shares_actual} → óptimo={shares_optimo})"
                ),
            )

    elif desviacion < -REBALANCE_THRESHOLD:
        delta = shares_optimo - shares_actual  # positivo: hay que comprar
        if abs(delta) >= REBALANCE_MIN_SHARES:
            return DecisionRebalanceo(
                symbol=symbol,
                accion="AMPLIAR",
                shares_actual=shares_actual,
                shares_optimo=shares_optimo,
                shares_delta=delta,
                valor_actual=valor_actual,
                valor_optimo=valor_optimo,
                motivo=(
                    f"Infradimensionada {desviacion:.1%} "
                    f"(actual={shares_actual} → óptimo={shares_optimo})"
                ),
            )

    return DecisionRebalanceo(
        symbol=symbol,
        accion="OK",
        shares_actual=shares_actual,
        shares_optimo=shares_optimo,
        shares_delta=0,
        valor_actual=valor_actual,
        valor_optimo=valor_optimo,
        motivo=f"Dentro del umbral ({desviacion:+.1%})",
    )


# --------------------------------------------------
# Punto de entrada principal
# --------------------------------------------------

def rebalancear(ib, capital: float, mode: str = "SIM", datos=None) -> List[DecisionRebalanceo]:
    """
    Evalúa y ajusta el tamaño de todas las posiciones abiertas.

    Parámetros:
        ib      : conexión IB activa, ya verificada por risk_check en el orquestador.
                  Puerto IB Gateway → controlado por IBKR_PORT en .env (4002).
        capital : NetLiquidation leído por el orquestador — no se re-lee aquí.
        mode    : SIM | PAPER | LIVE

    Retorna lista de DecisionRebalanceo para que el orquestador pueda
    incluir el resumen en su propio mensaje de Telegram.
    """

    decisiones: List[DecisionRebalanceo] = []

    if capital <= 0:
        log_event("WARN",
                  "rebalancear: capital=0 o negativo — rebalanceo omitido "
                  "para evitar cierre masivo involuntario")
        return decisiones

    log_event("INFO",
              f"REBALANCE_START | capital={capital:.2f} | "
              f"modo={mode} | umbral={REBALANCE_THRESHOLD:.0%} | "
              f"min_shares={REBALANCE_MIN_SHARES}")

    # --------------------------------------------------
    # H-4: Procesar órdenes AMPLIAR pendientes de ciclos anteriores
    # Si el fill ocurrió: eliminar entrada (el ciclo normal actualiza el stop GTC).
    # Si el fill no ocurrió aún: marcar símbolo para no duplicar el AMPLIAR.
    # --------------------------------------------------

    pendientes_procesados: set = set()

    if mode in ("PAPER", "LIVE"):
        pendientes = _leer_pendientes()
        if pendientes:
            try:
                pos_actuales = {p.contract.symbol: int(p.position)
                                for p in ib.positions() if p.position > 0}
            except Exception as e:
                log_event("WARN", f"pending_rebalance: error leyendo posiciones: {e}")
                pos_actuales = {}

            for sym, entrada in list(pendientes.items()):
                accion           = entrada.get("accion", "AMPLIAR")
                shares_esperadas = entrada.get("shares_esperadas", 0)
                pos_actual       = pos_actuales.get(sym, 0)

                if accion == "REDUCIR":
                    if pos_actual <= shares_esperadas:
                        # Fill de REDUCIR confirmado — actualizar stop GTC con cantidad correcta
                        log_event("INFO",
                                  f"pending_rebalance: REDUCIR confirmado {sym} "
                                  f"(pos={pos_actual} <= esperadas={shares_esperadas}) "
                                  f"— actualizando stop GTC",
                                  symbol=sym)
                        try:
                            stops_pendientes = _obtener_gtc_stops(ib)
                            if sym in stops_pendientes and pos_actual > 0:
                                stop_trade = stops_pendientes[sym]
                                stop_price = getattr(stop_trade.order, "auxPrice", None)
                                if stop_price and stop_price > 0:
                                    contrato_r = stop_trade.contract
                                    contrato_r.exchange = "SMART"
                                    if ib.qualifyContracts(contrato_r):
                                        _reemplazar_stop_gtc(
                                            ib, sym, contrato_r,
                                            pos_actual, stop_price, stop_trade
                                        )
                                        log_event("INFO",
                                                  f"pending_rebalance: stop GTC corregido "
                                                  f"a {pos_actual} acc @ {stop_price:.2f}",
                                                  symbol=sym)
                        except Exception as e_red:
                            log_event("WARN",
                                      f"pending_rebalance: error corrigiendo stop para {sym}: {e_red}",
                                      symbol=sym)
                        _eliminar_pendiente_ampliar(sym)
                    else:
                        pendientes_procesados.add(sym)
                        log_event("INFO",
                                  f"pending_rebalance: REDUCIR aún pendiente {sym} "
                                  f"(pos={pos_actual} > esperadas={shares_esperadas}) "
                                  f"— omitiendo nuevo REDUCIR este ciclo",
                                  symbol=sym)
                else:  # AMPLIAR (backward compat: accion ausente también trata como AMPLIAR)
                    if pos_actual >= shares_esperadas:
                        log_event("INFO",
                                  f"pending_rebalance: fill confirmado {sym} "
                                  f"(pos={pos_actual} >= esperadas={shares_esperadas}) "
                                  f"— stop GTC se actualizará en ciclo normal",
                                  symbol=sym)
                        _eliminar_pendiente_ampliar(sym)
                    else:
                        pendientes_procesados.add(sym)
                        log_event("INFO",
                                  f"pending_rebalance: AMPLIAR aún pendiente {sym} "
                                  f"(pos={pos_actual} < esperadas={shares_esperadas}) "
                                  f"— omitiendo nuevo AMPLIAR este ciclo",
                                  symbol=sym)

    # --------------------------------------------------
    # 1. Posiciones largas abiertas
    # --------------------------------------------------

    try:
        positions = [p for p in ib.positions() if p.position > 0]
    except Exception as e:
        log_event("ERROR", f"Rebalanceo: no se pudieron leer posiciones: {e}")
        return decisiones

    if not positions:
        log_event("INFO", "Rebalanceo: sin posiciones abiertas — nada que evaluar")
        return decisiones

    log_event("INFO",
              f"Rebalanceo: evaluando {len(positions)} posiciones — "
              f"{[p.contract.symbol for p in positions]}")

    # --------------------------------------------------
    # 2. Stops GTC activos (necesarios para reemplazarlos tras ajustar)
    # --------------------------------------------------

    stops_gtc = _obtener_gtc_stops(ib)

    # --------------------------------------------------
    # 3. Evaluar y ejecutar
    # --------------------------------------------------

    for pos in positions:

        symbol        = pos.contract.symbol
        shares_actual = int(pos.position)

        try:

            # Precio de cierre reciente
            precio = _precio_cierre_reciente(ib, symbol)
            if precio is None:
                log_event("WARN",
                          f"Rebalanceo: sin precio para {symbol} — posición omitida",
                          symbol=symbol)
                decisiones.append(DecisionRebalanceo(
                    symbol=symbol, accion="ERROR",
                    shares_actual=shares_actual, shares_optimo=0,
                    shares_delta=0, valor_actual=0.0, valor_optimo=0.0,
                    motivo="Sin precio de mercado disponible",
                ))
                continue

            # Datos históricos con indicadores (ATR, ATR_PERCENTIL, SMAs)
            df = (datos or {}).get(symbol)
            if df is None:
                df = obtener_datos(ib, symbol)
            if df is None or len(df) < 20:
                log_event("WARN",
                          f"Rebalanceo: datos insuficientes para {symbol} — posición omitida",
                          symbol=symbol)
                decisiones.append(DecisionRebalanceo(
                    symbol=symbol, accion="ERROR",
                    shares_actual=shares_actual, shares_optimo=0,
                    shares_delta=0, valor_actual=0.0, valor_optimo=0.0,
                    motivo="Datos históricos insuficientes (<20 barras)",
                ))
                continue

            # Auto-crear stop GTC si la posición no tiene protección activa
            if symbol not in stops_gtc and mode in ("PAPER", "LIVE"):
                log_event("WARN",
                          f"Posición sin stop GTC — calculando y colocando automáticamente",
                          symbol=symbol)
                df_sym = (datos or {}).get(symbol)
                if df_sym is None:
                    df_sym = obtener_datos(ib, symbol)
                if df_sym is not None:
                    nuevo_stop, mult = calcular_trailing_stop(df_sym)
                    if nuevo_stop and nuevo_stop > 0:
                        try:
                            contrato_s = pos.contract
                            contrato_s.exchange = "SMART"
                            if ib.qualifyContracts(contrato_s):
                                stop_nuevo = Order()
                                stop_nuevo.action        = "SELL"
                                stop_nuevo.orderType     = "STP"
                                stop_nuevo.totalQuantity = int(abs(pos.position))
                                stop_nuevo.auxPrice      = nuevo_stop
                                stop_nuevo.tif           = "GTC"
                                stop_nuevo.transmit      = True
                                ib.placeOrder(contrato_s, stop_nuevo)
                                ib.sleep(1)
                                log_event("INFO",
                                          f"Stop GTC creado automáticamente | {symbol} | "
                                          f"stop={nuevo_stop:.2f} | mult={mult}",
                                          symbol=symbol)
                                ib.reqAllOpenOrders()
                                ib.sleep(2)
                                stops_gtc = _obtener_gtc_stops(ib)
                                if symbol not in stops_gtc:
                                    log_event("ERROR",
                                              f"Stop GTC auto-creado NO confirmado en IBKR para {symbol} "
                                              f"— posición desprotegida",
                                              symbol=symbol)
                                    try:
                                        from telegram import send_telegram_critical
                                        send_telegram_critical(
                                            f"🔴 LIBERTAD_2045 — Stop GTC automático NO confirmado: "
                                            f"{symbol}. Posición desprotegida. Revisar manualmente."
                                        )
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        from telegram import send_telegram
                                        send_telegram(f"⚠️ LIBERTAD_2045 — Stop GTC creado automáticamente: "
                                                     f"{symbol} @ {nuevo_stop:.2f}")
                                    except Exception:
                                        pass
                        except Exception as e:
                            log_event("ERROR",
                                      f"Error creando stop GTC para {symbol}: {e}",
                                      symbol=symbol)

            # Decisión (función pura)
            decision = evaluar_posicion(symbol, shares_actual, precio, capital, df)

            log_event("INFO",
                      f"Rebalanceo eval | {symbol} | acción={decision.accion} | "
                      f"actual={shares_actual} | óptimo={decision.shares_optimo} | "
                      f"{decision.motivo}",
                      symbol=symbol, shares=shares_actual)

            # --------------------------------------------------
            # V6 FIX: Verificar fill parcial
            # Si el stop GTC protege mas shares de las que realmente tenemos,
            # corregir el stop con las shares reales de la posicion.
            # --------------------------------------------------
            if symbol in stops_gtc and mode in ("PAPER", "LIVE"):
                stop_trade = stops_gtc[symbol]
                stop_qty = int(getattr(stop_trade.order, "totalQuantity", 0))
                if stop_qty != shares_actual and shares_actual > 0:
                    log_event("WARN",
                              f"Fill parcial detectado | {symbol} | "
                              f"stop_qty={stop_qty} != pos_qty={shares_actual} | "
                              f"corrigiendo stop GTC",
                              symbol=symbol)
                    stop_price_actual = getattr(stop_trade.order, "auxPrice", None)
                    if stop_price_actual and stop_price_actual > 0:
                        contrato_v6 = pos.contract
                        contrato_v6.exchange = "SMART"
                        if ib.qualifyContracts(contrato_v6):
                            _reemplazar_stop_gtc(
                                ib, symbol, contrato_v6,
                                shares_actual, stop_price_actual,
                                stop_trade,
                            )
                            stops_gtc = _obtener_gtc_stops(ib)

            # --------------------------------------------------
            # Mejora 4: Break-even protection
            #
            # Si el precio de cierre supera entry + 1.5 × ATR, mover el stop
            # a entry + 0.5 × ATR para proteger beneficios sin cerrar la posición.
            # El stop solo se sube, nunca se baja.
            # Actúa de forma independiente al ajuste de tamaño.
            # --------------------------------------------------

            be_stop_aplicado = None  # H-11: nivel BE activo en este ciclo para este símbolo

            entry_price = getattr(pos, "avgCost", None)
            atr_actual  = df["ATR"].iloc[-1] if df is not None else float("nan")

            if not entry_price or entry_price <= 0:
                log_event("WARN",
                          f"Break-even omitido para {symbol} — avgCost no disponible "
                          f"(posición reciente o dato IBKR pendiente)",
                          symbol=symbol)

            if (entry_price and entry_price > 0 and
                    not pd.isna(atr_actual) and atr_actual > 0 and
                    precio >= entry_price + 1.5 * atr_actual):

                be_stop = round(entry_price + 0.5 * atr_actual, 2)

                # Leer stop actual para no bajarlo nunca
                stop_actual = None
                if symbol in stops_gtc:
                    t = stops_gtc[symbol]
                    if hasattr(t.order, "auxPrice") and t.order.auxPrice:
                        stop_actual = t.order.auxPrice

                if be_stop > 0 and (stop_actual is None or be_stop > stop_actual):

                    log_event("INFO",
                              f"Break-even activado | {symbol} | "
                              f"precio={precio:.2f} | entry={entry_price:.2f} | "
                              f"be_stop={be_stop:.2f} | stop_anterior="
                              f"{stop_actual if stop_actual else 'N/A'}",
                              symbol=symbol)

                    if mode in ("PAPER", "LIVE"):
                        contrato_be = pos.contract
                        contrato_be.exchange = "SMART"
                        if ib.qualifyContracts(contrato_be):
                            _reemplazar_stop_gtc(
                                ib, symbol, contrato_be,
                                int(abs(pos.position)), be_stop,
                                stops_gtc.get(symbol),
                            )
                            # Refrescar stops_gtc para que el rebalanceo use el stop actualizado
                            # y no coloque un segundo GTC stop encima del be_stop
                            stops_gtc = _obtener_gtc_stops(ib)
                            be_stop_aplicado = be_stop  # H-11: registrar nivel BE para preservarlo
                    else:
                        log_event("SIM",
                                  f"Break-even simulado | {symbol} | "
                                  f"nuevo_stop={be_stop:.2f}",
                                  symbol=symbol)

            # --------------------------------------------------
            # Ejecutar ajuste en PAPER / LIVE
            # --------------------------------------------------

            if decision.accion in ("AMPLIAR", "REDUCIR"):

                if mode in ("PAPER", "LIVE"):

                    contrato = pos.contract
                    contrato.exchange = "SMART"
                    if not ib.qualifyContracts(contrato):
                        log_event("ERROR",
                                  f"Rebalanceo: contrato no resuelto para {symbol}",
                                  symbol=symbol)
                        decision.motivo += " [ERROR: contrato no resuelto en IBKR]"
                        decisiones.append(decision)
                        continue

                    accion_orden  = "BUY" if decision.accion == "AMPLIAR" else "SELL"
                    shares_abs    = abs(decision.shares_delta)

                    # Guard anti-doble-SELL (fix cortos involuntarios):
                    # Si ya existe una orden MKT SELL pendiente para este símbolo
                    # (colocada por evaluar_stops_por_cierre en el mismo ciclo),
                    # omitir este SELL para evitar abrir un corto involuntario.
                    # reqAllOpenOrders garantiza caché actualizada incluso tras reconexión.
                    if accion_orden == "SELL":
                        ib.reqAllOpenOrders()
                        ib.sleep(1)
                        ordenes_venta_pendientes = [
                            t for t in ib.openTrades()
                            if t.contract.symbol == symbol
                            and t.order.action == "SELL"
                            and t.order.orderType == "MKT"
                        ]
                        if ordenes_venta_pendientes:
                            log_event("WARN",
                                      f"Rebalanceo SELL omitido — ya existe orden MKT SELL "
                                      f"pendiente para {symbol}",
                                      symbol=symbol)
                            decisiones.append(decision)
                            continue

                    # H-4: Omitir AMPLIAR si ya hay uno pendiente de apertura en ciclo anterior
                    if accion_orden == "BUY" and symbol in pendientes_procesados:
                        log_event("INFO",
                                  f"Rebalanceo AMPLIAR omitido para {symbol} "
                                  f"— ya existe AMPLIAR pendiente de ciclo anterior",
                                  symbol=symbol)
                        decisiones.append(decision)
                        continue

                    # DECISIÓN A-1 (documentada 18/06/2026): AMPLIAR se permite incluso con
                    # Risk Guardian activo (drawdown > 10%) porque no abre exposición nueva,
                    # solo ajusta el tamaño de una posición existente que ya superó el filtro
                    # de riesgo en su entrada original. REDUCIR siempre se permite (reduce
                    # exposición). Si se quisiera bloquear AMPLIAR durante drawdown, añadir
                    # aquí: `if risk_guardian_activo: continue`.

                    # Orden MKT DAY — solo horario regular (outsideRth=False).
                    # Si el mercado está cerrado IBKR la encola como PreSubmitted
                    # y la ejecuta en la próxima apertura; no se cancela aquí.
                    orden = Order()
                    orden.action        = accion_orden
                    orden.orderType     = "MKT"
                    orden.totalQuantity = shares_abs
                    orden.tif           = "DAY"
                    orden.outsideRth    = False
                    orden.transmit      = True

                    trade_ajuste = ib.placeOrder(contrato, orden)
                    ib.sleep(2)

                    estado = trade_ajuste.orderStatus.status
                    filled = trade_ajuste.orderStatus.filled

                    # Estados que indican orden aceptada por IBKR
                    ESTADOS_ACEPTADOS = {
                        "Filled", "PartiallyFilled",
                        "PreSubmitted", "Submitted",
                        "ApiPending", "PendingSubmit",
                    }

                    if estado not in ESTADOS_ACEPTADOS and filled == 0:
                        log_event("ERROR",
                                  f"Rebalanceo {decision.accion} RECHAZADO | "
                                  f"{accion_orden} {shares_abs} acc. | "
                                  f"estado={estado}",
                                  symbol=symbol)
                        decision.ejecutado = False
                        try:
                            ib.cancelOrder(trade_ajuste.order)
                        except Exception:
                            pass
                        decisiones.append(decision)
                        continue

                    decision.ejecutado = True

                    if estado == "Filled" and filled >= shares_abs:
                        log_event("TRADE",
                                  f"Rebalanceo {decision.accion} ejecutado | "
                                  f"{accion_orden} {shares_abs} acc. | "
                                  f"precio_ref={precio:.2f}",
                                  symbol=symbol, shares=shares_abs, entry=precio)
                    else:
                        log_event("INFO",
                                  f"Rebalanceo {decision.accion} encolado para apertura | "
                                  f"{accion_orden} {shares_abs} acc. | "
                                  f"estado={estado} — stop GTC se actualizará en el próximo ciclo",
                                  symbol=symbol)
                        # H-4: persistir pendiente para seguimiento entre ciclos
                        if decision.accion == "AMPLIAR":
                            _guardar_pendiente_ampliar(
                                symbol, decision.shares_optimo, decision.shares_delta
                            )
                        elif decision.accion == "REDUCIR":
                            _guardar_pendiente_reducir(
                                symbol, decision.shares_optimo, decision.shares_delta
                            )
                        # Orden pendiente: no tocar el stop GTC hasta que se ejecute
                        decisiones.append(decision)
                        continue

                    # Reemplazar stop GTC con la nueva cantidad (solo si ya filled).
                    # Usar calcular_trailing_stop (misma función que el trailing normal)
                    # para garantizar que TRAILING_FACTOR=0.75 se aplica también aquí.
                    shares_nuevas = decision.shares_optimo
                    stop_price_nuevo, _ = calcular_trailing_stop(df)

                    if stop_price_nuevo is not None and stop_price_nuevo > 0:
                        # H-9: stop por encima del precio actual → se activaría en apertura
                        if stop_price_nuevo >= precio:
                            log_event("WARN",
                                      f"Rebalanceo: stop calculado ({stop_price_nuevo:.2f}) >= "
                                      f"precio actual ({precio:.2f}) para {symbol} "
                                      f"— stop GTC no actualizado",
                                      symbol=symbol)
                            decisiones.append(decision)
                            continue

                        # H-11: no bajar el stop si el break-even ya está activo y es superior
                        if be_stop_aplicado is not None and stop_price_nuevo < be_stop_aplicado:
                            log_event("INFO",
                                      f"Rebalanceo: stop calculado ({stop_price_nuevo:.2f}) < "
                                      f"break-even ({be_stop_aplicado:.2f}) para {symbol} "
                                      f"— preservando break-even",
                                      symbol=symbol)
                        else:
                            _reemplazar_stop_gtc(
                                ib, symbol, contrato,
                                shares_nuevas, stop_price_nuevo,
                                stops_gtc.get(symbol),
                            )
                    else:
                        log_event("WARN",
                                  f"Rebalanceo: no se pudo calcular stop para {symbol} "
                                  f"— stop GTC no actualizado",
                                  symbol=symbol)

                    decision.ejecutado = True

                else:  # SIM
                    log_event("SIM",
                              f"Rebalanceo simulado | {decision.accion} | "
                              f"delta={decision.shares_delta:+d} acc.",
                              symbol=symbol, shares=decision.shares_delta)
                    decision.ejecutado = True

            decisiones.append(decision)

        except Exception as e:
            log_event("ERROR", f"Rebalanceo: excepción procesando {symbol}: {e}",
                      symbol=symbol)
            decisiones.append(DecisionRebalanceo(
                symbol=symbol, accion="ERROR",
                shares_actual=shares_actual, shares_optimo=0,
                shares_delta=0, valor_actual=0.0, valor_optimo=0.0,
                motivo=str(e),
            ))

    # --------------------------------------------------
    # 4. Resumen en logs y Telegram
    # --------------------------------------------------

    _enviar_resumen(decisiones, capital, mode)

    n_ajustes = sum(1 for d in decisiones if d.accion in ("AMPLIAR", "REDUCIR"))
    log_event("INFO",
              f"REBALANCE_END | evaluadas={len(decisiones)} | ajustes={n_ajustes}")

    return decisiones


# --------------------------------------------------
# Resumen y notificaciones
# --------------------------------------------------

def _enviar_resumen(
    decisiones: List[DecisionRebalanceo],
    capital: float,
    mode: str,
) -> None:
    """
    Registra el resumen completo en log y envía Telegram si hubo ajustes o errores.
    """

    ajustes = [d for d in decisiones if d.accion in ("AMPLIAR", "REDUCIR")]
    errores = [d for d in decisiones if d.accion == "ERROR"]
    ok_lst  = [d for d in decisiones if d.accion == "OK"]

    log_event("INFO",
              f"Rebalanceo resumen: {len(ajustes)} ajustes | "
              f"{len(ok_lst)} OK | {len(errores)} errores")

    for d in ajustes:
        log_event("INFO",
                  f"  {d.symbol}: {d.accion} {d.shares_delta:+d} acc. | "
                  f"actual={d.shares_actual} → óptimo={d.shares_optimo} | {d.motivo}")

    # No notificar si todo está dentro del umbral
    if not ajustes and not errores:
        return

    # Bloque de ajustes
    bloque_ajustes = ""
    for d in ajustes:
        icono   = "📈" if d.accion == "AMPLIAR" else "📉"
        exec_lbl = "ejecutado" if d.ejecutado else "simulado"
        bloque_ajustes += (
            f"{icono} {d.symbol}: {d.accion} {d.shares_delta:+d} acc. [{exec_lbl}]\n"
            f"   {d.shares_actual} → {d.shares_optimo} acc.\n"
            f"   {d.motivo}\n\n"
        )

    # Bloque de errores
    bloque_errores = ""
    for d in errores:
        bloque_errores += f"⚠️ {d.symbol}: {d.motivo}\n"

    mensaje = (
        f"⚖️ REBALANCEO — {mode}\n"
        f"Capital base : {capital:,.0f}\n"
        f"Evaluadas    : {len(decisiones)}\n"
        f"Ajustes      : {len(ajustes)}\n"
    )
    if bloque_ajustes:
        mensaje += f"\n{bloque_ajustes}"
    if bloque_errores:
        mensaje += f"\nErrores:\n{bloque_errores}"

    send_telegram(mensaje)


def resumen_texto(decisiones: List[DecisionRebalanceo]) -> str:
    """
    Devuelve una línea de resumen para integrar en el mensaje principal del bot.
    """
    if not decisiones:
        return "Rebalanceo          : sin posiciones"

    ajustes = [d for d in decisiones if d.accion in ("AMPLIAR", "REDUCIR")]
    ok      = sum(1 for d in decisiones if d.accion == "OK")
    errores = sum(1 for d in decisiones if d.accion == "ERROR")

    if not ajustes:
        parte_ok  = f"{ok} dentro del umbral" if ok else ""
        parte_err = f"{errores} errores" if errores else ""
        detalle   = " | ".join(filter(None, [parte_ok, parte_err]))
        return f"Rebalanceo          : {detalle or 'OK'}"

    detalle = ", ".join(
        f"{d.symbol} {d.accion} {d.shares_delta:+d}" for d in ajustes
    )
    return f"Rebalanceo          : {len(ajustes)} ajustes ({detalle})"
