# Infraestructura IB Gateway — Torre jabelo@192.168.1.139

*Documentado el 05/05/2026 tras análisis completo del sistema*

---

## Arquitectura general del ciclo de vida

La torre sigue un ciclo de encendido/apagado automático diseñado para ahorrar energía y garantizar que IB Gateway arranca limpio cada sesión:

```
[RTC alarm]
    └─> Boot del sistema
          ├─ trading-boot-rtcwake.service   → programa el próximo despertar
          └─ ibgateway.service (usuario)    → lanza ibc_autostart.sh
                ├─ Xvfb :1 -screen 0 1024x768x24   (display virtual)
                └─ gatewaystart.sh -inline 1037     (IBC arranca IBG Java, en primer plano)

[Timers systemd — se disparan mientras el sistema está encendido]
    12:10 L-V → trading-midday-shutdown.timer  → programa RTC 22:00 + apaga
    22:20 L-V → trading-night-shutdown.timer   → programa RTC 11:50 mañana + apaga

[Cron del usuario]
    22:10 L-V → run_bot.sh        (bot de trading)
    12:00 L-V → watchdog.sh       (monitor de salud)
```

**Flujo típico de un día laborable:**
1. 11:50 — RTC despierta la torre (ciclo de mediodía)
2. Boot → `ibgateway.service` arranca IBG automáticamente
3. 12:00 — watchdog verifica: heartbeat, IBG, stops GTC, RTC
4. 12:10 — `trading-midday-shutdown.timer` programa RTC a 22:00 y apaga la torre
5. 22:00 — RTC despierta la torre (ciclo de noche)
6. Boot → `ibgateway.service` arranca IBG automáticamente
7. 22:10 — bot de trading se ejecuta
8. 22:20 — `trading-night-shutdown.timer` programa RTC a 11:50 mañana y apaga la torre

---

## Ficheros relevantes

### IB Gateway

| Fichero | Ruta | Descripción |
|---|---|---|
| Script de arranque | `~/ibc_autostart.sh` | Arranca Xvfb + IBG vía IBC |
| Servicio systemd (usuario) | `~/.config/systemd/user/ibgateway.service` | Gestiona el ciclo de vida de IBG |
| Log de arranque IBG | `~/ibc_reboot.log` | stdout/stderr de ibc_autostart.sh |
| Config IBC | `~/ibc/config.ini` | Configuración de IBC (credenciales, modo paper, etc.) |
| IBG versión | `~/Jts/ibgateway/1037/` | Binarios de IB Gateway v1037 |

### Ciclo de encendido/apagado

| Fichero | Ruta | Descripción |
|---|---|---|
| rtcwake al arranque | `/etc/systemd/system/trading-boot-rtcwake.service` | Reprograma RTC en cada boot |
| Script rtcwake | `/usr/local/bin/trading-boot-rtcwake.sh` | Lógica de programación según hora/día |
| Timer mediodía | `/etc/systemd/system/trading-midday-shutdown.timer` | Dispara a las 12:10 L-V |
| Servicio mediodía | `/etc/systemd/system/trading-midday-shutdown.service` | Llama al script de apagado de mediodía |
| Script mediodía | `/usr/local/bin/trading-midday-shutdown.sh` | Programa RTC 22:00 + poweroff |
| Timer noche | `/etc/systemd/system/trading-night-shutdown.timer` | Dispara a las 22:20 L-V |
| Servicio noche | `/etc/systemd/system/trading-night-shutdown.service` | Llama al script de apagado de noche |
| Script noche | `/usr/local/bin/trading-night-shutdown.sh` | Programa RTC 11:50 mañana + poweroff |

---

## Contenido de los ficheros clave

### `~/ibc_autostart.sh`
```bash
#!/bin/bash
sleep 5
Xvfb :1 -screen 0 1024x768x24 &
sleep 10
DISPLAY=:1 /home/jabelo/ibc/gatewaystart.sh -inline 1037 -g \
  --ibc-ini=/home/jabelo/ibc/config.ini --mode=paper \
  >> /home/jabelo/ibc_reboot.log 2>&1
```

El `gatewaystart.sh -inline` corre en **primer plano**: el script no termina hasta que IBG muere. Esto es importante porque `ibgateway.service` es `Type=simple` — systemd considera el servicio activo mientras el script esté vivo.

### `~/.config/systemd/user/ibgateway.service`
```ini
[Unit]
Description=IB Gateway via IBC (Xvfb + gatewaystart)
After=network.target

[Service]
Type=simple
ExecStart=/home/jabelo/ibc_autostart.sh
Restart=on-failure
RestartSec=60
StandardOutput=append:/home/jabelo/ibc_reboot.log
StandardError=append:/home/jabelo/ibc_reboot.log

[Install]
WantedBy=default.target
```

### `/usr/local/bin/trading-boot-rtcwake.sh` (lógica resumida)
- Si es fin de semana → programa RTC para el lunes a las 22:00
- Si es L-V y hora < 12 → programa RTC para hoy a las 11:50
- Si es L-V y 12 ≤ hora < 22 → programa RTC para hoy a las 22:00
- Si es L-V y hora ≥ 22 → programa RTC para mañana a las 11:50 (o lunes si es viernes)

---

## Puertos y conexión

| Concepto | Valor |
|---|---|
| Host IBG | `127.0.0.1` |
| Puerto PAPER | `4002` |
| Puerto LIVE | `4001` |
| clientId watchdog | `8` |
| clientId bot | `1` |
| Display virtual | `:1` |

---

## Bugs encontrados y corregidos (05/05/2026)

### Bug 1 — Scripts de shutdown sin `poweroff`

**Síntoma:** La torre nunca se apagaba automáticamente. El ciclo RTC diseñado no funcionaba porque los scripts `trading-midday-shutdown.sh` y `trading-night-shutdown.sh` programaban el RTC pero terminaban sin llamar a `poweroff`.

**Consecuencia:**
- La torre corría 24/7 sin reiniciarse entre sesiones
- El RTC se programaba pero al estar el sistema ya encendido cuando la alarma disparaba, no hacía nada
- El watchdog veía el RTC vacío (la alarma ya había expirado) y lo reprogramaba manualmente

**Fix:** Añadido `/sbin/poweroff` al final de ambos scripts.

---

### Bug 2 — `ibgateway.service` con `Restart=no`

**Síntoma:** Si IB Gateway caía (crash de Java, timeout de sesión IB, restart diario de IBC que salía con error), el servicio quedaba en estado `inactive` y nadie lo levantaba.

**Consecuencia:**
- IBG podía estar caído horas hasta el siguiente boot manual o reinicio de la torre
- El bot de las 22:10 fallaba con `Errno 111 Connection refused` si IBG no había vuelto
- El watchdog de las 12:00 lo detectaba pero no podía remediarlo (solo puede relanzar el bot Python, no IBG)

**Fix:** Cambiado a `Restart=on-failure` con `RestartSec=60`. Si IBG cae con error, systemd lo relanza al minuto automáticamente.

---

## Causa raíz del incidente de 05/05/2026

1. La torre no se apagaba automáticamente (Bug 1), así que IBG llevaba días corriendo sin reinicio limpio
2. IBG cayó en algún momento de la madrugada (restart diario de IBC u otro motivo)
3. Con `Restart=no` (Bug 2), IBG no volvió a levantarse
4. A las 12:00, el watchdog detectó: IBG inaccesible (`Errno 111`) y RTC vacío
5. El watchdog reprogramó el RTC a las 22:00 manualmente
6. Javier reinició la torre manualmente → IBG arrancó → bot corrió bien a las 22:14

---

## Comandos útiles de diagnóstico

```bash
# Estado de IB Gateway
systemctl --user status ibgateway.service

# Log de arranque de IBG
tail -50 ~/ibc_reboot.log

# Próxima alarma RTC programada
cat /sys/class/rtc/rtc0/wakealarm | xargs -I{} date -d @{}

# Log del watchdog
tail -100 ~/PROYECTO_LIBERTAD_2045/logs/cron_watchdog.log

# Ver cuándo se dispararon los timers de shutdown
journalctl -u trading-midday-shutdown.service -u trading-night-shutdown.service --no-pager -n 20

# Reiniciar IBG manualmente si es necesario
systemctl --user restart ibgateway.service
```

---

## Notas sobre IBC y el restart diario de IBG

IBC gestiona el ciclo de vida de IB Gateway. Por defecto, IBC reinicia IBG una vez al día en la ventana de mantenimiento de IB (~23:45 ET = ~05:45 Madrid). Este restart es **interno** a `gatewaystart.sh` — IBG para y arranca dentro del mismo proceso, por lo que `ibgateway.service` no lo ve como una caída y no interviene. Es normal y esperado.

Lo que sí puede causar una salida con error (y disparar `Restart=on-failure`) es:
- Crash de Java (OOM, excepción no capturada)
- `on2fatimeout=exit` en config.ini: si IBC pide 2FA y no responde nadie, sale con error
- Fallo de red que IBC no puede recuperar

Con el fix del Bug 2, cualquiera de estos casos levantará IBG al minuto automáticamente.
