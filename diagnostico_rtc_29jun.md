# Diagnóstico RTC — 29/06/2026

## Causa exacta

La torre se apagó a las **02:15 del sábado 27/06** (DOW=6).
`apagar_torre.sh` en modo `auto` entró en la rama de fin de semana y programó el RTC para **lunes 22:00** en lugar de **lunes 11:50**, saltándose el watchdog.

## Secuencia real (del journal)

```
Jun 26 22:01  trading-boot-rtcwake   RTC = lun 29/06 11:50  ✓
Jun 26 22:20  trading-night-shutdown  RTC = lun 29/06 11:50  ✓  → poweroff
              -- arranque intermedio --
Jun 26 23:27  trading-boot-rtcwake   RTC = lun 29/06 11:50  ✓
Jun 27 02:15  apagar_torre.sh        RTC sobrescrito → lun 29/06 22:00  ✗
              set-rtc-alarm.sh       ídem (mismo código)                ✗
```

## Bug: rama de fin de semana

### apagar_torre.sh (línea 30-31) y set-rtc-alarm.sh (línea 18):

```bash
# ACTUAL (buggy):
if [ "${DOW}" -gt 5 ]; then
    WAKE=$(TZ=$TZ_LOC date -d "next Monday 22:00:00" +%s)

# CORRECTO:
if [ "${DOW}" -gt 5 ]; then
    WAKE=$(TZ=$TZ_LOC date -d "next Monday 11:50:00" +%s)
```

### trading-boot-rtcwake.sh (línea 7):

```bash
# ACTUAL (buggy):
    WAKE_EPOCH=$(TZ=Europe/Madrid date -d "next Monday 22:00:00" +%s)

# CORRECTO:
    WAKE_EPOCH=$(TZ=Europe/Madrid date -d "next Monday 11:50:00" +%s)
```

## Archivos a corregir

1. /usr/local/bin/apagar_torre.sh
2. /usr/local/bin/trading-boot-rtcwake.sh
3. /home/jabelo/PROYECTO_LIBERTAD_2045/install/apagar_torre.sh  (copia de instalación)
4. /home/jabelo/PROYECTO_LIBERTAD_2045/install/set-rtc-alarm.sh (copia de instalación)

El archivo en /lib/systemd/system-shutdown/set-rtc-alarm.sh es el que systemd
ejecuta en cada poweroff — es la copia de install/set-rtc-alarm.sh.

## Comandos sudo a ejecutar (archivos de sistema)

sudo sed -i \
  -e 's|date -d "next Monday 22:00:00"|date -d "next Monday 11:50:00"|' \
  -e 's|DESC="auto: lunes 22:00 (fin de semana)"|DESC="auto: lunes 11:50 (watchdog)"|' \
  /usr/local/bin/apagar_torre.sh

sudo sed -i \
  -e 's|date -d "next Monday 22:00:00"|date -d "next Monday 11:50:00"|' \
  -e 's|rtcwake lunes 22:00"|rtcwake lunes 11:50"|' \
  /usr/local/bin/trading-boot-rtcwake.sh

sudo sed -i \
  -e 's|date -d "next Monday 22:00:00"|date -d "next Monday 11:50:00"|' \
  -e 's|DESC="lunes 22:00 (fin de semana)"|DESC="lunes 11:50 (watchdog)"|' \
  /lib/systemd/system-shutdown/set-rtc-alarm.sh

## Por qué el fix no rompe nada

El primer evento del lunes es siempre el watchdog (11:50), no el bot (22:00).
Si el RTC despierta la máquina a las 11:50, el watchdog corre a las 12:00,
y a las 12:10 el trading-midday-shutdown.sh programa RTC → 22:00 y apaga.
La cadena se recupera sola sin ningún cambio adicional.
