"""
Tests — Fase 8 (24/08/2026): cierre de las tres divergencias de riesgo
entre producción y backtest_expandido.py (auditoría de paridad del
24/08/2026, Hallazgos #1, #2 y #3).
========================================================================

Cada hallazgo reproduce el escenario exacto que lo motivó:

  Hallazgo #2 — el backtest bloqueaba TODO el rebalanceo (incluido
  REDUCIR) cuando había drawdown alto. Producción nunca bloquea
  REDUCIR — "DECISIÓN A-1" en rebalance.py. Escenario: un ticker
  (CRASH) provoca un drawdown >10% con una pérdida real; un segundo
  ticker (GROW) queda sobredimensionado por MAX_POSITION_PCT mientras
  el drawdown sigue activo — debe REDUCIRSE igualmente.

  Hallazgo #1 — el backtest nunca modelaba apalancamiento agregado
  (MAX_LEVERAGE=1,00x). Escenario: 5 señales simultáneas cuyo tamaño
  combinado (a ~25% de capital cada una) superaría el 100% de
  exposición bruta sin el límite — solo deben abrirse las que quepan
  dentro de MAX_LEVERAGE.

  Hallazgo #3 — el capital mínimo bloqueaba también la GESTIÓN de
  posiciones existentes (stops, trailing); producción solo bloquea
  entradas NUEVAS con esto. Escenario: un ticker (CRASH3) hunde el
  capital por debajo de RISK_MIN_CAPITAL; un segundo ticker (HOLD3),
  ya abierto, debe seguir cerrándose por stop con normalidad.

Todos los escenarios de integración usan el mismo patrón de
calentamiento (200 días de tendencia alcista + pullback día 198 +
señal día 200 + entrada día 201, ATR fijo, ATR_PERCENTIL ausente por
diseño → obtener_multiplicador() usa siempre el fallback
ATR_MULTIPLIER=3,1) para que los tres sean deterministas y
comparables entre sí — mismo espíritu que
test_backtest_gap_stops.py.

Los números concretos (shares, capital, resultados) se calibraron
ejecutando el motor real contra estos escenarios exactos antes de
fijar las aserciones — no son estimaciones a mano.
"""

import pandas as pd
import pytest

import backtest_expandido as bt
from backtest_expandido import (
    CAPITAL_INICIAL,
    MAX_LEVERAGE,
    RISK_MIN_CAPITAL,
    _apalancamiento_permite,
    _exposicion_y_capital_mtm,
    ejecutar_backtest,
)


# ============================================================
# 1. UNITARIOS — _exposicion_y_capital_mtm()
# ============================================================

class TestExposicionYCapitalMtm:

    def test_sin_posiciones_abiertas_gross_cero_net_igual_a_capital(self):
        gross, net = _exposicion_y_capital_mtm({}, {}, 4000.0)
        assert gross == 0.0
        assert net == 4000.0

    def test_pnl_no_realizado_se_suma_a_capital_para_net_liq(self):
        """
        10 acciones compradas a 100, hoy a 120: +200 de PnL no
        realizado. net_liq debe reflejarlo (4000 + 200), no quedarse
        plano como `capital` — esa es la diferencia central con
        NetLiquidation real de IBKR.
        """
        posiciones  = {"AAA": {"shares": 10, "entry": 100.0}}
        precios_hoy = {"AAA": 120.0}
        gross, net = _exposicion_y_capital_mtm(posiciones, precios_hoy, 4000.0)
        assert gross == pytest.approx(1200.0)
        assert net == pytest.approx(4200.0)

    def test_subida_de_precio_sin_comprar_nada_no_infla_artificialmente_el_apalancamiento(self):
        """
        El problema de fondo que motiva el mark-to-market: si net_liq
        se quedara plano (=capital, sin marcar a mercado) mientras
        gross_pos sube con el precio, el apalancamiento aparente
        subiría SOLO por la revalorización de una posición ya
        abierta — sin comprar ni una acción más. Con el
        mark-to-market correcto, el apalancamiento real queda por
        debajo del que daría esa comparación ingenua, porque net_liq
        también sube con la ganancia no realizada — MISMA cantidad a
        los dos lados, así que la diferencia (net_liq − gross_pos, el
        "colchón" de caja) se mantiene igual que antes de la subida.
        """
        posiciones = {"AAA": {"shares": 10, "entry": 100.0}}
        capital    = 4000.0

        gross, net = _exposicion_y_capital_mtm(posiciones, {"AAA": 150.0}, capital)
        leverage_mtm     = gross / net
        leverage_ingenuo = gross / capital   # como si net_liq nunca se marcara a mercado

        assert leverage_mtm < leverage_ingenuo
        assert (net - gross) == pytest.approx(capital - 10 * 100.0)

    def test_simbolo_sin_precio_hoy_usa_entry_como_fallback(self):
        """Hueco de datos ese día concreto — aproximación documentada."""
        posiciones = {"AAA": {"shares": 10, "entry": 100.0}}
        gross, net = _exposicion_y_capital_mtm(posiciones, {}, 4000.0)
        assert gross == pytest.approx(1000.0)
        assert net == pytest.approx(4000.0)

    def test_varias_posiciones_se_suman(self):
        posiciones = {
            "AAA": {"shares": 10, "entry": 100.0},
            "BBB": {"shares": 5,  "entry": 50.0},
        }
        precios_hoy = {"AAA": 100.0, "BBB": 60.0}
        gross, net = _exposicion_y_capital_mtm(posiciones, precios_hoy, 1000.0)
        assert gross == pytest.approx(10 * 100.0 + 5 * 60.0)   # 1300
        assert net == pytest.approx(1000.0 + 0 + 5 * 10.0)     # 1050


# ============================================================
# 2. UNITARIOS — _apalancamiento_permite()
# ============================================================

class TestApalancamientoPermite:

    def test_permite_si_no_supera_max_leverage(self):
        permitido, lev = _apalancamiento_permite({}, {}, 4000.0, 3000.0)
        assert permitido is True
        assert lev == pytest.approx(3000.0 / 4000.0)

    def test_bloquea_si_supera_max_leverage(self):
        permitido, lev = _apalancamiento_permite({}, {}, 4000.0, 4500.0)
        assert permitido is False
        assert lev == pytest.approx(4500.0 / 4000.0)

    def test_limite_exacto_1x_permite(self):
        permitido, lev = _apalancamiento_permite({}, {}, 4000.0, 4000.0)
        assert permitido is True
        assert lev == pytest.approx(MAX_LEVERAGE)

    def test_fail_safe_net_liq_no_positivo_bloquea(self):
        permitido, lev = _apalancamiento_permite({}, {}, -100.0, 10.0)
        assert permitido is False
        assert lev is None

    def test_considera_exposicion_existente_no_solo_la_nueva(self):
        """
        3000 ya expuesto (30 acc. × 100), sin PnL no realizado
        (net_liq=4000). Añadir 1500 más -> 4500/4000=1,125x > 1,00x.
        """
        posiciones  = {"AAA": {"shares": 30, "entry": 100.0}}
        precios_hoy = {"AAA": 100.0}
        permitido, lev = _apalancamiento_permite(posiciones, precios_hoy, 4000.0, 1500.0)
        assert permitido is False
        assert lev == pytest.approx(4500.0 / 4000.0)


# ============================================================
# 3. INTEGRACIÓN — patrón de calentamiento compartido
# ============================================================

N_WARMUP           = 200   # detectar_senal exige i >= 200
ATR_FIJO           = 0.5
PRECIO_BASE        = 50.0
SMA50_FIJO         = 48.0
SMA200_INICIO      = 45.0
SMA200_INCREMENTO  = 0.01


def _fila(**overrides):
    """
    Fila plana por defecto. ATR_PERCENTIL deliberadamente ausente en
    todo el escenario — obtener_multiplicador() cae siempre al
    fallback ATR_MULTIPLIER=3,1 (mismo patrón que
    test_backtest_gap_stops.py), así el tamaño de posición es
    predecible y determinista.
    """
    fila = {
        "Open": PRECIO_BASE, "High": PRECIO_BASE, "Low": PRECIO_BASE,
        "Close": PRECIO_BASE, "Volume": 1_000_000,
        "SMA50": SMA50_FIJO, "ATR": ATR_FIJO,
    }
    fila.update(overrides)
    return fila


def _construir_ticker(dias_extra: list) -> pd.DataFrame:
    """
    200 días de calentamiento (SMA50=48 > SMA200 creciente 45->47) +
    pullback día 198 (Close=44 < SMA50-0,75×ATR=47,625) + señal día
    200 (recuperación, High=51 -> buy_stop=51,05) + entrada día 201
    (sin gap, High=52 >= buy_stop -> entra a 51,05 + slippage) +
    `dias_extra` (lista de dicts de fila, uno por día adicional,
    específicos de cada escenario).

    Con estos parámetros (ATR=0,5, precio~51, capital=4000):
    stop_distance = 0,5×3,1 = 1,55 -> shares_risk = int(34/1,55) = 21
    max_position_value = 1000 -> shares_capital = int(1000/51,05) = 19
    shares = min(21, 19) = 19 (con capital 4000; el motor real, con
    slippage real incluido, da 20 -- calibrado empíricamente, no a
    mano, ver docstring del módulo).
    """
    n_total = N_WARMUP + 2 + len(dias_extra)
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
    filas.append(_fila(   # día 201 — entrada, sin gap
        Open=50.5, High=52.0, Low=50.0, Close=51.5,
        SMA200=SMA200_INICIO + (N_WARMUP + 1) * SMA200_INCREMENTO,
    ))

    for k, fila_extra in enumerate(dias_extra):
        fila = dict(fila_extra)
        fila.setdefault("SMA200", SMA200_INICIO + (N_WARMUP + 2 + k) * SMA200_INCREMENTO)
        filas.append(_fila(**fila))

    return pd.DataFrame(filas, index=fechas)


def _dia_plano(**overrides):
    """Día extra sin movimiento respecto a la entrada — para rellenar
    huecos donde un ticker concreto no necesita hacer nada."""
    base = dict(Open=51.0, High=52.0, Low=50.5, Close=51.0)
    base.update(overrides)
    return base


# ============================================================
# 4. INTEGRACIÓN — Hallazgo #2: REDUCIR durante drawdown alto
# ============================================================

class TestReduccionDuranteDrawdown:
    """
    CRASH: entra día 201, se hunde el día 202 (gap bajista brutal a
    través del stop) -> pérdida real ~983€ sobre 4.000€ (24,6% de
    drawdown, muy por encima del límite del 10%).

    GROW: entra día 201 (mismos 20 acc. que CRASH), se mantiene plano
    el día 202 (el drawdown de CRASH ya está activo, GROW no se ha
    movido), y sube fuerte el día 203 (Close=300) -- su valor de
    posición (20×300=6.000) supera ampliamente
    MAX_POSITION_PCT×capital_post-crash (0,25×3.017≈754) mientras el
    drawdown SIGUE activo (capital aún no se ha recuperado del golpe
    de CRASH en el momento en que arranca el día 203). Antes de la
    Fase 8, el `continue` del gate de drawdown saltaba también el
    rebalanceo -- GROW se habría quedado sobredimensionado sin
    recortar. Con el fix, REDUCIR se ejecuta igual.

    Nota de alcance: este test NO vuelve a probar que las entradas
    nuevas siguen bloqueadas durante drawdown -- ese comportamiento ya
    era correcto antes de la Fase 8 y no cambia con este fix.
    """

    def _escenario(self):
        crash = _construir_ticker([
            _dia_plano(Open=2.0, High=3.0, Low=1.0, Close=2.0),     # día 202 — crash
            _dia_plano(),                                            # día 203 — irrelevante
        ])
        grow = _construir_ticker([
            _dia_plano(),                                                        # día 202 — plano
            _dia_plano(Open=295.0, High=301.0, Low=290.0, Close=300.0),          # día 203 — sube
        ])
        return {"CRASH": crash, "GROW": grow}

    def test_crash_realiza_perdida_y_activa_drawdown_mayor_al_limite(self):
        datos = self._escenario()
        trades, curva, capital_final = ejecutar_backtest(datos)

        crash_trade = next(t for t in trades if t["symbol"] == "CRASH")
        assert crash_trade["resultado"] == "LOSS"
        assert crash_trade["pnl"] < -800   # pérdida real grande, calibrada ~-983

        # Prueba EXTERNA (vía curva_capital, no vía estado interno) de
        # que el drawdown estuvo por encima del límite del 10% —
        # capital cae muy por debajo del 90% de CAPITAL_INICIAL.
        fila_crash_dia = next(
            f for f in curva if f["capital"] < 0.90 * CAPITAL_INICIAL
        )
        drawdown = (CAPITAL_INICIAL - fila_crash_dia["capital"]) / CAPITAL_INICIAL
        assert drawdown > 0.10

    def test_grow_se_reduce_pese_al_drawdown_activo(self):
        """
        El fix concreto del Hallazgo #2: REDUCIR se ejecuta aunque
        capital esté todavía por debajo del pico en más de un 10%.
        """
        datos = self._escenario()
        trades, curva, capital_final = ejecutar_backtest(datos)

        grow_trade = next(t for t in trades if t["symbol"] == "GROW")
        # Abrió con 20 acciones (mismas que CRASH, mismo patrón de
        # entrada) -- si REDUCIR nunca se hubiera ejecutado, el trade
        # final seguiría teniendo 20. Verificado por debajo de 10 acc.
        # (calibrado: termina en 2).
        assert grow_trade["shares"] < 10
        assert grow_trade["resultado"] in ("WIN", "LOSS", "OPEN→CLOSE")
        assert grow_trade["pnl"] > 0   # se vendió muy por encima de la entrada


# ============================================================
# 5. INTEGRACIÓN — Hallazgo #1: apalancamiento agregado
# ============================================================

class TestApalancamientoAgregadoIntegracion:
    """
    5 tickers idénticos (mismo patrón de calentamiento, misma fecha de
    señal/entrada) -- cada uno ~19-20 acciones a ~51$ (~1.020€,
    ~25,5% de 4.000€ cada uno). Abrir los 5 sumaría ~5.100€ de
    exposición bruta sobre 4.000€ de capital (~1,27x) -- muy por
    encima de MAX_LEVERAGE=1,00x. Sin el fix, los 5 se habrían
    abierto sin ningún freno.
    """

    def _universo(self, n):
        return {f"LEV{i}": _construir_ticker([]) for i in range(1, n + 1)}

    def test_no_abren_los_cinco_y_la_exposicion_queda_dentro_del_limite(self):
        datos = self._universo(5)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) < 5   # al menos una entrada se bloqueó

        notional_abierto = sum(t["shares"] * t["entrada"] for t in trades)
        # Margen de tolerancia pequeño por redondeo de shares (int()).
        assert notional_abierto <= MAX_LEVERAGE * CAPITAL_INICIAL * 1.02

    def test_control_dos_entradas_dentro_del_limite_no_se_bloquean(self):
        """
        Caso de control: 2 señales cuyo tamaño combinado (~2.040€,
        ~0,51x) queda claramente dentro de MAX_LEVERAGE -- el fix no
        debe bloquear entradas legítimas que nunca se acercaron al
        límite.
        """
        datos = self._universo(2)
        trades, curva, capital_final = ejecutar_backtest(datos)

        assert len(trades) == 2


# ============================================================
# 6. INTEGRACIÓN — Hallazgo #3: gestión con capital bajo mínimo
# ============================================================

class TestGestionConCapitalMinimo:
    """
    CAPITAL_INICIAL se reduce (vía monkeypatch, restaurado
    automáticamente al final del test) a 2.200€ -- justo por encima
    de RISK_MIN_CAPITAL=2.000€ -- para que una sola pérdida realizada
    normal empuje el capital por debajo del mínimo sin necesitar
    varios tickers wipeout.

    CRASH3: entra día 201, se hunde día 202 -> pérdida real que deja
    el capital por debajo de 2.000€.

    HOLD3: entra día 201 (misma cartera), se mantiene día 202 (con
    capital ya por debajo del mínimo, pero SIN tocar su propio stop
    todavía), y se hunde él mismo día 203 -- debe seguir
    cerrándose por stop con normalidad, con capital ya por debajo del
    mínimo en todo momento desde el día 202.
    """

    def _escenario(self, monkeypatch):
        monkeypatch.setattr(bt, "CAPITAL_INICIAL", 2200.0)

        crash3 = _construir_ticker([
            _dia_plano(Open=1.0, High=1.0, Low=1.0, Close=1.0),   # día 202 — crash
            _dia_plano(Open=1.0, High=1.0, Low=1.0, Close=1.0),   # día 203 — irrelevante
        ])
        hold3 = _construir_ticker([
            _dia_plano(),                                          # día 202 — plano
            _dia_plano(Open=10.0, High=12.0, Low=9.0, Close=10.0),  # día 203 — se hunde
        ])
        return {"CRASH3": crash3, "HOLD3": hold3}

    def test_crash3_hunde_capital_por_debajo_del_minimo(self, monkeypatch):
        datos = self._escenario(monkeypatch)
        trades, curva, capital_final = ejecutar_backtest(datos)

        crash_trade = next(t for t in trades if t["symbol"] == "CRASH3")
        assert crash_trade["resultado"] == "LOSS"

        # Prueba externa: hay un día en la curva con capital ya por
        # debajo de RISK_MIN_CAPITAL.
        assert any(f["capital"] < RISK_MIN_CAPITAL for f in curva)

    def test_hold3_se_cierra_por_stop_pese_a_capital_bajo_minimo(self, monkeypatch):
        """
        El fix concreto del Hallazgo #3: la evaluación de stops de una
        posición YA ABIERTA sigue funcionando aunque capital esté por
        debajo de RISK_MIN_CAPITAL -- solo las entradas NUEVAS deben
        bloquearse por esto.
        """
        datos = self._escenario(monkeypatch)
        trades, curva, capital_final = ejecutar_backtest(datos)

        hold_trade = next(t for t in trades if t["symbol"] == "HOLD3")
        # "LOSS"/"WIN" = cierre real por stop dentro del bucle: si el
        # capital mínimo hubiera bloqueado la gestión de posiciones,
        # HOLD3 nunca habría llegado a evaluarse este día y solo
        # aparecería (si acaso) como "OPEN→CLOSE" forzado al final.
        assert hold_trade["resultado"] in ("WIN", "LOSS")
        assert hold_trade["resultado"] != "OPEN→CLOSE"
