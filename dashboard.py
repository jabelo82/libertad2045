#!/usr/bin/env python3
"""
dashboard.py — Generador de dashboard visual para LIBERTAD_2045
Se ejecuta al final de cada ciclo operativo y sobreescribe dashboard.html.

Incluye la sección "// Composición de cartera" con datos en tiempo real
obtenidos de IB Gateway (puerto 4002).  Si IB no está disponible, usa
portfolio_cache.json como fallback.  Si tampoco existe, muestra la
sección vacía sin romper el resto del dashboard.
"""

import os
import re
import glob
import json
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# PRECIO ACTUAL VÍA YFINANCE
# ─────────────────────────────────────────────────────────────────────────────

def obtener_precio_yfinance(symbol):
    """Último precio de cierre para symbol via yfinance. Retorna float o None."""
    try:
        import yfinance as yf
        data = yf.download(symbol, period="2d", auto_adjust=True, progress=False)
        if not data.empty:
            return float(data["Close"].squeeze().iloc[-1])
    except Exception as e:
        print(f"[dashboard] yfinance: error obteniendo precio de {symbol}: {e}")
    return None


def _obtener_tipo_cambio_mercado():
    """
    Descarga el tipo de cambio EURUSD de yfinance (USD por 1 EUR).
    Retorna 0.0 si no está disponible; el llamador decide el fallback.
    """
    try:
        import yfinance as yf
        data = yf.download("EURUSD=X", period="2d", auto_adjust=True, progress=False)
        if not data.empty:
            val = float(data["Close"].squeeze().iloc[-1])
            if val > 0:
                return val
    except Exception:
        pass
    return 0.0


def _tipo_cambio(cartera):
    """
    Devuelve el tipo de cambio USD→EUR (USD por 1 EUR).
    Fuentes por prioridad:
      1. usd_per_eur guardado en cartera (calculado desde cuenta IB)
      2. EURUSD=X via yfinance
      3. 1.0 como fallback sin conversión
    """
    rate = cartera.get("usd_per_eur")
    if rate and rate > 0:
        return rate
    val = _obtener_tipo_cambio_mercado()
    return val if val > 0 else 1.0

LOG_DIR           = os.path.join(os.path.dirname(__file__), "logs")
OUTPUT            = os.path.join(os.path.dirname(__file__), "dashboard.html")
CAPITAL_PEAK_FILE = os.path.join(os.path.dirname(__file__), "capital_peak.txt")
PORTFOLIO_CACHE   = os.path.join(os.path.dirname(__file__), "portfolio_cache.json")

# Nueva etapa desde esta fecha — los logs anteriores se ignoran
RESET_DATE        = "2026-06-10"

# clientId exclusivo para el dashboard (bot=1, rebalancer=5, watchdog=6)
DASHBOARD_CLIENT_ID = 7

# Paleta de colores neón para el gráfico de composición (hasta 14 sectores)
PORTFOLIO_COLORS = [
    "#00c896",  # verde acento
    "#0088ff",  # azul acento
    "#ff6b35",  # naranja
    "#c678dd",  # violeta
    "#e06c75",  # rosa-rojo
    "#56b6c2",  # cian
    "#d19a66",  # ámbar dorado
    "#98c379",  # verde claro
    "#61afef",  # azul claro
    "#ffaa00",  # amarillo-naranja
    "#3a5068",  # gris azulado  ← CASH
    "#be5046",  # rojo oscuro   (posiciones extra)
    "#e5c07b",  # amarillo claro
    "#4dc0b5",  # turquesa
]


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE LOGS
# ─────────────────────────────────────────────────────────────────────────────

def leer_precios_salida():
    """
    Extrae precios de salida de los logs CSV.
    Fuentes:
      1. INFO "STOP ACTIVADO por cierre | SYMBOL | cierre=X.XX" — precio de cierre
         que activó la evaluación de salida al final de la sesión.
      2. TRADE_FILLED sin "BUY" en el evento — compatibilidad hacia atrás.
      3. TRADE_SOLD — fill de venta confirmado por IBKR (precio real de ejecución).
    Retorna {symbol: [(ts_str, precio_float), ...]} con todas las salidas conocidas,
    ordenadas cronológicamente por símbolo.
    """
    salidas = {}
    archivos = sorted(glob.glob(os.path.join(LOG_DIR, "LIBERTAD_*.csv")))
    for archivo in archivos:
        try:
            with open(archivo, encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    partes = linea.split(",", 7)
                    if len(partes) < 4:
                        continue
                    ts, level, event = partes[0], partes[1], partes[2]
                    symbol = partes[3].strip()
                    if not symbol:
                        continue

                    # Fuente 1: evaluación de stop al cierre de sesión
                    if level == "INFO":
                        m = re.search(
                            r"STOP ACTIVADO por cierre[^|]*\|[^|]*\| cierre=([0-9]+\.?[0-9]*)",
                            event,
                        )
                        if m:
                            try:
                                precio = float(m.group(1))
                                salidas.setdefault(symbol, []).append((ts, precio))
                            except ValueError:
                                pass

                    # Fuente 2: TRADE_FILLED SLD (fill de venta, si se loguea)
                    if level == "TRADE_FILLED" and "BUY" not in event.upper():
                        entry_col = partes[6].strip() if len(partes) > 6 else ""
                        if entry_col:
                            try:
                                precio = float(entry_col)
                                salidas.setdefault(symbol, []).append((ts, precio))
                            except ValueError:
                                pass

                    # Fuente 3: TRADE_SOLD — fill de venta confirmado por IBKR
                    if level == "TRADE_SOLD":
                        entry_col = partes[6].strip() if len(partes) > 6 else ""
                        if entry_col:
                            try:
                                precio = float(entry_col)
                                salidas.setdefault(symbol, []).append((ts, precio))
                            except ValueError:
                                pass
        except Exception:
            continue
    return salidas


def leer_logs():
    """Lee todos los CSVs y extrae métricas por sesión."""
    sesiones = []
    archivos = sorted(glob.glob(os.path.join(LOG_DIR, "LIBERTAD_*.csv")))

    for archivo in archivos:
        fecha = re.search(r"LIBERTAD_(\d{4}-\d{2}-\d{2})", archivo)
        if not fecha:
            continue
        fecha_str = fecha.group(1)
        if fecha_str < RESET_DATE:
            continue

        capital    = None
        drawdown   = None
        trades     = []
        señales    = 0
        posiciones = 0
        modo       = "PAPER"
        rg_status  = "OK"
        rg_bloqueo = None   # dict con causa y números exactos del bloqueo
        empresas   = None
        ts_start   = None   # timestamp SYSTEM_START para calcular runtime
        ts_end     = None   # timestamp SYSTEM_END
        _lev_tmp   = None   # buffer para línea "Apalancamiento actual" (pre-bloqueo)
        _dd_tmp    = None   # buffer para línea "Drawdown actual" (pre-bloqueo)

        try:
            with open(archivo, encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    partes = linea.split(",", 7)
                    if len(partes) < 3:
                        continue
                    ts, level, event = partes[0], partes[1], partes[2]
                    symbol = partes[3].strip() if len(partes) > 3 else ""
                    shares = partes[5] if len(partes) > 5 else ""
                    entry  = partes[6] if len(partes) > 6 else ""
                    stop   = partes[7] if len(partes) > 7 else ""

                    m = re.search(r"[Cc]apital disponible[:\s]+([0-9]+\.?[0-9]*)", event)
                    if m:
                        capital = float(m.group(1))

                    m = re.search(r"drawdown[:\s]+(-?[0-9]+\.?[0-9]*)%", event, re.IGNORECASE)
                    if m:
                        drawdown = float(m.group(1))

                    m = re.search(r"Modo[:\s]+(PAPER|LIVE|SIM)", event)
                    if m:
                        modo = m.group(1)

                    if level == "TRADE_FILLED" or "TRADE_EXECUTED" in event:
                        if symbol and shares:
                            try:
                                side = "SELL" if "SLD" in event.upper() else "BUY"
                                # Deduplicación de fills parciales por (fecha, symbol)
                                existing = next(
                                    (t for t in trades
                                     if t["ts"][:10] == ts[:10] and t["symbol"] == symbol
                                     and t.get("source") == "FILLED"),
                                    None
                                )
                                if existing is None:
                                    trades.append({"ts": ts, "symbol": symbol,
                                                   "shares": shares, "entry": entry,
                                                   "stop": stop, "source": "FILLED", "side": side})
                                else:
                                    try:
                                        existing["shares"] = str(int(existing["shares"]) + int(shares))
                                    except (ValueError, TypeError):
                                        pass
                                    if not existing["stop"] and stop:
                                        existing["stop"] = stop
                            except Exception:
                                pass
                    elif level == "TRADE":
                        if symbol and shares:
                            try:
                                side = "SELL" if "SLD" in event.upper() else "BUY"
                                key = (ts[:10], symbol, entry)
                                if key not in [(t["ts"][:10], t["symbol"], t["entry"]) for t in trades]:
                                    trades.append({"ts": ts, "symbol": symbol, "shares": shares,
                                                   "entry": entry, "stop": stop, "source": "ORDER",
                                                   "side": side})
                            except Exception:
                                pass

                    m = re.search(r"[Ss]eñales detectadas[:\s]+(\d+)", event)
                    if m:
                        señales = int(m.group(1))
                    m = re.search(r"Escaneo completado[:\s]+(\d+)\s+señales", event)
                    if m:
                        señales = int(m.group(1))
                    m = re.search(r"[Ss]eñales detectadas\s*:\s*(\d+)", event)
                    if m:
                        señales = int(m.group(1))

                    # Empresas escaneadas: "sobre N activos"
                    m = re.search(r"sobre\s+(\d+)\s+activos", event)
                    if m:
                        empresas = int(m.group(1))

                    m = re.search(r"[Pp]osiciones abiertas[:\s]+(\d+)", event)
                    if m:
                        posiciones = int(m.group(1))
                    m = re.search(r"[Pp]osiciones ocupadas[:\s]+(\d+)", event)
                    if m:
                        posiciones = int(m.group(1))
                    # Fallback: "Rebalanceo: evaluando N posiciones" (ciclos con RG bloqueado)
                    m = re.search(r"evaluando\s+(\d+)\s+posiciones", event)
                    if m:
                        posiciones = int(m.group(1))

                    # Runtime: diferencia SYSTEM_END − SYSTEM_START (runtime no se logea en CSV)
                    if "SYSTEM_START" in event and ts_start is None:
                        ts_start = ts
                    if event.strip() == "SYSTEM_END" and ts_end is None:
                        ts_end = ts

                    # ── Risk Guardian: buffers y detección de causa ──────────
                    m = re.search(
                        r"Apalancamiento actual:\s*([0-9.]+)x\s*"
                        r"\(exposición:\s*([0-9.]+)\s*\|\s*capital:\s*([0-9.]+)\)",
                        event,
                    )
                    if m:
                        _lev_tmp = {
                            "leverage":  float(m.group(1)),
                            "exposicion": float(m.group(2)),
                            "capital":   float(m.group(3)),
                        }

                    m = re.search(
                        r"Drawdown actual:\s*([0-9.]+)%\s*"
                        r"\(pico:\s*([0-9.]+)\s*\|\s*actual:\s*([0-9.]+)\)",
                        event,
                    )
                    if m:
                        _dd_tmp = {
                            "drawdown": float(m.group(1)),
                            "pico":     float(m.group(2)),
                            "actual":   float(m.group(3)),
                        }

                    m = re.search(
                        r"apalancamiento no permitido\s*\(([0-9.]+)x\s*>\s*límite\s*([0-9.]+)x\)",
                        event,
                    )
                    if m:
                        rg_bloqueo = {
                            "causa":           "apalancamiento",
                            "leverage":        float(m.group(1)),
                            "leverage_limite": float(m.group(2)),
                            "exposicion":      (_lev_tmp or {}).get("exposicion"),
                            "capital":         (_lev_tmp or {}).get("capital"),
                        }

                    m = re.search(
                        r"drawdown máximo superado\s*\(([0-9.]+)%\s*>\s*límite\s*([0-9.]+)%\)",
                        event,
                    )
                    if m:
                        rg_bloqueo = {
                            "causa":            "drawdown",
                            "drawdown":         float(m.group(1)),
                            "drawdown_limite":  float(m.group(2)),
                            "pico":             (_dd_tmp or {}).get("pico"),
                            "actual":           (_dd_tmp or {}).get("actual"),
                        }

                    m = re.search(
                        r"capital insuficiente\s*\(([0-9.]+)\s*<\s*mínimo\s*([0-9.]+)\)",
                        event,
                    )
                    if m:
                        rg_bloqueo = {
                            "causa":        "capital",
                            "capital_real": float(m.group(1)),
                            "capital_min":  float(m.group(2)),
                        }

                    if re.search(r"fuera de ventana horaria", event) and rg_bloqueo is None:
                        m_h = re.search(r"hora actual:\s*(\d+)h", event)
                        # "permitido:" puede quedar en otra columna si el campo CSV lleva comillas
                        # con coma interna — buscamos en la línea completa para capturarlo siempre.
                        m_r = re.search(r"permitido:\s*(\d+)-(\d+)h", linea)
                        rg_bloqueo = {
                            "causa":       "ventana_horaria",
                            "hora":        int(m_h.group(1)) if m_h else None,
                            "hora_inicio": int(m_r.group(1)) if m_r else None,
                            "hora_fin":    int(m_r.group(2)) if m_r else None,
                        }

                    if "Risk Guardian" in event and re.search(r"bloque[oó]", event):
                        rg_status = "BLOQUEADO"

        except Exception:
            continue

        # Calcular tiempo de ejecución desde timestamps si está disponible
        tiempo = None
        if ts_start and ts_end:
            try:
                fmt = "%Y-%m-%d %H:%M:%S"
                delta = datetime.strptime(ts_end, fmt) - datetime.strptime(ts_start, fmt)
                tiempo = int(delta.total_seconds())
            except Exception:
                pass

        if capital:
            sesiones.append({
                "fecha": fecha_str, "capital": capital, "drawdown": drawdown,
                "trades": trades, "señales": señales, "posiciones": posiciones,
                "modo": modo, "rg_status": rg_status, "rg_bloqueo": rg_bloqueo,
                "tiempo": tiempo, "empresas": empresas,
            })

    return sesiones


def calcular_stats(sesiones):
    if not sesiones:
        return {}

    capitales       = [s["capital"] for s in sesiones]
    capital_actual  = capitales[-1]
    capital_inicial = capitales[0]
    capital_pico    = max(capitales)
    rentabilidad    = (capital_actual - capital_inicial) / capital_inicial * 100
    dd_actual       = (capital_actual - capital_pico) / capital_pico * 100
    total_trades    = sum(len(s["trades"]) for s in sesiones)
    total_señales   = sum(s["señales"] for s in sesiones)

    return {
        "capital_actual":  capital_actual,
        "capital_inicial": capital_inicial,
        "capital_pico":    capital_pico,
        "rentabilidad":    rentabilidad,
        "dd_actual":       dd_actual,
        "total_trades":    total_trades,
        "total_señales":   total_señales,
        "sesiones":        len(sesiones),
        "ultima_fecha":    sesiones[-1]["fecha"],
        "modo":            sesiones[-1]["modo"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# DATOS DE CARTERA  (IB Gateway → caché → vacío)
# ─────────────────────────────────────────────────────────────────────────────

def _precios_ib(ib, symbols):
    """
    Obtiene precios de cierre recientes para una lista de símbolos usando
    una conexión IB ya abierta. Retorna {symbol: precio} con los que tenga éxito.
    """
    from ib_insync import Stock
    precios = {}
    for sym in symbols:
        try:
            contract = Stock(sym, "SMART", "USD")
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
                precios[sym] = bars[-1].close
        except Exception as e:
            print(f"[dashboard] IB: error obteniendo precio de {sym}: {e}")
    return precios


def obtener_cartera_ib(extra_symbols=None):
    """
    Conecta a IB Gateway (puerto configurable, por defecto 4002) con un
    clientId exclusivo para el dashboard y obtiene:
        - Posiciones largas con valor de mercado en EUR
        - Cash disponible en EUR
        - Capital total (NetLiquidation) en EUR
        - Precios actuales para extra_symbols (lista de tickers de trades)

    El EUR/USD implícito se deriva de los totales de cuenta para que
    los valores de cada posición sean coherentes con el capital real.

    Retorna un dict listo para guardar en caché, o None si falla.
    """
    extra_symbols = extra_symbols or []

    try:
        from ib_insync import IB
    except ImportError:
        print("[dashboard] ib_insync no disponible — saltando lectura IB")
        return None

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))

    ib = IB()
    try:
        ib.connect(host, port, clientId=DASHBOARD_CLIENT_ID, timeout=10)
        if not ib.isConnected():
            print(f"[dashboard] IB Gateway no conectado ({host}:{port})")
            return None

        # ── Account summary ──────────────────────────────────────────────────
        # IBKR devuelve cada tag en varias divisas (BASE, EUR, USD…).
        # Para NetLiquidation, BASE y EUR son equivalentes (total en moneda base).
        # Para TotalCashValue, BASE = neto total (puede ser negativo por deuda USD);
        # EUR = solo el componente en EUR — valor diferente y engañoso si se usa solo.
        # Estrategia: para TotalCashValue preferir BASE explícitamente; aceptar EUR
        # solo como fallback si BASE no aparece en la respuesta.
        _BASE_CURRENCIES = {"BASE", "EUR"}
        resumen = {}
        for item in ib.accountSummary():
            if item.tag == "NetLiquidation" and item.currency in _BASE_CURRENCIES:
                resumen["NetLiquidation"] = float(item.value)
            elif item.tag == "TotalCashValue" and item.currency in _BASE_CURRENCIES:
                if item.currency == "BASE" or "TotalCashValue" not in resumen:
                    resumen["TotalCashValue"] = float(item.value)

        total_eur = resumen.get("NetLiquidation", 0.0)
        cash_eur  = resumen.get("TotalCashValue",  0.0)

        if total_eur <= 0:
            print("[dashboard] NetLiquidation no disponible o cero")
            return None

        # ── Posiciones largas ─────────────────────────────────────────────────
        positions = [p for p in ib.positions() if p.position > 0]

        # Stops GTC activos (se obtiene antes del early-return para tenerlos en ambos paths)
        # reqAllOpenOrders devuelve órdenes de TODOS los clientes de la cuenta,
        # no solo las de esta conexión (clientId=7). Sin esto, las órdenes del
        # bot (clientId=1) son invisibles y stops_actuales queda vacío.
        stops_actuales = {}
        try:
            all_orders = ib.reqAllOpenOrders()
            for t in all_orders:
                if (t.order.action == 'SELL' and
                        t.order.orderType == 'STP' and
                        t.order.tif == 'GTC' and
                        t.orderStatus.status not in ('Cancelled', 'Filled', 'Inactive')):
                    sym   = t.contract.symbol.strip()
                    precio = round(t.order.auxPrice, 2)
                    # Si hay órdenes duplicadas para el mismo símbolo, conservar
                    # el stop más alto (el más favorable para una posición larga)
                    if sym not in stops_actuales or precio > stops_actuales[sym]:
                        stops_actuales[sym] = precio
            n_stops = len(stops_actuales)
            if n_stops:
                print(f'[dashboard] Stops GTC: {n_stops} órdenes activas')
        except Exception as e:
            print(f'[dashboard] Error leyendo stops GTC: {e}')

        if not positions:
            # Intentar precios de trades aunque no haya posiciones abiertas
            precios_trades = _precios_ib(ib, extra_symbols)
            return {
                "timestamp":      datetime.now().isoformat(),
                "capital_eur":    total_eur,
                "labels":         ["CASH"],
                "values_eur":     [round(cash_eur)],
                "fuente":         "ibkr",
                "usd_per_eur":    1.0,
                "precios_trades": precios_trades,
                "stops_actuales": stops_actuales,
            }

        # ── Precio de cierre reciente para cada posición ─────────────────────
        labels     = []
        values_usd = []

        for pos in positions:
            symbol = pos.contract.symbol
            shares = int(pos.position)

            precio = None
            try:
                bars = ib.reqHistoricalData(
                    pos.contract,
                    endDateTime="",
                    durationStr="2 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    keepUpToDate=False,
                )
                if bars:
                    precio = bars[-1].close
            except Exception as e:
                print(f"[dashboard] Error obteniendo precio de {symbol}: {e}")

            if precio is None:
                # Fallback: usar coste medio como proxy de precio
                precio = pos.avgCost if pos.avgCost > 0 else 0

            if precio > 0:
                labels.append(symbol)
                values_usd.append(shares * precio)

        # ── Conversión USD → EUR: tipo de mercado real (yfinance) ───────────
        # total_stocks_eur = GrossPositionValue cuando cash_eur es el neto BASE
        total_stocks_usd = sum(values_usd)
        total_stocks_eur = total_eur - cash_eur

        usd_per_eur = _obtener_tipo_cambio_mercado()
        if usd_per_eur <= 0:
            # Fallback: tipo derivado de los totales de cuenta (menos preciso)
            if total_stocks_usd > 0 and total_stocks_eur > 0:
                usd_per_eur = total_stocks_usd / total_stocks_eur
                print(f"[dashboard] WARN: yfinance no disponible — tipo derivado: {usd_per_eur:.4f}")
            else:
                usd_per_eur = 1.0
                print("[dashboard] WARN: tipo de cambio no disponible — usando 1.0 como fallback")

        values_eur = [round(v / usd_per_eur) for v in values_usd]

        # cash_eur puede ser negativo cuando hay deuda en USD: se preserva sin forzar a cero
        labels.append("CASH")
        values_eur.append(round(cash_eur))

        # ── Precios para símbolos de trades (mientras IB está conectado) ──────
        precios_trades = _precios_ib(ib, extra_symbols)

        return {
            "timestamp":      datetime.now().isoformat(),
            "capital_eur":    total_eur,
            "labels":         labels,
            "values_eur":     values_eur,
            "fuente":         "ibkr",
            "usd_per_eur":    usd_per_eur,
            "precios_trades": precios_trades,
            "stops_actuales": stops_actuales,
        }

    except Exception as e:
        print(f"[dashboard] Excepción obteniendo cartera de IB: {e}")
        return None

    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass


def guardar_cache_cartera(cartera):
    """Persiste los datos de cartera en disco para usarlos como fallback.
    Los precios de trades son efímeros y no se guardan en caché."""
    try:
        to_save = {k: v for k, v in cartera.items() if k != "precios_trades"}
        with open(PORTFOLIO_CACHE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[dashboard] Error guardando caché de cartera: {e}")


def leer_cache_cartera():
    """Carga la cartera desde el fichero de caché local."""
    try:
        if os.path.exists(PORTFOLIO_CACHE):
            with open(PORTFOLIO_CACHE, encoding="utf-8") as f:
                data = json.load(f)
            data["fuente"] = "cache"
            return data
    except Exception as e:
        print(f"[dashboard] Error leyendo caché de cartera: {e}")
    return None


def leer_cartera(extra_symbols=None):
    """
    Prioridad: IB Gateway en vivo → caché en disco → sección vacía.
    Guarda el resultado en caché siempre que los datos vengan de IB.
    Para extra_symbols (tickers de trades), obtiene precios actuales:
      - Vía IB si está disponible (en la misma conexión)
      - Vía yfinance como fallback (cache o sin IB)
    """
    print("[dashboard] Obteniendo composición de cartera...")
    extra_symbols = extra_symbols or []

    cartera = obtener_cartera_ib(extra_symbols)
    if cartera:
        guardar_cache_cartera(cartera)
        n_pos = len(cartera["labels"])
        print(f"[dashboard] Cartera IB: {n_pos} sectores | "
              f"capital={cartera['capital_eur']:,.0f} €")
        return cartera

    cartera = leer_cache_cartera()
    if cartera:
        ts = cartera.get("timestamp", "")[:10]
        print(f"[dashboard] Usando caché de cartera (fecha: {ts})")
        # Obtener precios de trades via yfinance (IB no disponible)
        precios = {}
        for sym in extra_symbols:
            p = obtener_precio_yfinance(sym)
            if p is not None:
                precios[sym] = p
        cartera["precios_trades"] = precios
        return cartera

    print("[dashboard] Sin datos de cartera — sección vacía")
    return {"timestamp": "", "capital_eur": 0.0,
            "labels": [], "values_eur": [], "fuente": "vacio",
            "usd_per_eur": 1.0, "precios_trades": {}, "stops_actuales": {}}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS HTML / CSS / JS  (devuelven strings planos — sin f-string interno)
# ─────────────────────────────────────────────────────────────────────────────

def _portfolio_css():
    """CSS para el gráfico de composición de cartera."""
    return """
  /* PORTFOLIO PIE */
  .portfolio-layout { display: flex; gap: 40px; align-items: center; }
  .pie-wrap {
    position: relative;
    width: 420px; height: 420px;
    flex-shrink: 0;
  }
  .pie-center {
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
    pointer-events: none;
  }
  .pie-center-value {
    font-family: var(--mono);
    font-size: 20px; font-weight: 700;
    color: var(--text); white-space: nowrap;
  }
  .pie-center-label {
    font-family: var(--mono); font-size: 11px;
    color: var(--muted); letter-spacing: 1px;
    text-transform: uppercase; margin-top: 4px;
  }
  .portfolio-legend {
    flex: 1;
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px;
  }
  .legend-item {
    display: flex; align-items: center; gap: 10px;
    font-family: var(--mono); font-size: 13px;
    padding: 7px 10px; border-radius: 3px;
    transition: background .15s; cursor: default;
  }
  .legend-item:hover { background: rgba(0,200,150,.06); }
  .legend-dot  { width: 11px; height: 11px; border-radius: 2px; flex-shrink: 0; }
  .legend-ticker { color: var(--text); font-weight: 700; min-width: 44px; }
  .legend-value  { color: var(--muted); flex: 1; }
  .legend-pct    { color: var(--accent); min-width: 46px; text-align: right; }
  @media (max-width: 768px) {
    .portfolio-layout  { flex-direction: column; }
    .pie-wrap          { width: 300px; height: 300px; }
    .portfolio-legend  { grid-template-columns: 1fr; width: 100%; }
  }"""


def _portfolio_html(cartera):
    """Bloque HTML de la sección '// Composición de cartera'."""
    if cartera["fuente"] == "vacio":
        return """
  <!-- COMPOSICIÓN DE CARTERA — sin datos -->
  <div class="section">
    <div class="section-header" onclick="toggle('cartera')">
      <div class="section-title">// Composición de cartera</div>
      <button class="toggle-btn" id="btn-cartera">▼ ocultar</button>
    </div>
    <div class="collapsible" id="cartera" style="max-height:80px">
      <div class="chart-box">
        <div class="chart-title">Sin datos de posiciones disponibles</div>
        <p style="font-family:var(--mono);font-size:11px;color:var(--muted);padding:4px 0">
          IB Gateway no disponible y sin caché local.
        </p>
      </div>
    </div>
  </div>"""

    total_eur  = sum(cartera["values_eur"])
    fuente_txt = ""
    if cartera["fuente"] == "cache":
        ts = cartera.get("timestamp", "")[:10]
        fuente_txt = f" · caché {ts}"

    titulo       = f"{total_eur:,.0f} €{fuente_txt}"
    center_value = f"{total_eur:,.0f} €"

    return (
        "\n  <!-- COMPOSICIÓN DE CARTERA -->\n"
        "  <div class=\"section\">\n"
        "    <div class=\"section-header\" onclick=\"toggle('cartera')\">\n"
        "      <div class=\"section-title\">// Composición de cartera</div>\n"
        "      <button class=\"toggle-btn\" id=\"btn-cartera\">▼ ocultar</button>\n"
        "    </div>\n"
        "    <div class=\"collapsible\" id=\"cartera\" style=\"max-height:700px\">\n"
        "      <div class=\"chart-box\">\n"
        f"        <div class=\"chart-title\">Distribución del capital — {titulo}</div>\n"
        "        <div class=\"portfolio-layout\">\n"
        "          <div class=\"pie-wrap\">\n"
        "            <canvas id=\"chartCartera\"></canvas>\n"
        "            <div class=\"pie-center\">\n"
        f"              <div class=\"pie-center-value\" id=\"pieHoverValue\">{center_value}</div>\n"
        "              <div class=\"pie-center-label\" id=\"pieHoverLabel\">capital total</div>\n"
        "            </div>\n"
        "          </div>\n"
        "          <div class=\"portfolio-legend\" id=\"portfolioLegend\"></div>\n"
        "        </div>\n"
        "      </div>\n"
        "    </div>\n"
        "  </div>"
    )


def _portfolio_js(cartera):
    """
    Bloque JavaScript para el gráfico doughnut.
    Devuelve string plano (sin f-string) para no interferir con las
    llaves {{ }} del f-string principal del HTML completo.
    """
    if cartera["fuente"] == "vacio":
        return ""

    labels_js = json.dumps(cartera["labels"])
    values_js = json.dumps(cartera["values_eur"])

    # Asignar colores cíclicamente si hay más sectores que colores
    n      = len(cartera["labels"])
    colors = (PORTFOLIO_COLORS * ((n // len(PORTFOLIO_COLORS)) + 1))[:n]
    # El último sector es siempre CASH → gris azulado si positivo, rojo si negativo
    if cartera["labels"] and cartera["labels"][-1] == "CASH":
        cash_val = cartera["values_eur"][-1] if cartera["values_eur"] else 0
        colors[-1] = "#e05c5c" if cash_val < 0 else "#3a5068"
    colors_js = json.dumps(colors)

    # ── Construir el JS sin f-string (evita conflictos con {{ }} del padre) ──
    lines = [
        "",
        "// ── PORTFOLIO PIE CHART ────────────────────────────────────────────",
        "const portfolio = {",
        "  labels: " + labels_js + ",",
        "  values: " + values_js + ",",
        "  colors: " + colors_js,
        "};",
        "",
        "// pieData: valores >= 0 para Chart.js (no soporta segmentos negativos)",
        "// pieTotal: suma real incluyendo cash negativo = NetLiquidation",
        "const pieData  = portfolio.values.map(v => Math.max(0, v));",
        "const pieTotal = portfolio.values.reduce((a, b) => a + b, 0);",
        "const fmt = v => v.toLocaleString('es-ES') + ' \u20ac';",
        "",
        "const pieValue = document.getElementById('pieHoverValue');",
        "const pieLabel = document.getElementById('pieHoverLabel');",
        "",
        "const chartCartera = new Chart(document.getElementById('chartCartera'), {",
        "  type: 'doughnut',",
        "  data: {",
        "    labels: portfolio.labels,",
        "    datasets: [{",
        "      data: pieData,",
        "      backgroundColor: portfolio.colors,",
        "      borderColor: '#080c10',",
        "      borderWidth: 2,",
        "      hoverBorderColor: 'rgba(255,255,255,0.6)',",
        "      hoverBorderWidth: 2,",
        "      hoverOffset: 10",
        "    }]",
        "  },",
        "  options: {",
        "    responsive: true,",
        "    maintainAspectRatio: false,",
        "    cutout: '58%',",
        "    animation: { animateRotate: true, duration: 700 },",
        "    plugins: {",
        "      legend: { display: false },",
        "      tooltip: {",
        "        callbacks: {",
        "          label: ctx => {",
        "            const pct = (ctx.raw / pieTotal * 100).toFixed(2);",
        r"            return `  ${fmt(ctx.raw)}  \u00b7  ${pct}%`;",
        "          }",
        "        },",
        "        backgroundColor: '#0d1117',",
        "        borderColor: '#1e2a38',",
        "        borderWidth: 1,",
        "        titleColor: '#e0e8f0',",
        "        bodyColor: '#e0e8f0',",
        "        titleFont: { family: \"'Space Mono', monospace\", size: 11, weight: '700' },",
        "        bodyFont:  { family: \"'Space Mono', monospace\", size: 11 },",
        "        padding: 12,",
        "        displayColors: true,",
        "        boxWidth: 10, boxHeight: 10",
        "      }",
        "    },",
        "    onHover: (evt, elements) => {",
        "      if (elements.length) {",
        "        const i = elements[0].index;",
        "        const pct = (portfolio.values[i] / pieTotal * 100).toFixed(2);",
        "        pieValue.textContent = fmt(portfolio.values[i]);",
        "        pieLabel.textContent = portfolio.labels[i] + ' \u00b7 ' + pct + '%';",
        "      } else {",
        "        pieValue.textContent = fmt(pieTotal);",
        "        pieLabel.textContent = 'capital total';",
        "      }",
        "    }",
        "  }",
        "});",
        "",
        "// Leyenda personalizada",
        "const legendEl = document.getElementById('portfolioLegend');",
        "portfolio.labels.forEach((label, i) => {",
        "  const isDebt = portfolio.values[i] < 0;",
        "  const pct = (portfolio.values[i] / Math.abs(pieTotal) * 100).toFixed(2);",
        "  const item = document.createElement('div');",
        "  item.className = 'legend-item';",
        "  const valStyle = isDebt ? ' style=\"color:#e05c5c\"' : '';",
        "  const pctStyle = isDebt ? ' style=\"color:#e05c5c\"' : '';",
        "  item.innerHTML =",
        r'    `<div class="legend-dot" style="background:${portfolio.colors[i]}"></div>` +',
        r'    `<span class="legend-ticker">${label}</span>` +',
        r'    `<span class="legend-value"${valStyle}>${fmt(portfolio.values[i])}</span>` +',
        r'    `<span class="legend-pct"${pctStyle}>${pct}%</span>`;',
        "  item.addEventListener('mouseenter', () => {",
        "    pieValue.textContent = fmt(portfolio.values[i]);",
        "    pieLabel.textContent = label + ' \u00b7 ' + pct + '%';",
        "    chartCartera.tooltip.setActiveElements([{ datasetIndex: 0, index: i }], { x: 0, y: 0 });",
        "    chartCartera.update('none');",
        "  });",
        "  item.addEventListener('mouseleave', () => {",
        "    pieValue.textContent = fmt(pieTotal);",
        "    pieLabel.textContent = 'capital total';",
        "    chartCartera.tooltip.setActiveElements([], {});",
        "    chartCartera.update('none');",
        "  });",
        "  legendEl.appendChild(item);",
        "});",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SECCIÓN RISK GUARDIAN BLOQUEADO
# ─────────────────────────────────────────────────────────────────────────────

_RG_MAX_VISIBLE = 5   # episodios recientes visibles al expandir sin "ver todos"


def _rg_bloqueo_css():
    return """
  /* RISK GUARDIAN BLOQUEADO */
  .rg-resumen {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.5px;
    padding: 4px 0 16px;
  }
  .rg-banner {
    background: rgba(255,170,0,.07);
    border: 1px solid rgba(255,170,0,.35);
    border-left: 3px solid var(--warn);
    border-radius: 4px;
    padding: 14px 20px;
    margin-bottom: 8px;
  }
  .rg-banner-title {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--warn);
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 10px;
  }
  .rg-detail {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text);
    display: flex;
    flex-wrap: wrap;
    gap: 6px 24px;
  }
  .rg-kv { display: flex; gap: 6px; align-items: baseline; }
  .rg-key { color: var(--muted); font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }
  .rg-val { color: var(--warn); font-weight: 700; }
  .rg-val.danger { color: var(--danger); }"""


def _rg_detalle_html(b):
    """Genera el bloque de detalles para un bloqueo según su causa."""
    causa = b.get("causa", "desconocida")

    if causa == "apalancamiento":
        lev      = b.get("leverage")
        lim      = b.get("leverage_limite")
        exp      = b.get("exposicion")
        cap      = b.get("capital")
        lev_str  = f"{lev:.2f}x" if lev is not None else "—"
        lim_str  = f"{lim:.2f}x" if lim is not None else "—"
        exp_str  = f"{exp:,.2f} €" if exp is not None else "—"
        cap_str  = f"{cap:,.2f} €" if cap is not None else "—"
        return (
            f'<span class="rg-kv"><span class="rg-key">Causa</span>'
            f'<span class="rg-val danger">Apalancamiento</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Leverage</span>'
            f'<span class="rg-val danger">{lev_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Límite</span>'
            f'<span class="rg-val">{lim_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Exposición</span>'
            f'<span class="rg-val">{exp_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Capital</span>'
            f'<span class="rg-val">{cap_str}</span></span>'
        )

    if causa == "drawdown":
        dd   = b.get("drawdown")
        lim  = b.get("drawdown_limite")
        pico = b.get("pico")
        act  = b.get("actual")
        dd_str  = f"{dd:.2f}%" if dd is not None else "—"
        lim_str = f"{lim:.2f}%" if lim is not None else "—"
        pico_str = f"{pico:,.2f} €" if pico is not None else "—"
        act_str  = f"{act:,.2f} €" if act is not None else "—"
        return (
            f'<span class="rg-kv"><span class="rg-key">Causa</span>'
            f'<span class="rg-val danger">Drawdown máximo</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Drawdown</span>'
            f'<span class="rg-val danger">{dd_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Límite</span>'
            f'<span class="rg-val">{lim_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Capital pico</span>'
            f'<span class="rg-val">{pico_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Capital actual</span>'
            f'<span class="rg-val">{act_str}</span></span>'
        )

    if causa == "capital":
        cap_r = b.get("capital_real")
        cap_m = b.get("capital_min")
        cap_r_str = f"{cap_r:,.2f} €" if cap_r is not None else "—"
        cap_m_str = f"{cap_m:,.2f} €" if cap_m is not None else "—"
        return (
            f'<span class="rg-kv"><span class="rg-key">Causa</span>'
            f'<span class="rg-val danger">Capital mínimo</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Capital actual</span>'
            f'<span class="rg-val danger">{cap_r_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Mínimo requerido</span>'
            f'<span class="rg-val">{cap_m_str}</span></span>'
        )

    if causa == "ventana_horaria":
        hora      = b.get("hora")
        h_ini     = b.get("hora_inicio")
        h_fin     = b.get("hora_fin")
        hora_str  = f"{hora}h" if hora is not None else "—"
        rango_str = f"{h_ini}h-{h_fin}h" if h_ini is not None else "—"
        return (
            f'<span class="rg-kv"><span class="rg-key">Causa</span>'
            f'<span class="rg-val">Fuera de ventana</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Hora ejecución</span>'
            f'<span class="rg-val">{hora_str}</span></span>'
            f'<span class="rg-kv"><span class="rg-key">Ventana permitida</span>'
            f'<span class="rg-val">{rango_str}</span></span>'
        )

    return f'<span class="rg-kv"><span class="rg-key">Causa</span><span class="rg-val">{causa}</span></span>'


def _rg_banner(s):
    """Genera el HTML de un banner individual de bloqueo."""
    b = s.get("rg_bloqueo") or {}
    detalle = _rg_detalle_html(b) if b else (
        '<span class="rg-kv"><span class="rg-key">Causa</span>'
        '<span class="rg-val">—</span></span>'
    )
    return (
        f'      <div class="rg-banner">\n'
        f'        <div class="rg-banner-title">{s["fecha"]}</div>\n'
        f'        <div class="rg-detail">{detalle}</div>\n'
        f'      </div>\n'
    )


def _rg_seccion_html(sesiones):
    """Genera la sección Risk Guardian: colapsada por defecto, resumen agregado arriba,
    N episodios recientes visibles al expandir y el resto bajo un segundo toggle."""
    bloqueadas = [s for s in sesiones if s.get("rg_status") == "BLOQUEADO"]
    if not bloqueadas:
        return ""

    n = len(bloqueadas)
    titulo = f"⚠ Risk Guardian — {n} sesión{'es' if n > 1 else ''} bloqueada{'s' if n > 1 else ''}"

    # ── Resumen por causa ────────────────────────────────────────────────────
    conteo = {"apalancamiento": 0, "drawdown": 0, "capital": 0,
               "ventana_horaria": 0, "otro": 0}
    for s in bloqueadas:
        causa = (s.get("rg_bloqueo") or {}).get("causa", "otro")
        conteo[causa] = conteo.get(causa, 0) + 1

    etiquetas = [
        ("apalancamiento",  "Apalancamiento"),
        ("drawdown",        "Drawdown"),
        ("capital",         "Capital mínimo"),
        ("ventana_horaria", "Fuera de ventana"),
        ("otro",            "Otro"),
    ]
    partes = [f"{label}: {conteo[k]}" for k, label in etiquetas if conteo.get(k)]
    resumen_html = (
        f'      <div class="rg-resumen">{" · ".join(partes)}</div>\n'
        if partes else ""
    )

    # ── Episodios más reciente primero ───────────────────────────────────────
    desc = list(reversed(bloqueadas))
    recientes  = desc[:_RG_MAX_VISIBLE]
    historicos = desc[_RG_MAX_VISIBLE:]

    filas_recientes = "".join(_rg_banner(s) for s in recientes)

    # Bloque "ver históricos" solo si hay más de N
    if historicos:
        n_hist = len(historicos)
        filas_hist = "".join(_rg_banner(s) for s in historicos)
        ver_todos = (
            f'      <div style="padding:4px 0 12px">\n'
            f'        <button class="toggle-btn" id="btn-rg-historicos" '
            f'onclick="toggle(\'rg-historicos\')" '
            f'style="font-size:10px;color:var(--muted)">'
            f'▶ ver {n_hist} episodio{"s" if n_hist > 1 else ""} anterior{"es" if n_hist > 1 else ""}'
            f'</button>\n'
            f'      </div>\n'
            f'      <div class="collapsible collapsed" id="rg-historicos" '
            f'style="max-height:9999px">\n'
            f'{filas_hist}'
            f'      </div>\n'
        )
    else:
        ver_todos = ""

    return (
        "\n  <!-- RISK GUARDIAN BLOQUEADO -->\n"
        "  <div class=\"section\">\n"
        f"    <div class=\"section-header\" onclick=\"toggle('rg-bloqueado')\">\n"
        f"      <div class=\"section-title\" style=\"color:var(--warn)\">{titulo}</div>\n"
        f"      <button class=\"toggle-btn\" id=\"btn-rg-bloqueado\" "
        f"style=\"border-color:rgba(255,170,0,.4);color:var(--warn)\">▶ mostrar</button>\n"
        "    </div>\n"
        "    <div class=\"collapsible collapsed\" id=\"rg-bloqueado\" style=\"max-height:9999px\">\n"
        f"{resumen_html}"
        f"{filas_recientes}"
        f"{ver_todos}"
        "    </div>\n"
        "  </div>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR HTML
# ─────────────────────────────────────────────────────────────────────────────

def _tipo_cell(side):
    if side == "SELL":
        return '<td><span style="color:#ff4455">▼ Venta</span></td>'
    return '<td><span style="color:#00c896">▲ Compra</span></td>'


def generar_html(sesiones, stats, cartera, precios_trades=None, usd_per_eur=1.0, stops_actuales=None, precios_salida=None):
    fechas_json    = json.dumps([s["fecha"]     for s in sesiones])
    capitales_json = json.dumps([s["capital"]   for s in sesiones])
    señales_json   = json.dumps([s["señales"]   for s in sesiones])
    posiciones_json = json.dumps([s["posiciones"] for s in sesiones])

    precios_trades = precios_trades or {}

    # Índice FILLED por símbolo y fecha: {sym: [ts, ...]}
    # Un ORDER es duplicado del FILLED si hay un FILLED del mismo símbolo
    # dentro de los 5 días siguientes (misma operación, fill confirmado al día siguiente)
    fills_por_simbolo_fecha: "dict[str, list[str]]" = {}
    for s in sesiones:
        for t in s["trades"]:
            if t.get("source") == "FILLED":
                sym = t["symbol"].strip()
                fills_por_simbolo_fecha.setdefault(sym, []).append(t["ts"][:10])

    def es_orden_duplicada(t):
        """True si hay un TRADE_FILLED para el mismo símbolo en los 5 días siguientes."""
        if t.get("source") != "ORDER":
            return False
        sym = t["symbol"].strip()
        fills = fills_por_simbolo_fecha.get(sym, [])
        if not fills:
            return False
        order_date = t["ts"][:10]
        from datetime import datetime, timedelta
        try:
            dt_order = datetime.strptime(order_date, "%Y-%m-%d")
        except ValueError:
            return False
        for fill_date in fills:
            try:
                dt_fill = datetime.strptime(fill_date, "%Y-%m-%d")
                if timedelta(0) <= dt_fill - dt_order <= timedelta(days=5):
                    return True
            except ValueError:
                continue
        return False

    # ── TRADES ACTUALES: desde cartera IBKR ──────────────────────────────────
    # Índice del trade más reciente por símbolo (para entry, shares, stop inicial)
    # Preferimos FILLED sobre ORDER para el mismo símbolo
    ultimo_trade = {}
    for s in sesiones:
        for t in s['trades']:
            if es_orden_duplicada(t):
                continue
            sym = t["symbol"].strip()
            if sym not in ultimo_trade or t["ts"] > ultimo_trade[sym]["ts"]:
                ultimo_trade[sym] = t

    posiciones_abiertas = [sym for sym in (cartera.get("labels") or []) if sym != "CASH"]
    filas_trades_abiertos = ""
    for sym in posiciones_abiertas:
        t = ultimo_trade.get(sym, {})
        precio_actual = precios_trades.get(sym)
        precio_actual_str = f"{precio_actual:.2f}" if precio_actual is not None else "—"
        _sa = (stops_actuales or {}).get(sym)
        entrada = t.get("entry", "—") or "—"
        shares  = t.get("shares", "—") or "—"
        fecha   = (t.get("ts") or "")[:10] or "—"
        stop_inicial_str = (t.get("stop") or "").strip() or "—"

        try:
            _entrada_f = float(entrada)
        except (ValueError, TypeError):
            _entrada_f = None

        if _sa is not None and _entrada_f:
            _color = "#00c896" if _sa > _entrada_f else "inherit"
            stop_salida_str = f'<span style="color:{_color}">{_sa:.2f}</span>'
        else:
            stop_salida_str = "—"

        pnl_cell = "—"
        pct_cell = "—"
        try:
            shares_f = float(shares)
            if precio_actual is not None and _entrada_f and _entrada_f > 0:
                pnl_usd  = (precio_actual - _entrada_f) * shares_f
                pnl_eur  = pnl_usd / usd_per_eur
                pct      = (precio_actual - _entrada_f) / _entrada_f * 100
                color    = "#00c896" if pnl_eur >= 0 else "#ff4455"
                pnl_cell = f'<span style="color:{color}">{pnl_eur:+,.0f} €</span>'
                pct_cell = f'<span style="color:{color}">{pct:+.2f}%</span>'
        except (ValueError, TypeError, ZeroDivisionError):
            pass

        filas_trades_abiertos += f"""
            <tr data-date="{fecha}">
                <td>{fecha}</td>
                <td><strong>{sym}</strong></td>
                {_tipo_cell(t.get("side", "BUY"))}
                <td class="num">{shares}</td>
                <td class="num">{entrada}</td>
                <td class="num">{precio_actual_str}</td>
                <td class="num">{stop_inicial_str}</td>
                <td class="num">{stop_salida_str}</td>
                <td class="num">{pnl_cell}</td>
                <td class="num">{pct_cell}</td>
            </tr>"""

    if not filas_trades_abiertos:
        filas_trades_abiertos = ('<tr><td colspan="10" style="text-align:center;opacity:.5">'
                                 'Sin posiciones abiertas</td></tr>')

    # ── TRADES EJECUTADOS: desde logs, solo cerrados ──────────────────────────
    filas_trades_cerrados = ""
    for s in reversed(sesiones):
        for t in s['trades']:
            if es_orden_duplicada(t):
                continue
            _sym = t["symbol"].strip()
            _sa  = (stops_actuales or {}).get(_sym)
            try:
                _entrada_f = float(t["entry"]) if t.get("entry") else None
            except (ValueError, TypeError):
                _entrada_f = None

            # Buscar exit price siempre — un trade cerrado puede tener aún
            # un stop GTC stale en stops_actuales si no se canceló al vender
            _exit_precio = None
            _salidas_sym = (precios_salida or {}).get(_sym, [])
            if _salidas_sym and t.get("ts"):
                for _sal_ts, _sal_precio in _salidas_sym:
                    if _sal_ts > t["ts"]:
                        _exit_precio = _sal_precio
                        break

            if _exit_precio is None:
                continue  # sin precio de salida → posición abierta o sin datos

            _color = "#ff4455" if _exit_precio < (_entrada_f or _exit_precio) else "#00c896"
            stop_salida_str = f'<span style="color:{_color}">{_exit_precio:.2f}</span>'
            stop_inicial_str = t["stop"].strip() if t.get("stop", "").strip() else "—"
            precio_actual = precios_trades.get(_sym)
            precio_actual_str = f"{precio_actual:.2f}" if precio_actual is not None else "—"

            pnl_cell = "—"
            pct_cell = "—"
            try:
                entrada_f = float(t["entry"])
                shares_f  = float(t["shares"])
                if entrada_f > 0:
                    pnl_usd  = (_exit_precio - entrada_f) * shares_f
                    pnl_eur  = pnl_usd / usd_per_eur
                    pct      = (_exit_precio - entrada_f) / entrada_f * 100
                    color    = "#00c896" if pnl_eur >= 0 else "#ff4455"
                    pnl_cell = f'<span style="color:{color}">{pnl_eur:+,.0f} €</span>'
                    pct_cell = f'<span style="color:{color}">{pct:+.2f}%</span>'
            except (ValueError, TypeError, ZeroDivisionError):
                pass

            filas_trades_cerrados += f"""
            <tr data-date="{t['ts'][:10]}">
                <td>{t['ts'][:10]}</td>
                <td><strong>{t['symbol']}</strong></td>
                {_tipo_cell(t.get("side", "BUY"))}
                <td class="num">{t['shares']}</td>
                <td class="num">{t['entry']}</td>
                <td class="num">{precio_actual_str}</td>
                <td class="num">{stop_inicial_str}</td>
                <td class="num">{stop_salida_str}</td>
                <td class="num">{pnl_cell}</td>
                <td class="num">{pct_cell}</td>
            </tr>"""

    if not filas_trades_cerrados:
        filas_trades_cerrados = ('<tr><td colspan="10" style="text-align:center;opacity:.5">'
                                 'Sin trades cerrados registrados</td></tr>')

    rentabilidad_color = "#00ff88" if stats.get("rentabilidad", 0) >= 0 else "#ff4444"
    dd_color = ("#ff4444" if stats.get("dd_actual", 0) < -5
                else "#ffaa00" if stats.get("dd_actual", 0) < 0
                else "#00ff88")

    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Bloques de cartera (strings planos — sin f-string internos)
    portfolio_css  = _portfolio_css()
    portfolio_html = _portfolio_html(cartera)
    portfolio_js   = _portfolio_js(cartera)

    # Sección Risk Guardian bloqueado
    rg_css     = _rg_bloqueo_css()
    rg_seccion = _rg_seccion_html(sesiones)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LIBERTAD_2045 — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root {{
    --bg: #080c10;
    --surface: #0d1117;
    --border: #1e2a38;
    --accent: #00c896;
    --accent2: #0088ff;
    --warn: #ffaa00;
    --danger: #ff4455;
    --text: #e0e8f0;
    --muted: #4a6070;
    --mono: 'Space Mono', monospace;
    --sans: 'Syne', sans-serif;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
    padding: 0;
  }}

  /* HEADER */
  .header {{
    border-bottom: 1px solid var(--border);
    padding: 24px 40px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header-left {{ display: flex; align-items: center; gap: 16px; }}
  .logo {{
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 2px;
    text-transform: uppercase;
  }}
  .mode-badge {{
    font-family: var(--mono);
    font-size: 10px;
    padding: 3px 10px;
    border-radius: 2px;
    background: rgba(0,200,150,.15);
    color: var(--accent);
    border: 1px solid rgba(0,200,150,.3);
    letter-spacing: 1px;
  }}
  .last-update {{
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
  }}

  /* LAYOUT */
  .main {{ padding: 32px 40px; max-width: 1400px; margin: 0 auto; }}

  /* KPI GRID */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .kpi {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 20px 24px;
    position: relative;
    overflow: hidden;
  }}
  .kpi::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent);
  }}
  .kpi.warn::before {{ background: var(--warn); }}
  .kpi.danger::before {{ background: var(--danger); }}
  .kpi-label {{
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .kpi-value {{
    font-family: var(--mono);
    font-size: 26px;
    font-weight: 700;
    line-height: 1;
  }}
  .kpi-sub {{
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    margin-top: 6px;
  }}

  /* SECCIÓN */
  .section {{ margin-bottom: 32px; }}
  .section-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    cursor: pointer;
    user-select: none;
  }}
  .section-title {{
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--accent);
  }}
  .toggle-btn {{
    font-family: var(--mono);
    font-size: 11px;
    background: none;
    border: 1px solid var(--border);
    padding: 4px 12px;
    border-radius: 2px;
    cursor: pointer;
    color: var(--text);
    transition: border-color .2s;
  }}
  .toggle-btn:hover {{ border-color: var(--accent); }}
  .collapsible {{ overflow: hidden; transition: max-height .3s ease; }}
  .collapsible.collapsed {{ max-height: 0 !important; }}

  /* CHARTS */
  .chart-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }}
  .chart-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 20px;
  }}
  .chart-title {{
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 16px;
  }}
  .chart-wrap {{ position: relative; height: 220px; }}
{portfolio_css}
{rg_css}

  /* FILTER BUTTONS */
  .filter-bar {{
    display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;
    align-items: center;
  }}
  .filter-btn {{
    font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
    padding: 4px 12px; border-radius: 2px; cursor: pointer;
    background: none; border: 1px solid var(--border); color: var(--muted);
    transition: all .15s; text-transform: uppercase;
  }}
  .filter-btn:hover, .filter-btn.active {{
    border-color: var(--accent); color: var(--accent);
    background: rgba(0,200,150,.08);
  }}

  /* TABLA */
  .table-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: auto;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: var(--mono);
    font-size: 12px;
  }}
  thead th {{
    background: #0a1520;
    padding: 10px 16px;
    text-align: left;
    font-size: 10px;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  tbody tr {{ border-bottom: 1px solid rgba(30,42,56,.5); transition: background .15s; }}
  tbody tr:hover {{ background: rgba(0,200,150,.04); }}
  tbody td {{ padding: 10px 16px; }}
  .num {{ text-align: right; }}

  .badge {{
    font-size: 9px;
    padding: 2px 8px;
    border-radius: 2px;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-weight: 700;
  }}
  .badge.ok {{ background: rgba(0,200,150,.15); color: var(--accent); }}
  .badge.warn {{ background: rgba(255,68,85,.15); color: var(--danger); }}

  /* FOOTER */
  .footer {{
    text-align: center;
    padding: 24px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    border-top: 1px solid var(--border);
    letter-spacing: 1px;
  }}

  @media (max-width: 768px) {{
    .main {{ padding: 16px; }}
    .header {{ padding: 16px; }}
    .chart-grid {{ grid-template-columns: 1fr; }}
    .kpi-value {{ font-size: 20px; }}
  }}
</style>
</head>
<body>

<header class="header">
  <div class="header-left">
    <div class="logo">⚙ LIBERTAD_2045</div>
    <div class="mode-badge">{stats.get('modo','PAPER')}</div>
  </div>
  <div class="last-update">Actualizado: {now}</div>
</header>

<main class="main">

  <!-- KPIs -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Capital actual</div>
      <div class="kpi-value" style="color:var(--accent)">{stats.get('capital_actual',0):,.0f} €</div>
      <div class="kpi-sub">Pico: {stats.get('capital_pico',0):,.0f} €</div>
    </div>
    <div class="kpi {'danger' if stats.get('rentabilidad',0) < 0 else ''}">
      <div class="kpi-label">Rentabilidad</div>
      <div class="kpi-value" style="color:{rentabilidad_color}">{stats.get('rentabilidad',0):+.2f}%</div>
      <div class="kpi-sub">Desde inicio PAPER</div>
    </div>
    <div class="kpi {'danger' if stats.get('dd_actual',0) < -5 else 'warn' if stats.get('dd_actual',0) < 0 else ''}">
      <div class="kpi-label">Drawdown actual</div>
      <div class="kpi-value" style="color:{dd_color}">{stats.get('dd_actual',0):.2f}%</div>
      <div class="kpi-sub">Límite: -10.00%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Sesiones</div>
      <div class="kpi-value">{stats.get('sesiones',0)}</div>
      <div class="kpi-sub">Última: {stats.get('ultima_fecha','—')}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Trades ejecutados</div>
      <div class="kpi-value">{stats.get('total_trades',0)}</div>
      <div class="kpi-sub">Señales detectadas: {stats.get('total_señales',0)}</div>
    </div>
  </div>
{rg_seccion}

  <!-- GRÁFICAS -->
  <div class="section">
    <div class="section-header" onclick="toggle('charts')">
      <div class="section-title">// Gráficas</div>
      <button class="toggle-btn" id="btn-charts">▼ ocultar</button>
    </div>
    <div class="collapsible" id="charts" style="max-height:700px">
      <div class="filter-bar" id="chart-filter-bar">
        <span style="font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:1px">PERÍODO:</span>
        <button class="filter-btn active" onclick="filtrarGraficas(0,this)">MÁX</button>
        <button class="filter-btn" onclick="filtrarGraficas(365,this)">1A</button>
        <button class="filter-btn" onclick="filtrarGraficas(90,this)">3M</button>
        <button class="filter-btn" onclick="filtrarGraficas(30,this)">1M</button>
        <button class="filter-btn" onclick="filtrarGraficas(7,this)">1S</button>
      </div>
      <div class="chart-grid">
        <div class="chart-box">
          <div class="chart-title">Evolución del capital</div>
          <div class="chart-wrap"><canvas id="chartCapital"></canvas></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">Señales por sesión</div>
          <div class="chart-wrap"><canvas id="chartSenales"></canvas></div>
        </div>
      </div>
      <div class="chart-grid" style="grid-template-columns:1fr 1fr">
        <div class="chart-box">
          <div class="chart-title">Posiciones abiertas por sesión</div>
          <div class="chart-wrap"><canvas id="chartPosiciones"></canvas></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">Drawdown desde pico</div>
          <div class="chart-wrap"><canvas id="chartDD"></canvas></div>
        </div>
      </div>
    </div>
  </div>
{portfolio_html}

  <!-- TABLA TRADES ACTUALES -->
  <div class="section">
    <div class="section-header" onclick="toggle('trades-abiertos')">
      <div class="section-title">// Trades actuales</div>
      <button class="toggle-btn" id="btn-trades-abiertos">▼ ocultar</button>
    </div>
    <div class="collapsible" id="trades-abiertos" style="max-height:9999px">
      <div class="table-wrap" style="max-height:520px;overflow-y:auto">
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Símbolo</th>
              <th>Tipo</th>
              <th class="num">Acciones</th>
              <th class="num">Entrada</th>
              <th class="num">Precio actual</th>
              <th class="num">Stop inicial</th>
              <th class="num">Stop / Salida</th>
              <th class="num">PnL (€)</th>
              <th class="num">%</th>
            </tr>
          </thead>
          <tbody id="trades-abiertos-tbody">{filas_trades_abiertos}</tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TABLA TRADES EJECUTADOS (CERRADOS) -->
  <div class="section">
    <div class="section-header" onclick="toggle('trades')">
      <div class="section-title">// Trades ejecutados</div>
      <button class="toggle-btn" id="btn-trades">▼ ocultar</button>
    </div>
    <div class="collapsible" id="trades" style="max-height:9999px">
      <div class="filter-bar">
        <span style="font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:1px">PERÍODO:</span>
        <button class="filter-btn active" onclick="filtrarTrades(0,this)">Todo</button>
        <button class="filter-btn" onclick="filtrarTrades(90,this)">3 meses</button>
        <button class="filter-btn" onclick="filtrarTrades(30,this)">1 mes</button>
        <button class="filter-btn" onclick="filtrarTrades(7,this)">1 semana</button>
      </div>
      <div class="table-wrap" style="max-height:520px;overflow-y:auto">
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Símbolo</th>
              <th>Tipo</th>
              <th class="num">Acciones</th>
              <th class="num">Entrada</th>
              <th class="num">Precio actual</th>
              <th class="num">Stop inicial</th>
              <th class="num">Stop / Salida</th>
              <th class="num">PnL (€)</th>
              <th class="num">%</th>
            </tr>
          </thead>
          <tbody id="trades-tbody">{filas_trades_cerrados}</tbody>
        </table>
      </div>
    </div>
  </div>

</main>

<footer class="footer">
  LIBERTAD_2045 · Sistema autónomo de trading · Objetivo 2045
</footer>

<script>
const fechas = {fechas_json};
const capitales = {capitales_json};
const senales = {señales_json};
const posiciones = {posiciones_json};

// Drawdown desde pico
const dds = capitales.map((c, i) => {{
  const pico = Math.max(...capitales.slice(0, i+1));
  return pico > 0 ? ((c - pico) / pico * 100) : 0;
}});

const gridColor = 'rgba(30,42,56,0.8)';
const tickColor = '#4a6070';

Chart.defaults.color = tickColor;
Chart.defaults.font.family = "'Space Mono', monospace";
Chart.defaults.font.size = 10;

function makeChart(id, type, labels, data, color, fill=false) {{
  return new Chart(document.getElementById(id), {{
    type,
    data: {{
      labels,
      datasets: [{{
        data,
        borderColor: color,
        backgroundColor: fill ? color.replace(')', ', 0.15)').replace('rgb', 'rgba') : 'transparent',
        borderWidth: 1.5,
        pointRadius: 3,
        pointHoverRadius: 5,
        fill,
        tension: 0.3
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: gridColor }}, ticks: {{ maxRotation: 45 }} }},
        y: {{ grid: {{ color: gridColor }} }}
      }}
    }}
  }});
}}

let chartCapital    = makeChart('chartCapital',    'line', fechas, capitales,  'rgb(0,200,150)', true);
let chartSenales    = makeChart('chartSenales',    'bar',  fechas, senales,    'rgb(0,136,255)');
let chartPosiciones = makeChart('chartPosiciones', 'line', fechas, posiciones, 'rgb(255,170,0)', true);

let chartDD = new Chart(document.getElementById('chartDD'), {{
  type: 'line',
  data: {{
    labels: fechas,
    datasets: [{{
      data: dds,
      borderColor: 'rgb(255,68,85)',
      backgroundColor: 'rgba(255,68,85,0.1)',
      borderWidth: 1.5,
      pointRadius: 3,
      fill: true,
      tension: 0.3
    }},
    {{
      data: Array(fechas.length).fill(-10),
      borderColor: 'rgba(255,68,85,0.4)',
      borderWidth: 1,
      borderDash: [4,4],
      pointRadius: 0,
      fill: false,
      label: 'Límite -10%'
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: gridColor }}, ticks: {{ maxRotation: 45 }} }},
      y: {{ grid: {{ color: gridColor }} }}
    }}
  }}
}});

function filtrarGraficas(dias, btn) {{
  document.querySelectorAll('#chart-filter-bar .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  let f, c, s, p, d;
  if (dias === 0) {{
    f = fechas; c = capitales; s = senales; p = posiciones; d = dds;
  }} else {{
    const cutoff = new Date(Date.now() - dias * 86400000).toISOString().slice(0, 10);
    const idx = fechas.reduce((acc, fecha, i) => {{ if (fecha >= cutoff) acc.push(i); return acc; }}, []);
    f = idx.map(i => fechas[i]);
    c = idx.map(i => capitales[i]);
    s = idx.map(i => senales[i]);
    p = idx.map(i => posiciones[i]);
    d = idx.map(i => dds[i]);
  }}
  chartCapital.data.labels    = f; chartCapital.data.datasets[0].data    = c; chartCapital.update();
  chartSenales.data.labels    = f; chartSenales.data.datasets[0].data    = s; chartSenales.update();
  chartPosiciones.data.labels = f; chartPosiciones.data.datasets[0].data = p; chartPosiciones.update();
  chartDD.data.labels = f;
  chartDD.data.datasets[0].data = d;
  chartDD.data.datasets[1].data = Array(f.length).fill(-10);
  chartDD.update();
}}

function filtrarTrades(dias, btn) {{
  document.querySelectorAll('#trades .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const rows = document.querySelectorAll('#trades-tbody tr');
  const cutoff = dias ? new Date(Date.now() - dias * 86400000).toISOString().slice(0, 10) : null;
  rows.forEach(r => {{
    r.style.display = (!cutoff || (r.dataset.date && r.dataset.date >= cutoff)) ? '' : 'none';
  }});
}}

{portfolio_js}

// Collapsibles
function toggle(id) {{
  const el = document.getElementById(id);
  const btn = document.getElementById('btn-' + id);
  if (el.classList.contains('collapsed')) {{
    el.classList.remove('collapsed');
    btn.textContent = '▼ ocultar';
  }} else {{
    el.classList.add('collapsed');
    btn.textContent = '▶ mostrar';
  }}
}}
</script>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("[dashboard] Generando dashboard...")

    sesiones = leer_logs()
    if not sesiones:
        stats = {
            "capital_actual":  8000.0,
            "capital_inicial": 8000.0,
            "capital_pico":    8000.0,
            "rentabilidad":    0.0,
            "dd_actual":       0.0,
            "total_trades":    0,
            "total_señales":   0,
            "sesiones":        0,
            "ultima_fecha":    "—",
            "modo":            "PAPER",
        }
        print(f"[dashboard] Sin sesiones desde {RESET_DATE} — capital inicial 8.000 €")
    else:
        stats = calcular_stats(sesiones)

    # Recopilar símbolos únicos de todos los trades para obtener precios actuales
    symbols_trades = list({t["symbol"] for s in sesiones for t in s["trades"] if t["symbol"]})
    if symbols_trades:
        print(f"[dashboard] Símbolos en trades: {', '.join(sorted(symbols_trades))}")

    cartera       = leer_cartera(extra_symbols=symbols_trades)
    precios_trades = cartera.get("precios_trades", {})
    usd_per_eur   = _tipo_cambio(cartera)

    if precios_trades:
        print(f"[dashboard] Precios obtenidos para: {', '.join(sorted(precios_trades))}")

    stops_actuales = cartera.get("stops_actuales", {})
    precios_salida = leer_precios_salida()
    html = generar_html(sesiones, stats, cartera, precios_trades, usd_per_eur, stops_actuales, precios_salida)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    fuente_txt = {"ibkr": "IB live", "cache": "caché", "vacio": "sin datos"}
    print(f"[dashboard] Dashboard generado: {OUTPUT}")
    print(f"[dashboard] Capital: {stats['capital_actual']:,.2f} € | "
          f"Sesiones: {stats['sesiones']} | "
          f"Cartera: {fuente_txt.get(cartera['fuente'], '?')} "
          f"({len(cartera['labels'])} sectores)")


if __name__ == "__main__":
    main()
