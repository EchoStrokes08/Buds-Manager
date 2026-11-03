"""Listening-dose estimate (WHO safe listening: 80 dB for 40 h a week, 3 dB exchange rate).

The PC can't measure the sound level in your ear. Every minute that audio plays through the earbuds while
you wear them, this estimates the level from the system volume — PipeWire volume v is cubic, so the gain
is 60·log10(v) dB — plus a reference: the level typical music reaches at 100 % volume ("ref_spl" in
config.json, default 95 dB). Each minute adds (1 min) / (safe time at that level) to the weekly dose;
100 % = the WHO weekly limit. Alerts at 80 % and 100 % of a rolling 7-day window.

~/.local/state/redmi-buds/exposure.json — {"YYYY-MM-DD": {"minutes": n, "dose": fraction}}
"""
import json
import math
import os
import re
import subprocess
import threading
import time

from . import audio
from .automations import load_config, log, notify

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "redmi-buds")
PATH = os.path.join(STATE_DIR, "exposure.json")
WHO_LEVEL, WHO_HOURS, EXCHANGE = 80.0, 40.0, 3.0
DEFAULT_REF = 95.0
ALERTS = (0.8, 1.0)
KEEP_DAYS = 60


def safe_hours(level):
    """Weekly hours allowed at `level` dB (WHO, 3 dB exchange)."""
    return WHO_HOURS * 2 ** ((WHO_LEVEL - level) / EXCHANGE)


def level_db(volume, ref=DEFAULT_REF):
    return ref + 60 * math.log10(max(volume, 1e-4))


def _sink_volume():
    out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                         timeout=3).stdout
    m = re.search(r"Volume: ([\d.]+)", out)
    return (float(m.group(1)), "MUTED" in out) if m else (None, False)


def _playing_on(sink):
    for s in audio._json("list", "sink-inputs"):
        if not s.get("corked") and s.get("sink") == sink:
            return True
    return False


def _load(path=PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def summary(path=PATH, now=None):
    """{"week": dose fraction over the last 7 days, "today_min": minutes today, "days": [(date, dose)…]}"""
    now = now or time.time()
    data = _load(path)
    days = [time.strftime("%Y-%m-%d", time.localtime(now - i * 86400)) for i in range(7)]
    week = sum(data.get(d, {}).get("dose", 0) for d in days)
    today = data.get(days[0], {})
    return {"week": week, "today_min": today.get("minutes", 0),
            "days": [(d, data.get(d, {}).get("dose", 0)) for d in reversed(days)]}


class Exposure:
    """Runs in the daemon. `get_mac()` → MAC or None; `get_wear()` → wear dict or None (unknown)."""

    def __init__(self, get_mac, get_wear, path=PATH):
        self.get_mac, self.get_wear, self.path = get_mac, get_wear, path
        self.level_now = None
        week = summary(path)["week"]
        self._alerted = {th for th in ALERTS if week >= th}    # don't repeat alerts after a restart

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(60)
            try:
                self.sample()
            except Exception as e:  # noqa: BLE001 - never kill the daemon
                log("exposición: error:", e)

    def sample(self, minutes=1.0):
        cfg = load_config()
        if not cfg.get("exposure", True):
            self.level_now = None
            return
        mac = self.get_mac()
        sink = audio.default_sink()
        if not mac or not audio.is_buds_node(sink, mac):
            self.level_now = None
            return
        wear = self.get_wear()
        if wear is not None and "ear" not in wear.values():
            self.level_now = None
            return
        sink_index = next((s["index"] for s in audio._json("list", "sinks") if s["name"] == sink), None)
        vol, muted = _sink_volume()
        if vol is None or muted or not _playing_on(sink_index):
            self.level_now = None
            return
        lvl = level_db(vol, float(cfg.get("ref_spl", DEFAULT_REF)))
        self.level_now = lvl
        self._add(minutes, (minutes / 60) / safe_hours(lvl))

    def _add(self, minutes, dose):
        data = _load(self.path)
        day = time.strftime("%Y-%m-%d")
        d = data.setdefault(day, {"minutes": 0, "dose": 0.0})
        d["minutes"] += minutes
        d["dose"] += dose
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
        data = {k: v for k, v in data.items() if k >= cutoff}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        week = summary(self.path)["week"]
        for th in ALERTS:
            if week >= th and th not in self._alerted:
                self._alerted.add(th)
                if th >= 1:
                    notify("Límite semanal de escucha alcanzado",
                           "Llevas el 100 % de la dosis segura de la OMS en 7 días (estimado). Baja el volumen o "
                           "descansa los oídos.", "critical")
                else:
                    notify("Exposición al sonido: 80 % de la semana",
                           "Vas por el 80 % de la dosis segura semanal (estimado). Bajar 3 dB duplica el tiempo "
                           "seguro.")
                log(f"exposición: aviso {int(th * 100)} %")
            elif week < th * 0.9:
                self._alerted.discard(th)

    def status(self):
        s = summary(self.path)
        s["level_now"] = round(self.level_now, 1) if self.level_now else None
        return s
