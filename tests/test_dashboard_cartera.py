"""
tests/test_dashboard_cartera.py

Tests unitarios para el filtro de moneda base y tipo de cambio de mercado
en obtener_cartera_ib() de dashboard.py.

Verifica:
  - TotalCashValue[BASE] tiene prioridad sobre [EUR] (valores distintos)
  - NetLiquidation[BASE/EUR] se filtran correctamente ignorando USD
  - cash_eur negativo se preserva en values_eur[-1] sin forzarse a cero
  - _obtener_tipo_cambio_mercado() devuelve el tipo de yfinance o 0.0 si falla

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_dashboard_cartera.py -v
"""

import sys
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap: insertar raíz del proyecto en sys.path
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Stubs mínimos para evitar I/O en importación de dashboard
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
    sys.modules[_mod] = _m

sys.modules.pop("dashboard", None)
import dashboard  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNT = "DU999999"

def _av(tag: str, value: float, currency: str) -> AccountValue:
    return AccountValue(_ACCOUNT, tag, str(value), currency, "")


def _make_position(symbol: str, qty: int, avg_cost: float) -> MagicMock:
    pos = MagicMock()
    pos.contract.symbol = symbol
    pos.position = qty
    pos.avgCost = avg_cost
    return pos


def _make_bar(close: float) -> MagicMock:
    bar = MagicMock()
    bar.close = close
    return bar


def _make_ib(account_items, positions, bar_close=100.0) -> MagicMock:
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.accountSummary.return_value = list(account_items)
    ib.positions.return_value = list(positions)
    ib.reqHistoricalData.return_value = [_make_bar(bar_close)]
    ib.reqAllOpenOrders.return_value = []
    return ib


# ---------------------------------------------------------------------------
# Tests: filtro de moneda en accountSummary
# ---------------------------------------------------------------------------

class TestAccountSummaryCurrencyFilter(unittest.TestCase):

    def _call(self, account_items, positions=None, fx=1.18):
        """
        Ejecuta obtener_cartera_ib() con IB mocked y yfinance patcheado.
        Retorna el dict de cartera o None.
        """
        if positions is None:
            positions = [_make_position("TRV", 4, 275.0)]

        ib_mock = _make_ib(account_items, positions, bar_close=327.0)

        # Parchear IB() para devolver nuestro mock en lugar de conectarse
        with patch.object(dashboard, "_obtener_tipo_cambio_mercado", return_value=fx):
            with patch("ib_insync.IB", return_value=ib_mock):
                return dashboard.obtener_cartera_ib()

    def test_totalcashvalue_base_tiene_prioridad_sobre_eur(self):
        """
        Cuando IBKR devuelve TotalCashValue[BASE]=-306 y luego [EUR]=+7,
        el código debe usar BASE (-306), no EUR (+7).
        """
        items = [
            _av("NetLiquidation", 7762.0, "BASE"),
            _av("TotalCashValue",  -306.0, "BASE"),   # neto real → debe ganar
            _av("TotalCashValue",     7.0, "EUR"),    # componente EUR → debe ignorarse
            _av("TotalCashValue",  -313.0, "USD"),    # USD → ignorado por filtro
        ]
        result = self._call(items)
        self.assertIsNotNone(result)
        # Último elemento de values_eur es CASH
        cash_val = result["values_eur"][-1]
        self.assertEqual(result["labels"][-1], "CASH")
        self.assertLess(cash_val, 0, f"CASH debe ser negativo, got {cash_val}")
        self.assertAlmostEqual(cash_val, -306, delta=1)

    def test_totalcashvalue_eur_como_fallback_si_no_hay_base(self):
        """
        Si IBKR no envía TotalCashValue[BASE], se acepta [EUR] como fallback.
        """
        items = [
            _av("NetLiquidation", 7762.0, "EUR"),
            _av("TotalCashValue",   500.0, "EUR"),    # único disponible → se usa
            _av("TotalCashValue",  -100.0, "USD"),    # ignorado
        ]
        result = self._call(items)
        self.assertIsNotNone(result)
        cash_val = result["values_eur"][-1]
        self.assertAlmostEqual(cash_val, 500, delta=1)

    def test_netliquidation_ignora_usd(self):
        """
        NetLiquidation[USD]=99999 debe ignorarse; solo se usa BASE/EUR.
        """
        items = [
            _av("NetLiquidation",  99999.0, "USD"),   # debe ignorarse
            _av("NetLiquidation",   7762.0, "EUR"),   # correcto
            _av("TotalCashValue",   -306.0, "BASE"),
            _av("TotalCashValue",      7.0, "EUR"),
        ]
        result = self._call(items)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["capital_eur"], 7762.0, delta=1)

    def test_cash_negativo_preservado_en_values_eur(self):
        """
        Cuando cash_eur es negativo, values_eur[-1] debe ser negativo
        (no forzado a 0 ni omitido).
        """
        items = [
            _av("NetLiquidation",  7762.0, "EUR"),
            _av("TotalCashValue",  -306.0, "BASE"),
            _av("TotalCashValue",     7.0, "EUR"),
        ]
        result = self._call(items)
        self.assertIsNotNone(result)
        self.assertIn("CASH", result["labels"])
        cash_idx = result["labels"].index("CASH")
        self.assertLess(result["values_eur"][cash_idx], 0)

    def test_sum_values_eur_es_posiciones_mas_cash(self):
        """
        sum(values_eur) = posiciones_eur + cash_eur.
        Con fx=1.187 y 4 TRV a 327 USD: pos ≈ round(4*327/1.187) = 1102.
        cash = round(-306) = -306. Suma ≈ 796. Verifica la consistencia interna.
        """
        fx = 1.187
        items = [
            _av("NetLiquidation",  7762.0, "EUR"),
            _av("TotalCashValue",  -306.0, "BASE"),
            _av("TotalCashValue",     7.0, "EUR"),
        ]
        result = self._call(items, fx=fx)
        self.assertIsNotNone(result)
        # Separar posiciones (todos menos CASH) y cash
        cash_idx = result["labels"].index("CASH")
        pos_sum  = sum(v for i, v in enumerate(result["values_eur"]) if i != cash_idx)
        cash_val = result["values_eur"][cash_idx]
        expected_sum = pos_sum + cash_val
        self.assertEqual(sum(result["values_eur"]), expected_sum)
        # cash debe ser ≈ -306 (BASE tiene prioridad sobre EUR=+7)
        self.assertAlmostEqual(cash_val, -306, delta=1)


# ---------------------------------------------------------------------------
# Tests: _obtener_tipo_cambio_mercado
# ---------------------------------------------------------------------------

class TestObtenerTipoCambioMercado(unittest.TestCase):

    def test_devuelve_tipo_de_yfinance_cuando_disponible(self):
        import pandas as pd
        # Dos filas para que .squeeze() devuelva Series (no escalar) y .iloc[-1] funcione
        df = pd.DataFrame(
            {"Close": [1.1850, 1.1870]},
            index=pd.to_datetime(["2026-06-25", "2026-06-26"]),
        )
        df.index.name = "Date"
        with patch("yfinance.download", return_value=df):
            rate = dashboard._obtener_tipo_cambio_mercado()
        self.assertAlmostEqual(rate, 1.1870, places=4)

    def test_devuelve_cero_si_yfinance_lanza_excepcion(self):
        with patch("yfinance.download", side_effect=Exception("timeout")):
            rate = dashboard._obtener_tipo_cambio_mercado()
        self.assertEqual(rate, 0.0)

    def test_devuelve_cero_si_dataframe_vacio(self):
        import pandas as pd
        with patch("yfinance.download", return_value=pd.DataFrame()):
            rate = dashboard._obtener_tipo_cambio_mercado()
        self.assertEqual(rate, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
