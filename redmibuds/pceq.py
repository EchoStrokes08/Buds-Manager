"""PC-side equalizer and left/right balance for the earbuds (PipeWire filter-chain), run by the daemon.

The earbuds' own EQ has 10 bands of ±6 dB. This adds, on the PC, a virtual output
"REDMI Buds (ecualizador de la PC)" — 10 peaking bands of ±12 dB, an automatic pre-gain so boosts don't
clip, and a balance control — that forwards to the earbuds. It only runs while the earbuds are in music
mode (A2DP); during a call it stops and comes back afterwards. Changes apply live (pw-cli set-param).

config.json: "pc_eq": {"enabled": false, "bands": [10 × dB], "balance": -100..100}
"""
import json
import os
import subprocess
import tempfile
import threading
import time

from . import audio
from .automations import load_config, log

FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
Q = 1.41
LIMIT = 12.0
PREFIX = "buds_pceq."


def node_name(mac):
    return PREFIX + mac.replace(":", "_")


def settings(cfg=None):
    pe = (cfg or load_config()).get("pc_eq") or {}
    bands = [max(-LIMIT, min(LIMIT, float(b))) for b in (pe.get("bands") or [0] * 10)][:10]
    bands += [0.0] * (10 - len(bands))
    bal = max(-100, min(100, int(pe.get("balance", 0))))
    return {"enabled": bool(pe.get("enabled")), "bands": bands, "balance": bal}


def gains(s):
    """Linear gains (left, right): automatic pre-gain (-max boost) × balance."""
    pre = 10 ** (-max(0.0, max(s["bands"])) / 20)
    b = s["balance"]
    left = 1.0 if b <= 0 else 1 - b / 100
    right = 1.0 if b >= 0 else 1 + b / 100
    return pre * left, pre * right


def params(s):
    """Flat list of (control, value) for pw-cli set-param."""
    gl, gr = gains(s)
    out = [("mixL:Gain 1", gl), ("mixR:Gain 1", gr)]
    for ch in "LR":
        for i, g in enumerate(s["bands"], 1):
            out.append((f"eq{ch}_{i}:Gain", g))
    return out


def _config(mac, target, s):
    gl, gr = gains(s)
    nodes, links = [], []
    for ch, g in (("L", gl), ("R", gr)):
        nodes.append(f'{{ type = builtin name = mix{ch} label = mixer control = {{ "Gain 1" = {g:.6f} }} }}')
        prev = f"mix{ch}:Out"
        for i, (f, gain) in enumerate(zip(FREQS, s["bands"]), 1):
            nodes.append(f'{{ type = builtin name = eq{ch}_{i} label = bq_peaking '
                         f'control = {{ "Freq" = {float(f)} "Q" = {Q} "Gain" = {gain:.2f} }} }}')
            links.append(f'{{ output = "{prev}" input = "eq{ch}_{i}:In" }}')
            prev = f"eq{ch}_{i}:Out"
    name = node_name(mac)
    return f"""
context.properties = {{ log.level = 0 }}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "REDMI Buds (ecualizador de la PC)"
            media.name       = "REDMI Buds (ecualizador de la PC)"
            filter.graph = {{
                nodes = [ {" ".join(nodes)} ]
                links = [ {" ".join(links)} ]
                inputs = [ "mixL:In 1" "mixR:In 1" ]
                outputs = [ "eqL_10:Out" "eqR_10:Out" ]
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                node.name = "{name}"
                media.class = Audio/Sink
                device.icon-name = "audio-headphones"
            }}
            playback.props = {{
                node.name = "{name}.out"
                node.passive = true
                target.object = "{target}"
                node.dont-reconnect = true
                stream.dont-remix = true
            }}
        }}
    }}
]
"""


def _node_id(name):
    try:
        out = subprocess.run(["pw-dump", "Node"], capture_output=True, text=True, timeout=5).stdout
        for o in json.loads(out or "[]"):
            if (o.get("info", {}).get("props", {}) or {}).get("node.name") == name:
                return o["id"]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


class PcEq:
    def __init__(self, get_mac):
        self.get_mac = get_mac
        self.proc = None
        self.conf = None
        self.target = None
        self.mac = None
        self._kick = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        self._kick.set()

    def poke(self):
        self._kick.set()

    def _watch(self):
        while True:
            try:
                p = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True)
                for line in p.stdout:
                    if "on sink #" in line or "on card" in line:
                        self._kick.set()
            except OSError:
                pass
            time.sleep(5)

    def _loop(self):
        while True:
            self._kick.wait(30)
            time.sleep(0.5)
            self._kick.clear()
            try:
                with self._lock:
                    self._step()
            except Exception as e:  # noqa: BLE001 - never kill the daemon
                log("ecualizador de la PC: error:", e)

    def _step(self):
        s = settings()
        mac = self.get_mac()
        target = None
        if s["enabled"] and mac:
            _, active = audio.profiles(mac)
            if audio.is_music(active):
                target = audio.buds_sink(mac)          # real bluez sink (never our own)
        if self.running and target == self.target and self.mac == mac:
            return
        if self.running:
            self._stop()
        if target:
            self._start(mac, target, s)

    def _start(self, mac, target, s):
        was_default = audio.default_sink() == target
        fd, self.conf = tempfile.mkstemp(prefix="buds-pceq-", suffix=".conf")
        with os.fdopen(fd, "w") as f:
            f.write(_config(mac, target, s))
        self.proc = subprocess.Popen(["pipewire", "-c", self.conf], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        self.mac, self.target = mac, target
        name = node_name(mac)
        for _ in range(25):
            if any(x["name"] == name for x in audio._json("list", "sinks")):
                break
            if self.proc.poll() is not None:
                log("ecualizador de la PC: el filtro no arrancó")
                self._stop()
                return
            time.sleep(0.2)
        if was_default or audio.default_sink() in ("", target) or audio.configured_sink() in (name, target):
            audio.set_default_sink(name)
        log("ecualizador de la PC: activo sobre", target)

    def _stop(self):
        name = node_name(self.mac) if self.mac else None
        on_us = name and audio.default_sink() == name
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.conf:
            try:
                os.unlink(self.conf)
            except OSError:
                pass
            self.conf = None
        if on_us and self.target and any(x["name"] == self.target for x in audio._json("list", "sinks")):
            audio.set_default_sink(self.target)       # keep the sound on the earbuds
        log("ecualizador de la PC: detenido")
        self.target = None

    def apply(self):
        """Re-reads the config: starts/stops the filter or updates its controls live."""
        with self._lock:
            s = settings()
            if not s["enabled"] or not self.running:
                self._kick.set()
                return
            nid = _node_id(node_name(self.mac))
            if nid is None:
                self._kick.set()
                return
            body = " ".join(f'"{k}" {v:.6f}' for k, v in params(s))
            r = subprocess.run(["pw-cli", "set-param", str(nid), "Props", f"{{ params = [ {body} ] }}"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode:
                log("ecualizador de la PC: no se pudo cambiar en vivo, reinicio el filtro")
                self._stop()
                self._kick.set()


# ---- named curves (config "pc_eq_presets": {name: {"bands": [...], "balance": n}}) ----

def presets(cfg=None):
    out = {}
    for name, p in ((cfg or load_config()).get("pc_eq_presets") or {}).items():
        s = settings({"pc_eq": {"bands": p.get("bands"), "balance": p.get("balance", 0)}})
        out[name] = {"bands": s["bands"], "balance": s["balance"]}
    return out


def current_value(cfg=None):
    """The PC EQ as one comparable value for per-app profiles: "off", a preset name, or "@<bands,balance>"
    for a curve that isn't saved under a name."""
    cfg = cfg or load_config()
    s = settings(cfg)
    if not s["enabled"]:
        return "off"
    for name, p in presets(cfg).items():
        if [round(b) for b in p["bands"]] == [round(b) for b in s["bands"]] and p["balance"] == s["balance"]:
            return name
    return "@" + json.dumps({"bands": [round(b) for b in s["bands"]], "balance": s["balance"]})


def set_value(value, cfg=None):
    """Writes a value from current_value() back to the config. Returns the new config."""
    from .automations import save_config
    cfg = cfg or load_config()
    pe = dict(cfg.get("pc_eq") or {})
    if value == "off":
        pe["enabled"] = False
    elif value.startswith("@"):
        pe.update(json.loads(value[1:]), enabled=True)
    else:
        p = presets(cfg).get(value)
        if p is None:
            raise ValueError(f"no existe la curva «{value}»")
        pe.update(bands=p["bands"], balance=p["balance"], enabled=True)
    cfg["pc_eq"] = pe
    save_config(cfg)
    return cfg
