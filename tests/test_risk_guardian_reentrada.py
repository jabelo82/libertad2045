"""
tests/test_risk_guardian_reentrada.py

Tests unitarios para verificar_riesgo_entrada() en risk_guardian.py —
CRÍTICA #2 de la auditoría 07/08/2026 (LIBERTAD2045_Auditoria_20260807):
Risk Guardian evaluado una sola vez ante el escaneo de ~500 símbolos.

Cubre:
  - Entrada bloqueada cuando el drawdown ya supera MAX_DRAWDOWN_PCT.
  - Entrada bloqueada cuando el apalancamiento actual ya supera MAX_LEVERAGE.
  - Entrada bloqueada cuando el apalancamiento actual está dentro de límite
    pero ESTA entrada concreta lo cruzaría (leverage proyectado, no solo
    el actual).
  - Entrada permitida cuando drawdown y apalancamiento (actual y
    proyectado) están dentro de límite.
  - Fail-safe: si accountSummary falla o no trae NetLiquidation/
    GrossPositionValue, se bloquea la entrada por precaución.

Mismo patrón de stubs y helpers que tests/test_risk_guardian_ampliar.py.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_risk_guardian_reentrada.py -v
"""

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

_ACCOUNT        = "DU999999"
_NET_LIQ        = 10000.00   # NetLiquidation en moneda base
_GROSS_OK       = 9000.00    # GrossPositionValue < NetLiq → 0.90x, dentro de límite
_GROSS_OVER     = 10500.00   # GrossPositionValue > NetLiq → 1.05x, ya bloqueado
_ENTRADA_PEQUENA = 500.00    # exposición que NO cruza el límite (0.90x -> 0.95x)
_ENTRADA_CRUZA   = 1200.00   # exposición que SÍ cruza el límite (0.90x -> 1.02x)


def _av(tag: str, value: float, currency: str) -> AccountValue:
    return AccountValue(_ACCOUNT, tag, str(value), currency, "")


def _make_ib(*items: AccountValue) -> MagicMock:
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.accountSummary.return_value = list(items)
    return ib


class _PeakFileTestCase(unittest.TestCase):
    """Base con capital_peak.txt aislado en un fichero temporal por test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._peak_path = Path(self._tmpdir.name) / "capital_peak.txt"
        self._orig_peak_file = risk_guardian.PEAK_FILE
        risk_guardian.PEAK_FILE = str(self._peak_path)

    def tearDown(self):
        risk_guardian.PEAK_FILE = self._orig_peak_file
        self._tmpdir.cleanup()

    def _set_pico(self, valor: float):
        self._peak_path.write_text(str(valor))


class TestVerificarRiesgoEntradaDrawdown(_PeakFileTestCase):

    def test_bloquea_cuando_drawdown_ya_supera_limite(self):
        self._set_pico(12000.00)  # pico 12000, actual 10000 -> DD 16,67% > 10%
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,  "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido, "Debe bloquear: drawdown 16.67% > límite 10%")
        self.assertIn("drawdown", motivo)

    def test_permite_cuando_drawdown_dentro_de_limite(self):
        self._set_pico(10500.00)  # pico 10500, actual 10000 -> DD 4,76% < 10%
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,  "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertTrue(permitido)
        self.assertEqual(motivo, "OK")


class TestVerificarRiesgoEntradaLeverage(_PeakFileTestCase):

    def test_bloquea_cuando_apalancamiento_actual_ya_supera_limite(self):
        self._set_pico(_NET_LIQ)  # sin drawdown, aísla el chequeo de leverage
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,    "EUR"),
            _av("GrossPositionValue", _GROSS_OVER, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido, "Debe bloquear: 1.05x > límite 1.00x sin ni siquiera sumar la entrada")
        self.assertIn("apalancamiento", motivo)

    def test_permite_cuando_apalancamiento_dentro_de_limite_sin_entrada(self):
        self._set_pico(_NET_LIQ)
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,  "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertTrue(permitido, "0.90x < límite 1.00x — debe permitir")
        self.assertEqual(motivo, "OK")

    def test_permite_cuando_la_entrada_concreta_no_cruza_el_limite(self):
        self._set_pico(_NET_LIQ)
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,  "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(
            ib, exposicion_adicional=_ENTRADA_PEQUENA
        )

        self.assertTrue(permitido)
        self.assertEqual(motivo, "OK")

    def test_bloquea_cuando_esta_entrada_concreta_cruzaria_el_limite(self):
        """
        Apalancamiento actual 0.90x (dentro de límite), pero esta entrada
        concreta añade 1200€ de exposición -> proyectado 1.02x, por encima
        de 1.00x -> debe bloquear ESTA entrada aunque el apalancamiento
        actual, por sí solo, pasaría el chequeo.
        """
        self._set_pico(_NET_LIQ)
        ib = _make_ib(
            _av("NetLiquidation",     _NET_LIQ,  "EUR"),
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(
            ib, exposicion_adicional=_ENTRADA_CRUZA
        )

        self.assertFalse(
            permitido,
            "Debe bloquear: apalancamiento actual 0.90x pasaría, pero el "
            "proyectado con esta entrada (1.02x) supera el límite",
        )
        self.assertIn("proyectado", motivo)


class TestVerificarRiesgoEntradaFailSafe(_PeakFileTestCase):

    def test_failsafe_accountsummary_lanza_excepcion(self):
        ib = MagicMock()
        ib.accountSummary.side_effect = Exception("timeout IBKR")

        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido, "Fail-safe: error leyendo la cuenta debe bloquear la entrada")
        self.assertIn("fail-safe", motivo)

    def test_failsafe_netliquidation_no_disponible(self):
        ib = _make_ib(
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
            # Sin NetLiquidation en BASE/EUR
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido, "Fail-safe: sin NetLiquidation no se puede evaluar riesgo")
        self.assertIn("NetLiquidation", motivo)

    def test_failsafe_grosspositionvalue_no_disponible(self):
        self._set_pico(_NET_LIQ)
        ib = _make_ib(
            _av("NetLiquidation", _NET_LIQ, "EUR"),
            # Sin GrossPositionValue en BASE/EUR
        )
        permitido, motivo = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido, "Fail-safe: sin GrossPositionValue no se puede evaluar apalancamiento")
        self.assertIn("GrossPositionValue", motivo)

    def test_failsafe_no_asume_ok_ante_dato_ausente(self):
        ib = _make_ib()  # accountSummary vacío -> ni NetLiq ni GrossPos
        permitido, _ = risk_guardian.verificar_riesgo_entrada(ib)

        self.assertFalse(permitido)


if __name__ == "__main__":
    unittest.main(verbosity=2)
