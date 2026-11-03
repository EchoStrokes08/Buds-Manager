"""'Hot or cold' finder: the earbuds beep while a meter shows the Bluetooth signal getting stronger or weaker."""
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import device as d  # noqa: E402
from . import rssi  # noqa: E402

# RSSI range mapped onto the meter (dBm): about -90 (far / walls) … -40 (right next to the PC)
WEAK, STRONG = -90, -40


def _words(v):
    if v >= -55:
        return "Muy cerca"
    if v >= -65:
        return "Cerca"
    if v >= -75:
        return "Lejos"
    return "Muy lejos"


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
                          label="Los audífonos están sonando. Mueve la laptop (o camina con ella): la barra se "
                                "llena al acercarte. Mide la señal del audífono que está conectado.")
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
        self.bar.set_value(max(0.0, min(1.0, (avg - WEAK) / (STRONG - WEAK))))
        self.big.set_label(_words(avg))
        if len(self.samples) >= 4:
            delta = (self.samples[-1] + self.samples[-2]) / 2 - (self.samples[0] + self.samples[1]) / 2
            self.trend.set_label("🔥 Más cerca" if delta >= 3 else "❄ Más lejos" if delta <= -3 else "Igual")
        self.detail.set_label(f"{avg:.0f} dBm")
        return False

    def _on_closed(self, *_):
        self.running = False
        self.win.run("buscar", lambda c: d.find(c, "stop"))
