# Cutover formal Torre→VPS — COMPLETADO
**Fecha:** 2026-07-03

## Verificación pre-cutover
| Punto | Torre | VPS |
|---|---|---|
| Git HEAD | 677e1f5 | 677e1f5 |
| Tests | 34/34 | 34/34 |
| .env | PAPER/4002 | PAPER/4002 |
| capital_peak.txt | 8000 | 8000 |

VPS estaba 2 commits por detrás (971adb3). Se hizo pull --rebase + push de un
commit local pendiente (677e1f5, chmod +x run_bot.sh). Identidad git
configurada por primera vez en VPS: jabelo82 <jabelo82@gmail.com>,
credential.helper=store con GITHUB_TOKEN leído de .env.

## Corte formal — orden ejecutado
1. Parar IB Gateway torre → inactive (dead)
2. Arrancar IB Gateway VPS → puerto 4002 LISTEN tras ~65s
3. Comentar cron torre: bot 22:10, watchdog 12:00, universe updater lunes 11:52
4. Descomentar cron VPS: bot 22:10, watchdog 12:00
5. Cron universe updater: no existía en VPS, añadido de cero

## Hardening torre post-cutover
1. ibgateway.service → disabled (no arranca solo aunque la torre encienda)
2. Alarma RTC pendiente (03/07 11:50) → vaciada
3. trading-boot-rtcwake.service, trading-midday-shutdown.timer,
   trading-night-shutdown.timer → disabled los 3
4. Torre queda inerte: no se enciende sola, Gateway no arranca solo.
   Rollback manual disponible si hiciera falta.

## Estado final
- IB Gateway: activo en VPS, parado y disabled en torre
- Cron: activo en VPS, comentado en torre
- Ciclo RTC torre: completamente desactivado
- Próximo ciclo real: lunes 06/07 22:10, primero desde VPS
- Roadmap: PAPER en VPS 06/07 → 31/07 → LIVE lunes 03/08

## Pendiente
- Lunes 06/07: confirmar en Telegram/dashboard que universe updater 11:52 +
  watchdog 12:00 + bot 22:10 corrieron limpio en VPS.
