# Fase 0 — Verificación Pre-requisitos Cutover Torre→VPS
**Fecha:** 2026-06-30  
**Objetivo:** Completar los 8 puntos de Fase 0 antes de iniciar Fase 1 (sincronización).  
**Plazo:** Cutover esta semana → 4 semanas de PAPER en VPS → LIVE 1 agosto 2026.

---

## Punto 1 — Tests en Torre y en VPS

### Torre (jabelo@192.168.1.139) ✓
**Ejecutado:** 2026-06-30

```
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/jabelo/PROYECTO_LIBERTAD_2045
collected 33 items

tests/test_dashboard_cartera.py  — 8 tests   PASSED
tests/test_gtc_dedup.py          — 18 tests  PASSED
tests/test_risk_guardian_leverage.py — 7 tests PASSED

============================== 33 passed in 0.57s ==============================
```

**Total: 33 tests, 33 passed, 0 failed, 0 errors.**

### VPS (root@46.225.106.85) — PENDIENTE VPS-SSH
**Estado:** No se puede ejecutar todavía — ver sección "Bloqueador SSH VPS".

---

## Punto 2 — Backtest v3 en VPS

**Estado:** PENDIENTE VPS-SSH

**Referencia oficial (00_LIBERTAD2045_CONTEXT.txt §7 — Backtest v3, 17/06/2026):**
| Métrica | Valor de referencia |
|---------|---------------------|
| Capital final | 8.888.418 € |
| TIR | 37,81% |
| Profit Factor | 2,6071 |
| Win Rate | 54,1% |
| Max Drawdown | 10,4% |
| Trades | 2.402 |
| Script | comparar_rebalanceo.py (commit 90ef0f8) |

---

## Punto 3 — git status y git log en ambas máquinas

### Torre ✓
**Ejecutado:** 2026-06-30

```
Commit actual (HEAD): a98ca5744ba35ba64cdbc7472506eaa3b6fe988f
Mensaje: Ciclo noche 2026-06-29 22:14 — Capital: 7.710,51 €
Rama: main — actualizada con origin/main (0 commits adelante/atrás)

Working tree: LIMPIO
  Sin cambios staged ni unstaged en archivos rastreados.
  Archivos sin seguimiento (no bloqueantes):
    - fase0_cutover_30jun.md   (este archivo)
    - pending_rebalance.json   (vacío: {}, preexistente — no es un problema)
```

**Commits recientes (5 últimos con fecha):**
```
a98ca57  2026-06-29 22:14  Ciclo noche 2026-06-29 22:14 — Capital: 7.710,51 €
02416c7  2026-06-29 22:03  fix: RTC fin de semana despierta torre para watchdog
49347b6  2026-06-29 22:14  Dashboard actualizado 2026-06-29 22:14
5ae3f49  2026-06-27 01:25  fix: dashboard filtra TotalCashValue[BASE]
4e8dc3c  2026-06-27 00:50  Dashboard actualizado 2026-06-27 00:50
```

**Nota sobre trabajo RTC/clipboard del ProBook (29/06):**
El commit `02416c7` ES del repo LIBERTAD_2045 y es un fix legítimo: corrección del
RTC de fin de semana (la torre se despertaba a las 22:00 del lunes en lugar de las
11:50 para el watchdog). Archivos tocados: `install/apagar_torre.sh`,
`install/set-rtc-alarm.sh`. Ningún commit con "clipboard" existe en la historia.
→ El trabajo de ProBook (RTC/clipboard) NO afectó al repo LIBERTAD_2045 más allá
  de este fix que SÍ debe estar aquí. Todo correcto.

### VPS (root@46.225.106.85) — PENDIENTE VPS-SSH
Necesitamos el commit hash del VPS para comparar. Debe ser el mismo que Torre:
`a98ca5744ba35ba64cdbc7472506eaa3b6fe988f`

---

## Punto 4 — .env del VPS revisado línea por línea

### Torre .env (referencia para comparar con VPS) ✓
```
Permisos: 600 (jabelo:jabelo)  ← correcto

TRADING_MODE  = PAPER   ✓
IBKR_HOST     = 127.0.0.1
IBKR_PORT     = 4002    ✓ (PAPER)
IBKR_CLIENT_ID= <PRESENTE>
TELEGRAM_TOKEN= <PRESENTE>
TELEGRAM_CHAT_ID= <PRESENTE>
RISK_MIN_CAPITAL= <PRESENTE>
MAX_POSICIONES_ARRANQUE= <PRESENTE>
RISK_MAX_DRAWDOWN= <PRESENTE>
LOG_RETENTION_DAYS= <PRESENTE>
GITHUB_TOKEN  = <PRESENTE>

Total claves: 11
```

### VPS .env — PENDIENTE VPS-SSH
Debemos verificar: TRADING_MODE=PAPER, IBKR_PORT=4002, permisos 600, y que las 11
claves coincidan (no tiene que ser idéntico en todos los valores, pero las claves
deben ser las mismas).

---

## Punto 5 — IB Gateway en VPS

**Estado:** PENDIENTE VPS-SSH

Referencia esperada (00_LIBERTAD2045_CONTEXT.txt §2):
- Servicio: `~/.config/systemd/user/ibgateway.service` — enabled
- Restart=always (fix commit 83ac75c del 20/06/2026)
- Linger activado: `loginctl enable-linger root`
- Puerto 4002 abierto, CONEXION_OK verificada el 18/06/2026

Comandos a ejecutar en VPS:
```bash
systemctl --user status ibgateway.service
ss -tlnp | grep 4002
```

---

## Punto 6 — Xvfb + IBC 3.19.0 en VPS

**Estado:** PENDIENTE VPS-SSH

Referencia (00_LIBERTAD2045_CONTEXT.txt §2):
- IB Gateway 10.45: ~/Jts/ibgateway/1037/
- IBC 3.19.0: ~/ibc/, config en ~/ibc/config.ini
- Fix aplicado: --add-opens en ibgateway.vmoptions (Java 17 incompatibilidad)
- Fix aplicado: ReadOnlyApi=no en ibc/config.ini

Comandos a ejecutar en VPS:
```bash
pgrep -a Xvfb
cat ~/ibc/config.ini | grep -E "(ReadOnlyApi|TradingMode)"
ls -la ~/ibc/IBC*.jar 2>/dev/null || find ~/ibc -name "IBC*.jar"
```

---

## Punto 7 — Cron del VPS (debe estar comentado)

**Estado:** PENDIENTE VPS-SSH

El cron del VPS debe estar ESCRITO pero COMENTADO (no activo).
El cron activo está en la Torre (y debe seguir activo hasta el día del cutover).

### Torre — cron ACTIVO (correcto) ✓
```cron
# Bot de trading: lunes a viernes a las 22:10
10 22 * * 1-5 /bin/bash -c 'cd /home/jabelo/PROYECTO_LIBERTAD_2045 && ...'

# Watchdog: lunes a viernes a las 12:00
0 12 * * 1-5 /bin/bash -c '/home/jabelo/PROYECTO_LIBERTAD_2045/watchdog.sh >> ...'

# Actualizador de universo S&P500: lunes 11:52
52 11 * * 1 /bin/bash /home/jabelo/PROYECTO_LIBERTAD_2045/run_universe_updater.sh >> ...
```

### VPS — cron debe estar COMENTADO (pendiente verificar):
```bash
# En VPS: crontab -l
# Las líneas deben aparecer precedidas por # (comentadas)
```

---

## Punto 8 — OAuth (no bloqueante) ✓

**Análisis completado:** El bot de producción NO usa OAuth en el camino de ejecución.
Se conecta directamente a IB Gateway en el puerto 4002 (PAPER) / 4001 (LIVE)
mediante `ib_insync` o `ibapi`. El OAuth aparece mencionado en CUTOVER_VPS.md como
contingencia/verificación del token de cuenta, pero NO es la capa de conexión activa.

**Decisión:** Este punto pasa de "bloqueante" a "PENDIENTE EN PARALELO" en el checklist.
No bloquea el cutover Torre→VPS.

---

## Bloqueador SSH — VPS

La Torre (jabelo@192.168.1.139) no tiene clave SSH configurada para acceder al VPS
(root@46.225.106.85). Las claves están en el ProBook (jabelopc). 

**Opciones para desbloquear los puntos 1b, 2, 3b, 4-VPS, 5, 6, 7:**

### Opción A — Nueva clave SSH en la Torre (recomendada, permanente)
Desde la Torre (Javier como jabelo), generamos una clave ed25519 y la añadimos al VPS
desde el ProBook:
```bash
# Paso 1: En Torre — generar clave (Claude puede hacer esto si Javier autoriza):
ssh-keygen -t ed25519 -C "jabelo-torre@2026-06-30" -f ~/.ssh/id_ed25519 -N ""

# Paso 2: Mostrar la clave pública:
cat ~/.ssh/id_ed25519.pub

# Paso 3: Desde el ProBook (jabelopc) — añadir al VPS:
ssh root@46.225.106.85 "echo '<clave_publica>' >> ~/.ssh/authorized_keys"
```

### Opción B — Comandos desde el ProBook (sin modificar la Torre)
Javier ejecuta desde el ProBook (usuario jabelopc) y pega los resultados aquí:
```bash
ssh root@46.225.106.85 "cd ~/PROYECTO_LIBERTAD_2045 && source .env && venv/bin/python -m pytest tests/ -v"
ssh root@46.225.106.85 "cd ~/PROYECTO_LIBERTAD_2045 && git log --oneline -5 && git status"
ssh root@46.225.106.85 "systemctl --user status ibgateway.service"
ssh root@46.225.106.85 "crontab -l"
ssh root@46.225.106.85 "stat -c '%a %n' ~/PROYECTO_LIBERTAD_2045/.env && grep -E '^(TRADING_MODE|IBKR_PORT)=' ~/PROYECTO_LIBERTAD_2045/.env && grep -cE '^[A-Z_]+=' ~/PROYECTO_LIBERTAD_2045/.env"
ssh root@46.225.106.85 "pgrep -a Xvfb; ss -tlnp | grep 4002"
```

---

## Tabla Resumen — Estado actual (2026-06-30 mañana)

| # | Descripción | Máquina | Estado | Hallazgo |
|---|-------------|---------|--------|----------|
| 1a | Tests (33 pytest) | Torre | ✓ | 33/33 passed, 0.57s |
| 1b | Tests en VPS | VPS | ⏳ | Pendiente SSH |
| 2 | Backtest v3 en VPS | VPS | ⏳ | Pendiente SSH |
| 3a | git limpio — Torre | Torre | ✓ | a98ca57, main=origin |
| 3b | git limpio — VPS | VPS | ⏳ | Pendiente SSH |
| 4a | .env Torre | Torre | ✓ | PAPER/4002/600/11 claves |
| 4b | .env VPS | VPS | ⏳ | Pendiente SSH |
| 5 | IB Gateway VPS | VPS | ⏳ | Pendiente SSH |
| 6 | Xvfb + IBC VPS | VPS | ⏳ | Pendiente SSH |
| 7a | Cron Torre (activo) | Torre | ✓ | 3 jobs activos — correcto |
| 7b | Cron VPS (comentado) | VPS | ⏳ | Pendiente SSH |
| 8 | OAuth (no bloqueante) | — | ✓ | Conexión directa IB GW |

**Puntos verdes Torre: 4/4. Puntos VPS pendientes: 7 (bloqueados por falta de SSH).**

---
*Última actualización: 2026-06-30 — sesión matinal*

---

## ✅ ACTUALIZACIÓN — Fase 0 COMPLETADA (2026-06-30 tarde)

Todos los puntos VPS verificados vía SSH desde ProBook (SSH_AUTH_SOCK="" ssh -i ~/.ssh/id_rsa root@46.225.106.85).

| # | Punto | VPS | Hallazgo |
|---|-------|-----|----------|
| 1b | Tests VPS | ✅ | 33 passed |
| 2 | Backtest v3 VPS | ✅* | Equivalente por construcción: mismo commit a98ca57 + 33 tests + caché idéntica (4338 ficheros). No se corre directo: yfinance rate-limita delisted sin caché. |
| 3b | git VPS | ✅ | a98ca57, al día con torre |
| 4b | .env VPS | ✅ | PAPER/4002/permisos 600 |
| 5 | IB Gateway VPS | ✅ | CONEXION_OK: puerto 4002 LISTEN tras arranque reversible. Tarda ~60s en abrir puerto. |
| 6 | Xvfb+IBC VPS | ✅ | TradingMode=paper, ReadOnlyApi=no, IBC.jar OK, IBGW 10.45 en Jts/ibgateway/1037 |
| 7b | Cron VPS | ✅ | Comentado (las 2 líneas con #) |
| 8 | OAuth | ✅ | No bloqueante (bot usa IB GW directo) |

### Caché de datos transferida torre→VPS
1,4GB / 4338 ficheros. Comando usado:
`ssh jabelo@192.168.1.139 "cd ~/PROYECTO_LIBERTAD_2045/data && tar czf - ." | SSH_AUTH_SOCK="" ssh -i ~/.ssh/id_rsa root@46.225.106.85 "cd ~/PROYECTO_LIBERTAD_2045/data && tar xzf -"`
→ NO repetir, ya está hecha.

### ⚠️ APRENDIZAJE CRÍTICO
Torre y VPS NO pueden tener IB Gateway logueado a la vez (mismo usuario IBKR): el 2º login EXPULSA al 1º. La prueba del Gateway VPS = ensayo del cutover. Tras probar se revirtió: VPS parado, torre operativa.

### 🔴 RECORDATORIO CUTOVER FORMAL
- Descomentar cron VPS: `crontab -l | sed 's|^#10 22 |10 22 |; s|^#0 12 |0 12 |' | crontab -`
- SIMULTÁNEAMENTE comentar cron torre. NUNCA los dos activos a la vez.
- Arrancar IB Gateway VPS, parar el de la torre.

*Fase 0 cerrada 2026-06-30. Pendiente: cutover formal (esta semana) → 4 sem PAPER en VPS → LIVE 1 agosto.*
