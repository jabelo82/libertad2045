import os
from datetime import datetime
from pathlib import Path

from logger import log_event

_PROJECT_DIR = Path(__file__).resolve().parent

# --------------------------------------------------
# Umbrales de riesgo
# Configurables desde variables de entorno
# --------------------------------------------------

MIN_CAPITAL        = float(os.getenv("RISK_MIN_CAPITAL",    "2000"))  # Capital mínimo operativo  # Ver también config.py — MIN_CAPITAL
MAX_DRAWDOWN_PCT   = float(os.getenv("RISK_MAX_DRAWDOWN",   "0.10"))  # Drawdown máximo: 10%  # Ver también config.py — MAX_DRAWDOWN
PEAK_FILE          = os.getenv("RISK_PEAK_FILE", str(_PROJECT_DIR / "capital_peak.txt"))
MAX_LEVERAGE       = float(os.getenv("RISK_MAX_LEVERAGE",   "1.00"))  # Apalancamiento máximo: 100%  # Ver también config.py — MAX_LEVERAGE

# Ventana horaria de operación (hora local del servidor)
HOUR_START         = int(os.getenv("RISK_HOUR_START", "21"))  # Ver también config.py — HORA_INICIO
HOUR_END           = int(os.getenv("RISK_HOUR_END",   "23"))  # Ver también config.py — HORA_FIN
 
 
def _leer_capital_pico(capital_actual=None):
    """
    Lee el capital pico registrado en disco.
    Si el archivo no existe, devuelve None.
    Si capital_actual se proporciona y el pico es más del doble, alerta de posible archivo stale.
    """
    try:
        path = Path(PEAK_FILE)
        if path.exists():
            pico = float(path.read_text().strip())
            if capital_actual is not None and pico > capital_actual * 2:
                log_event("WARN",
                          f"capital_peak.txt ({pico:.2f}) es más del doble del capital actual "
                          f"({capital_actual:.2f}) — posible archivo stale de otra cuenta. "
                          f"Verificar antes de operar en LIVE.")
            return pico
    except Exception as e:
        log_event("WARN", f"No se pudo leer capital pico: {e}")
    return None
 
 
def _guardar_capital_pico(capital):
    """
    Guarda el capital pico en disco si es mayor que el registrado.
    """
    try:
        pico_actual = _leer_capital_pico()
        if pico_actual is None or capital > pico_actual:
            Path(PEAK_FILE).write_text(str(capital))
            log_event("INFO", f"Nuevo capital pico registrado: {capital:.2f}")
    except Exception as e:
        log_event("WARN", f"No se pudo guardar capital pico: {e}")
 
 
def risk_check(ib):
    """
    Evalúa las condiciones de seguridad antes de permitir la operación.
 
    Comprobaciones en orden:
        1. Conexión activa con IBKR
        2. Ventana horaria de operación permitida
        3. Capital mínimo operativo
        4. Drawdown máximo desde el capital pico
        5. Apalancamiento: exposición total <= capital real (sin margen)
 
    Retorna True si todas las condiciones se cumplen.
    Retorna False y registra el motivo si alguna falla.
    """
 
    # --------------------------------------------------
    # 1. Conexión activa
    # --------------------------------------------------
 
    if not ib.isConnected():
        log_event("WARN", "Risk Guardian: IBKR no conectado")
        return False, "IBKR no conectado"
 
 
    # --------------------------------------------------
    # 2. Ventana horaria
    # --------------------------------------------------
 
    hour = datetime.now().hour
    force_bypass = os.getenv("FORCE_HOUR_BYPASS", "false").lower() == "true"
 
    if not force_bypass and not (HOUR_START <= hour <= HOUR_END):
        log_event("WARN", f"Risk Guardian: fuera de ventana horaria "
                           f"(hora actual: {hour}h, permitido: {HOUR_START}-{HOUR_END}h)")
        return False, f"fuera de ventana horaria ({hour}h, permitido {HOUR_START}-{HOUR_END}h)"
 
    if force_bypass:
        log_event("INFO", f"Risk Guardian: bypass horario activado (hora actual: {hour}h)")
 
 
    # --------------------------------------------------
    # 3. Capital de la cuenta
    # --------------------------------------------------
 
    try:
        account   = ib.accountSummary()
        net_liq   = None
        gross_pos = None
 
        # Filtramos por moneda base para evitar comparar divisas distintas.
        # IBKR puede devolver el mismo tag en varias monedas (p.ej. USD para el
        # componente en USD y EUR/BASE para el total en moneda base de la cuenta).
        # Aceptamos "BASE" (valor documentado en el API TWS) y "EUR" (código real
        # devuelto en cuentas con base EUR según la implementación observada).
        _BASE_CURRENCIES = {"BASE", "EUR"}

        for item in account:
            if item.tag == "NetLiquidation" and item.currency in _BASE_CURRENCIES:
                net_liq = float(item.value)
            elif item.tag == "GrossPositionValue" and item.currency in _BASE_CURRENCIES:
                gross_pos = float(item.value)
 
    except Exception as e:
        log_event("ERROR", f"Risk Guardian: error leyendo cuenta IBKR: {e}")
        return False, f"error leyendo cuenta IBKR: {e}"
 
    if net_liq is None:
        log_event("ERROR", "Risk Guardian: NetLiquidation[BASE/EUR] no encontrado en "
                           "accountSummary — bloqueando por precaución (fail-safe)")
        return False, "NetLiquidation no disponible (fail-safe)"
 
    if net_liq < MIN_CAPITAL:
        log_event("WARN", f"Risk Guardian: capital insuficiente "
                           f"({net_liq:.2f} < mínimo {MIN_CAPITAL:.2f})")
        return False, f"capital insuficiente ({net_liq:.2f}€ < mínimo {MIN_CAPITAL:.2f}€)"
 
 
    # --------------------------------------------------
    # 4. Drawdown máximo desde capital pico
    # --------------------------------------------------
 
    capital_pico = _leer_capital_pico(capital_actual=net_liq)
    _guardar_capital_pico(net_liq)

    if capital_pico is None:
        log_event("WARN", "capital_peak.txt no disponible -- usando capital actual como pico")
        try:
            from telegram import send_telegram
            send_telegram("WARNING LIBERTAD_2045 - capital_peak.txt no disponible. Drawdown no calculable este ciclo.")
        except Exception:
            pass
        capital_pico = net_liq
 
    if capital_pico > 0:
 
        drawdown = (capital_pico - net_liq) / capital_pico
 
        log_event("INFO", f"Drawdown actual: {drawdown:.2%} "
                           f"(pico: {capital_pico:.2f} | actual: {net_liq:.2f})")
 
        if drawdown > MAX_DRAWDOWN_PCT:
            log_event("WARN", f"Risk Guardian: drawdown máximo superado "
                               f"({drawdown:.2%} > límite {MAX_DRAWDOWN_PCT:.2%}). "
                               f"Entradas bloqueadas.")
            return False, f"drawdown {drawdown:.2%} > límite {MAX_DRAWDOWN_PCT:.2%} (pico {capital_pico:.2f}€ | actual {net_liq:.2f}€)"
 
 
    # --------------------------------------------------
    # 5. Control de apalancamiento
    # El sistema opera exclusivamente con capital propio.
    # La exposición total (GrossPositionValue) nunca puede
    # superar el capital real de la cuenta (NetLiquidation).
    # Si no se puede leer GrossPositionValue se permite
    # continuar pero se registra un aviso.
    # --------------------------------------------------
 
    if gross_pos is not None and net_liq > 0:
 
        leverage = gross_pos / net_liq
 
        log_event("INFO", f"Apalancamiento actual: {leverage:.2f}x "
                           f"(exposición: {gross_pos:.2f} | capital: {net_liq:.2f})")
 
        if leverage > MAX_LEVERAGE:
            log_event("WARN", f"Risk Guardian: apalancamiento no permitido "
                               f"({leverage:.2f}x > límite {MAX_LEVERAGE:.2f}x). "
                               f"LIBERTAD_2045 opera exclusivamente con capital propio. "
                               f"Entradas bloqueadas.")
            return False, f"apalancamiento {leverage:.2f}x > límite {MAX_LEVERAGE:.2f}x (exposición {gross_pos:.2f}€ | capital {net_liq:.2f}€)"
 
    else:
        log_event("ERROR", "Risk Guardian: GrossPositionValue[BASE/EUR] no encontrado en "
                            "accountSummary — bloqueando nuevas entradas por precaución (fail-safe)")
        try:
            from telegram import send_telegram_critical
            send_telegram_critical(
                "⚠️ LIBERTAD_2045 — Risk Guardian: GrossPositionValue no disponible. "
                "Nuevas entradas bloqueadas este ciclo por precaución."
            )
        except Exception:
            pass
        return False, "GrossPositionValue no disponible (fail-safe)"
 
 
    # --------------------------------------------------
    # Todas las comprobaciones superadas
    # --------------------------------------------------
 
    log_event("INFO", f"Risk Guardian: OK — capital {net_liq:.2f} | "
                       f"hora {hour}h | drawdown dentro de límites | "
                       f"sin apalancamiento")

    return True, "OK"


def resetear_capital_peak(capital_inicial: float) -> None:
    """
    Resetea capital_peak.txt al capital de inicio de una cuenta nueva.

    Llamar manualmente antes del primer ciclo LIVE para evitar que el
    Risk Guardian calcule drawdown contra el pico de la cuenta PAPER anterior.

    Uso desde consola:
        cd ~/PROYECTO_LIBERTAD_2045
        venv/bin/python -c "from risk_guardian import resetear_capital_peak; resetear_capital_peak(4000)"
    """
    try:
        Path(PEAK_FILE).write_text(str(capital_inicial))
        log_event("INFO",
                  f"capital_peak.txt reseteado a {capital_inicial:.2f} "
                  f"— inicio de nueva cuenta (LIVE transition)")
        print(f"capital_peak.txt reseteado a {capital_inicial:.2f}")
    except Exception as e:
        log_event("ERROR", f"No se pudo resetear capital_peak.txt: {e}")
        raise
 