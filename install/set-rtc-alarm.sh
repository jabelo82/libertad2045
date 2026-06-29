#!/bin/bash
# /lib/systemd/system-shutdown/set-rtc-alarm.sh
# Ejecutado por systemd-shutdown JUSTO ANTES del poweroff, después de que todos
# los servicios han parado. Garantiza que el RTC alarm sobrevive cualquier
# limpieza que systemd haga durante el shutdown.

SHUTDOWN_MODE="${1:-poweroff}"
[ "$SHUTDOWN_MODE" != "poweroff" ] && exit 0

TZ_LOC="Europe/Madrid"
HOUR=$(TZ=$TZ_LOC date +%H)
MIN=$(TZ=$TZ_LOC date +%M)
DOW=$(TZ=$TZ_LOC date +%u)   # 1=lun ... 7=dom
MINS=$(( 10#${HOUR} * 60 + 10#${MIN} ))

if [ "${DOW}" -gt 5 ]; then
    # Sábado o domingo → lunes 22:00
    WAKE=$(TZ=$TZ_LOC date -d "next Monday 11:50:00" +%s)
    DESC="lunes 11:50 (watchdog)"
elif [ "${MINS}" -lt $(( 11 * 60 + 50 )) ]; then
    # Antes de 11:50 → watchdog de hoy 11:50
    WAKE=$(TZ=$TZ_LOC date -d "today 11:50:00" +%s)
    DESC="hoy 11:50 (watchdog)"
elif [ "${MINS}" -lt $(( 22 * 60 )) ]; then
    # 11:50–22:00 → bot de esta noche 22:00
    WAKE=$(TZ=$TZ_LOC date -d "today 22:00:00" +%s)
    DESC="hoy 22:00 (bot)"
else
    # Después de 22:00 (bot corriendo o apagado nocturno) → próximo laborable 11:50
    if [ "${DOW}" -ge 5 ]; then
        WAKE=$(TZ=$TZ_LOC date -d "next Monday 11:50:00" +%s)
        DESC="lunes 11:50 (próximo laborable)"
    else
        WAKE=$(TZ=$TZ_LOC date -d "tomorrow 11:50:00" +%s)
        DESC="mañana 11:50 (próximo ciclo)"
    fi
fi

/sbin/rtcwake -m no -t "${WAKE}"
logger -t "trading-rtc-hook" "poweroff → RTC programado: ${DESC} ($(TZ=$TZ_LOC date -d @${WAKE}))"
