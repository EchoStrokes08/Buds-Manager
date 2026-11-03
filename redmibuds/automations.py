"""Reactions the daemon runs when the earbuds' state changes.

* Auto-pause: taking an earbud out pauses whatever was playing on this PC (through MPRIS/playerctl);
  putting it back resumes only the players we paused.
* Battery alerts: desktop notifications when the earbuds drop to 20 % / 10 %, and when they finish
  charging in the case.

Options live in ~/.config/redmi-buds/config.json and are re-read on every change, so `buds auto …`
takes effect without restarting the daemon.
"""
import json
import os
import shutil
import subprocess
import time

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "redmi-buds")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "auto_pause": True,        # pause when an earbud leaves the ear
    "auto_resume": True,       # resume when it goes back in
    "battery_alerts": True,    # low battery / fully charged notifications
    "auto_output": True,       # both earbuds out -> PC speakers; back in -> earbuds
    "case_reminder": True,     # warn when the earbuds are left out of the case
    "call_mic": "calls",       # earbuds' mic: calls | any | never (see calls.py)
    "exposure": True,          # listening-dose estimate and alerts (see exposure.py)
    "ref_spl": 95.0,           # dB that typical music reaches at 100 % volume (exposure calibration)
    "pc_eq": {"enabled": False, "bands": [0] * 10, "balance": 0},   # see pceq.py
    "call_denoise": True,      # RNNoise on the earbuds' mic during calls (see denoise.py)
    "call_apps": [],           # extra app names treated as call apps
    "app_profiles": [],        # per-app noise mode / EQ (see profiles.py)
    "low_levels": [20, 10],
    "gesture_actions": {},     # media key from the earbuds -> PC action (see gestures.py)
}
RESUME_WINDOW = 15 * 60       # don't resume something paused longer ago than this
LEFT_OUT_AFTER = 10 * 60      # remind after the earbuds sit outside the case (not worn) this long
SIDE_NAME = {"left": "izquierdo", "right": "derecho"}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = {k: v for k, v in cfg.items() if k in DEFAULTS}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def notify(summary, body="", urgency="normal"):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Redmi Buds", "-i", "audio-headphones", "-u", urgency,
                        summary, body], stderr=subprocess.DEVNULL)


def _run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _audio_on_buds(mac):
    """Only react when this PC is actually sending audio to the earbuds."""
    from . import audio
    return audio.is_buds_node(_run("pactl", "get-default-sink"), mac)


def _playing_players():
    if not shutil.which("playerctl"):
        return []
    out = _run("playerctl", "-a", "-f", "{{playerInstance}}\t{{status}}", "metadata")
    return [line.split("\t")[0] for line in out.splitlines() if line.endswith("\tPlaying")]


def _in_ear(wear):
    return sum(1 for side in ("left", "right") if (wear or {}).get(side) == "ear")


class Automations:
    def __init__(self):
        self.wear = None
        self.paused = []           # players we paused
        self.paused_at = 0
        self.paused_count = 0      # earbuds in ear right before the pause
        self.batt_alerted = {}     # side -> lowest threshold already announced
        self.charged_alerted = set()
        self.output_moved = None   # (earbuds sink, PC sink) after we sent the audio to the speakers
        self.last_wear = None      # survives disconnects (for the "left out of the case" alert)
        self.out_since = 0
        self.out_alerted = False

    def on_state(self, mac, st):
        """Called by the daemon with every fresh state (and None when the earbuds disconnect)."""
        cfg = load_config()
        if st is None:
            self.wear = None
            try:
                if cfg["auto_output"]:
                    self._output_on_disconnect(mac)
            except Exception as e:  # noqa: BLE001
                log("cambio de salida falló:", e)
            if cfg["case_reminder"]:
                self._left_out_on_disconnect(mac)
            return
        try:
            if st.get("wear") and cfg["case_reminder"]:
                self._left_out(st["wear"])
        except Exception as e:  # noqa: BLE001
            log("aviso de estuche falló:", e)
        try:
            self._output(mac, self.wear, st.get("wear"), cfg)   # before _wear: resume must play on the buds
        except Exception as e:  # noqa: BLE001
            log("cambio de salida falló:", e)
        try:
            self._wear(mac, st.get("wear"), cfg)
        except Exception as e:  # noqa: BLE001 - an automation must never kill the daemon
            log("auto-pausa falló:", e)
        try:
            if cfg["battery_alerts"]:
                self._battery(st.get("battery") or {}, st.get("wear") or {}, cfg)
        except Exception as e:  # noqa: BLE001
            log("aviso de batería falló:", e)

    # ---- auto-pause ----

    def _wear(self, mac, wear, cfg):
        if not wear:
            return
        prev, self.wear = self.wear, wear
        if prev is None:
            return
        before, now = _in_ear(prev), _in_ear(wear)
        if now < before and cfg["auto_pause"]:
            if not _audio_on_buds(mac):
                return
            players = _playing_players()
            if players:
                for pl in players:
                    _run("playerctl", "-p", pl, "pause")
                log("auto-pausa:", ", ".join(players))
                # Remember the fullest "in ear" count, so taking out the second one doesn't reset it.
                if not self.paused or time.time() - self.paused_at > RESUME_WINDOW:
                    self.paused_count = before
                self.paused = sorted(set(self.paused) | set(players))
                self.paused_at = time.time()
        elif now > before and self.paused:
            if now < self.paused_count:
                return
            players, self.paused = self.paused, []
            if not cfg["auto_resume"] or time.time() - self.paused_at > RESUME_WINDOW:
                return
            if not _audio_on_buds(mac):
                return
            for pl in players:
                _run("playerctl", "-p", pl, "play")
            log("auto-reanudar:", ", ".join(players))

    # ---- earbuds left outside the case ----

    @staticmethod
    def _outside(wear):
        """Sides lying outside the case, or [] if one is worn (then they're with you)."""
        if not wear or "ear" in wear.values():
            return []
        return [s for s in ("left", "right") if wear.get(s) == "out"]

    def _left_out(self, wear):
        self.last_wear = dict(wear)
        out = self._outside(wear)
        if not out:
            self.out_since, self.out_alerted = 0, False
            return
        if not self.out_since:
            self.out_since = time.time()
        elif not self.out_alerted and time.time() - self.out_since >= LEFT_OUT_AFTER:
            self.out_alerted = True
            two = len(out) == 2
            notify("¿Guardaste los audífonos?",
                   f"{_subject(out)} {'llevan' if two else 'lleva'} {LEFT_OUT_AFTER // 60} min fuera del "
                   f"estuche y sin usar; así {'se descargan' if two else 'se descarga'}.")
            log("aviso de estuche:", out)

    def _left_out_on_disconnect(self, mac):
        out = self._outside(self.last_wear)
        self.last_wear, self.out_since, self.out_alerted = None, 0, False
        if not out:
            return

        def check():
            time.sleep(5)          # a session can end without the earbuds leaving (e.g. the phone took over)
            info = _run("bluetoothctl", "info", mac)
            if "Connected: yes" in info:
                return
            two = len(out) == 2
            notify("Los audífonos se desconectaron fuera del estuche",
                   f"{_subject(out)} {'quedaron' if two else 'quedó'} afuera: "
                   f"{'guárdalos' if two else 'guárdalo'} para que no se descargue{'n' if two else ''} "
                   "ni se pierda" + ("n." if two else "."), "critical")
            log("aviso de estuche: desconectados afuera", out)
        import threading
        threading.Thread(target=check, daemon=True).start()

    # ---- audio output follows the earbuds ----

    def _output(self, mac, prev, wear, cfg):
        from . import audio
        if not wear or not cfg["auto_output"]:
            return
        now = _in_ear(wear)
        moved = self.output_moved or _load_moved()
        if prev is None:
            # first reading after (re)starting the daemon: finish a pending return we may have forgotten
            if moved and now > 0:
                self._return_output(mac, moved)
            return
        before = _in_ear(prev)
        if before > 0 and now == 0:
            sink = audio.default_sink()
            pc = audio.pc_sink()
            if pc and audio.is_buds_node(sink, mac):
                audio.set_default_sink(pc)
                self.output_moved = (sink, pc)
                _save_moved(self.output_moved)
                log("salida: audífonos fuera →", pc)
                notify("Audio en las bocinas de la PC", "Vuelve a los audífonos al ponértelos")
        elif before == 0 and now > 0 and moved:
            self._return_output(mac, moved)

    def _return_output(self, mac, moved):
        from . import audio
        self.output_moved = None
        _save_moved(None)
        buds, pc = moved
        if audio.default_sink() != pc:
            return                      # the user picked another output meanwhile; leave it
        target = _buds_output(mac, buds)
        if target:
            audio.set_default_sink(target)
            log("salida: audífonos puestos →", target)

    def _output_on_disconnect(self, mac):
        """The earbuds left while the audio sat on the speakers (e.g. put away in the case): make them the
        preferred output again, so the next connection plays through them instead of the speakers."""
        from . import audio
        moved = self.output_moved or _load_moved()
        if not moved:
            return
        self.output_moved = None
        _save_moved(None)
        buds, pc = moved
        if audio.default_sink() not in (pc, ""):
            return
        real = buds if buds.startswith("bluez_") else "bluez_output." + mac.replace(":", "_") + ".1"
        audio.set_configured_sink(real)
        log("salida: desconectados; la próxima conexión vuelve a", real)

    # ---- battery ----

    def _battery(self, batt, wear, cfg):
        levels = sorted(cfg["low_levels"], reverse=True)
        names = {"left": "izquierdo", "right": "derecho"}
        low = []
        for side in ("left", "right"):
            b = batt.get(side)
            if not b:
                continue
            if b["charging"]:
                self.batt_alerted.pop(side, None)
                if b["level"] >= 100 and side not in self.charged_alerted:
                    self.charged_alerted.add(side)
                    if all((batt.get(s) or {}).get("level", 0) >= 100 or not batt.get(s)
                           for s in ("left", "right")):
                        notify("Audífonos cargados", "Los dos están al 100 %")
                continue
            if b["level"] < 100:
                self.charged_alerted.discard(side)
            crossed = [lv for lv in levels if b["level"] <= lv]
            if not crossed:
                self.batt_alerted.pop(side, None)
                continue
            th = crossed[-1]
            if self.batt_alerted.get(side, 999) > th:
                self.batt_alerted[side] = th
                low.append((side, b["level"], th))
        if low:
            lowest = min(lvl for _, lvl, _ in low)
            sides = " y ".join(names[s] for s, _, _ in low)
            critical = lowest <= min(levels)
            notify("Batería baja" if not critical else "Batería muy baja",
                   f"Audífono {sides}: {lowest} %", "critical" if critical else "normal")
            log("aviso de batería:", low)


def _subject(sides):
    return "Los dos audífonos" if len(sides) == 2 else f"El audífono {SIDE_NAME[sides[0]]}"


MOVED_PATH = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "redmi-buds",
                          "output-moved.json")


def _save_moved(moved):
    try:
        if moved:
            os.makedirs(os.path.dirname(MOVED_PATH), exist_ok=True)
            with open(MOVED_PATH, "w") as f:
                json.dump(list(moved), f)
        elif os.path.exists(MOVED_PATH):
            os.remove(MOVED_PATH)
    except OSError:
        pass


def _load_moved():
    try:
        with open(MOVED_PATH) as f:
            buds, pc = json.load(f)
            return buds, pc
    except (OSError, ValueError, TypeError):
        return None


def _buds_output(mac, preferred):
    """Where to send the audio back: the PC equalizer's output if it runs, else the earbuds themselves."""
    from . import audio
    sinks = [x["name"] for x in audio._json("list", "sinks")]
    eq = [n for n in sinks if n.startswith("buds_pceq.") and audio.is_buds_node(n, mac)]
    if eq:
        return eq[0]
    if preferred in sinks:
        return preferred
    return audio.buds_sink(mac)
