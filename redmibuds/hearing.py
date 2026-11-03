"""Hearing-based EQ: match the loudness of each EQ band against a 1 kHz reference tone.

The user hears "reference, test, reference, test…" and moves a slider until both sound equally loud.
Equal loudness at the eardrum is not equal SPL (the ear is less sensitive to bass and to the very
top end), so the expected difference from the ISO 226:2003 equal-loudness contour (60 phon) is
subtracted. What is left is how the earbuds (plus your fit and your ear) deviate from neutral, and
its inverse becomes the earbuds' 10-band EQ curve.
"""
import math
import struct
import subprocess
import threading

RATE = 48000
REF_FREQ = 1000
REF_DBFS = -30.0          # reference tone level; the system volume sets the actual loudness
GAIN_RANGE = 18           # the test tone can be moved ±18 dB
PHON = 60

# ISO 226:2003 table: f, alpha_f, L_U, T_f
_ISO = [
    (20, 0.532, -31.6, 78.5), (25, 0.506, -27.2, 68.7), (31.5, 0.480, -23.0, 59.5),
    (40, 0.455, -19.1, 51.1), (50, 0.432, -15.9, 44.0), (63, 0.409, -13.0, 37.5),
    (80, 0.387, -10.3, 31.5), (100, 0.367, -8.1, 26.5), (125, 0.349, -6.2, 22.1),
    (160, 0.330, -4.5, 17.9), (200, 0.315, -3.1, 14.4), (250, 0.301, -2.0, 11.4),
    (315, 0.288, -1.1, 8.6), (400, 0.276, -0.4, 6.2), (500, 0.267, 0.0, 4.4),
    (630, 0.259, 0.3, 3.0), (800, 0.256, 0.5, 2.2), (1000, 0.255, 0.0, 2.4),
    (1250, 0.254, -2.7, 3.5), (1600, 0.248, -4.1, 1.7), (2000, 0.254, -1.0, -1.3),
    (2500, 0.252, 1.7, -4.2), (3150, 0.256, 2.5, -6.0), (4000, 0.249, 1.2, -5.4),
    (5000, 0.249, -2.1, -1.5), (6300, 0.251, -7.1, 6.0), (8000, 0.258, -11.2, 12.6),
    (10000, 0.266, -10.7, 13.9), (12500, 0.256, -3.1, 12.3),
]


def _iso_spl(row, phon):
    _f, a, lu, tf = row
    af = 4.47e-3 * (10 ** (0.025 * phon) - 1.15) + (0.4 * 10 ** ((tf + lu) / 10 - 9)) ** a
    return 10 / a * math.log10(af) - lu + 94


def contour(freq, phon=PHON):
    """SPL (dB) that sounds as loud as `phon` at 1 kHz, interpolated on a log-frequency axis.
    Above 12.5 kHz the standard has no data; the 12.5 kHz value is used."""
    if freq <= _ISO[0][0]:
        return _iso_spl(_ISO[0], phon)
    for lo, hi in zip(_ISO, _ISO[1:]):
        if lo[0] <= freq <= hi[0]:
            t = math.log(freq / lo[0]) / math.log(hi[0] / lo[0])
            return _iso_spl(lo, phon) * (1 - t) + _iso_spl(hi, phon) * t
    return _iso_spl(_ISO[-1], phon)


def curve_from_matches(matches, eq_min=-6, eq_max=6):
    """matches: {freq: dB the test tone had to be raised (+) or lowered (−) to sound like 1 kHz}.
    Returns {freq: EQ level}, centred so the curve uses the ±6 dB range sensibly."""
    ref = contour(REF_FREQ)
    raw = {f: g - (contour(f) - ref) for f, g in matches.items()}
    # Centre on the median so a single extreme band doesn't shift everything.
    vals = sorted(raw.values())
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {f: max(eq_min, min(eq_max, round(v - mid))) for f, v in raw.items()}


class TonePlayer:
    """Loops `reference — test — pause` through PipeWire, picking up gain changes on the fly."""

    PATTERN = [("ref", 0.55), (None, 0.2), ("test", 0.55), (None, 0.5)]
    FADE = 0.025
    CHUNK = 1024

    def __init__(self):
        self.freq = 125
        self.gain = 0.0           # dB applied to the test tone
        self.only = None          # "ref" / "test" to play only one of them
        self._proc = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._proc = subprocess.Popen(
            ["pw-cat", "--playback", "--raw", "--format", "s16", "--rate", str(RATE), "--channels", "2",
             "--latency", "60ms", "--media-role", "Music", "-"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(1)
        self._thread = None
        if self._proc:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            self._proc.terminate()
            self._proc = None

    def _loop(self):
        phase = 0.0
        while not self._stop.is_set():
            for what, dur in self.PATTERN:
                n = int(dur * RATE)
                if what is None or (self.only and what != self.only):
                    self._write(b"\x00\x00\x00\x00" * n)
                    continue
                freq = REF_FREQ if what == "ref" else self.freq
                db = REF_DBFS + (self.gain if what == "test" else 0)
                amp = 32767 * 10 ** (db / 20)
                fade = int(self.FADE * RATE)
                buf = bytearray()
                for i in range(n):
                    # Slight warble (±2 %, 6 Hz) avoids standing waves in the sealed ear canal.
                    f = freq * (1 + 0.02 * math.sin(2 * math.pi * 6 * i / RATE))
                    phase += 2 * math.pi * f / RATE
                    env = min(1.0, i / fade, (n - i) / fade)
                    s = int(amp * env * math.sin(phase))
                    buf += struct.pack("<hh", s, s)
                    if len(buf) >= self.CHUNK * 4:
                        if not self._write(bytes(buf)):
                            return
                        buf.clear()
                if buf and not self._write(bytes(buf)):
                    return
                if self._stop.is_set():
                    return

    def _write(self, data):
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
            return True
        except (OSError, AttributeError, ValueError):
            return False
