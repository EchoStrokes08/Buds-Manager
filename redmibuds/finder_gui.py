"""'Hot or cold' finder: the earbuds beep while a meter shows the Bluetooth signal getting stronger or weaker."""
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import device as d  # noqa: E402
from . import rssi  # noqa: E402

# Classic Bluetooth reports RSSI relative to the "golden receive power range": 0 while the signal is good
# (anywhere reasonably close), negative dB once it gets weaker. Some adapters report absolute dBm instead.
REL_WEAK = -20          # relative scale: 0 = good … -20 = about to lose the link
ABS_WEAK, ABS_STRONG = -90, -40


def _scale(v):
    """0..1 meter value and a word, for either kind of reading."""
    if v > -30:   # relative reading
        frac = max(0.0, min(1.0, 1 - v / REL_WEAK))
        word = "Cerca" if v >= 0 else "A media distancia" if v >= -6 else "Lejos" if v >= -12 else "Muy lejos"
    else:
        frac = max(0.0, min(1.0, (v - ABS_WEAK) / (ABS_STRONG - ABS_WEAK)))
        word = "Muy cerca" if v >= -55 else "Cerca" if v >= -65 else "Lejos" if v >= -75 else "Muy lejos"
    return frac, word


class FinderDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Buscar audífonos", content_width=380)
        self.win = win
        self.samples = []
        self.running = True
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=12, margin_bottom=24,
                      margin_start=24, margin_end=24)
        intro = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"],
                          label="Los audífonos están sonando. Camina con la laptop: la barra baja al alejarte. "
                                "De cerca la señal se ve llena en todos lados (Bluetooth solo distingue cuando se "
                                "debilita), así que úsala para saber hacia dónde ir y luego guíate por el sonido.")
        self.big = Gtk.Label(label="Midiendo…", css_classes=["title-1"])
        self.bar = Gtk.LevelBar(min_value=0, max_value=1, height_request=14)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], ["Intensidad de la señal"])
        self.trend = Gtk.Label(label=" ", css_classes=["title-4"])
        self.detail = Gtk.Label(label=" ", css_classes=["caption", "dim-label"])
        stop = Gtk.Button(label="Detener el sonido", halign=Gtk.Align.CENTER, css_classes=["pill"])
        stop.connect("clicked", lambda *_: self.close())
        for w in (intro, self.big, self.bar, self.trend, self.detail, stop):
            box.append(w)
        tv.set_content(box)
        self.set_child(tv)
        self.connect("closed", self._on_closed)
        win.run("buscar", lambda c: d.find(c, "both"))
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        while self.running:
            v, problem = rssi.read(self.win.mac) if self.win.mac else (None, "sin audífonos")
            GLib.idle_add(self._show, v, problem)
            time.sleep(0.8)

    def _show(self, v, problem):
        if not self.running:
            return False
        if v is None:
            self.big.set_label("Sin señal")
            self.trend.set_label(" ")
            self.detail.set_label(problem or "")
            self.bar.set_value(0)
            return False
        self.samples = (self.samples + [v])[-4:]
        avg = sum(self.samples) / len(self.samples)
        frac, word = _scale(avg)
        self.bar.set_value(frac)
        self.big.set_label(word)
        if len(self.samples) >= 4:
            delta = (self.samples[-1] + self.samples[-2]) / 2 - (self.samples[0] + self.samples[1]) / 2
            thr = 1.5 if avg > -30 else 3
            self.trend.set_label("🔥 Más cerca" if delta >= thr else "❄ Más lejos" if delta <= -thr else "Igual")
        self.detail.set_label(f"señal {avg:+.0f} dB respecto al rango óptimo" if avg > -30 else f"{avg:.0f} dBm")
        return False

    def _on_closed(self, *_):
        self.running = False
        self.win.run("buscar", lambda c: d.find(c, "stop"))
