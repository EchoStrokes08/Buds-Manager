# redmi-buds

Control de Redmi Buds (probado con **REDMI Buds 8 Pro**, firmware 3.2.2.6) desde Linux, sin dependencias: solo Python 3 y BlueZ.

Usa el protocolo Xiaomi "MIWEAR" (RFCOMM canal 28, servicio `fd2d`), portado de
[Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) (GPL-3.0).

## Buds Manager (interfaz gráfica)

```
buds-manager
```

También aparece como **Buds Manager** en el lanzador (fuzzel) y se abre con clic central en el módulo de Waybar.
GTK4 + libadwaita, cinco pestañas:

- **Sonido**: batería de cada auricular y del estuche, modo (apagado / cancelación / transparencia),
  intensidad de la cancelación o tipo de transparencia, ANC adaptativo, ANC personalizado, sonido adaptativo.
- **Ecualizador**: perfiles de fábrica y curva de 10 bandas (−6..+6 dB) con curvas rápidas.
- **Controles**: acción de 1, 2 y 3 toques y de mantener pulsado por auricular, y qué modos recorre.
- **Dispositivo**: detección de oído, respuesta automática, prueba de ajuste de almohadillas, buscar auriculares,
  modelo/firmware/número de serie, copias de seguridad y restaurar la configuración original.
- **Sonido** también incluye *Viaje inmersivo* (avión / transporte público / tren) y los niveles de
  cancelación del 8 Pro (ligera / profunda + cancelación inteligente).
- **Controles** incluye *Deslizar* (ajustar volumen / nada).
- **Conexión**: códec en esta PC (LDAC / AAC / llamadas con micrófono) y calidad LDAC (regla de WirePlumber), multipunto (escuchar en dos dispositivos) y gestor Bluetooth: buscar, emparejar,
  conectar, desconectar y olvidar dispositivos (BlueZ por D-Bus). También aparece si los audífonos no están conectados.

Las tarjetas de batería indican si cada audífono está en el oído, en el estuche o fuera.

Se actualiza sola cada 8 s con la ventana enfocada (45 s en segundo plano). Cada operación abre una sesión
corta, así que el CLI, Waybar y los atajos siguen funcionando con la ventana abierta.

## Uso del CLI

```
buds                         # estado completo
buds battery
buds mode [off|anc|transparency|next|toggle] [--notify]
buds strength anc light|deep
buds strength transparency regular|voice|ambient
buds adaptive-anc on|off
buds commute off|airplane|transit|train
buds gesture swipe left|right|both volume|none
buds personalized-anc on|off
buds eq [standard|voice|bass|treble|volume|custom|balanced]
buds eq-band 62 +4           # una banda (-6..+6), activa el preset custom
buds eq-curve 4 3 2 0 0 0 1 2 2 1
buds gesture single|double|triple|long left|right|both <acción>
buds cycle left|right|both anc-off|transparency-off|anc-transparency|all
buds ear-detection|auto-answer|multipoint|adaptive-sound on|off
buds find [left|right|both|stop]
buds backup [archivo] / buds restore [archivo]
buds status --raw --json     # bytes sin decodificar, para investigar
```

La configuración original está en `~/.local/state/redmi-buds/backup-initial.json`; `buds restore` la recupera.

## Ecualizador a tu oído

En la pestaña Ecualizador, «Ajustar a tu oído» abre una prueba de tonos: suena un tono de referencia
de 1 kHz y luego el de cada banda, y mueves la barra hasta que ambos suenen igual de fuerte. A esos
ajustes se les resta la curva de igual sonoridad ISO 226:2003 (60 fon), porque el oído es menos
sensible a los graves aunque los audífonos sean neutros; el resto se invierte, se centra en la
mediana y se limita a ±6 dB. La curva se guarda en los audífonos como perfil «Personalizado». Si
cierras la prueba sin guardar, se restaura el ecualizador anterior. Código: `hearing.py`,
`hearing_gui.py`.

## Automatizaciones (servicio en segundo plano)

Las ejecuta `buds-manager-daemon` mientras tiene el control (los avisos de batería también funcionan
cuando el celular tiene el control):

- **Pausa automática**: al quitarte un audífono se pausa lo que esté sonando en la PC (vía `playerctl`),
  solo si el audio sale por los audífonos. Al ponértelo de nuevo se reanuda únicamente lo que se pausó
  (si fue hace menos de 15 min).
- **Avisos de batería**: notificación al bajar a 20 % y a 10 % (crítica), y cuando terminan de cargar.
- **Salida automática**: al quitarte los dos audífonos el audio pasa a las bocinas de la PC; al ponerte
  uno vuelve a los audífonos (si no elegiste otra salida mientras tanto).
- **Micrófono solo en llamadas** (`buds mic calls|any|never`, o en Conexión): usar el micrófono de unos
  audífonos Bluetooth baja la música a calidad de llamada. WirePlumber lo hacía con *cualquier* app que
  grabara, porque los audífonos eran el micrófono predeterminado. En modo `calls` el servicio apaga el
  cambio automático de WirePlumber, deja el micrófono de la PC como predeterminado y solo cambia al
  perfil de llamada cuando graba una app de llamadas (Discord, Zoom, Teams, Telegram, Signal, Slack,
  navegadores…; añade otras en `call_apps`). Mueve esa grabación al micrófono de los audífonos y, 3 s
  después de colgar, vuelve al códec de música que tenías. `any` = comportamiento de WirePlumber;
  `never` = nunca usar el micrófono de los audífonos.

```
buds auto                 # ver qué está activo
buds auto pausa off       # salida | pausa | reanudar | bateria  on|off
```

Se guarda en `~/.config/redmi-buds/config.json` (también `low_levels`, por defecto `[20, 10]`).

## Atajos en esta PC (gestos → acciones)

Los toques de los audífonos llegan a la PC como teclas multimedia (reproducir/pausar, siguiente,
anterior, subir/bajar volumen), según lo que tenga asignado cada toque. El servicio toma ese dispositivo
de entrada (`REDMI Buds 8 Pro (AVRCP)`) en exclusiva y ejecuta lo que elijas para cada tecla: la acción
normal, silenciar el micrófono, cambiar cancelación ↔ transparencia, captura de pantalla, nada o
cualquier comando. Solo actúa cuando el audio sale por esta PC; el celular no cambia.

Se configura en **Controles → Atajos en esta PC** o con:

```
buds atajos                                   # ver la tabla y si está escuchando
buds atajos previous mic-mute                 # triple toque «anterior» → silenciar micrófono
buds atajos next "cmd:hyprctl dispatch workspace +1"
buds atajos next next                         # volver a lo normal
```

Necesita el servicio activo (`buds hold on`) y la regla udev para leer `/dev/input`:
`sudo cp 70-redmi-buds.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`.

## Integración

- Waybar: módulo `custom/buds` (clic: siguiente modo, clic derecho: ANC/apagado, clic central: Buds Manager).
- Hyprland: `SUPER+B` siguiente modo, `SUPER+SHIFT+B` ANC/apagado.

## Protocolo descubierto con la captura de la app Xiaomi (2026-11-01)

| Config | Uso |
|---|---|
| `0x05` = 01 | inicia la prueba de ajuste; resultado por notificación de `0x06` (izq, der): 03 midiendo, 01 bien |
| `0x0c` | bits 0/1 izq/der en estuche, bits 2/3 izq/der en el oído |
| `0x28` | sincroniza la hora (timestamp Unix de 4 bytes) |
| `0x76` = 01 | la app lo envía al abrirse |
| `0x67` | viaje inmersivo: 0 off, 1 avión, 2 transporte, 3 tren |
| `0x1d` → `0x1e` | audio espacial: 02 off, 03 on, 0b on + seguimiento de cabeza. **Rechazado (estado 9) si no viene del celular**: el render lo hace el celular |
| `0x68` | motor espacial: 0 Xiaomi dimensional, 1 Dolby |
| `0x0b` (ANC) | 8 Pro: 01 ligera, 12 profunda (13 al entrar a ANC); `0x25` = cancelación inteligente |
| gesto `0x05` | deslizar: 0b volumen, 08 nada |
| `0x36` | [activo, nivel 1–5] del audio espacial (se activa con `0x1d`); inútil en la PC |
| `0x75`, `0x23`, `0x66` | sin identificar |

**Mantener el control en esta PC** (`buds hold on|off`, o el interruptor en la pestaña Conexión): activa
`buds-manager-daemon.service` (systemd de usuario), que toma el canal en cuanto los audífonos llegan a la PC,
reintenta cada segundo si el celular lo tiene y lo conserva aunque el celular se reconecte (probado). CLI, Waybar
y la app le hablan por `$XDG_RUNTIME_DIR/redmi-buds.sock`; los cambios hechos con gestos llegan al instante.
El celular sigue reproduciendo, pero su menú de los audífonos queda sin control.

Las lecturas piden varios ajustes por petición, como la app de Xiaomi (pedirlos uno a uno hacía que los
audífonos ignoraran los últimos).

**Límite de hardware:** el canal de control (RFCOMM 28) acepta un solo cliente. En un celular Xiaomi conectado,
el servicio `com.xiaomi.bluetooth` lo ocupa todo el tiempo, así que la PC no puede cambiar ajustes hasta que los
audífonos se desconecten del celular. El audio por multipunto no se ve afectado. El canal 17 es un servicio de información
básica sin autenticación que acepta varios clientes: al conectar envía tramas `03 <id> <len16> <dato>` con
id 01 = modelo (`11728a`), 02 = MAC, 03 = batería izq/der/estuche (mismo formato que GET_DEVICE_INFO).
El celular solo le manda `03 08 0002 0124`. Buds Manager lo usa para mostrar la batería cuando el celular
tiene el control.

## Pendiente

- Gesto tipo `0x05` con acción `0x0b` (propio de los 8 Pro, sin identificar).
- Audio espacial, LDAC, modo juego, prueba de ajuste: requieren capturar el tráfico de la app Xiaomi Earbuds en Android.
