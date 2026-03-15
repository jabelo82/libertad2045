import os
from datetime import datetime
from pathlib import Path

from logger import log_event


# --------------------------------------------------
# Umbrales de riesgo
# Configurables desde variables de entorno
# --------------------------------------------------

MIN_CAPITAL        = float(os.getenv("RISK_MIN_CAPITAL",    "2000"))  # Capital mínimo operativo
MAX_DRAWDOWN_PCT   = float(os.getenv("RISK_MAX_DRAWDOWN",   "0.10"))  # Drawdown máximo: 10%
PEAK_FILE          = os.getenv("RISK_PEAK_FILE", "capital_peak.txt")  # Archivo de capital pico

# Ventana horaria de operación (hora local del servidor)
HOUR_START         = int(os.getenv("RISK_HOUR_START", "21"))
HOUR_END           = int(os.getenv("RISK_HOUR_END",   "23"))


def _leer_capital_pico():
    """
    Lee el capital pico registrado en disco.
    Si el archivo no existe, devuelve None.
    """
    try:
        path = Path(PEAK_FILE)
        if path.exists():
            return float(path.read_text().strip())
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

    Retorna True si todas las condiciones se cumplen.
    Retorna False y registra el motivo si alguna falla.
    """

    # --------------------------------------------------
    # 1. Conexión activa
    # --------------------------------------------------

    if not ib.isConnected():
        log_event("WARN", "Risk Guardian: IBKR no conectado")
        return False


    # --------------------------------------------------
    # 2. Ventana horaria
    # --------------------------------------------------

    hour = datetime.now().hour

    if not (HOUR_START <= hour <= HOUR_END):
        log_event("WARN", f"Risk Guardian: fuera de ventana horaria "
                           f"(hora actual: {hour}h, permitido: {HOUR_START}-{HOUR_END}h)")
        return False


    # --------------------------------------------------
    # 3. Capital de la cuenta
    # --------------------------------------------------

    try:
        account  = ib.accountSummary()
        net_liq  = None

        for item in account:
            if item.tag == "NetLiquidation":
                net_liq = float(item.value)
                break

    except Exception as e:
        log_event("ERROR", f"Risk Guardian: error leyendo cuenta IBKR: {e}")
        return False

    if net_liq is None:
        log_event("WARN", "Risk Guardian: NetLiquidation no disponible")
        return False

    if net_liq < MIN_CAPITAL:
        log_event("WARN", f"Risk Guardian: capital insuficiente "
                           f"({net_liq:.2f} < mínimo {MIN_CAPITAL:.2f})")
        return False


    # --------------------------------------------------
    # 4. Drawdown máximo desde capital pico
    # --------------------------------------------------

    # Actualizar el pico si el capital actual es nuevo máximo
    _guardar_capital_pico(net_liq)

    capital_pico = _leer_capital_pico()

    if capital_pico and capital_pico > 0:

        drawdown = (capital_pico - net_liq) / capital_pico

        log_event("INFO", f"Drawdown actual: {drawdown:.2%} "
                           f"(pico: {capital_pico:.2f} | actual: {net_liq:.2f})")

        if drawdown > MAX_DRAWDOWN_PCT:
            log_event("WARN", f"Risk Guardian: drawdown máximo superado "
                               f"({drawdown:.2%} > límite {MAX_DRAWDOWN_PCT:.2%}). "
                               f"Sistema detenido.")
            return False


    # --------------------------------------------------
    # Todas las comprobaciones superadas
    # --------------------------------------------------

    log_event("INFO", f"Risk Guardian: OK — capital {net_liq:.2f} | "
                       f"hora {hour}h | drawdown dentro de límites")

    return True