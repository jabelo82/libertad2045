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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
from ib_insync import ExecutionFilter, Order, Stock

from data_loader import obtener_datos
from logger import log_event
from position_size import ENTRY_BUFFER, MAX_POSITION_PCT, calcular_posicion, calcular_trailing_stop
from risk_guardian import verificar_apalancamiento_ampliar
from telegram import send_telegram


# --------------------------------------------------
# Parámetros — configurables desde .env
# --------------------------------------------------

# Desviación relativa mínima para disparar un ajuste (0.25 = 25 %)
REBALANCE_THRESHOLD = float(os.getenv("REBALANCE_THRESHOLD", "0.25"))  # Ver también config.py — REBALANCE_THRESHOLD

# Delta mínimo de acciones para ejecutar el ajuste.
# Evita micro-operaciones que generarían comisiones sin beneficio real.
REBALANCE_MIN_SHARES = int(os.getenv("REBALANCE_MIN_SHARES", "5"))  # Ver también config.py — REBALANCE_MIN_SHARES

# Días (naturales) desde que se guardó una entrada pendiente sin encontrar
# rastro alguno del order_id (ni fill, ni orden abierta) antes de darla por
# huérfana y descartarla. Las órdenes de rebalanceo son tif=DAY — IBKR las
# resuelve (fill o cancelación) en un único día de mercado, así que este
# umbral solo da margen para fines de semana / reconexiones tardías del
# Gateway, no para esperar una ejecución legítima que tarde más.
# Ver incidente DVN (10-11/08/2026, sección 12 del contexto).
PENDING_ORPHAN_THRESHOLD_DAYS = int(os.getenv("PENDING_ORPHAN_THRESHOLD_DAYS", "3"))

# --------------------------------------------------
# Confirmación activa del reemplazo de stop GTC (vía "cantidad cambia" de
# _reemplazar_stop_gtc) -- Fase 2 del fix del hallazgo ALTA #2 (auditoría
# 07/08/2026: confirmación de órdenes por sondeo de tiempo fijo, sin
# verificar el estado real). Ver investigación previa a este fix.
# --------------------------------------------------

# Timeout para confirmar que el stop NUEVO alcanzó un estado real
# (PreSubmitted/Submitted/Filled) antes de cancelar el antiguo. 8s: ~2,7x
# el margen ya validado empíricamente en trade_executor.py para el mismo
# tipo de orden (STP) -- 1s resultó insuficiente, 3s resolvió el problema
# real la primera noche LIVE (03/08/2026), sin necesidad posterior de
# ampliarlo más.
REPLACE_STOP_CONFIRM_TIMEOUT = float(os.getenv("REPLACE_STOP_CONFIRM_TIMEOUT", "8.0"))

# Timeout para confirmar la CANCELACIÓN del stop antiguo -- más corto:
# si falla, el peor caso es un stop duplicado (el nuevo ya está
# confirmado protegiendo la posición), no una posición desprotegida --
# y reconciliar_stops_gtc() ya limpia duplicados en un ciclo posterior
# (mismo mecanismo del hallazgo MEDIA "GTC duplicados").
REPLACE_STOP_CANCEL_TIMEOUT = float(os.getenv("REPLACE_STOP_CANCEL_TIMEOUT", "5.0"))

# Intervalo de sondeo -- más grueso que los 0,2s de obtener_precio_vivo()
# (portfolio_manager.py, incidente ANET): ahí se sondea un precio que
# cambia tick a tick; aquí una transición de estado de orden, que no
# necesita esa precisión.
REPLACE_STOP_POLL_INTERVAL = float(os.getenv("REPLACE_STOP_POLL_INTERVAL", "0.5"))

# Estados que cuentan como "el stop nuevo está realmente protegiendo".
# Deliberadamente un SUBCONJUNTO de Trade.isActive() de ib_insync 0.9.86
# (ActiveStates = {PendingSubmit, ApiPending, PreSubmitted, Submitted},
# confirmado leyendo el order.py instalado) -- PendingSubmit/ApiPending
# NO cuentan aquí: significan que ni siquiera hay confirmación del
# servidor todavía. PreSubmitted es el estado de reposo NORMAL de una
# orden STP GTC ya aceptada (IBKR la vigila a la espera del disparador)
# -- no es un estado "a medias". Filled cubre el caso raro de disparo
# inmediato al colocarla.
_ESTADOS_STOP_NUEVO_CONFIRMADO = frozenset({"PreSubmitted", "Submitted", "Filled"})

# Estados que cuentan como "el stop antiguo ya no compite con el nuevo".
# Filled incluido a propósito: si el antiguo se dispara de verdad durante
# la espera de cancelación (ventana pequeña pero real, ver diseño), es un
# desenlace correcto -- la posición se cerró -- no un fallo de esta espera.
_ESTADOS_STOP_ANTERIOR_RESUELTO = frozenset({"Cancelled", "ApiCancelled", "Filled"})


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


def _guardar_pendiente_ampliar(symbol: str, shares_esperadas: int, shares_delta: int,
                                order_id: Optional[int] = None) -> None:
    pendientes = _leer_pendientes()
    pendientes[symbol] = {
        "accion":           "AMPLIAR",
        "shares_esperadas": shares_esperadas,
        "shares_delta":     shares_delta,
        "order_id":         order_id,
        "timestamp":        datetime.now().isoformat(),
    }
    _guardar_pendientes(pendientes)
    log_event("INFO",
              f"pending_rebalance: AMPLIAR guardado para {symbol} "
              f"(shares_esperadas={shares_esperadas}, order_id={order_id})",
              symbol=symbol)


def _guardar_pendiente_reducir(symbol: str, shares_esperadas: int, shares_delta: int,
                                order_id: Optional[int] = None) -> None:
    pendientes = _leer_pendientes()
    pendientes[symbol] = {
        "accion":           "REDUCIR",
        "shares_esperadas": shares_esperadas,
        "shares_delta":     shares_delta,
        "order_id":         order_id,
        "timestamp":        datetime.now().isoformat(),
    }
    _guardar_pendientes(pendientes)
    log_event("INFO",
              f"pending_rebalance: REDUCIR guardado para {symbol} "
              f"(shares_esperadas={shares_esperadas}, order_id={order_id})",
              symbol=symbol)


def _eliminar_pendiente_ampliar(symbol: str) -> None:
    pendientes = _leer_pendientes()
    if symbol in pendientes:
        del pendientes[symbol]
        _guardar_pendientes(pendientes)


def _edad_dias(ts_str: Optional[str]) -> Optional[int]:
    """Días naturales transcurridos desde un timestamp ISO. None si no hay
    timestamp o es ilegible."""
    if not ts_str:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(ts_str)).days
    except Exception:
        return None


def _verificar_ejecucion_pendiente(ib, order_id: Optional[int], symbol: str) -> str:
    """
    Verifica en IBKR si existe una ejecución real asociada al order_id de una
    entrada pendiente de pending_rebalance.json.

    No basta con que exista *alguna* posición del símbolo con la cantidad
    esperada: una entrada nueva e independiente del escáner, una operación
    manual, o el resto de una fase anterior (PAPER, otro cutover) pueden
    coincidir por casualidad en símbolo y cantidad sin tener ninguna
    relación con la orden que se está rastreando — ver incidente real DVN,
    10-11/08/2026 (sección 12 del contexto): un AMPLIAR arrastrado de PAPER
    se dio por resuelto porque el escáner abrió una entrada nueva
    independiente en el mismo símbolo.

    Nota sobre el alcance de ib.fills()/ib.trades(): ib_insync los rellena
    en connect() con un backfill automático de reqCompletedOrders() +
    reqExecutions() sin filtro contra IBKR (ib_insync/ib.py::connectAsync),
    no solo con lo ocurrido literalmente tras esa conexión — pero no está
    confirmado hasta qué punto IBKR conserva ese historial a través de un
    reinicio del propio Gateway (no solo de la conexión del cliente), y
    aquí el Gateway se reinicia cada noche (AutoRestartTime). Por eso, si
    no aparece rastro en la caché local, se hace ADEMÁS una consulta
    explícita a reqExecutions() con un ExecutionFilter acotado a los
    últimos días relevantes antes de concluir que la orden es huérfana —
    para no confundir "no está en la caché local" con "nunca se ejecutó".

    Devuelve uno de:
        "CONFIRMADA"       -> hay un fill real (en la caché local o tras la
                               consulta explícita) para ESE order_id
                               concreto, o su trade en ib.trades() ya
                               aparece Filled/PartiallyFilled.
        "PENDIENTE_ACTIVA" -> el order_id sigue vivo en ib.trades() sin
                               fill todavía (Submitted/PreSubmitted/
                               PendingSubmit/ApiPending) — seguir esperando.
        "HUERFANA"         -> el order_id no aparece ni en fills() ni en
                               trades(), NI tras la consulta explícita de
                               reqExecutions() acotada en el tiempo (o su
                               trade ya está Cancelled/ApiCancelled/
                               Inactive/Expired) — la orden ya no existe.
        "NO_VERIFICABLE"   -> no hay order_id que verificar (entrada legacy
                               guardada antes de este fix), o hubo un error
                               leyendo fills()/trades()/reqExecutions —
                               usar el comportamiento previo como fallback.
    """
    if order_id is None:
        return "NO_VERIFICABLE"

    def _fill_encontrado() -> bool:
        return any(getattr(f.execution, "orderId", None) == order_id
                   for f in ib.fills())

    try:
        if _fill_encontrado():
            return "CONFIRMADA"
    except Exception as e:
        log_event("WARN",
                  f"pending_rebalance: error leyendo fills para verificar "
                  f"order_id={order_id}: {e}", symbol=symbol)
        return "NO_VERIFICABLE"

    try:
        for trade in ib.trades():
            if getattr(trade.order, "orderId", None) == order_id:
                estado = trade.orderStatus.status
                if estado in ("Filled", "PartiallyFilled"):
                    return "CONFIRMADA"
                if estado in ("Cancelled", "ApiCancelled", "Inactive", "Expired"):
                    # Estado terminal ya confirmado por IBKR para ESTA
                    # orden concreta (no una ausencia ambigua) — no hace
                    # falta la consulta explícita adicional de abajo.
                    return "HUERFANA"
                return "PENDIENTE_ACTIVA"
    except Exception as e:
        log_event("WARN",
                  f"pending_rebalance: error leyendo trades para verificar "
                  f"order_id={order_id}: {e}", symbol=symbol)
        return "NO_VERIFICABLE"

    # Ni en fills() ni en trades() de la caché local — antes de declarar
    # huérfana, forzar una consulta explícita a IBKR acotada a los
    # últimos días relevantes (ver docstring: reinicio nocturno del
    # Gateway, backfill de connect() no garantizado más allá de eso).
    try:
        desde = datetime.now() - timedelta(days=PENDING_ORPHAN_THRESHOLD_DAYS + 1)
        ib.reqExecutions(ExecutionFilter(
            time=desde.strftime("%Y%m%d-%H:%M:%S"), symbol=symbol
        ))
        if _fill_encontrado():
            return "CONFIRMADA"
    except Exception as e:
        log_event("WARN",
                  f"pending_rebalance: error en reqExecutions explícito "
                  f"verificando order_id={order_id}: {e}", symbol=symbol)
        return "NO_VERIFICABLE"

    return "HUERFANA"


def _actualizar_stop_tras_reduccion(ib, symbol: str, pos_actual: int) -> None:
    """Tras confirmar un REDUCIR, corrige el stop GTC a la cantidad real."""
    try:
        stops_pendientes = _obtener_gtc_stops(ib)
        if symbol in stops_pendientes and pos_actual > 0:
            stop_trade = stops_pendientes[symbol]
            stop_price = getattr(stop_trade.order, "auxPrice", None)
            if stop_price and stop_price > 0:
                contrato_r = stop_trade.contract
                contrato_r.exchange = "SMART"
                if ib.qualifyContracts(contrato_r):
                    _reemplazar_stop_gtc(
                        ib, symbol, contrato_r, pos_actual, stop_price, stop_trade
                    )
                    log_event("INFO",
                              f"pending_rebalance: stop GTC corregido "
                              f"a {pos_actual} acc @ {stop_price:.2f}",
                              symbol=symbol)
    except Exception as e_red:
        log_event("WARN",
                  f"pending_rebalance: error corrigiendo stop para {symbol}: {e_red}",
                  symbol=symbol)


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
                        f"⚠️ LIBERTAD_2045 — Stop GTC duplicado detectado: {symbol} | "
                        f"Órdenes {stops[symbol].order.orderId} y {trade.order.orderId}. "
                        f"Ventana normal de reemplazo (cantidad cambio) — "
                        f"se autorresuelve en la próxima reconciliación."
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


def _hay_stop_gtc_activo(ib, symbol: str, stop_price: float, shares: int) -> bool:
    """
    Comprueba si ya existe en IBKR un stop GTC activo para el símbolo con
    exactamente el precio y cantidad indicados. Previene crear duplicados.
    """
    for trade in ib.trades():
        if (trade.contract.symbol == symbol and
                trade.order.orderType in ("STP", "TRAIL") and
                trade.order.action == "SELL" and
                trade.order.tif == "GTC" and
                trade.orderStatus.status in ("PreSubmitted", "Submitted") and
                int(trade.order.totalQuantity) == shares and
                abs(getattr(trade.order, "auxPrice", 0) - stop_price) < 0.01):
            return True
    return False


def _esperar_confirmacion_orden(ib, trade, timeout, estados_confirmados,
                                 poll=None):
    """
    Espera ACTIVA (bucle de sondeo con ib.sleep(), nunca un sleep fijo
    seguido de una única lectura) a que trade.orderStatus.status alcance
    un estado realmente confirmado -- Fase 2 del fix del hallazgo ALTA #2
    (auditoría 07/08/2026).

    `poll` por defecto None -- deliberado: un default `poll=REPLACE_STOP_
    POLL_INTERVAL` se evaluaría UNA VEZ al definir la función (al
    importar el módulo), congelando el valor de entonces -- un cambio
    posterior de REPLACE_STOP_POLL_INTERVAL (vía .env o en tests) no
    tendría ningún efecto real. Resolviéndolo dentro del cuerpo de la
    función se lee el valor vigente en cada llamada, igual que timeout.

    ib.sleep() bombea el loop de asyncio de fondo -- orderStatus llega tan
    al día sondeando cada `poll` segundos como con un handler de evento
    (confirmado leyendo util.py de ib_insync 0.9.86 antes de este
    diseño). Se elige sondeo sobre suscripción a eventos por ser el mismo
    idioma ya validado en obtener_precio_vivo() (portfolio_manager.py,
    incidente ANET) -- sin añadir el riesgo de una fuga de handler si una
    excepción salta antes de desuscribirlo.

    "Rejected" e "Inactive" nunca forman parte de Trade.isDone() ni
    Trade.isActive() en ib_insync 0.9.86 (confirmado leyendo el order.py
    instalado) -- se comprueban aquí de forma explícita y cortan la
    espera de inmediato, sin agotar el timeout.

    Retorna (resultado, estado_final):
        resultado = "CONFIRMADO" | "RECHAZADO" | "TIMEOUT"
        estado_final = el último trade.orderStatus.status observado.
    """
    if poll is None:
        poll = REPLACE_STOP_POLL_INTERVAL

    deadline = time.monotonic() + timeout
    estado = trade.orderStatus.status if trade else "Unknown"

    while True:
        if estado in ("Rejected", "Inactive"):
            return "RECHAZADO", estado
        if estado in estados_confirmados:
            return "CONFIRMADO", estado
        if time.monotonic() >= deadline:
            return "TIMEOUT", estado
        ib.sleep(poll)
        estado = trade.orderStatus.status if trade else "Unknown"


def _reemplazar_stop_gtc(
    ib,
    symbol: str,
    contrato,
    shares_nuevas: int,
    stop_price: float,
    stop_anterior,
) -> bool:
    """
    Actualiza el stop GTC de una posición con cero riesgo de duplicado.

    Estrategia:
        · Si la cantidad no cambia → modificar in-place el stop existente
          (mismo orderId, IBKR lo interpreta como modificación). Sin ventana.
        · Si la cantidad cambia → crear nuevo, esperar confirmación ACTIVA
          de que está realmente activo (Fase 2, fix ALTA #2 auditoría
          07/08/2026 -- ver _esperar_confirmacion_orden), y solo entonces
          cancelar el anterior (H-8). Antes de crear, verificar que no
          existe ya un stop idéntico (guard).

    Nunca cancelar el stop existente si el nuevo no es válido, falla, o no
    se confirma realmente activo dentro del timeout.
    """
    # Validar precio antes de tocar nada
    if not (stop_price and stop_price > 0):
        log_event("ERROR",
                  f"Rebalanceo: stop_price inválido ({stop_price}) para {symbol} "
                  f"— stop GTC anterior conservado sin cambios",
                  symbol=symbol)
        return False

    # ── Vía rápida: cantidad idéntica → modificar in-place (sin duplicado posible) ──
    if (stop_anterior is not None and
            int(stop_anterior.order.totalQuantity) == shares_nuevas):
        try:
            stop_anterior.order.auxPrice = stop_price
            ib.placeOrder(stop_anterior.contract, stop_anterior.order)
            ib.sleep(0.5)
            log_event("INFO",
                      f"Stop GTC actualizado in-place | qty={shares_nuevas} | stop={stop_price:.2f}",
                      symbol=symbol, shares=shares_nuevas, stop=stop_price)
            return True
        except Exception as e:
            log_event("WARN",
                      f"Rebalanceo: fallo al actualizar stop in-place para {symbol}: {e} "
                      f"— continuando con reemplazo normal",
                      symbol=symbol)
            # Si falla el in-place, continúa con la vía estándar

    # ── Guard de idempotencia: no crear si ya existe stop idéntico ──
    if _hay_stop_gtc_activo(ib, symbol, stop_price, shares_nuevas):
        log_event("WARN",
                  f"Prevención duplicado: stop GTC {stop_price:.2f} × {shares_nuevas} acc "
                  f"ya existe para {symbol} — no se crea nuevo",
                  symbol=symbol)
        return True

    # ── Vía estándar: crear nuevo GTC y cancelar el anterior (cantidad cambia) ──
    try:
        nuevo_stop = Order()
        nuevo_stop.action        = "SELL"
        nuevo_stop.orderType     = "STP"
        nuevo_stop.totalQuantity = shares_nuevas
        nuevo_stop.auxPrice      = stop_price
        nuevo_stop.tif           = "GTC"
        nuevo_stop.transmit      = True

        trade_nuevo = ib.placeOrder(contrato, nuevo_stop)

        # Fase 2 (fix ALTA #2, auditoría 07/08/2026): espera ACTIVA a que
        # el nuevo stop alcance un estado real -- antes era ib.sleep(1) +
        # una lista de exclusión que trataba cualquier estado "todavía
        # pendiente" (PendingSubmit/ApiPending, ninguno terminal) como
        # éxito sin más verificación. Nunca se cancela el stop antiguo
        # hasta confirmar de verdad que el nuevo está activo.
        resultado, estado = _esperar_confirmacion_orden(
            ib, trade_nuevo, REPLACE_STOP_CONFIRM_TIMEOUT,
            _ESTADOS_STOP_NUEVO_CONFIRMADO,
        )

        if resultado != "CONFIRMADO":
            motivo = ("rechazado" if resultado == "RECHAZADO"
                      else f"sin confirmar tras {REPLACE_STOP_CONFIRM_TIMEOUT:.0f}s")
            log_event("ERROR",
                      f"Rebalanceo: nuevo stop GTC de {symbol} {motivo} "
                      f"(estado={estado}) — stop anterior conservado sin cambios",
                      symbol=symbol)
            try:
                from telegram import send_telegram_critical
                send_telegram_critical(
                    f"🔴 LIBERTAD_2045 — Stop GTC {motivo}: {symbol} | "
                    f"stop={stop_price:.2f} | estado={estado}"
                )
            except Exception:
                pass
            return False

        if estado == "Filled":
            log_event("WARN",
                      f"Rebalanceo: el nuevo stop GTC de {symbol} se disparó "
                      f"de inmediato (Filled) al colocarlo -- la posición ya "
                      f"se cerró por el stop nuevo, no por el antiguo",
                      symbol=symbol)
        else:
            log_event("INFO",
                      f"Nuevo stop GTC confirmado ({estado}) | qty={shares_nuevas} | "
                      f"stop={stop_price:.2f}",
                      symbol=symbol, shares=shares_nuevas, stop=stop_price)

    except Exception as e:
        log_event("ERROR",
                  f"Rebalanceo: error colocando nuevo stop GTC de {symbol}: {e} "
                  f"— stop GTC anterior conservado sin cambios",
                  symbol=symbol)
        return False

    # Cancelar el stop anterior solo si el nuevo se colocó y confirmó sin errores
    if stop_anterior is not None:
        try:
            ib.cancelOrder(stop_anterior.order)

            # Fase 2: espera activa también para la cancelación, pero con
            # timeout más corto y sin escalar a Telegram crítico si falla
            # -- el nuevo stop YA está confirmado protegiendo la posición
            # en este punto, así que el peor caso de no confirmar la
            # cancelación es un stop duplicado (redundante, no peligroso),
            # no una posición desprotegida. reconciliar_stops_gtc() ya
            # limpia duplicados en un ciclo posterior (mismo mecanismo del
            # hallazgo MEDIA "GTC duplicados").
            resultado_cancel, estado_cancel = _esperar_confirmacion_orden(
                ib, stop_anterior, REPLACE_STOP_CANCEL_TIMEOUT,
                _ESTADOS_STOP_ANTERIOR_RESUELTO,
            )

            if resultado_cancel == "CONFIRMADO" and estado_cancel == "Filled":
                # Nota A del diseño: ventana pequeña pero real -- el stop
                # antiguo se disparó de verdad antes de que la cancelación
                # surtiera efecto. Desenlace correcto (la posición se
                # cerró), pero el stop nuevo puede haber quedado huérfano.
                log_event("WARN",
                          f"Rebalanceo: el stop GTC antiguo de {symbol} se "
                          f"disparó (Filled) durante la espera de "
                          f"cancelación -- la posición ya se cerró por el "
                          f"stop antiguo, el nuevo stop puede haber "
                          f"quedado huérfano",
                          symbol=symbol)
            elif resultado_cancel != "CONFIRMADO":
                log_event("WARN",
                          f"Rebalanceo: cancelación del stop GTC antiguo de "
                          f"{symbol} sin confirmar tras "
                          f"{REPLACE_STOP_CANCEL_TIMEOUT:.0f}s (estado="
                          f"{estado_cancel}) -- el nuevo stop ya protege la "
                          f"posición; reconciliar_stops_gtc() debería "
                          f"limpiar el duplicado en un ciclo posterior",
                          symbol=symbol)
            else:
                log_event("INFO", f"Stop GTC anterior cancelado ({estado_cancel})",
                          symbol=symbol)
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
# H-4: Reconciliación de órdenes AMPLIAR/REDUCIR pendientes entre ciclos
# --------------------------------------------------

def _reconciliar_pendientes(ib, mode: str) -> set:
    """
    Procesa las entradas de pending_rebalance.json de ciclos anteriores.

    Para cada entrada con order_id, verifica contra IBKR (_verificar_
    ejecucion_pendiente) que la posición reportada proviene REALMENTE de
    esa orden concreta antes de darla por resuelta — no basta con que
    exista alguna posición del símbolo con la cantidad esperada (ver
    incidente DVN, 10-11/08/2026, sección 12 del contexto: una entrada
    nueva e independiente del escáner en el mismo símbolo se confundió con
    el fill del AMPLIAR pendiente).

    Si el order_id no tiene rastro alguno en IBKR (ni fill, ni orden
    abierta) durante más de PENDING_ORPHAN_THRESHOLD_DAYS, la entrada se
    trata como huérfana y se descarta con aviso — en vez de darla por
    resuelta con una coincidencia no verificada, o dejarla viva
    indefinidamente.

    Entradas legacy sin order_id (guardadas antes de este fix) usan el
    comportamiento previo (comparación de símbolo/cantidad) como fallback,
    con un WARN explícito para que quede visible en los logs.

    Devuelve el conjunto de símbolos con una entrada aún pendiente (no
    resuelta, no huérfana este ciclo) — el llamador lo usa para evitar
    duplicar un AMPLIAR el mismo ciclo.
    """
    pendientes_procesados: set = set()

    if mode not in ("PAPER", "LIVE"):
        return pendientes_procesados

    pendientes = _leer_pendientes()
    if not pendientes:
        return pendientes_procesados

    try:
        pos_actuales = {p.contract.symbol: int(p.position)
                        for p in ib.positions() if p.position > 0}
    except Exception as e:
        log_event("WARN", f"pending_rebalance: error leyendo posiciones: {e}")
        pos_actuales = {}

    for sym, entrada in list(pendientes.items()):
        accion           = entrada.get("accion", "AMPLIAR")
        shares_esperadas = entrada.get("shares_esperadas", 0)
        order_id         = entrada.get("order_id")
        pos_actual       = pos_actuales.get(sym, 0)

        estado = _verificar_ejecucion_pendiente(ib, order_id, sym)

        if estado == "CONFIRMADA":
            if accion == "REDUCIR":
                log_event("INFO",
                          f"pending_rebalance: REDUCIR confirmado {sym} "
                          f"(order_id={order_id}, pos={pos_actual}) "
                          f"— actualizando stop GTC",
                          symbol=sym)
                _actualizar_stop_tras_reduccion(ib, sym, pos_actual)
            else:
                log_event("INFO",
                          f"pending_rebalance: fill confirmado {sym} "
                          f"(order_id={order_id}, pos={pos_actual}) "
                          f"— stop GTC se actualizará en ciclo normal",
                          symbol=sym)
            _eliminar_pendiente_ampliar(sym)
            continue

        if estado == "HUERFANA":
            age_days = _edad_dias(entrada.get("timestamp"))
            if age_days is not None and age_days >= PENDING_ORPHAN_THRESHOLD_DAYS:
                log_event("WARN",
                          f"pending_rebalance: {accion} de {sym} huérfana — "
                          f"order_id={order_id} sin fill ni orden activa tras "
                          f"{age_days}d — descartando entrada "
                          f"(pos_actual={pos_actual} puede venir de otro origen "
                          f"no relacionado, p.ej. entrada nueva del escáner)",
                          symbol=sym)
                try:
                    send_telegram(
                        f"ℹ️ LIBERTAD_2045 — pending_rebalance: {accion} de {sym} "
                        f"nunca se ejecutó, orden {order_id} expirada/cancelada. "
                        f"Entrada pendiente descartada tras {age_days}d."
                    )
                except Exception:
                    pass
                _eliminar_pendiente_ampliar(sym)
                continue
            # Dentro del umbral de gracia: puede ser una cancelación muy
            # reciente aún no reflejada en fills()/trades() de la sesión.
            pendientes_procesados.add(sym)
            log_event("INFO",
                      f"pending_rebalance: {accion} {sym} sin rastro todavía "
                      f"de order_id={order_id} ({age_days if age_days is not None else '?'}d, "
                      f"umbral {PENDING_ORPHAN_THRESHOLD_DAYS}d) — esperando "
                      f"antes de declarar huérfana",
                      symbol=sym)
            continue

        if estado == "PENDIENTE_ACTIVA":
            pendientes_procesados.add(sym)
            log_event("INFO",
                      f"pending_rebalance: {accion} {sym} aún pendiente "
                      f"(order_id={order_id} sigue activo en IBKR) — "
                      f"omitiendo nuevo {accion} este ciclo",
                      symbol=sym)
            continue

        # estado == "NO_VERIFICABLE": entrada legacy sin order_id, o error
        # leyendo fills/trades/reqExecutions — fallback al comportamiento
        # previo a este fix, comparando solo símbolo/cantidad.
        if order_id is None:
            log_event("WARN",
                      f"pending_rebalance: entrada legacy de {sym} sin "
                      f"order_id (guardada antes del fix 11/08/2026) — no se "
                      f"puede verificar el origen de la posición, usando "
                      f"comparación de cantidad como fallback",
                      symbol=sym)
        else:
            log_event("WARN",
                      f"pending_rebalance: no se pudo verificar order_id="
                      f"{order_id} para {sym} este ciclo (error leyendo "
                      f"fills/trades/reqExecutions en IBKR) — usando "
                      f"comparación de cantidad como fallback por este ciclo",
                      symbol=sym)
        if accion == "REDUCIR":
            if pos_actual <= shares_esperadas:
                log_event("INFO",
                          f"pending_rebalance: REDUCIR confirmado {sym} "
                          f"(pos={pos_actual} <= esperadas={shares_esperadas}) "
                          f"— actualizando stop GTC",
                          symbol=sym)
                _actualizar_stop_tras_reduccion(ib, sym, pos_actual)
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

    return pendientes_procesados


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
    # H-4: Procesar órdenes AMPLIAR/REDUCIR pendientes de ciclos anteriores.
    # Ver _reconciliar_pendientes() — verifica contra el order_id real de
    # IBKR, no solo contra una coincidencia de símbolo/cantidad (fix
    # incidente DVN, 10-11/08/2026).
    # --------------------------------------------------

    pendientes_procesados = _reconciliar_pendientes(ib, mode)

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

            # Precio de entrada real estimado para un eventual AMPLIAR — el
            # mismo buy-stop (high + buffer) que usa libertad2045.py para
            # entradas nuevas, no el cierre de _precio_cierre_reciente()
            # (Hallazgo MEDIA #5, auditoría 07/08/2026: el AMPLIAR se
            # transmite como orden MKT que puede ejecutarse horas después,
            # en la siguiente apertura — el cierre de anoche no es su precio
            # real). Solo se usa para el chequeo de apalancamiento más abajo;
            # el sizing del AMPLIAR ya queda corregido dentro de
            # calcular_posicion() (position_size.py). Fallback defensivo a
            # `precio` si high viene NaN (dato de mercado incompleto) — no
            # bloquea el ciclo por esto, pero deja constancia en el log.
            high_hoy = df["high"].iloc[-1]
            if pd.isna(high_hoy):
                log_event("WARN",
                          f"Rebalanceo: high de hoy no disponible para {symbol} — "
                          f"chequeo de apalancamiento del AMPLIAR usa el cierre como fallback",
                          symbol=symbol)
                precio_entrada_ampliar = precio
            else:
                precio_entrada_ampliar = round(high_hoy + ENTRY_BUFFER, 2)

            # Auto-crear stop GTC si la posición no tiene protección activa
            if symbol not in stops_gtc and mode in ("PAPER", "LIVE"):
                log_event("WARN",
                          f"Posición sin stop GTC — calculando y colocando automáticamente",
                          symbol=symbol)
                df_sym = (datos or {}).get(symbol)
                if df_sym is None:
                    df_sym = obtener_datos(ib, symbol)
                if df_sym is not None:
                    nuevo_stop, mult = calcular_trailing_stop(df_sym, symbol=symbol)
                    if nuevo_stop and nuevo_stop > 0:
                        # Guard: no crear si ya existe stop activo (puede pasar tras reinicio)
                        if _hay_stop_gtc_activo(ib, symbol, nuevo_stop, int(abs(pos.position))):
                            log_event("WARN",
                                      f"Auto-GTC omitido — ya existe stop GTC activo para {symbol}",
                                      symbol=symbol)
                        else:
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

                    # DECISIÓN A-1 (documentada 18/06/2026, actualizada 22/08/2026 —
                    # CRÍTICA #1 auditoría 07/08/2026): AMPLIAR se permite incluso con
                    # Risk Guardian activo (drawdown > 10%) porque no abre exposición nueva,
                    # solo ajusta el tamaño de una posición existente que ya superó el filtro
                    # de riesgo en su entrada original. Eso sigue sin cambiar — drawdown NO
                    # bloquea AMPLIAR. Lo que sí se comprueba ahora es el apalancamiento:
                    # rebalancear() corre ANTES que risk_check() en el ciclo (libertad2045.py)
                    # y nunca ve su veredicto, así que un AMPLIAR podía antes ejecutarse por
                    # encima de MAX_LEVERAGE sin ningún control (Artículo II). Se comprueba
                    # aquí mismo, justo antes de transmitir, con la misma lógica del punto 5
                    # de risk_check() (ver verificar_apalancamiento_ampliar en
                    # risk_guardian.py) — fail-safe: si no se puede leer el dato de cuenta,
                    # se bloquea este AMPLIAR por precaución. REDUCIR nunca pasa por aquí:
                    # reduce exposición, nunca la aumenta.
                    #
                    # exposicion_adicional usa precio_entrada_ampliar (high + buffer,
                    # calculado más arriba), no `precio` (cierre reciente) — Hallazgo
                    # MEDIA #5, auditoría 07/08/2026: la orden AMPLIAR es MKT y puede
                    # ejecutarse horas después en la siguiente apertura, no al cierre
                    # de anoche. Sin este cambio el apalancamiento proyectado se
                    # subestima justo en el caso que este chequeo existe para atrapar.
                    if accion_orden == "BUY":
                        exposicion_adicional = shares_abs * precio_entrada_ampliar
                        permitido, motivo_leverage, leverage_proy = verificar_apalancamiento_ampliar(
                            ib, exposicion_adicional=exposicion_adicional
                        )
                        if not permitido:
                            log_event("WARN",
                                      f"Rebalanceo AMPLIAR omitido para {symbol} — {motivo_leverage}",
                                      symbol=symbol)
                            try:
                                send_telegram(
                                    f"⚠️ LIBERTAD_2045 — AMPLIAR omitido para {symbol}: "
                                    f"{motivo_leverage}"
                                )
                            except Exception:
                                pass
                            decision.motivo += f" [AMPLIAR omitido: {motivo_leverage}]"
                            decisiones.append(decision)
                            continue

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
                        # H-4: persistir pendiente para seguimiento entre ciclos,
                        # con el order_id real para poder verificar más tarde
                        # que el fill que la resuelve viene de ESTA orden
                        # concreta (fix incidente DVN, 10-11/08/2026).
                        order_id_ajuste = getattr(trade_ajuste.order, "orderId", None)
                        if decision.accion == "AMPLIAR":
                            _guardar_pendiente_ampliar(
                                symbol, decision.shares_optimo, decision.shares_delta,
                                order_id=order_id_ajuste
                            )
                        elif decision.accion == "REDUCIR":
                            _guardar_pendiente_reducir(
                                symbol, decision.shares_optimo, decision.shares_delta,
                                order_id=order_id_ajuste
                            )
                        # Orden pendiente: no tocar el stop GTC hasta que se ejecute
                        decisiones.append(decision)
                        continue

                    # Reemplazar stop GTC con la nueva cantidad (solo si ya filled).
                    # Usar calcular_trailing_stop (misma función que el trailing normal)
                    # para garantizar que TRAILING_FACTOR=0.75 se aplica también aquí.
                    shares_nuevas = decision.shares_optimo
                    stop_price_nuevo, _ = calcular_trailing_stop(df, symbol=symbol)

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
                            # M-1: no bajar el stop por debajo del nivel GTC ya activo en IBKR.
                            # El trailing stop solo puede subir, nunca bajar.
                            stop_actual_gtc = None
                            if stops_gtc.get(symbol):
                                stop_actual_gtc = getattr(
                                    stops_gtc[symbol].order, "auxPrice", None
                                )
                            if stop_actual_gtc and stop_price_nuevo < stop_actual_gtc:
                                log_event("INFO",
                                          f"Rebalanceo: stop calculado ({stop_price_nuevo:.2f}) < "
                                          f"stop activo en IBKR ({stop_actual_gtc:.2f}) para {symbol} "
                                          f"— stop no bajado",
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


# --------------------------------------------------
# Reconciliación de stops GTC duplicados
# --------------------------------------------------

def reconciliar_stops_gtc(ib, mode: str = "PAPER") -> int:
    """
    Detecta y elimina stops GTC duplicados para cualquier posición.

    Para cada símbolo con más de un stop GTC activo simultáneo:
        · Conserva el stop de precio mayor (más favorable para la posición larga:
          requiere una bajada mayor para activarse, preservando más beneficio).
        · Cancela todos los demás, con log completo de cada cancelación.

    Criterio «precio mayor»: consistente con la lógica ya aplicada en
    _obtener_gtc_stops() y evaluar_stops_por_cierre() cuando detectan duplicados.

    Se debe llamar:
        1. Al arranque del bot, antes de evaluar_stops_por_cierre().
        2. Desde el watchdog como parte de check_ordenes_gtc().

    Retorna el número de stops cancelados (0 = sin duplicados).
    """
    try:
        ib.reqAllOpenOrders()
        ib.sleep(2)
    except Exception as e:
        log_event("ERROR", f"reconciliar_stops_gtc: error al solicitar órdenes: {e}")
        return 0

    # Agrupar todos los stops GTC activos por símbolo
    stops_por_simbolo: dict[str, list] = {}
    for trade in ib.trades():
        if (trade.order.orderType in ("STP", "TRAIL") and
                trade.order.action == "SELL" and
                trade.order.tif == "GTC" and
                trade.orderStatus.status in ("PreSubmitted", "Submitted")):
            sym = trade.contract.symbol
            stops_por_simbolo.setdefault(sym, []).append(trade)

    duplicados = {sym: trades for sym, trades in stops_por_simbolo.items()
                  if len(trades) > 1}

    if not duplicados:
        log_event("INFO", "Reconciliación GTC: sin duplicados detectados")
        return 0

    cancelados_total = 0

    for sym, trades in duplicados.items():
        # Ordenar de mayor a menor precio: el primero se conserva
        trades_ord = sorted(
            trades,
            key=lambda t: getattr(t.order, "auxPrice", 0) or 0,
            reverse=True,
        )
        conservar      = trades_ord[0]
        a_cancelar     = trades_ord[1:]
        precio_conserv = getattr(conservar.order, "auxPrice", 0) or 0

        log_event("CRITICAL",
                  f"Reconciliación GTC: {len(trades)} stops GTC para {sym} — "
                  f"conservando orderId={conservar.order.orderId} "
                  f"(precio={precio_conserv:.2f}), cancelando {len(a_cancelar)}",
                  symbol=sym)

        try:
            from telegram import send_telegram_critical
            send_telegram_critical(
                f"🔴 LIBERTAD_2045 — Reconciliación GTC: {sym} tenía "
                f"{len(trades)} stops activos. Conservando orderId="
                f"{conservar.order.orderId} @ {precio_conserv:.2f}. "
                f"Cancelando {len(a_cancelar)} duplicado(s)."
            )
        except Exception:
            pass

        for t in a_cancelar:
            precio_t = getattr(t.order, "auxPrice", 0) or 0
            log_event("INFO",
                      f"Reconciliación GTC: cancelando orderId={t.order.orderId} "
                      f"(precio={precio_t:.2f}, qty={int(t.order.totalQuantity)})",
                      symbol=sym)
            if mode in ("PAPER", "LIVE"):
                try:
                    ib.cancelOrder(t.order)
                    ib.sleep(1)
                    log_event("INFO",
                              f"Reconciliación GTC: orderId={t.order.orderId} cancelado OK",
                              symbol=sym)
                    cancelados_total += 1
                except Exception as e:
                    log_event("ERROR",
                              f"Reconciliación GTC: error cancelando orderId="
                              f"{t.order.orderId} para {sym}: {e}",
                              symbol=sym)
            else:
                log_event("SIM",
                          f"Reconciliación GTC [SIM]: orderId={t.order.orderId} "
                          f"no cancelado (modo simulación)",
                          symbol=sym)
                cancelados_total += 1

    log_event("INFO",
              f"Reconciliación GTC: {cancelados_total} stop(s) duplicado(s) cancelado(s)")
    return cancelados_total
