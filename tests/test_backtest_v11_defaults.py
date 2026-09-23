"""
tests/test_backtest_v11_defaults.py

v11 (23/09/2026) — mergea como DEFAULT del motor las dos palancas que
hasta ahora solo vivían en scripts experimentales sin comitear
(backtest_v8_salida_intradia.py, backtest_v9_composicion_corregida.py,
backtest_v10_fiel.py), que el propio docstring de v10-fiel describe
como "NO se mergea ni se despliega nada":

    SALIDA_POR_CIERRE = False   (antes True)
    SP500_COMP_CACHE  = "sp500_composicion_CORREGIDO.csv"
                        (antes "sp500_composicion.csv", congelado en
                        2019-01-11)

Motivo del merge: la revisión de
02.LIBERTAD_2045_PROTOCOLO_FIDELIDAD_BACKTEST.md (23/09/2026) encontró
que sus Artículos 1 y 2 estaban marcados "Resuelto" mientras el motor
real seguía arrancando con el comportamiento viejo salvo que un script
externo hiciera el monkeypatch -- exactamente el tipo de fallo
silencioso ("medir mal el propio sistema y creer el número
equivocado") que el Protocolo dice existir para evitar.

Este fichero prueba tres cosas, cada una con su propia clase:

  1. TestComportamientoPorDefectoSinMonkeypatch — que el comportamiento
     correcto ahora ocurre SIN que nadie tenga que activar ninguna
     palanca. Reutiliza (self-contenido, mismo patrón que el resto de
     la suite) el escenario determinista de
     test_backtest_salida_intradia_v8.py y el hecho real de la entrada
     de Interactive Brokers (IBKR) al S&P500 en agosto de 2025.

  2. TestDefaultDifiereDelComportamientoViejo — comparación directa
     default-vs-fichero-congelado, para que quede explícito que el
     default nuevo no es solo "distinto", sino que corrige el caso
     concreto que lo motivó.

  3. TestDefaultsNoRevertidosSinQuerer — guardia de regresión (pin
     test): si alguien revierte cualquiera de las dos constantes sin
     querer, falla con un mensaje que señala el Artículo del Protocolo
     que se rompe, en vez de fallar en silencio dentro de un backtest
     que nadie audita línea a línea.

Verificado manualmente antes de fijar este fichero (mismo patrón que
exige el docstring de test_backtest_salida_intradia_v8.py): con
SALIDA_POR_CIERRE=True y SP500_COMP_CACHE="sp500_composicion.csv"
(el estado del módulo antes de este commit), los tests de las clases
1 y 2 fallan. Con los nuevos defaults, pasan.

Ejecutar desde la raíz del proyecto:
    venv/bin/python3 -m pytest tests/test_backtest_v11_defaults.py -v
"""

import pandas as pd
import pytest

import backtest_expandido as bt
from backtest_expandido import ejecutar_backtest


# ============================================================
# 1. Comportamiento por defecto — salida por Low intradía,
#    sin ningún monkeypatch de SALIDA_POR_CIERRE.
# ============================================================

N_WARMUP    = 200
ATR_FIJO    = 0.5
PRECIO_BASE = 50.0
SMA50_FIJO  = 48.0
SMA200_INICIO     = 45.0
SMA200_INCREMENTO = 0.01


def _fila(**overrides):
    fila = {
        "Open": PRECIO_BASE, "High": PRECIO_BASE, "Low": PRECIO_BASE,
        "Close": PRECIO_BASE, "Volume": 1_000_000,
        "SMA50": SMA50_FIJO, "ATR": ATR_FIJO,
    }
    fila.update(overrides)
    return fila


def _escenario_toque_intradia() -> dict:
    """
    Mismo escenario que test_backtest_salida_intradia_v8.py (día 201:
    Open=50,5 High=52,0 Low=50,0 Close=51,5 -- el propio día de
    entrada ya genera la divergencia: el trailing recalculado con el
    High de ese día, 50,8375, queda por encima del Low (50,0) pero por
    debajo del Close (51,5)). Reimplementado aquí en vez de importado
    para que este fichero quede autocontenido, igual que el resto de
    la suite.
    """
    n_total = N_WARMUP + 2 + 5
    fechas  = pd.bdate_range(start="2016-01-04", periods=n_total)

    filas = []
    for i in range(N_WARMUP):
        filas.append(_fila(SMA200=SMA200_INICIO + i * SMA200_INCREMENTO))

    filas[198] = _fila(
        Open=44.0, High=45.0, Low=43.0, Close=44.0,
        SMA200=SMA200_INICIO + 198 * SMA200_INCREMENTO,
    )
    filas.append(_fila(   # día 200 — señal
        Open=50.0, High=51.0, Low=49.0, Close=50.0,
        SMA200=SMA200_INICIO + N_WARMUP * SMA200_INCREMENTO,
    ))
    filas.append(_fila(   # día 201 — entrada
        Open=50.5, High=52.0, Low=50.0, Close=51.5,
        SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
    ))

    dias_extra = [   # 202-206 — subida sostenida, solo relevante si la
                      # posición sobrevive al día de entrada (v7)
        dict(Open=51.5, High=52.0, Low=51.3, Close=51.8),
        dict(Open=51.8, High=53.0, Low=51.5, Close=52.8),
        dict(Open=52.8, High=54.0, Low=52.5, Close=53.5),
        dict(Open=53.5, High=54.2, Low=53.0, Close=53.8),
        dict(Open=53.8, High=55.0, Low=53.5, Close=54.8),
    ]
    for k, fila_extra in enumerate(dias_extra):
        fila = dict(fila_extra)
        fila.setdefault("SMA200", SMA200_INICIO + (N_WARMUP + 2 + k) * SMA200_INCREMENTO)
        filas.append(_fila(**fila))

    return {"WICK": pd.DataFrame(filas, index=fechas)}


class TestComportamientoPorDefectoSinMonkeypatch:
    """Ningún test de esta clase toca bt.SALIDA_POR_CIERRE ni
    bt.SP500_COMP_CACHE -- corren contra el default real del módulo,
    tal cual lo vería cualquier script nuevo (v11 en adelante) que
    importe backtest_expandido sin saber que estas dos palancas
    existieron alguna vez."""

    def test_stop_se_evalua_por_low_y_cierra_el_dia_de_entrada(self):
        """
        Artículo 1 del Protocolo. Contra el estado del módulo ANTES de
        este commit (SALIDA_POR_CIERRE=True por defecto) este test
        falla: la posición sobrevive al día 201 (Close=51,5 > stop
        recalculado 50,8375) y termina en una ganancia grande, no en
        la pérdida pequeña que se comprueba aquí.
        """
        datos = _escenario_toque_intradia()
        fecha_entrada = datos["WICK"].index[201]

        trades, _, _ = ejecutar_backtest(datos)
        wick_trade = next(t for t in trades if t["symbol"] == "WICK")

        assert wick_trade["fecha_salida"] == fecha_entrada
        assert wick_trade["pnl"] < 0


class TestDefaultDifiereDelComportamientoViejo:
    """Artículo 2 del Protocolo. Comparación directa entre el default
    real del módulo y el fichero congelado, usando un hecho real
    verificable: Interactive Brokers (IBKR) entró en el S&P500 el
    28/08/2025 (sp500_composicion_CORREGIDO.csv) -- fecha muy
    posterior al congelamiento de sp500_composicion.csv en
    2019-01-11, así que un `.asof()` contra el fichero viejo para
    cualquier fecha de 2025 devuelve la composición estática de 2019,
    sin IBKR."""

    def test_default_incluye_ibkr_tras_su_entrada_real_en_2025(self):
        """
        Contra el estado del módulo ANTES de este commit
        (SP500_COMP_CACHE="sp500_composicion.csv") este test falla:
        la composición cargada por defecto nunca pasa de 2019-01-11 y
        "IBKR" no aparece en ninguna fecha de 2025.
        """
        comp_df = bt.cargar_composicion_sp500()
        universo_hoy = bt.sp500_en_fecha(comp_df, "2025-09-01")

        assert universo_hoy is not None
        assert "IBKR" in universo_hoy

    def test_default_difiere_del_fichero_congelado_para_la_misma_fecha(self, monkeypatch):
        """
        Comparación explícita, no tautológica: el mismo `sp500_en_fecha`
        con la misma fecha, una vez contra el default real del módulo
        y otra vez forzando el fichero congelado -- deben diferir
        exactamente en el hecho que motiva el Artículo 2.
        """
        comp_default = bt.cargar_composicion_sp500()
        universo_default = bt.sp500_en_fecha(comp_default, "2025-09-01")

        monkeypatch.setattr(bt, "SP500_COMP_CACHE", "sp500_composicion.csv")
        comp_congelada = bt.cargar_composicion_sp500()
        universo_congelado = bt.sp500_en_fecha(comp_congelada, "2025-09-01")

        assert "IBKR" in universo_default
        assert universo_congelado is None or "IBKR" not in universo_congelado


# ============================================================
# 3. Guardia de regresión — pin test
# ============================================================

class TestDefaultsNoRevertidosSinQuerer:
    """
    Si cualquiera de estos dos valores vuelve alguna vez a su estado
    antiguo -- un merge descuidado, un revert parcial, un editor que
    "limpia" una constante sin leer por qué está así -- este test
    falla con un mensaje que señala exactamente qué Artículo del
    Protocolo se rompió, en vez de fallar en silencio dentro de un
    backtest que nadie audita línea a línea.
    """

    def test_salida_por_cierre_sigue_en_false(self):
        assert bt.SALIDA_POR_CIERRE is False, (
            "SALIDA_POR_CIERRE ha vuelto a True -- esto revierte el "
            "Articulo 1 del Protocolo de Fidelidad del Backtest "
            "(02.LIBERTAD_2045_PROTOCOLO_FIDELIDAD_BACKTEST.md): el "
            "motor volveria a evaluar el stop contra el Close en vez "
            "del toque intradia real, el mismo sesgo que causo que "
            "v7 sobreestimara el sistema (Experimento 27, paron de "
            "emergencia 10/09/2026)."
        )

    def test_sp500_comp_cache_sigue_en_corregido(self):
        assert bt.SP500_COMP_CACHE == "sp500_composicion_CORREGIDO.csv", (
            "SP500_COMP_CACHE ha dejado de apuntar a "
            "sp500_composicion_CORREGIDO.csv -- esto revierte el "
            "Articulo 2 del Protocolo de Fidelidad del Backtest: el "
            "motor volveria a usar una composicion del S&P500 "
            "congelada en 2019-01-11 (sp500_composicion.csv) o una "
            "fuente sin verificar contra el VPS real "
            "(sp500_composicion_updated_fase7a.csv, la que uso v9)."
        )
