# Fix: dashboard no se generaba cuando Risk Guardian bloqueaba entradas

## Diagnóstico

**Archivo:** `libertad2045.py`

**El flujo del bug, paso a paso:**

Líneas 452–457 — cuando el Risk Guardian bloquea:
```python
if not risk_check(ib):
    log_event("WARN", "Risk Guardian bloqueó nuevas entradas — gestión de posiciones completada")
    send_telegram_critical("⚠️ LIBERTAD_2045 — Risk Guardian: nuevas entradas bloqueadas. Stops y rebalanceo activos.")
    _escribir_last_run()
    log_event("INFO", "last_run.txt actualizado — RG bloqueó entradas pero ciclo completado")
    return   # ← AQUÍ SALÍA. No llegaba al dashboard.
```

Líneas 675–686 — generación del dashboard (nunca alcanzada cuando RG bloqueaba):
```python
try:
    _dashboard.main()
    log_event("INFO", "Dashboard regenerado")
    ok_gh, msg_gh = publicar_dashboard()
    ...
```

El `return` en la línea 457 salía directamente fuera de la función, pasando por encima de todo el bloque del dashboard. El dashboard solo se generaba cuando el ciclo completo llegaba al final sin que el RG lo interrumpiera.

---

## Paso 2 — Test ANTES del fix (reproduce el bug)

**Fichero creado:** `tests/test_dashboard_rg_bloqueo.py`

Simula `risk_check=False` (apalancamiento 1,08× > 1,00×) y verifica que `_dashboard.main()` se llama.

**Resultado con código original:**
```
FAILED tests/test_dashboard_rg_bloqueo.py::TestDashboardConRGBloqueado::test_dashboard_se_genera_cuando_rg_bloquea_apalancamiento
AssertionError: Expected 'main' to have been called once. Called 0 times.
```
Bug confirmado y reproducido.

---

## Paso 3 — Fix mínimo aplicado

**Archivo modificado:** `libertad2045.py`

Se añade el bloque de generación del dashboard dentro de la rama de bloqueo RG, antes del `return`, como paso terminal desacoplado del escaneo de señales:

```python
if not risk_check(ib):
    log_event("WARN", "Risk Guardian bloqueó nuevas entradas — gestión de posiciones completada")
    send_telegram_critical("⚠️ LIBERTAD_2045 — Risk Guardian: nuevas entradas bloqueadas. Stops y rebalanceo activos.")
    _escribir_last_run()
    log_event("INFO", "last_run.txt actualizado — RG bloqueó entradas pero ciclo completado")

    # Dashboard — paso terminal desacoplado del escaneo de señales.
    # Se genera aunque RG haya bloqueado nuevas entradas para reflejar
    # el estado actualizado de la cartera (stops, rebalanceo ejecutados).
    try:
        _dashboard.main()
        log_event("INFO", "Dashboard regenerado")
        ok_gh, msg_gh = publicar_dashboard()
        if ok_gh:
            log_event("INFO", f"GitHub Pages actualizado: {msg_gh}")
        else:
            log_event("WARN", f"GitHub Pages no actualizado: {msg_gh}")
    except Exception as e_dash:
        log_event("WARN", f"Dashboard no regenerado: {e_dash}")

    return
```

---

## Paso 4 — Validación

**Test nuevo (después del fix):**
```
PASSED tests/test_dashboard_rg_bloqueo.py::TestDashboardConRGBloqueado::test_dashboard_se_genera_cuando_rg_bloquea_apalancamiento
```

**Suite completa:**
```
34 passed in 0.36s
```
(33 tests previos + 1 nuevo — ninguna regresión)

---

## Paso 5 — Commit local

```
commit cda3c4d
fix: generar dashboard siempre, incluso cuando Risk Guardian bloquea entradas (regresión)

El `return` en la rama de bloqueo del Risk Guardian (libertad2045.py) saltaba
por encima del bloque de generación del dashboard, dejando dashboard.html del
día anterior sin actualizar. Reproducido el 30/06/2026 (RG bloqueó por
apalancamiento 1,08× > 1,00×, ciclo completó stops y rebalanceo pero no
generó dashboard).

Fix: el bloque de generación del dashboard se ejecuta ahora en la rama de
bloqueo RG como paso terminal desacoplado del escaneo de señales, antes del
return. Cubre ambas rutas del ciclo (con y sin bloqueo RG).

Test añadido: tests/test_dashboard_rg_bloqueo.py — simula risk_check=False y
verifica que _dashboard.main() se llama. 34 passed (33 previos + 1 nuevo).
```

**NO se ha hecho push.** Pendiente de revisión antes del despliegue.

---

## Ficheros modificados / creados

| Fichero | Cambio |
|---|---|
| `libertad2045.py` | +15 líneas (bloque dashboard en rama RG) |
| `tests/test_dashboard_rg_bloqueo.py` | nuevo (174 líneas) |
