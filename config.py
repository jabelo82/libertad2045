"""
config.py — Parámetros centralizados de LIBERTAD_2045

Referencia única de todos los parámetros operativos del sistema.
Los módulos aún definen sus propias constantes (deuda técnica documentada);
este archivo es la fuente de verdad para revisión y futura refactorización.
"""

# ── Estrategia ────────────────────────────────────────────────────────────────

MAX_POSITIONS        = 10       # Posiciones simultáneas máximas
RISK_PER_TRADE       = 0.0085   # Riesgo por operación: 0.85% del capital
TRAILING_FACTOR      = 0.75     # Aprobado en Experimento 40-ter (stress test 3/3 crisis)
B1_MULT_MIN          = 2.2      # Multiplicador ATR mínimo (volatilidad en máximos históricos)
B1_MULT_MAX          = 4.0      # Multiplicador ATR máximo (volatilidad en mínimos históricos)
ATR_VENTANA          = 14       # Período ATR
B1_VENTANA           = 252      # Ventana rolling para ATR percentil (1 año bursátil)
PULLBACK_ATR_FACTOR  = 0.75     # Umbral pullback: SMA50 − ATR × 0.75
PULLBACK_VENTANA     = 3        # Días de ventana para detectar pullback
BREAKEVEN_ENTRY_MULT = 1.5      # Break-even: precio ≥ entry + 1.5×ATR
BREAKEVEN_STOP_MULT  = 0.5      # Break-even: stop sube a entry + 0.5×ATR
DATA_DURATION        = "2 Y"    # Ventana histórica para descarga de datos IBKR

# ── Riesgo ────────────────────────────────────────────────────────────────────

MAX_DRAWDOWN  = 0.10    # Drawdown máximo desde pico: 10%
MAX_LEVERAGE  = 1.0     # Apalancamiento máximo: sin margen
MIN_CAPITAL   = 2000.0  # Capital mínimo operativo en €
HORA_INICIO   = 21      # Inicio ventana horaria de operación (CET)
HORA_FIN      = 23      # Fin ventana horaria de operación (CET)

# ── Sistema ───────────────────────────────────────────────────────────────────

MAX_POS              = 10    # Alias de MAX_POSITIONS (compatibilidad)
REBALANCE_THRESHOLD  = 0.25  # Desviación para rebalanceo: ±25%
REBALANCE_MIN_SHARES = 5     # Mínimo de acciones para ejecutar ajuste
