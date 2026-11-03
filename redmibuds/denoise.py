"""Noise suppression for the earbuds' microphone during calls (RNNoise through a PipeWire filter-chain).

While a call holds the earbuds' mic, the daemon runs a small standalone PipeWire client (`pipewire -c …`)
that reads the earbuds' mic, removes background noise and offers the result as a new microphone,
"Micrófono de los audífonos (sin ruido)". Call apps are moved to it; the filter stops when the call ends.

Needs the RNNoise LADSPA plugin: `sudo pacman -S noise-suppression-for-voice`.
"""
import glob
import os
import subprocess
import tempfile
import time

from . import audio
from .automations import log

NODE = "buds_mic_denoised"
PLUGIN_PATHS = ["/usr/lib/ladspa/librnnoise_ladspa.so", "/usr/lib64/ladspa/librnnoise_ladspa.so",
                "/usr/local/lib/ladspa/librnnoise_ladspa.so"]


def plugin():
    for path in PLUGIN_PATHS + glob.glob("/usr/lib/*/ladspa/librnnoise_ladspa.so"):
        if os.path.exists(path):
            return path
    return None


def _config(source, plugin_path, vad):
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
            node.description = "Micrófono de los audífonos (sin ruido)"
            media.name       = "Micrófono de los audífonos (sin ruido)"
            filter.graph = {{
                nodes = [
                    {{
                        type   = ladspa
                        name   = rnnoise
                        plugin = "{plugin_path}"
                        label  = noise_suppressor_mono
                        control = {{ "VAD Threshold (%)" = {float(vad)} }}
                    }}
                ]
            }}
            audio.rate = 48000
            audio.position = [ MONO ]
            capture.props = {{
                node.name = "{NODE}_input"
                node.passive = true
                target.object = "{source}"
                stream.dont-remix = true
            }}
            playback.props = {{
                node.name = "{NODE}"
                media.class = Audio/Source
                priority.session = 0
            }}
        }}
    }}
]
"""


class Denoiser:
    def __init__(self):
        self.proc = None
        self.conf = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, source, vad=50):
        """Starts the filter on `source`; returns the clean source's name, or None if unavailable."""
        self.stop()
        path = plugin()
        if not path:
            log("micrófono: sin RNNoise (instala noise-suppression-for-voice); sigo sin filtro")
            return None
        fd, self.conf = tempfile.mkstemp(prefix="buds-denoise-", suffix=".conf")
        with os.fdopen(fd, "w") as f:
            f.write(_config(source, path, vad))
        self.proc = subprocess.Popen(["pipewire", "-c", self.conf], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(25):
            if any(s["name"] == NODE for s in audio._json("list", "sources")):
                log("micrófono: filtro de ruido activo")
                return NODE
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        log("micrófono: el filtro de ruido no arrancó; sigo sin filtro")
        self.stop()
        return None

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
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
