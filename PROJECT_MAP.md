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

00.LIBERTAD2045_CONTEXT.txt
Contexto completo del proyecto.

01.LIBERTAD_2045_MANIFESTO.docx
Principios, filosofía y diseño del sistema.

03.chat.txt
Historial de diseño del proyecto.

enlaces_chats.txt
Índice histórico de conversaciones del proyecto.


------------------------------------------------------------
3. ARQUITECTURA DEL SOFTWARE
------------------------------------------------------------

libertad2045.py
Orquestador principal del sistema.

conexion_ib.py
Conexión con Interactive Brokers.

data_loader.py
Descarga datos históricos del mercado.

signal_engine.py
Detección de señales de trading.

position_size.py
Cálculo del tamaño de posición basado en riesgo.

portfolio_manager.py
Gestión de posiciones abiertas.

order_manager.py
Gestión de órdenes pendientes.

trade_executor.py
Ejecución de operaciones.

logger.py
Registro histórico del sistema.

telegram.py
Notificaciones externas del sistema.

universe_sp500.py
Universo de activos analizados.


------------------------------------------------------------
4. FLUJO OPERATIVO DEL SISTEMA
------------------------------------------------------------

cron
↓
run_bot.sh
↓
libertad2045.py
↓
descarga datos
↓
detección de señales
↓
gestión de riesgo
↓
ejecución de trades
↓
registro en logs
↓
notificación Telegram


------------------------------------------------------------
5. INFRAESTRUCTURA
------------------------------------------------------------

Sistema operativo: Linux

Lenguaje: Python

Broker: Interactive Brokers

Notificaciones: Telegram

Automatización: cron

Servidor operativo: máquina dedicada


------------------------------------------------------------
6. MODO DE OPERACIÓN
------------------------------------------------------------

SIM
Simulación completa sin enviar órdenes.

PAPER
Trading en cuenta paper.

LIVE
Trading con capital real.


------------------------------------------------------------
7. FASE ACTUAL DEL SISTEMA
------------------------------------------------------------

FASE 1 — PROTOTIPO OPERATIVO

Estado actual:

• motor de trading operativo
• conexión IBKR verificada
• decision log activo
• estado de salud del sistema
• notificaciones Telegram funcionando

Siguiente fase:

FASE 2 — AUTOMATIZACIÓN

Objetivo inmediato:

• activar ejecución automática mediante cron


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