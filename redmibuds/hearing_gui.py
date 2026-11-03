"""Buds Manager dialog for the hearing-based EQ (see hearing.py)."""
import subprocess

from gi.repository import Adw, GLib, Gtk

from . import device as d
from . import hearing as h
from .automations import _audio_on_buds, _playing_players


def _db(v):
    return f"{v:+d} dB" if v else "0 dB"


class HearingDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Ecualizador a tu oído", content_width=480, content_height=560)
        self.win = win
        st = win.state or {}
        self.prev_curve = st.get("eq_curve") or [{"freq": f, "level": 0} for f in d.DEFAULT_EQ_FREQS]
        self.prev_preset = st.get("eq_preset")
        self.freqs = [b["freq"] for b in self.prev_curve]
        self.test_freqs = [f for f in self.freqs if f != h.REF_FREQ]
        self.matches = {}
        self.skipped = set()
        self.idx = 0
        self.result = None
        self.started = self.applied = False
        self.paused_players = []
        self.player = h.TonePlayer()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vhomogeneous=False)
        self.stack.add_named(self._intro_page(), "intro")
        self.stack.add_named(self._band_page(), "band")
        self.stack.add_named(self._result_page(), "result")
        toolbar.set_content(self.stack)
        self.set_child(toolbar)
        self.connect("closed", self._on_closed)

    # ---------- pages ----------

    def _intro_page(self):
        page = Adw.StatusPage(
            icon_name="audio-headphones-symbolic", title="Ajusta el sonido a tu oído",
            description=(
                "Vas a oír dos tonos seguidos: primero uno de referencia y luego uno de prueba. "
                "Mueve la barra hasta que los dos suenen <b>igual de fuerte</b>. "
                f"Son {len(self.test_freqs)} tonos y toma unos 3 minutos.\n\n"
                "Ponte bien los dos audífonos, busca un lugar silencioso y deja el volumen a un nivel "
                "cómodo, como para escuchar música bajito. Mientras dure la prueba, la música se pausa "
                "y el ecualizador queda plano."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.intro_warn = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, visible=False)
        self.intro_warn.add_css_class("warning")
        box.append(self.intro_warn)
        btn = Gtk.Button(label="Empezar")
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", self._start)
        box.append(btn)
        page.set_child(box)
        if not (self.win.mac and _audio_on_buds(self.win.mac)):
            self.intro_warn.set_label("⚠ El sonido no está saliendo por los audífonos. "
                                      "Cámbialo antes de empezar.")
            self.intro_warn.set_visible(True)
        return page

    def _band_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24, valign=Gtk.Align.CENTER)
        self.band_step = Gtk.Label()
        self.band_step.add_css_class("dim-label")
        self.band_title = Gtk.Label()
        self.band_title.add_css_class("title-1")
        q = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                      label="Mueve la barra hasta que el segundo tono suene igual de fuerte que el primero.")
        box.append(self.band_step)
        box.append(self.band_title)
        box.append(q)

        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=True, digits=0,
                               value_pos=Gtk.PositionType.TOP, hexpand=True)
        self.scale.set_range(-h.GAIN_RANGE, h.GAIN_RANGE)
        self.scale.set_increments(1, 3)
        self.scale.set_round_digits(0)
        self.scale.set_has_origin(False)
        self.scale.add_mark(0, Gtk.PositionType.BOTTOM, "igual")
        self.scale.set_format_value_func(lambda _s, v: _db(int(round(v))))
        self.scale.connect("value-changed", lambda s: setattr(self.player, "gain", round(s.get_value())))
        box.append(self.scale)
        ends = Gtk.Box()
        lo = Gtk.Label(label="más suave", hexpand=True, xalign=0)
        hi = Gtk.Label(label="más fuerte", hexpand=True, xalign=1)
        for lbl in (lo, hi):
            lbl.add_css_class("caption")
            lbl.add_css_class("dim-label")
            ends.append(lbl)
        box.append(ends)

        btns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=12)
        self.back_btn = Gtk.Button(label="Atrás")
        self.back_btn.connect("clicked", lambda *_: self._go(self.idx - 1))
        skip = Gtk.Button(label="No lo oigo", tooltip_text="Esta banda se deja sin cambios")
        skip.connect("clicked", self._skip)
        nxt = Gtk.Button(label="Siguiente")
        nxt.add_css_class("suggested-action")
        nxt.connect("clicked", self._next)
        for b in (self.back_btn, skip, nxt):
            b.add_css_class("pill")
            btns.append(b)
        box.append(btns)
        return box

    def _result_page(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24,
                      margin_start=18, margin_end=18)
        title = Gtk.Label(label="Tu curva")
        title.add_css_class("title-2")
        desc = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                         label="Se guarda en los audífonos como perfil «Personalizado», así que también "
                               "suena así en el celular. Luego puedes retocarla en el ecualizador.")
        desc.add_css_class("dim-label")
        box.append(title)
        box.append(desc)
        self.result_frame = Gtk.Box(spacing=2, homogeneous=True)
        self.result_frame.add_css_class("card")
        box.append(self.result_frame)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        again = Gtk.Button(label="Repetir")
        again.connect("clicked", lambda *_: self._go(0))
        save = Gtk.Button(label="Guardar en los audífonos")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        for b in (again, save):
            b.add_css_class("pill")
            btns.append(b)
        box.append(btns)
        scroll.set_child(box)
        return scroll

    # ---------- flow ----------

    def _start(self, *_):
        self.started = True
        self.paused_players = _playing_players()
        for pl in self.paused_players:
            subprocess.run(["playerctl", "-p", pl, "pause"], stderr=subprocess.DEVNULL)
        flat = [{"freq": f, "level": 0} for f in self.freqs]

        def job(c):
            d.set_eq_curve(c, flat)
            d.set_eq_preset(c, "custom")
        self.win.worker.submit("ecualizador plano", job)
        self.player.start()
        self._go(0)

    def _go(self, idx):
        if idx < 0:
            return
        if idx >= len(self.test_freqs):
            self._show_result()
            return
        self.idx = idx
        f = self.test_freqs[idx]
        self.player.freq = f
        self.player.only = None
        self.band_step.set_label(f"Tono {idx + 1} de {len(self.test_freqs)}")
        self.band_title.set_label(f"{f / 1000:g} kHz" if f >= 1000 else f"{f} Hz")
        self.scale.set_value(self.matches.get(f, 0))
        self.player.gain = self.matches.get(f, 0)
        self.back_btn.set_sensitive(idx > 0)
        self.player.start()
        self.stack.set_visible_child_name("band")

    def _next(self, *_):
        f = self.test_freqs[self.idx]
        self.matches[f] = round(self.scale.get_value())
        self.skipped.discard(f)
        self._go(self.idx + 1)

    def _skip(self, *_):
        f = self.test_freqs[self.idx]
        self.matches.pop(f, None)
        self.skipped.add(f)
        self._go(self.idx + 1)

    def _show_result(self):
        self.player.stop()
        curve = h.curve_from_matches({h.REF_FREQ: 0, **self.matches})
        self.result = [{"freq": f, "level": curve.get(f, 0)} for f in self.freqs]
        child = self.result_frame.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.result_frame.remove(child)
            child = nxt
        for b in self.result:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10, margin_bottom=10)
            lvl = Gtk.LevelBar(orientation=Gtk.Orientation.VERTICAL, inverted=True, height_request=120,
                               min_value=0, max_value=12, value=b["level"] + 6, halign=Gtk.Align.CENTER)
            lvl.remove_offset_value("low")
            lvl.remove_offset_value("high")
            lvl.remove_offset_value("full")
            val = Gtk.Label(label="—" if b["freq"] in self.skipped else f"{b['level']:+d}" if b["level"] else "0")
            val.add_css_class("caption")
            name = Gtk.Label(label=f"{b['freq'] / 1000:g}k" if b["freq"] >= 1000 else str(b["freq"]))
            name.add_css_class("eq-band-label")
            col.append(val)
            col.append(lvl)
            col.append(name)
            self.result_frame.append(col)
        self.stack.set_visible_child_name("result")

    def _save(self, *_):
        curve = self.result

        def job(c):
            d.set_eq_curve(c, curve)
            d.set_eq_preset(c, "custom")
        self.win.worker.submit("curva a tu oído", job)
        self.applied = True
        self.win.toast("Curva guardada en los audífonos")
        self.close()

    def _on_closed(self, *_):
        self.player.stop()
        if self.started and not self.applied:
            prev_curve, prev_preset = self.prev_curve, self.prev_preset

            def job(c):
                d.set_eq_curve(c, prev_curve)
                if prev_preset in d.EQ_PRESETS:
                    d.set_eq_preset(c, prev_preset)
            self.win.worker.submit("restaurar ecualizador", job)
            self.win.toast("Prueba cancelada; ecualizador restaurado")
        for pl in self.paused_players:
            subprocess.run(["playerctl", "-p", pl, "play"], stderr=subprocess.DEVNULL)
