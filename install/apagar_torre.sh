#!/bin/bash
# Apagado manual seguro — siempre programa el RTC antes de poweroff.
# Uso: apagar_torre.sh [ciclo]
#   ciclo = "mediodia" | "noche" | auto (default: auto, calcula según hora)

set -euo pipefail
TZ_LOC="Europe/Madrid"

HORA=$(TZ=$TZ_LOC date +%H)
MIN=$(TZ=$TZ_LOC date +%M)
DOW=$(TZ=$TZ_LOC date +%u)
MINS=$(( 10#${HORA} * 60 + 10#${MIN} ))

CICLO="${1:-auto}"

if [ "$CICLO" = "mediodia" ]; then
    WAKE=$(TZ=$TZ_LOC date -d "today 22:00:00" +%s)
    DESC="forzado: hoy 22:00 (ciclo noche)"
elif [ "$CICLO" = "noche" ]; then
    if [ "${DOW}" -ge 5 ]; then
        WAKE=$(TZ=$TZ_LOC date -d "next Monday 11:50:00" +%s)
        DESC="forzado: lunes 11:50"
    else
        WAKE=$(TZ=$TZ_LOC date -d "tomorrow 11:50:00" +%s)
        DESC="forzado: mañana 11:50"
    fi
else
    # auto
    if [ "${DOW}" -gt 5 ]; then
        WAKE=$(TZ=$TZ_LOC date -d "next Monday 22:00:00" +%s)
        DESC="auto: lunes 22:00 (fin de semana)"
    elif [ "${MINS}" -lt $(( 11 * 60 + 50 )) ]; then
        WAKE=$(TZ=$TZ_LOC date -d "today 11:50:00" +%s)
        DESC="auto: hoy 11:50 (watchdog)"
    elif [ "${MINS}" -lt $(( 22 * 60 )) ]; then
        WAKE=$(TZ=$TZ_LOC date -d "today 22:00:00" +%s)
        DESC="auto: hoy 22:00 (bot)"
    else
        if [ "${DOW}" -ge 5 ]; then
            WAKE=$(TZ=$TZ_LOC date -d "next Monday 11:50:00" +%s)
            DESC="auto: lunes 11:50"
        else
            WAKE=$(TZ=$TZ_LOC date -d "tomorrow 11:50:00" +%s)
            DESC="auto: mañana 11:50"
        fi
    fi
fi

WAKE_HR=$(TZ=$TZ_LOC date -d "@${WAKE}")
echo "Programando RTC: ${DESC}"
echo "  Wake: ${WAKE_HR}"
echo "  Epoch: ${WAKE}"
echo ""
echo "¿Confirmar apagado? [s/N]"
read -r RESP
if [[ "$RESP" =~ ^[sS]$ ]]; then
    /usr/sbin/rtcwake -m no -t "${WAKE}"
    logger -t "apagar-torre" "apagado manual: ${DESC}"
    echo "RTC programado. Apagando..."
    /sbin/poweroff
else
    echo "Cancelado."
fi
