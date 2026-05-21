# LIBERTAD_2045
Sistema de trading algorítmico autónomo

Arquitecto del sistema: Javier Beneito
Inicio del proyecto: 2026


------------------------------------------------------------
1. PROPÓSITO
------------------------------------------------------------

LIBERTAD_2045 es un sistema de trading sistemático diseñado para:

• analizar mercados
• tomar decisiones basadas en reglas
• ejecutar operaciones automáticamente
• gestionar riesgo de forma estricta
• operar durante años

El objetivo es construir una máquina financiera autónoma.


------------------------------------------------------------
2. DOCUMENTOS FUNDACIONALES
------------------------------------------------------------

00_LIBERTAD2045_CONTEXT.txt
Contexto completo del proyecto — estado actual, parámetros,
arquitectura, backtests, infraestructura y próximos pasos.

01.LIBERTAD_2045_CONSTITUCIÓN.txt
Principios, filosofía y diseño del sistema.

INFRAESTRUCTURA_IBG.md
Arquitectura de IB Gateway en la torre: ciclo de encendido/apagado,
servicios systemd, bugs corregidos y comandos de diagnóstico.

enlaces_chats.txt
Índice histórico de conversaciones del proyecto.


------------------------------------------------------------
3. ARQUITECTURA DEL SOFTWARE
------------------------------------------------------------

NÚCLEO OPERATIVO

libertad2045.py
Orquestador principal del sistema. Controla el ciclo diario completo.

conexion_ib.py
Conexión con Interactive Brokers. Reintentos automáticos.

data_loader.py
Descarga datos históricos (2Y). Calcula SMA50, SMA200, ATR, ATR_PERCENTIL.

signal_engine.py
Detección de señales: tendencia + pullback adaptativo (ATR×0.75) + recuperación.

position_size.py
Tamaño de posición por riesgo fijo (0.85% capital). Multiplicador ATR dinámico.

portfolio_manager.py
Gestión de posiciones abiertas. Evaluación de stops por precio de cierre.

order_manager.py
Gestión de órdenes pendientes. Cancela DAY, protege GTC.

trade_executor.py
Ejecución de órdenes BUY STOP + SELL STOP GTC. Confirmación de fills.

risk_guardian.py
Control de riesgo previo a operar: conexión, horario, capital, drawdown, apalancamiento.

rebalance.py
Rebalanceo diario de posiciones y break-even automático (+1.5×ATR).

logger.py
Registro histórico en CSV diarios. Limpieza automática > 90 días.

telegram.py
Notificaciones push al operador. Reintentos automáticos en alertas críticas.

universe_sp500.py
Universo de ~420 acciones del S&P 500 operables.

process_guard.py
Lock file para evitar ejecuciones simultáneas del bot.

dashboard.py
Genera dashboard.html con posiciones, PnL actual, historial de trades.

github_publisher.py
Publica el dashboard en GitHub Pages tras cada ejecución.

watchdog.py
Monitor de salud (12:00 L–V): heartbeat, IBG, GTC, RTC, auto-relaunch.

SCRIPTS DE AUTOMATIZACIÓN

run_bot.sh          Activa venv y lanza libertad2045.py (cron 22:10 L–V)
watchdog.sh         Lanza watchdog.py (cron 12:00 L–V)
setup.sh            Instalación inicial del entorno virtual
ibc_autostart.sh    Arranca Xvfb + IB Gateway vía IBC (en ~/ibc_autostart.sh)

HERRAMIENTAS DE ANÁLISIS (uso en Fase 3, no operativas)

backtest_expandido.py              Motor principal de backtesting con reinversión
backtest_2005/2010/2015.py         Backtests por período histórico
market_filter.py                   Filtro macro por estado del mercado (preparado)
data_manager.py                    Caché Yahoo Finance para backtests. Descarga datos
                                   una vez y los guarda en data/. El bot en vivo usa
                                   data_loader.py (IBKR), no este módulo.
montecarlo_analysis.py             Análisis Montecarlo
generar_reportes.py                Generación de informes HTML
generar_linea_cordura.py           Gráfico comparativo vs S&P500
add_montecarlo_sheet.py            Hoja Montecarlo en Excel

_cuarentena/                       Scripts retirados del sistema, conservados por
                                   precaución. Contenido actual: fix_aapl_stop.py


------------------------------------------------------------
4. FLUJO OPERATIVO DEL SISTEMA
------------------------------------------------------------

22:10 CET (L–V)
↓
run_bot.sh → libertad2045.py
↓
process_guard: acquire_lock
↓
conexion_ib: conectar IBKR
↓
risk_guardian: 5 comprobaciones
↓
portfolio_manager: evaluar stops por cierre
↓
rebalance: rebalanceo + break-even
↓
data_loader: descargar datos 2Y (~420 tickers)
↓
signal_engine: detectar señales
↓
position_size: calcular tamaño
↓
order_manager: cancelar órdenes DAY
↓
trade_executor: enviar órdenes + confirmar fills
↓
logger: CSV diario
↓
telegram: notificar operador
↓
dashboard: regenerar HTML
↓
github_publisher: publicar en Pages
↓
process_guard: release_lock

12:00 CET (L–V)
↓
watchdog.sh → watchdog.py
↓
heartbeat + IBG + GTC + RTC + auto-relaunch


------------------------------------------------------------
5. INFRAESTRUCTURA
------------------------------------------------------------

Sistema operativo: Linux (Zorin OS)
Lenguaje: Python 3.12
Broker: Interactive Brokers
Notificaciones: Telegram
Automatización: cron + systemd timers
Servidor operativo: Torre dedicada 192.168.1.139

Ciclo automático de encendido/apagado:
  11:50 → RTC despierta la torre
  12:00 → watchdog
  12:10 → apagado de mediodía (programa RTC 22:00)
  22:00 → RTC despierta la torre
  22:10 → bot de trading
  22:20 → apagado de noche (programa RTC 11:50 siguiente L–V)

IB Gateway:
  Servicio: ~/.config/systemd/user/ibgateway.service
  Restart=on-failure, RestartSec=60
  Ver INFRAESTRUCTURA_IBG.md para detalles completos.


------------------------------------------------------------
6. MODO DE OPERACIÓN
------------------------------------------------------------

SIM
Simulación completa sin enviar órdenes.

PAPER
Trading en cuenta paper (puerto 4002). Fase actual.

LIVE
Trading con capital real (puerto 4001). Fase futura.


------------------------------------------------------------
7. FASE ACTUAL DEL SISTEMA
------------------------------------------------------------

FASE 2 — PAPER TRADING ACTIVO (desde 2026-04-14)
Actualización: 2026-05-06

Sistema ejecutándose automáticamente:

• cron activo: lunes a viernes 22:10 CET
• modo: PAPER (cuenta simulada IBKR)
• universo: ~420 acciones S&P500
• notificaciones Telegram operativas
• watchdog activo: lunes a viernes 12:00 CET
• ciclo encendido/apagado automático operativo

Estado del portfolio (05 mayo 2026):

• capital PAPER:  1.113.884,28 € (pico histórico)
• posiciones abiertas (10/10 MAX_POS):
    INTC · LLY · C · BMY · ILMN
    AKAM · AAPL · BWA · CBOE · CAT

Siguiente fase:

FASE 3 — REVISIÓN Y MEJORA (agosto 2026)

Objetivo:
• analizar resultados del paper trading (mayo–julio 2026)
• ejecutar nuevo ciclo de mejoras
• comparar contra la línea base v2


------------------------------------------------------------
8. FILOSOFÍA DEL SISTEMA
------------------------------------------------------------

LIBERTAD_2045 no intenta predecir el mercado.

El sistema busca:

• identificar tendencias
• gestionar el riesgo
• explotar probabilidades

El sistema acepta pérdidas frecuentes
si las ganancias superan a las pérdidas.


------------------------------------------------------------
8.1 CONSTITUCIÓN DEL SISTEMA
------------------------------------------------------------

Los 7 artículos que rigen el diseño, operación y evolución.
Documento completo: 01.LIBERTAD_2045_CONSTITUCIÓN.txt

I.   DISCIPLINA DEL SISTEMA
     Las decisiones de trading no se alteran manualmente durante la ejecución.
     El operador no interviene en la lógica de decisión durante el ciclo operativo.

II.  SUPERVIVENCIA DEL SISTEMA
     La supervivencia tiene prioridad sobre la maximización del beneficio.
     Toda operación debe estar protegida por mecanismos de control de riesgo.
     El sistema limitará: riesgo por operación, exposición por posición,
     exposición total de cartera.

III. EVOLUCIÓN CONTROLADA
     El sistema puede evolucionar, pero toda modificación debe ser controlada.
     Los módulos no se modifican parcialmente — cuando se introduce una mejora,
     el módulo completo se reemplaza.
     La evolución debe preservar la coherencia de la arquitectura.

IV.  SIMPLICIDAD ESTRUCTURAL
     La arquitectura debe mantenerse simple y legible.
     Cada módulo tiene una única responsabilidad claramente definida.
     La complejidad innecesaria se considera una amenaza para la estabilidad.

V.   ROBUSTEZ OPERATIVA
     El sistema debe resistir fallos técnicos y operativos:
     errores de datos, fallos en activos individuales,
     interrupciones de servicios externos, reinicios del sistema.
     La arquitectura privilegia la resiliencia frente a la fragilidad.

VI.  REGISTRO HISTÓRICO
     El sistema registra los eventos relevantes para permitir auditorías futuras.
     Las decisiones de trading y eventos operativos quedan documentados de forma
     persistente. El comportamiento debe poder reconstruirse años después.

VII. AUTOMATIZACIÓN
     LIBERTAD_2045 está diseñado para operar de forma autónoma.
     La intervención humana se limita a: mantenimiento técnico, supervisión
     del sistema, evolución controlada de la arquitectura.
     La ejecución del proceso de inversión es completamente automática.


------------------------------------------------------------
9. OPERADOR DEL SISTEMA
------------------------------------------------------------

Javier Beneito

Su función es:

• supervisar
• mejorar
• proteger el sistema


------------------------------------------------------------
10. VISIÓN DEL PROYECTO
------------------------------------------------------------

LIBERTAD_2045 no es un bot.

Es una máquina.

Una máquina diseñada para:

observar
decidir
actuar
registrar
mejorar

Durante años.


------------------------------------------------------------
11. VERSIONES DEL SISTEMA
------------------------------------------------------------

──────────────────────────────────────────
VERSIÓN 2 — activa desde 2026-04-14
──────────────────────────────────────────

5 mejoras implementadas sobre la v1:

  M1 · data_loader.py
       Ventana de descarga: 1Y → 2Y
       Activa el stop dinámico B1 (ATR percentil).

  M2 · signal_engine.py
       Ventana de pullback: 1 día → 3 días

  M3 · libertad2045.py
       Score de ranking mejorado:
       ANTES : (close − SMA50) / ATR
       AHORA : (close − SMA50) / ATR + pendiente_SMA200_5d / ATR

  M4 · rebalance.py
       Break-even automático:
       Si precio ≥ entry + 1.5×ATR → stop sube a entry + 0.5×ATR

  M5 · signal_engine.py
       Umbral de pullback: fijo 2% → ATR-adaptativo
       ANTES : close_prev < SMA50_prev × 0.98
       AHORA : close_prev < SMA50_prev − ATR_prev × 0.75

LÍNEA BASE v2 — resultados validados en backtest (2026-04-14):

  Período 2015–2025 : retorno  6.997% | win rate 60.3% | PF 2.2484 | DD  8.8%
  Período 2010–2025 : retorno 33.826% | win rate 62.1% | PF 2.2709 | DD  8.9%
  Período 2005–2025 : retorno 94.256% | win rate 60.9% | PF 2.2711 | DD 12.2%

  Capital inicial en backtest  :  4.000 € + 4.000 €/año
  Capital final 2015–2025      :    283.884 €
  Capital final 2010–2025      :  1.357.059 €
  Capital final 2005–2025      :  3.774.259 €

  Montecarlo 1000 sim: prob. ruina 0%, DD p95 10.6%, capital peor 5% 945K€

Cualquier mejora futura debe superar estos números para ser adoptada.

──────────────────────────────────────────
VERSIÓN 1 — archivada (hasta 2026-04-14)
──────────────────────────────────────────

  Período 2015–2025 : retorno  4.439% | win rate 49.3% | PF 2.1991 | DD  9.3%
  Período 2010–2025 : retorno 14.258% | win rate 51.2% | PF 2.1921 | DD  9.3%
  Período 2005–2025 : retorno 27.032% | win rate 47.9% | PF 2.1758 | DD 12.6%


------------------------------------------------------------
