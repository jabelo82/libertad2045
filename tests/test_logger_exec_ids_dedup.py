"""
tests/test_logger_exec_ids_dedup.py

Tests del mecanismo compartido de deduplicación de exec_ids en logger.py
(FILLS_IDS_FILE / logged_exec_ids.txt) — fix del hallazgo KPI "Trades
ejecutados" (31/08/2026): unificar AMPLIAR con fill inmediato
(rebalance.py) y las entradas nuevas (registrar_fills_recientes() en
libertad2045.py) bajo el mismo fichero de exec_ids ya contados, para que
un mismo fill real de IBKR nunca genere dos eventos TRADE_FILLED.

El test clave es test_contrato_end_to_end_rebalance_luego_libertad2045:
reproduce exactamente el escenario de riesgo identificado en el diseño
— rebalance.py registra un exec_id al loguear el AMPLIAR, y una
verificación posterior (equivalente a la que haría
registrar_fills_recientes() en un ciclo posterior, dado que
ib.fills()/reqExecutions() no está acotado a la conexión actual — ver
docstring de rebalance.py::_verificar_ejecucion_pendiente) debe ver ese
exec_id como ya contado y no volver a generar TRADE_FILLED para él.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_logger_exec_ids_dedup.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Otros ficheros de test sustituyen sys.modules["logger"] por un stub
# incompleto (sin FILLS_IDS_FILE) para aislar libertad2045.py/rebalance.py
# de I/O real. pytest ejecuta todo el árbol en el mismo proceso, así que
# ese stub puede seguir en sys.modules cuando este fichero se importa —
# hay que descartarlo explícitamente para obtener aquí el logger.py real.
# Mismo patrón que tests/test_detectar_stops_gtc_duplicados.py.
sys.modules.pop("logger", None)

import logger  # noqa: E402 — módulo real, sin stubs (no tiene dependencias de proyecto)


class TestExecIdsDedup(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fichero = Path(self._tmpdir.name) / "logged_exec_ids.txt"
        self._patcher = patch.object(logger, "FILLS_IDS_FILE", self._fichero)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_leer_exec_ids_registrados_vacio_si_no_existe_fichero(self):
        self.assertEqual(logger.leer_exec_ids_registrados(), set())

    def test_registrar_exec_ids_persiste_y_se_puede_releer(self):
        logger.registrar_exec_ids(["EXEC-1", "EXEC-2"])
        self.assertEqual(logger.leer_exec_ids_registrados(), {"EXEC-1", "EXEC-2"})

    def test_registrar_exec_ids_fusiona_con_lo_existente(self):
        logger.registrar_exec_ids(["EXEC-1"])
        logger.registrar_exec_ids(["EXEC-2"])
        self.assertEqual(logger.leer_exec_ids_registrados(), {"EXEC-1", "EXEC-2"})

    def test_registrar_exec_ids_lista_vacia_no_toca_el_fichero(self):
        logger.registrar_exec_ids([])
        self.assertFalse(self._fichero.exists())

    def test_exec_ids_pendientes_de_registrar_filtra_los_ya_contados(self):
        logger.registrar_exec_ids(["EXEC-1"])
        pendientes = logger.exec_ids_pendientes_de_registrar(["EXEC-1", "EXEC-2"])
        self.assertEqual(pendientes, ["EXEC-2"])

    def test_exec_ids_pendientes_de_registrar_todo_nuevo_si_fichero_vacio(self):
        pendientes = logger.exec_ids_pendientes_de_registrar(["EXEC-1", "EXEC-2"])
        self.assertEqual(pendientes, ["EXEC-1", "EXEC-2"])

    def test_cap_a_10000_exec_ids(self):
        # Nota: el cap trunca la lista concatenada `existentes + nuevos`, pero
        # `existentes` sale de un set (sin orden garantizado) -- igual que el
        # comportamiento previo a este fix (registrar_fills_recientes() ya
        # partía de `set(...)` antes de este refactor). No se puede asumir
        # qué id concreto se expulsa, solo que el tamaño queda acotado y que
        # lo recién añadido en ESTA llamada sí sobrevive (va al final de la
        # lista antes del corte).
        viejos = [f"OLD-{i}" for i in range(9999)]
        logger.registrar_exec_ids(viejos)
        logger.registrar_exec_ids(["A", "B"])
        registrados = logger.leer_exec_ids_registrados()
        self.assertEqual(len(registrados), 10000)
        self.assertIn("A", registrados)
        self.assertIn("B", registrados)

    # ------------------------------------------------------------------
    # Contrato end-to-end: qué se compara contra qué para decidir si un
    # fill ya se contó — el punto concreto pedido antes de escribir código.
    # ------------------------------------------------------------------

    def test_contrato_end_to_end_rebalance_luego_libertad2045(self):
        """
        Simula la secuencia real:
          1. rebalance.py ve un AMPLIAR con estado=="Filled" en el propio
             ciclo, extrae el exec_id de trade_ajuste.fills, comprueba que
             no está registrado, loguea TRADE_FILLED y lo registra.
          2. Un ciclo posterior, registrar_fills_recientes() (libertad2045.py)
             vuelve a ver ese mismo exec_id vía ib.fills()/reqExecutions()
             (comportamiento de ib_insync documentado en
             _verificar_ejecucion_pendiente: no está acotado a la conexión
             actual) — debe descartarlo, NO generar un TRADE_FILLED duplicado.
        """
        exec_id_del_fill_real = "0001a2b3.01"

        # Paso 1: rebalance.py
        pendientes_rebalance = logger.exec_ids_pendientes_de_registrar([exec_id_del_fill_real])
        self.assertEqual(pendientes_rebalance, [exec_id_del_fill_real],
                          "primera vez que se ve -- debe considerarse nuevo")
        # (aquí rebalance.py loguearía TRADE_FILLED)
        logger.registrar_exec_ids(pendientes_rebalance)

        # Paso 2: registrar_fills_recientes(), ciclo posterior, mismo exec_id
        # reaparece en ib.fills().
        logged_ids = logger.leer_exec_ids_registrados()
        self.assertIn(exec_id_del_fill_real, logged_ids,
                       "el exec_id que registró rebalance.py debe ser visible "
                       "para registrar_fills_recientes() en el ciclo siguiente")
        # registrar_fills_recientes() hace `if exec_id in logged_ids: continue`
        # antes de bucketear el fill -- lo replicamos aquí explícitamente.
        se_saltaria = exec_id_del_fill_real in logged_ids
        self.assertTrue(se_saltaria,
                         "el fill ya contado por rebalance.py debe saltarse "
                         "en registrar_fills_recientes() -- sin esto, el "
                         "mismo trade genera dos TRADE_FILLED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
