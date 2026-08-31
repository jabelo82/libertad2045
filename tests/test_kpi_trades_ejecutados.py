"""
tests/test_kpi_trades_ejecutados.py

Tests del KPI "Trades ejecutados" en dashboard.py (calcular_stats() /
leer_logs()) -- fix del hallazgo 31/08/2026: el KPI contaba órdenes
BUY STOP DAY enviadas (level=="TRADE", "Orden enviada a IBKR") igual que
fills confirmados (level=="TRADE_FILLED"), así que:

  (a) una BUY STOP que nunca se disparó (expira sin fill, TIF=DAY)
      contaba como "1 trade ejecutado" para siempre -- ningún evento
      posterior la corregía.
  (b) un trade que SÍ se ejecutó se contaba dos veces -- una vez como
      "TRADE" (orden enviada) y otra como "TRADE_FILLED" (fill
      confirmado).

test_ocho_huerfanas_reales_agosto_no_cuentan reproduce el caso real que
motivó la investigación: 8 símbolos con BUY STOP enviada en LIVE entre
el 03/08 y el 31/08/2026 que nunca llegaron a dispararse (verificado
contra los CSV de producción del VPS), cruzados contra 2 casos que sí
dispararon.

dashboard.py no tiene dependencias de proyecto -- se importa directo,
sin stubs.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_kpi_trades_ejecutados.py -v
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Otros ficheros de test sustituyen sys.modules["dashboard"] por un stub
# vacío (para poder importar libertad2045.py sin generar el dashboard real).
# pytest ejecuta todo el árbol en el mismo proceso, así que ese stub puede
# seguir en sys.modules cuando este fichero se importa — hay que
# descartarlo explícitamente para obtener aquí el dashboard.py real.
# Mismo patrón que tests/test_detectar_stops_gtc_duplicados.py.
sys.modules.pop("dashboard", None)

import dashboard  # noqa: E402 — sin dependencias de proyecto, import directo


def _escribir_log(directorio: Path, fecha: str, lineas: list) -> None:
    """lineas: lista de tuplas (level, event, symbol, score, shares, entry, stop).

    Antepone siempre una línea "Capital disponible" -- leer_logs() solo
    añade una sesión a la lista si `capital` quedó fijado (`if capital:`,
    dashboard.py), y calcular_stats() necesita capital numérico en todas
    las sesiones para no reventar en la resta de rentabilidad/drawdown.
    El valor concreto es irrelevante para estos tests (solo miran
    total_trades).
    """
    archivo = directorio / f"LIBERTAD_{fecha}.csv"
    filas = ["timestamp,level,event,symbol,score,shares,entry,stop",
              f"{fecha} 22:10:00,INFO,Capital disponible: 10000.00,,,,,"]
    for i, (level, event, symbol, score, shares, entry, stop) in enumerate(lineas):
        ts = f"{fecha} 22:{11+i:02d}:00"
        filas.append(f"{ts},{level},{event},{symbol},{score},{shares},{entry},{stop}")
    archivo.write_text("\n".join(filas) + "\n")


class TestKpiTradesEjecutados(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._logdir = Path(self._tmpdir.name)
        self._patcher = patch.object(dashboard, "LOG_DIR", str(self._logdir))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _stats(self):
        sesiones = dashboard.leer_logs()
        return dashboard.calcular_stats(sesiones), sesiones

    # ------------------------------------------------------------
    # Caso base: BUY STOP huérfana (nunca dispara) no cuenta
    # ------------------------------------------------------------

    def test_buy_stop_huerfana_no_cuenta_en_kpi(self):
        _escribir_log(self._logdir, "2026-08-06", [
            ("INFO", "SYSTEM_START | Modo: LIVE", "", "", "", "", ""),
            ("TRADE", "Orden enviada a IBKR [LIVE]", "SLB", "", "10", "52.48", "50.10"),
            # Nunca aparece un TRADE_FILLED para SLB -- la orden expiró sin dispararse.
        ])
        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 0,
                          "una BUY STOP sin fill no debe contar como trade ejecutado")

    # ------------------------------------------------------------
    # Caso base: BUY STOP con fill confirmado cuenta UNA vez, no dos
    # ------------------------------------------------------------

    def test_buy_stop_con_fill_confirmado_cuenta_una_vez(self):
        _escribir_log(self._logdir, "2026-08-10", [
            ("TRADE", "Orden enviada a IBKR [LIVE]", "DVN", "", "20", "45.48", "43.00"),
        ])
        _escribir_log(self._logdir, "2026-08-11", [
            ("TRADE_FILLED", "Fill BUY STOP confirmado | precio_real=45.50",
             "DVN", "", "20", "45.50", "43.00"),
        ])
        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 1,
                          "un trade real (orden + fill confirmado) debe contar una vez, no dos")

    # ------------------------------------------------------------
    # AMPLIAR con fill inmediato (TRADE_FILLED tras el fix) cuenta
    # ------------------------------------------------------------

    def test_ampliar_fill_inmediato_cuenta_como_trade(self):
        _escribir_log(self._logdir, "2026-08-12", [
            ("TRADE_FILLED", "Rebalanceo AMPLIAR ejecutado | BUY 10 acc. | precio_ref=100.55",
             "WMB", "", "10", "100.55", ""),
        ])
        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 1)

    def test_ampliar_fill_inmediato_no_se_confunde_con_precio_de_salida(self):
        """Regresión: leer_precios_salida() (Fuente 2) excluye TRADE_FILLED
        que contienen 'BUY' -- un AMPLIAR no debe aparecer como salida."""
        _escribir_log(self._logdir, "2026-08-12", [
            ("TRADE_FILLED", "Rebalanceo AMPLIAR ejecutado | BUY 10 acc. | precio_ref=100.55",
             "WMB", "", "10", "100.55", ""),
        ])
        salidas = dashboard.leer_precios_salida()
        self.assertNotIn("WMB", salidas)

    # ------------------------------------------------------------
    # REDUCIR con fill inmediato (sigue en level="TRADE") también cuenta
    # ------------------------------------------------------------

    def test_reducir_fill_inmediato_cuenta_como_trade(self):
        _escribir_log(self._logdir, "2026-08-13", [
            ("TRADE", "Rebalanceo REDUCIR ejecutado | SELL 10 acc. | precio_ref=90.00",
             "IBKR", "", "10", "90.00", ""),
        ])
        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 1)

    def test_entrada_huerfana_y_reducir_confirmado_mismo_dia_se_distinguen(self):
        """Ambas líneas comparten level=="TRADE" -- deben clasificarse
        distinto: la entrada nunca disparada no cuenta, el REDUCIR
        confirmado sí."""
        _escribir_log(self._logdir, "2026-08-14", [
            ("TRADE", "Orden enviada a IBKR [LIVE]", "VTR", "", "15", "91.67", "88.00"),
            ("TRADE", "Rebalanceo REDUCIR ejecutado | SELL 5 acc. | precio_ref=90.00",
             "IBKR", "", "5", "90.00", ""),
        ])
        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 1)

    # ------------------------------------------------------------
    # Caso real: las 8 huérfanas de agosto identificadas en la
    # investigación (VPS, LIBERTAD_2026-08-*.csv, cruce TRADE vs
    # TRADE_FILLED en ventana de 5 días), más 2 casos que sí dispararon.
    # ------------------------------------------------------------

    def test_ocho_huerfanas_reales_agosto_no_cuentan(self):
        huerfanas = [
            ("2026-08-06", "SLB",   "52.48"),
            ("2026-08-11", "ADM",   "80.95"),
            ("2026-08-12", "IBKR",  "92.81"),
            ("2026-08-14", "VTR",   "91.67"),
            ("2026-08-17", "BG",    "115.58"),
            ("2026-08-20", "EIX",   "75.95"),
            ("2026-08-24", "VMRK",  "68.67"),
            ("2026-08-27", "APH",   "163.35"),
        ]
        for fecha, symbol, entry in huerfanas:
            _escribir_log(self._logdir, fecha, [
                ("TRADE", "Orden enviada a IBKR [LIVE]", symbol, "", "10", entry, "0.0"),
            ])

        # 2 casos de control que sí dispararon (fill al día siguiente,
        # patrón real de registrar_fills_recientes()).
        _escribir_log(self._logdir, "2026-08-04", [
            ("TRADE", "Orden enviada a IBKR [LIVE]", "GOOGL", "", "5", "380.57", "370.00"),
        ])
        _escribir_log(self._logdir, "2026-08-05", [
            ("TRADE_FILLED", "Fill BUY STOP confirmado | precio_real=383.61",
             "GOOGL", "", "5", "383.61", "370.00"),
        ])

        stats, _ = self._stats()
        self.assertEqual(stats["total_trades"], 1,
                          "las 8 huérfanas (TRADE sin fill nunca) no deben contar; "
                          "de GOOGL solo cuenta su TRADE_FILLED, no su TRADE de envío "
                          "-- total esperado: 1, no 9 ni 10")


if __name__ == "__main__":
    unittest.main(verbosity=2)
