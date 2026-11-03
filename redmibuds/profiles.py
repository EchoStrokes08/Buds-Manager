"""Per-app sound profiles: the daemon changes the noise mode / EQ preset while an app is in use.

config.json:
    "app_profiles": [
        {"app": "spotify", "when": "playing", "mode": null, "eq": "bass"},
        {"app": "zoom",    "when": "focused", "mode": "transparency", "eq": null}
    ]

"pc_eq" (optional) is "off" or the name of a saved PC equalizer curve (see pceq.py).
"app" is matched (case-insensitive substring) against the Hyprland window class for "focused" profiles
and against the app name / binary of audio streams for "playing" ones. null = don't touch that setting.
A focused profile beats a playing one. When no profile applies any more, the settings a profile changed go
back to what they were before — unless you changed them by hand meanwhile.
"""
import json
import os
import socket
import subprocess
import threading
import time

from .automations import load_config, log

WHEN = {"focused": "En primer plano", "playing": "Mientras suena"}
SETTLE = 1.5        # seconds a window must keep focus before its profile applies (alt-tab friendly)


def _hypr_socket():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    base = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "hypr")
    if not sig and os.path.isdir(base):    # started before Hyprland exported it: take the newest instance
        dirs = sorted(os.listdir(base), key=lambda d: os.path.getmtime(os.path.join(base, d)), reverse=True)
        sig = dirs[0] if dirs else None
    return os.path.join(base, sig, ".socket2.sock") if sig else None


def active_class():
    try:
        out = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=3).stdout
        return (json.loads(out or "{}") or {}).get("class", "")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def window_classes():
    """Classes of the open windows (for the GUI's app picker)."""
    try:
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=3).stdout
        return sorted({c["class"] for c in json.loads(out) if c.get("class")}, key=str.lower)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def playing_apps():
    """Names/binaries of apps with a running (not corked) playback stream."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                             timeout=3).stdout
        apps = []
        for s in json.loads(out or "[]"):
            if s.get("corked"):
                continue
            pr = s.get("properties", {})
            apps.append(f"{pr.get('application.name', '')} {pr.get('application.process.binary', '')}".lower())
        return apps
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def pick(profiles, focused, playing):
    """The profile that applies now, or None."""
    focused = (focused or "").lower()
    for pr in profiles:
        if pr.get("when", "focused") == "focused" and pr.get("app") and pr["app"].lower() in focused:
            return pr
    for pr in profiles:
        if pr.get("when") == "playing" and pr.get("app") and any(pr["app"].lower() in a for a in playing):
            return pr
    return None


class AppProfiles:
    """Runs inside the daemon. `apply(changes)` sets {"mode": …, "eq": …} on the earbuds;
    `current()` returns the earbuds' {"mode": …, "eq": …} (None while not holding the channel)."""

    def __init__(self, apply, current):
        self.apply, self.current = apply, current
        self.active = None           # profile in effect
        self.base = {}               # settings before the profiles touched them
        self.applied = {}            # what we set (to detect manual changes)
        self._kick = threading.Event()
        self._focus = active_class()
        self._focus_at = 0

    def start(self):
        threading.Thread(target=self._watch_hypr, daemon=True).start()
        threading.Thread(target=self._watch_audio, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        self._kick.set()

    def poke(self):
        self._kick.set()

    def _watch_hypr(self):
        while True:
            path = _hypr_socket()
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(path)
                for line in s.makefile("r", errors="replace"):
                    if line.startswith("activewindow>>"):
                        cls = line.split(">>", 1)[1].split(",", 1)[0]
                        if cls != self._focus:       # also fires on every title change: ignore those
                            self._focus, self._focus_at = cls, time.time()
                            self._kick.set()
            except (OSError, TypeError):
                pass
            time.sleep(5)

    def _watch_audio(self):
        while True:
            try:
                p = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for line in p.stdout:
                    if "sink-input" in line:
                        self._kick.set()
            except OSError:
                pass
            time.sleep(5)

    def _loop(self):
        while True:
            self._kick.wait(30)
            time.sleep(0.5)                  # coalesce bursts of audio events
            self._kick.clear()
            while time.time() - self._focus_at < SETTLE:    # alt-tabbing: wait until focus settles
                time.sleep(0.2)
            try:
                self._step()
            except Exception as e:  # noqa: BLE001 - never kill the daemon
                log("perfiles: error:", e)

    def _step(self):
        profiles = load_config().get("app_profiles") or []
        cur = self.current()
        if cur is None:                      # not holding the earbuds: nothing to do (keep our memory)
            return
        want = pick(profiles, self._focus, playing_apps() if any(p.get("when") == "playing" for p in profiles)
                    else [])
        if want == self.active:
            return
        changes = {}
        if self.active:                      # leaving a profile: restore what it changed, unless touched by hand
            for k, v in self.applied.items():
                if cur.get(k) == v and k in self.base:
                    changes[k] = self.base[k]
            self.applied = {}
        if want:
            for k in ("mode", "eq", "pc_eq"):
                if want.get(k):
                    if k not in self.base or not self.active:
                        self.base[k] = cur.get(k)
                    changes[k] = want[k]
                    self.applied[k] = want[k]
        else:
            self.base = {}
        changes = {k: v for k, v in changes.items() if v and v != cur.get(k)}
        self.active = want
        if changes:
            log("perfiles:", (want or {}).get("app", "ninguno"), "→", changes)
            self.apply(changes)
