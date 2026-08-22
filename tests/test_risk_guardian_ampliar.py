"""
tests/test_risk_guardian_ampliar.py

Tests unitarios para verificar_apalancamiento_ampliar() en risk_guardian.py —
CRÍTICA #1 de la auditoría 07/08/2026 (LIBERTAD2045_Auditoria_20260807):
AMPLIAR (rebalance.py) nunca verificaba apalancamiento.

Cubre:
  - AMPLIAR bloqueado cuando el apalancamiento actual ya supera MAX_LEVERAGE.
  - AMPLIAR permitido cuando el apalancamiento (actual y proyectado) está
    dentro del límite.
  - AMPLIAR bloqueado cuando el apalancamiento actual está dentro de límite
    pero ESTA orden concreta lo cruzaría (leverage proyectado, no solo el
    actual — ver docstring de verificar_apalancamiento_ampliar).
  - Fail-safe: si accountSummary falla o no trae NetLiquidation/
    GrossPositionValue, se bloquea el AMPLIAR por precaución.

Mismo patrón de stubs y helpers que tests/test_risk_guardian_leverage.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_risk_guardian_ampliar.py -v
"""

import os
import sys
import tempfile
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

AccountValue = namedtuple("AccountValue", ["account", "tag", "value", "currency", "modelCode"])

ib_stub = types.ModuleType("ib_insync")
for _name in ("IB", "MarketOrder", "Trade", "OrderStatus", "Stock", "Order"):
    setattr(ib_stub, _name, MagicMock)
sys.modules["ib_insync"] = ib_stub

for _mod in ("logger", "telegram"):
    _m = types.ModuleType(_mod)
    _m.log_event = MagicMock()
    _m.send_telegram = MagicMock()
    _m.send_telegram_critical = MagicMock()
    sys.modules[_mod] = _m

sys.modules.pop("risk_guardian", None)

import risk_guardian  # noqa: E402 — debe importarse después de los stubs

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

_ACCOUNT      = "DU999999"
_NET_LIQ      = 10000.00   # NetLiquidation en moneda base
_GROSS_OK     = 9000.00    # GrossPositionValue < NetLiq → 0.90x, dentro de límite
_GROSS_OVER   = 10500.00   # GrossPositionValue > NetLiq → 1.05x, ya bloqueado
_ORDEN_PEQUENA  = 500.00   # exposición adicional que NO cruza el límite (0.90x -> 0.95x)
_ORDEN_CRUZA    = 1200.00  # exposición adicional que SÍ cruza el límite (0.90x -> 1.02x)


def _av(tag: str, value: float, currency: str) -> AccountValue:
    return AccountValue(_ACCOUNT, tag, str(value), currency, "")


def _make_ib(*items: AccountValue) -> MagicMock:
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.accountSummary.return_value = list(items)
    return ib


class TestVerificarApalancamientoAmpliar(unittest.TestCase):

    # ------------------------------------------------------------
    # Bloqueo por apalancamiento actual ya por encima del límite
    # ------------------------------------------------------------

    def test_bloquea_cuando_apalancamiento_actual_ya_supera_limite(self):
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,    "EUR"),
            _av("GrossPositionValue", _GROSS_OVER, "EUR"),
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertFalse(permitido, "Debe bloquear: 1.05x > límite 1.00x sin ni siquiera sumar la orden")
        self.assertIn("apalancamiento", motivo)
        self.assertAlmostEqual(leverage, _GROSS_OVER / _NET_LIQ, places=4)

    # ------------------------------------------------------------
    # Permitido cuando está dentro de límite
    # ------------------------------------------------------------

    def test_permite_cuando_apalancamiento_actual_dentro_de_limite_sin_orden(self):
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ, "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertTrue(permitido, "0.90x < límite 1.00x — debe permitir")
        self.assertEqual(motivo, "OK")
        self.assertAlmostEqual(leverage, _GROSS_OK / _NET_LIQ, places=4)

    def test_permite_cuando_la_orden_concreta_no_cruza_el_limite(self):
        """
        Apalancamiento actual 0.90x, la orden añade 500€ de exposición ->
        proyectado 0.95x, sigue por debajo de 1.00x -> permitido.
        """
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ, "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(
            ib, exposicion_adicional=_ORDEN_PEQUENA
        )

        self.assertTrue(permitido)
        self.assertAlmostEqual(leverage, (_GROSS_OK + _ORDEN_PEQUENA) / _NET_LIQ, places=4)

    # ------------------------------------------------------------
    # Apalancamiento proyectado (no solo el actual): la orden concreta
    # es la que cruzaría el límite, aunque el apalancamiento actual esté
    # por debajo. Es la razón por la que se comprueba el proyectado y no
    # solo el actual (ver docstring de verificar_apalancamiento_ampliar).
    # ------------------------------------------------------------

    def test_bloquea_cuando_esta_orden_concreta_cruzaria_el_limite(self):
        """
        Apalancamiento actual 0.90x (dentro de límite), pero esta orden
        AMPLIAR concreta añade 1200€ de exposición -> proyectado 1.02x,
        por encima de 1.00x -> debe bloquear ESTA orden aunque el
        apalancamiento actual, por sí solo, pasaría el chequeo.
        """
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ, "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(
            ib, exposicion_adicional=_ORDEN_CRUZA
        )

        self.assertFalse(
            permitido,
            "Debe bloquear: apalancamiento actual 0.90x pasaría, pero el "
            "proyectado con esta orden (1.02x) supera el límite",
        )
        self.assertIn("proyectado", motivo)
        self.assertAlmostEqual(leverage, (_GROSS_OK + _ORDEN_CRUZA) / _NET_LIQ, places=4)

    # ------------------------------------------------------------
    # Fail-safe: dato de cuenta no disponible -> bloquear, nunca asumir OK
    # ------------------------------------------------------------

    def test_failsafe_accountsummary_lanza_excepcion(self):
        ib = MagicMock()
        ib.accountSummary.side_effect = Exception("timeout IBKR")

        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertFalse(permitido, "Fail-safe: error leyendo la cuenta debe bloquear el AMPLIAR")
        self.assertIsNone(leverage)
        self.assertIn("fail-safe", motivo)

    def test_failsafe_netliquidation_no_disponible(self):
        ib = _make_ib(
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
            # Sin NetLiquidation en BASE/EUR
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertFalse(permitido, "Fail-safe: sin NetLiquidation no se puede calcular apalancamiento")
        self.assertIsNone(leverage)
        self.assertIn("NetLiquidation", motivo)

    def test_failsafe_grosspositionvalue_no_disponible(self):
        ib = _make_ib(
            _av("NetLiquidation", _NET_LIQ, "EUR"),
            # Sin GrossPositionValue en BASE/EUR
        )
        permitido, motivo, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertFalse(permitido, "Fail-safe: sin GrossPositionValue no se puede calcular apalancamiento")
        self.assertIsNone(leverage)
        self.assertIn("GrossPositionValue", motivo)

    def test_failsafe_no_asume_ok_ante_dato_ausente(self):
        """
        Verificación explícita del criterio fail-safe pedido: ante ausencia
        de dato, el resultado es bloquear, NUNCA permitir por defecto.
        """
        ib = _make_ib()  # accountSummary vacío -> ni NetLiq ni GrossPos
        permitido, _, leverage = risk_guardian.verificar_apalancamiento_ampliar(ib)

        self.assertFalse(permitido)
        self.assertIsNone(leverage)


if __name__ == "__main__":
    unittest.main(verbosity=2)
