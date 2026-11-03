"""Turn earbud gestures into PC shortcuts.

The earbuds send their tap gestures to the PC as AVRCP media keys, which BlueZ exposes as an input
device called "<name> (AVRCP)". This listener grabs that device exclusively (so Hyprland's own media
key binds don't also fire) and runs the action configured for each key in config.json:

    "gesture_actions": {"play-pause": "play-pause", "next": "next", "previous": "mic-mute"}

An action is one of BUILTIN's names or "cmd:<shell command>". Keys only reach this PC while it is
the earbuds' active audio source; the phone keeps its normal behaviour.

Reading /dev/input needs the udev rule in 70-redmi-buds.rules (TAG+="uaccess").
"""
import fcntl
import glob
import os
import struct
import subprocess
import threading
import time

from .automations import load_config, log, notify

EVIOCGRAB = 0x40044590
EV_KEY = 1
EVENT = struct.Struct("llHHi")

KEYS = {
    164: "play-pause", 200: "play-pause", 201: "play-pause", 166: "play-pause",   # PLAYPAUSE, PLAYCD, PAUSECD, STOPCD
    163: "next", 208: "next",                                                     # NEXTSONG, FASTFORWARD
    165: "previous", 168: "previous",                                             # PREVIOUSSONG, REWIND
    115: "volume-up", 114: "volume-down",
}

BUILTIN = {
    "play-pause": ("Reproducir / pausar", "playerctl play-pause"),
    "next": ("Siguiente pista", "playerctl next"),
    "previous": ("Pista anterior", "playerctl previous"),
    "volume-up": ("Subir volumen", "wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"),
    "volume-down": ("Bajar volumen", "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),
    "mic-mute": ("Silenciar / activar micrófono", None),
    "anc-toggle": ("Cancelación de ruido ↔ transparencia", None),
    "screenshot": ("Captura de pantalla", "grim -g \"$(slurp)\" - | wl-copy"),
    "none": ("Nada", None),
}

BUDS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_device():
    for path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(path) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name.endswith("(AVRCP)") and ("buds" in name.lower()):
            return "/dev/input/" + path.split("/")[4], name
    return None, None


def _mic_mute():
    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
    out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True).stdout
    notify("Micrófono silenciado" if "MUTED" in out else "Micrófono activado")


def _anc_toggle():
    subprocess.run([os.path.join(BUDS_DIR, "buds"), "mode", "flip", "--notify"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_action(action):
    if not action or action == "none":
        return
    if action == "mic-mute":
        return _mic_mute()
    if action == "anc-toggle":
        return _anc_toggle()
    cmd = action[4:] if action.startswith("cmd:") else BUILTIN.get(action, (None, None))[1]
    if cmd:
        subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


class GestureListener:
    def __init__(self):
        self.device = None
        self.problem = None      # human-readable reason when the device can't be read

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        warned = False
        while True:
            path, name = find_device()
            if not path:
                self.device, self.problem = None, "los audífonos no mandan teclas a esta PC ahora"
                time.sleep(3)
                continue
            try:
                fd = os.open(path, os.O_RDONLY)
            except PermissionError:
                self.problem = "falta la regla de udev (ver README)"
                if not warned:
                    log("gestos: sin permiso para leer", path, "— instala 70-redmi-buds.rules")
                    warned = True
                time.sleep(10)
                continue
            except OSError:
                time.sleep(3)
                continue
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
                self.device, self.problem = name, None
                log("gestos: escuchando", name)
                self._read(fd)
            except OSError as e:
                log("gestos: dispositivo cerrado:", e)
            finally:
                self.device = None
                os.close(fd)

    def _read(self, fd):
        while True:
            data = os.read(fd, EVENT.size * 16)
            if not data:
                return
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _s, _us, typ, code, value = EVENT.unpack_from(data, i)
                if typ != EV_KEY or value != 1:      # key presses only
                    continue
                key = KEYS.get(code)
                if not key:
                    continue
                actions = load_config().get("gesture_actions") or {}
                action = actions.get(key, key)
                log(f"gestos: tecla {key} → {action}")
                try:
                    run_action(action)
                except Exception as e:  # noqa: BLE001
                    log("gestos: la acción falló:", e)
