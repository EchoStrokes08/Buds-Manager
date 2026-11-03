"""Sleep timer: fade the volume out, pause playback and switch noise control off after N minutes.

Runs inside the daemon (so it keeps counting if the tray or the app close). The fade takes the last
FADE seconds (at most a third of the timer), lowering the default sink's volume to FADE_TO of where it
was; afterwards the volume is put back, so the next morning starts at the usual level.
"""
import re
import subprocess
import threading
import time

from .automations import log, notify

FADE = 5 * 60
FADE_TO = 0.25
CHOICES = [15, 30, 45, 60, 90]


def _sink_volume():
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                             timeout=3).stdout
        m = re.search(r"Volume: ([\d.]+)", out)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _set_sink_volume(v):
    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0.0, v):.3f}"],
                   capture_output=True, timeout=3)


class SleepTimer:
    def __init__(self, set_mode):
        self.set_mode = set_mode         # callable(mode) on the earbuds; may raise
        self.end = 0
        self.total = 0
        self._gen = 0                    # bumps on every start/cancel; old threads see it and quit
        self._restore = None

    def remaining(self):
        return max(0, int(self.end - time.time())) if self.end else 0

    def status(self):
        return {"remaining": self.remaining(), "total": self.total}

    def start(self, minutes):
        self.cancel(quiet=True)
        self._gen += 1
        self.total = int(minutes * 60)
        self.end = time.time() + self.total
        threading.Thread(target=self._run, args=(self._gen,), daemon=True).start()
        log(f"temporizador: {minutes} min")

    def cancel(self, quiet=False):
        was = bool(self.end)
        self._gen += 1
        self.end = self.total = 0
        if self._restore is not None:
            _set_sink_volume(self._restore)
            self._restore = None
        if was and not quiet:
            log("temporizador cancelado")

    def _run(self, gen):
        fade = min(FADE, self.total / 3)
        while self._gen == gen and time.time() < self.end - fade:
            time.sleep(1)
        if self._gen != gen:
            return
        start_vol = _sink_volume()
        if start_vol:
            self._restore = start_vol
            while self._gen == gen and time.time() < self.end:
                left = max(0.0, self.end - time.time())
                frac = left / fade if fade else 0
                _set_sink_volume(start_vol * (FADE_TO + (1 - FADE_TO) * frac))
                time.sleep(2)
        else:
            while self._gen == gen and time.time() < self.end:
                time.sleep(1)
        if self._gen != gen:
            return
        subprocess.run(["playerctl", "-a", "pause"], capture_output=True)
        time.sleep(1)
        if self._gen != gen:             # a new timer started (or this one was cancelled) meanwhile
            return
        if self._restore is not None:
            _set_sink_volume(self._restore)
            self._restore = None
        self.end = self.total = 0
        try:
            self.set_mode("off")         # saves battery while you sleep
        except Exception as e:  # noqa: BLE001
            log("temporizador: no se pudo apagar la cancelación:", e)
        log("temporizador: terminado")
        notify("Buenas noches", "Se pausó la reproducción y se apagó la cancelación de ruido.")
