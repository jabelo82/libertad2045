"""
tests/test_risk_guardian_leverage.py

Tests unitarios para el filtro de moneda base en el cálculo de apalancamiento
de risk_guardian.

Verifica que accountSummary() solo usa entradas con currency in {"BASE", "EUR"}
para NetLiquidation y GrossPositionValue, ignorando cualquier otra divisa (p.ej.
USD) aunque aparezca en el mismo listado.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_risk_guardian_leverage.py -v
"""

import os
import sys
import tempfile
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: insertar raíz del proyecto en sys.path
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Stubs mínimos para evitar importar ib_insync real o hacer I/O de red
# ---------------------------------------------------------------------------

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

# Forzar reimportación del módulo real aunque test_gtc_dedup.py haya instalado
# un stub de risk_guardian en sys.modules durante la sesión de pytest.
sys.modules.pop("risk_guardian", None)

import risk_guardian  # noqa: E402 — debe importarse después de los stubs

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

_ACCOUNT = "DU999999"
_NET_LIQ_EUR = 7762.00   # NetLiquidation en moneda base (EUR)
_GROSS_BASE   = 8068.10  # GrossPositionValue en moneda base (reproduce 26/06: 1.04x → bloquea)
_GROSS_OK     = 7000.00  # GrossPositionValue en moneda base que NO bloquea (< NetLiq)
_GROSS_USD    = 9500.00  # Valor ficticio en USD que bloquearía si se usara sin filtro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _av(tag: str, value: float, currency: str) -> AccountValue:
    return AccountValue(_ACCOUNT, tag, str(value), currency, "")


def _make_ib(*items: AccountValue) -> MagicMock:
    """Construye un mock de IB cuyo accountSummary() devuelve la lista dada."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.accountSummary.return_value = list(items)
    return ib


# ---------------------------------------------------------------------------
# Clase base: gestiona el archivo de capital pico para cada test
# ---------------------------------------------------------------------------

class RiskGuardianTestCase(unittest.TestCase):

    def setUp(self):
        # Ventana horaria: siempre bypass para tests
        os.environ["FORCE_HOUR_BYPASS"] = "true"

        # Redirigir PEAK_FILE a un archivo temporal para no tocar el de producción
        self._original_peak_file = risk_guardian.PEAK_FILE
        self._tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w")
        self._tmp.write(str(_NET_LIQ_EUR))  # peak = net_liq → drawdown = 0 %
        self._tmp.close()
        risk_guardian.PEAK_FILE = self._tmp.name

    def tearDown(self):
        os.environ.pop("FORCE_HOUR_BYPASS", None)
        risk_guardian.PEAK_FILE = self._original_peak_file
        Path(self._tmp.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: filtro de moneda base
# ---------------------------------------------------------------------------

class TestAccountSummaryBaseCurrencyFilter(RiskGuardianTestCase):

    def test_ignora_usd_usa_eur_cuando_gross_en_dos_monedas(self):
        """
        IBKR devuelve GrossPositionValue en USD (>NetLiq → bloquearía) y en EUR
        (< NetLiq → pasa). El código debe usar EUR e ignorar USD.
        """
        ib = _make_ib(
            _av("NetLiquidation",    _NET_LIQ_EUR, "EUR"),
            _av("GrossPositionValue", _GROSS_USD,   "USD"),   # 9500 → ratio 1.22x si se usara
            _av("GrossPositionValue", _GROSS_OK,    "EUR"),   # 7000 → ratio 0.90x → OK
        )
        self.assertTrue(
            risk_guardian.risk_check(ib),
            "Debe pasar: GrossPositionValue[EUR]=7000 < NetLiq=7762",
        )

    def test_ignora_usd_usa_base_cuando_gross_en_dos_monedas(self):
        """
        Igual que el anterior pero IBKR usa el literal 'BASE' en lugar de 'EUR'.
        El código debe aceptar ambos.
        """
        ib = _make_ib(
            _av("NetLiquidation",    _NET_LIQ_EUR, "BASE"),
            _av("GrossPositionValue", _GROSS_USD,   "USD"),
            _av("GrossPositionValue", _GROSS_OK,    "BASE"),
        )
        self.assertTrue(
            risk_guardian.risk_check(ib),
            "Debe pasar: GrossPositionValue[BASE]=7000 < NetLiq=7762",
        )

    def test_bloquea_cuando_base_supera_netliq_reproduce_historico(self):
        """
        GrossPositionValue[EUR]=8068.10 > NetLiquidation[EUR]=7762 → 1.04x → bloquea.
        Reproduce exactamente el bloqueo del 26/06/2026 registrado en los logs.
        Verifica que el fix es de robustez, no de comportamiento.
        """
        ib = _make_ib(
            _av("NetLiquidation",    _NET_LIQ_EUR, "EUR"),
            _av("GrossPositionValue", _GROSS_USD,   "USD"),   # si se usara: 9500/7762=1.22x
            _av("GrossPositionValue", _GROSS_BASE,  "EUR"),   # correcto: 8068/7762=1.04x → bloquea
        )
        self.assertFalse(
            risk_guardian.risk_check(ib),
            "Debe bloquear: GrossPositionValue[EUR]=8068.10 > NetLiq=7762 (1.04x > 1.00x)",
        )

    def test_ignora_usd_en_netliquidation(self):
        """
        NetLiquidation llega primero en USD (valor alto) y luego en EUR (valor real).
        El código debe usar EUR e ignorar USD.
        """
        ib = _make_ib(
            _av("NetLiquidation",    99999.00,    "USD"),   # si se usara: capital falso enorme
            _av("NetLiquidation",    _NET_LIQ_EUR, "EUR"),  # correcto
            _av("GrossPositionValue", _GROSS_OK,   "EUR"),
        )
        self.assertTrue(
            risk_guardian.risk_check(ib),
            "Debe usar NetLiquidation[EUR]=7762, no el de USD=99999",
        )

    def test_fail_safe_gross_position_sin_moneda_base(self):
        """
        accountSummary no devuelve GrossPositionValue en ninguna moneda base
        (solo USD) → el sistema bloquea por precaución.
        """
        ib = _make_ib(
            _av("NetLiquidation",    _NET_LIQ_EUR, "EUR"),
            _av("GrossPositionValue", _GROSS_USD,   "USD"),   # solo USD, sin BASE ni EUR
        )
        self.assertFalse(
            risk_guardian.risk_check(ib),
            "Debe bloquear: GrossPositionValue[BASE/EUR] no disponible",
        )

    def test_fail_safe_netliquidation_sin_moneda_base(self):
        """
        accountSummary no devuelve NetLiquidation en ninguna moneda base
        (solo USD) → el sistema bloquea por precaución.
        """
        ib = _make_ib(
            _av("NetLiquidation",    99999.00,   "USD"),   # solo USD, sin BASE ni EUR
            _av("GrossPositionValue", _GROSS_OK, "EUR"),
        )
        self.assertFalse(
            risk_guardian.risk_check(ib),
            "Debe bloquear: NetLiquidation[BASE/EUR] no disponible",
        )

    def test_pasa_cuando_solo_hay_entradas_base_correctas(self):
        """
        Caso normal: IBKR devuelve solo una entrada por tag (en moneda base).
        Exposición < capital → pasa sin bloquear.
        """
        ib = _make_ib(
            _av("NetLiquidation",    _NET_LIQ_EUR, "EUR"),
            _av("GrossPositionValue", _GROSS_OK,   "EUR"),
        )
        self.assertTrue(
            risk_guardian.risk_check(ib),
            "Caso normal sin duplicados: debe pasar",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
