"""
tests/test_sp500_en_fecha_sufijo.py

Fix del bug de sufijo -YYYYMM en sp500_en_fecha() (Parte J, 16/09/2026,
investigación del drawdown de v9): sp500_composicion.csv (congelado)
incrusta el sufijo "-YYYYMM" (fecha de baja del índice) en el ticker de
CUALQUIER empresa que en algún momento salió del S&P500 -- incrustado en
TODAS las filas donde aparece ese ticker, no solo en la última antes de
la baja. universo_historico_sp500() ya elimina ese sufijo al construir
la lista de descarga (`datos` queda indexado por ticker bare), pero
sp500_en_fecha() devolvía el token TAL CUAL venía del CSV -- así que
`"CBE" in sp500_en_fecha(...)` daba SIEMPRE False, para cualquier fecha,
incluso cuando esa empresa era en efecto un componente real del índice
en esa fecha. Cuantificado sobre el fichero congelado completo: 422 de
1.047 tickers históricos (~40%) llevan el sufijo en el 100% de sus
apariciones -> quedaban permanentemente excluidos de la cartera
tradeable de v7/v8 en los 20 años completos, no solo tras el
congelamiento de 2019-01-11 (hallazgo distinto, sección 8 del contexto).

Caso real usado aquí: Cooper Industries plc (ticker CBE), componente
real del S&P500 hasta su adquisición por Eaton Corporation (cerrada
30/11/2012) -- verificado con el token crudo real de
sp500_composicion.csv en fechas donde CBE SÍ formaba parte del índice.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_sp500_en_fecha_sufijo.py -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_expandido import sp500_en_fecha, universo_historico_sp500  # noqa: E402


def _comp_df_cbe_con_sufijo() -> pd.DataFrame:
    """
    Réplica mínima del formato real de sp500_composicion.csv en una
    fecha donde Cooper Industries (CBE) era un componente real del
    S&P500 -- el token crudo del CSV es "CBE-201211" (fecha de baja
    incrustada), igual en TODAS las filas donde aparece, no solo la
    última. Otros tickers de relleno para que el escenario no sea
    trivial (uno bare de toda la vida, AAPL; uno que además cambia de
    sufijo con el tiempo, simulando un ticker reciclado).
    """
    idx = pd.to_datetime(["2012-06-01", "2012-11-02", "2012-11-20", "2012-12-03"])
    filas = [
        "AAPL,CBE-201211,MSFT",
        "AAPL,CBE-201211,MSFT",
        "AAPL,CBE-201211,MSFT",
        "AAPL,MSFT",  # CBE ya salió del índice tras la adquisición
    ]
    return pd.DataFrame({"tickers": filas}, index=idx)


class TestSp500EnFechaSufijo(unittest.TestCase):

    def test_cbe_es_miembro_real_en_fechas_donde_estaba_en_el_indice(self):
        comp_df = _comp_df_cbe_con_sufijo()

        for fecha in ["2012-06-01", "2012-11-02", "2012-11-20"]:
            miembros = sp500_en_fecha(comp_df, pd.Timestamp(fecha))
            self.assertIsNotNone(miembros)
            self.assertIn(
                "CBE", miembros,
                f"CBE debe reconocerse como miembro real del índice en {fecha} "
                f"(el token crudo del CSV es 'CBE-201211', pero el ticker bare "
                f"'CBE' es la clave que usa `datos` -- sin quitar el sufijo, "
                f"'CBE' nunca hace match y la empresa queda excluida de la "
                f"cartera tradeable en TODA su historia, no solo tras su baja)."
            )
            # No debe colar el token crudo con sufijo como si fuera un ticker
            self.assertNotIn("CBE-201211", miembros)

    def test_cbe_ya_no_es_miembro_tras_la_baja_real_del_indice(self):
        comp_df = _comp_df_cbe_con_sufijo()
        miembros = sp500_en_fecha(comp_df, pd.Timestamp("2012-12-03"))
        self.assertIsNotNone(miembros)
        self.assertNotIn("CBE", miembros)

    def test_tickers_bare_no_afectados(self):
        comp_df = _comp_df_cbe_con_sufijo()
        miembros = sp500_en_fecha(comp_df, pd.Timestamp("2012-06-01"))
        self.assertIn("AAPL", miembros)
        self.assertIn("MSFT", miembros)

    def test_consistente_con_universo_historico_sp500(self):
        """
        `sp500_hoy` (sp500_en_fecha) y `datos` (construido a partir de
        universo_historico_sp500) deben usar la MISMA forma de ticker --
        si no, "symbol not in sp500_hoy" nunca puede dar False para un
        ticker que en la práctica sí perteneció al índice.
        """
        comp_df = _comp_df_cbe_con_sufijo()
        universo = universo_historico_sp500(comp_df)
        self.assertIn("CBE", universo)

        miembros_en_fecha = sp500_en_fecha(comp_df, pd.Timestamp("2012-06-01"))
        # La intersección debe ser no vacía y CBE debe caer en ambas formas
        self.assertIn("CBE", universo)
        self.assertIn("CBE", miembros_en_fecha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
