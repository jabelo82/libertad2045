# Experimentos Descartados

Inventario de experimentos que fallaron en backtest o fueron abandonados.
Registro obligatorio per Artículo VI de la Constitución: todo fracaso documentado
es conocimiento, no basura.

---

## EL MOTOR — Momentum puro de breakouts

**Archivo:** `el_motor_breakout_momentum.py`
**Fecha:** junio 2026
**Estado:** Descartado — fracaso en backtest

### Filosofía del experimento

Momentum sistemático puro: poseer siempre las acciones con mayor impulso relativo
del universo. Comprar breakouts y fuerza confirmada, nunca esperar pullbacks.

Diferencias clave vs LIBERTAD_2045:
- L2045 espera pullbacks; EL MOTOR compraba breakouts directos.
- L2045 tiene señal binaria; EL MOTOR rankeaba y rotaba los N mejores scores.
- L2045 no tiene filtro de régimen global; EL MOTOR usaba % stocks > SMA200.
- Tamaño de posición por ATR (riesgo fijo por trade).

### Resultado

Fracaso en backtest. El sistema no superó a LIBERTAD_2045 en las métricas
que importan (Sharpe, drawdown máximo, consistencia de retornos).

### Conclusión

La Constitución de LIBERTAD_2045 no son adornos decorativos sino el núcleo
del sistema. Los principios de supervivencia (Artículo II), simplicidad
(Artículo IV) y robustez probada (Artículo V) no son restricciones arbitrarias:
son la destilación de lo que funciona. Un experimento libre que los ignora no
es innovación, es ruido.

Los pullbacks, la señal binaria, la ausencia de filtro de régimen — son
decisiones de diseño respaldadas por evidencia, no descuidos.
