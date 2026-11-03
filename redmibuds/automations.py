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
    "low_levels": [20, 10],
    "gesture_actions": {},     # media key from the earbuds -> PC action (see gestures.py)
}
RESUME_WINDOW = 15 * 60       # don't resume something paused longer ago than this


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
    sink = _run("pactl", "get-default-sink")
    return sink.startswith("bluez_output.") and mac.replace(":", "_").upper() in sink.upper()


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

    def on_state(self, mac, st):
        """Called by the daemon with every fresh state (and None when the earbuds disconnect)."""
        cfg = load_config()
        if st is None:
            self.wear = None
            return
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
