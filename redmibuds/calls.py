"""Use the earbuds' microphone only for calls.

Bluetooth earbuds can't do high-quality music (A2DP/LDAC) and the microphone (HFP) at the same time:
using the mic drops the earbuds into the low-quality "call" profile. WirePlumber's built-in autoswitch
does that whenever *any* app records from them, and because the earbuds become the default microphone,
that happens a lot (browsers, dictation, voice search…).

Modes (config "call_mic"):
  calls  – WirePlumber's autoswitch off; the PC's own mic is the default. When a call app (Discord,
           Zoom, Telegram, a browser…) starts recording, switch the earbuds to the call profile and move
           that recording to their mic; when the last call recording ends, go back to the music codec.
  any    – WirePlumber's default behaviour (any app using the earbuds' mic switches them).
  never  – the earbuds always stay in music mode; every app uses the PC's mic.
"""
import subprocess
import threading
import time

from . import audio
from .automations import load_config, log, notify

MODES = {"calls": "Solo en llamadas", "any": "Con cualquier app", "never": "Nunca"}

# Matched (case-insensitively, as substrings) against the recording's app name and process binary.
CALL_APPS = ["discord", "vesktop", "legcord", "webcord", "zoom", "teams", "telegram", "signal", "slack",
             "element", "mumble", "skype", "jitsi", "whatsapp", "zapzap", "firefox", "librewolf", "zen",
             "chromium", "chrome", "brave", "vivaldi", "opera", "edge"]
CALL_ROLES = {"communication", "phone"}
RELEASE_DELAY = 3.0     # seconds without call recordings before going back to music


def is_call(rec, extra=()):
    if rec["role"].lower() in CALL_ROLES:
        return True
    hay = f"{rec['app']} {rec['binary']}".lower()
    return any(app in hay for app in [*CALL_APPS, *[e.lower() for e in extra]])


class CallMic:
    def __init__(self, get_mac):
        self.get_mac = get_mac           # returns the earbuds' MAC (or None)
        self.mode = None
        self.switched = None             # (mac, music profile, previous default source) while we hold HFP
        self.last_call = 0
        self._kick = threading.Event()

    def start(self):
        self._kick.set()                 # apply the mode right away
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()

    # pactl subscribe wakes the loop on every recording stream / card change
    def _watch(self):
        while True:
            try:
                p = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
                for line in p.stdout:
                    if "source-output" in line or "'change' on card" in line or "on server" in line:
                        self._kick.set()
            except OSError as e:
                log("micrófono: pactl subscribe falló:", e)
            time.sleep(5)

    def _loop(self):
        while True:
            self._kick.wait(10)
            self._kick.clear()
            time.sleep(0.3)               # let the stream finish linking
            try:
                self._step()
            except Exception as e:  # noqa: BLE001 - never kill the daemon
                log("micrófono: error:", e)

    def _apply_mode(self, mode, mac):
        self.mode = mode
        audio.wp_autoswitch(mode == "any")
        if mode != "any":
            # stop the earbuds being the default mic (that is what triggered the switches)
            src = audio.default_source()
            pc = audio.pc_source()
            if mac and pc and audio.is_buds_node(src, mac) and not self.switched:
                audio.set_default_source(pc)
        log("micrófono de los audífonos:", MODES[mode])

    def _step(self):
        cfg = load_config()
        mode = cfg.get("call_mic", "calls")
        mac = self.get_mac()
        if mode != self.mode:
            if self.switched and mode != "calls":
                self._release()
            self._apply_mode(mode, mac)
        if mode != "calls" or not mac:
            return
        calls = [r for r in audio.recordings() if is_call(r, cfg.get("call_apps") or ())]
        if calls:
            self.last_call = time.time()
            if not self.switched:
                self._engage(mac, calls)
            else:
                mic = audio.buds_mic(mac)
                for r in calls:
                    if mic and r["source"] is not None:
                        audio.move_recording(r["index"], mic)
        elif self.switched:
            if time.time() - self.last_call >= RELEASE_DELAY:
                self._release()
            else:
                threading.Timer(RELEASE_DELAY, self._kick.set).start()

    def _engage(self, mac, calls):
        _, active = audio.profiles(mac)
        if not audio.is_music(active):
            return                         # already in call mode (by hand) or not on this PC
        if not audio.is_buds_node(audio.default_sink(), mac):
            return                         # the PC isn't playing through the earbuds
        profs = [p for p, _ in audio.profiles(mac)[0]]
        hfp = "headset-head-unit" if "headset-head-unit" in profs else next(
            (p for p in profs if p.startswith("headset")), None)
        if not hfp:
            return
        self.switched = (mac, active, audio.default_source())
        audio.set_profile(mac, hfp)
        mic = None
        for _ in range(20):
            mic = audio.buds_mic(mac)
            if mic:
                break
            time.sleep(0.2)
        if mic:
            audio.set_default_source(mic)
            for r in calls:
                audio.move_recording(r["index"], mic)
        apps = ", ".join(sorted({r["app"] or r["binary"] for r in calls}))
        log("micrófono: llamada en", apps, "→ perfil", hfp)
        notify("Micrófono de los audífonos activado", f"Llamada en {apps}. Al terminar vuelve la calidad de música.")

    def _release(self):
        mac, music, prev_src = self.switched
        self.switched = None
        try:
            audio.ensure_music(mac, prefer=music)
        except RuntimeError as e:
            log("micrófono: no se pudo volver a la música:", e)
        pc = audio.pc_source()
        audio.set_default_source(prev_src if prev_src and not audio.is_buds_node(prev_src, mac) else pc or prev_src)
        log("micrófono: llamada terminada → perfil", music)
