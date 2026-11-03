"""Interactive EQ curve: a smooth line through the band levels, gradient fill, draggable nodes.

Each band is exposed as an EqBand with the small Gtk.Scale API the app already used (get_value, set_value,
connect("value-changed"), freq_label.set_label), so the EQ logic didn't have to change.
Keyboard: ←/→ choose a band, ↑/↓ change it. Mouse wheel over a band changes it too.
"""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import theme  # noqa: E402


class _FreqLabel:
    def __init__(self, band):
        self.band = band

    def set_label(self, text):
        self.band.freq_text = text
        self.band.curve.queue_draw()


class EqBand:
    """Gtk.Scale-like handle for one band of an EqCurve."""

    def __init__(self, curve, index, freq_text):
        self.curve, self.index = curve, index
        self.value = 0.0
        self.freq_text = freq_text
        self.freq_label = _FreqLabel(self)
        self._handlers = []

    def connect(self, signal, cb, *extra):
        if signal == "value-changed":
            self._handlers.append((cb, extra))

    def get_value(self):
        return self.value

    def set_value(self, v):
        v = max(-self.curve.limit, min(self.curve.limit, round(v)))
        if v == self.value:
            return
        self.value = v
        self.curve.queue_draw()
        for cb, extra in self._handlers:
            cb(self, *extra)


class EqCurve(Gtk.Widget):
    PAD_L, PAD_R, PAD_T, PAD_B = 34, 16, 26, 26

    def __init__(self, freq_texts, limit):
        super().__init__(hexpand=True, focusable=True)
        self.limit = limit
        self.bands = [EqBand(self, i, t) for i, t in enumerate(freq_texts)]
        self.active = None          # band being dragged / focused
        self.hover = None
        self.set_size_request(-1, 220)
        self.set_accessible_role(Gtk.AccessibleRole.GROUP)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Ecualizador"])

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", lambda *_: self._set_active(None))
        self.add_controller(drag)
        self._drag_origin = (0, 0)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self._set_hover(self._nearest(x)))
        motion.connect("leave", lambda *_: self._set_hover(None))
        self.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._kbd = 0

    # ---- geometry ----
    def _xs(self):
        w = self.get_width()
        n = len(self.bands)
        span = w - self.PAD_L - self.PAD_R
        return [self.PAD_L + span * (i + 0.5) / n for i in range(n)]

    def _y(self, v):
        h = self.get_height()
        return self.PAD_T + (self.limit - v) / (2 * self.limit) * (h - self.PAD_T - self.PAD_B)

    def _v(self, y):
        h = self.get_height()
        return self.limit - (y - self.PAD_T) / (h - self.PAD_T - self.PAD_B) * 2 * self.limit

    def _nearest(self, x):
        xs = self._xs()
        return min(range(len(xs)), key=lambda i: abs(xs[i] - x)) if xs else None

    # ---- input ----
    def _set_active(self, i):
        self.active = i
        self.queue_draw()

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def _drag_begin(self, _g, x, y):
        self.grab_focus()
        self._drag_origin = (x, y)
        i = self._nearest(x)
        self._kbd = i
        self._set_active(i)
        self.bands[i].set_value(self._v(y))

    def _drag_update(self, _g, dx, dy):
        if self.active is not None:
            self.bands[self.active].set_value(self._v(self._drag_origin[1] + dy))

    def _scroll(self, _c, _dx, dy):
        i = self.hover if self.hover is not None else self._kbd
        if i is not None and dy:
            b = self.bands[i]
            b.set_value(b.value + (-1 if dy > 0 else 1))
        return True

    def _key(self, _c, keyval, _code, _state):
        name = Gdk.keyval_name(keyval)
        if name in ("Left", "Right"):
            self._kbd = max(0, min(len(self.bands) - 1, self._kbd + (1 if name == "Right" else -1)))
            self._set_active(self._kbd)
            return True
        if name in ("Up", "Down"):
            b = self.bands[self._kbd]
            b.set_value(b.value + (1 if name == "Up" else -1))
            self._set_active(self._kbd)
            return True
        return False

    # ---- drawing ----
    def _layout(self, text, pt=8.5, bold=False):
        layout = self.create_pango_layout(text)
        fd = Pango.FontDescription.from_string("Sans Bold" if bold else "Sans")
        fd.set_size(int(pt * Pango.SCALE))
        layout.set_font_description(fd)
        return layout

    def _text(self, snapshot, text, x, y, color, align="center", pt=8.5, bold=False):
        layout = self._layout(text, pt, bold)
        w, h = layout.get_pixel_size()
        x = x - w / 2 if align == "center" else (x - w if align == "right" else x)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y - h / 2))
        snapshot.append_layout(layout, color)
        snapshot.restore()

    def _path(self, pts):
        """Smooth curve through the points (Catmull-Rom → cubic Bézier)."""
        b = Gsk.PathBuilder.new()
        b.move_to(*pts[0])
        for i in range(len(pts) - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < len(pts) else p2
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            # keep the curve inside ±limit (Catmull-Rom overshoots between steep neighbours)
            lo, hi = self._y(self.limit), self._y(-self.limit)
            c1 = (c1[0], min(hi, max(lo, c1[1])))
            c2 = (c2[0], min(hi, max(lo, c2[1])))
            b.cubic_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
        return b

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        fg = self.get_color()
        accent = theme.accent_rgba(self)
        ink = lambda a: theme._with_alpha(fg, a)  # noqa: E731
        xs = self._xs()
        top, bottom = self._y(self.limit), self._y(-self.limit)

        # grid: 0 dB stronger, ±half and ±limit faint; y labels
        for v in (self.limit, self.limit / 2, 0, -self.limit / 2, -self.limit):
            y = round(self._y(v))
            snapshot.append_color(ink(0.16 if v == 0 else 0.06),
                                  Graphene.Rect().init(self.PAD_L, y, w - self.PAD_L - self.PAD_R, 1))
            if v in (self.limit, 0, -self.limit):
                self._text(snapshot, f"{int(v):+d}" if v else "0", self.PAD_L - 6, y, ink(0.45), "right", 8)
        for x in xs:
            snapshot.append_color(ink(0.035), Graphene.Rect().init(round(x), top, 1, bottom - top))

        pts = [(x, self._y(b.value)) for x, b in zip(xs, self.bands)]
        if len(pts) < 2:
            return
        # extend flat to the edges so the curve reads as a response, not a polyline
        line = [(self.PAD_L, pts[0][1])] + pts + [(w - self.PAD_R, pts[-1][1])]
        zero = self._y(0)

        # fill between the curve and 0 dB with a vertical accent gradient
        fill = self._path(line)
        fill.line_to(w - self.PAD_R, zero)
        fill.line_to(self.PAD_L, zero)
        fill.close()
        snapshot.push_fill(fill.to_path(), Gsk.FillRule.WINDING)
        a_top, a_mid = theme._with_alpha(accent, 0.30), theme._with_alpha(accent, 0.02)
        stops = [Gsk.ColorStop(), Gsk.ColorStop(), Gsk.ColorStop()]
        for st_, off, col in zip(stops, (0.0, 0.5, 1.0), (a_top, a_mid, a_top)):
            st_.offset, st_.color = off, col
        snapshot.append_linear_gradient(Graphene.Rect().init(0, top, w, bottom - top),
                                        Graphene.Point().init(0, top), Graphene.Point().init(0, bottom), stops)
        snapshot.pop()

        # the curve
        stroke = Gsk.Stroke.new(2.6)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        snapshot.append_stroke(self._path(line).to_path(), stroke, accent)

        # nodes, values and frequencies
        card = theme.card_rgba(self)
        for i, ((x, y), b) in enumerate(zip(pts, self.bands)):
            on = i in (self.active, self.hover)
            r = 7.5 if on else 5.5
            ring = Gsk.PathBuilder.new()
            ring.add_circle(Graphene.Point().init(x, y), r + 2.5)
            snapshot.append_fill(ring.to_path(), Gsk.FillRule.WINDING, card)
            dot = Gsk.PathBuilder.new()
            dot.add_circle(Graphene.Point().init(x, y), r)
            snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, accent if (on or b.value) else ink(0.85))
            label = f"{int(b.value):+d}" if b.value else "0"
            ly = y + 16 if b.value >= self.limit - 1 else y - 16     # above the node, unless at the very top
            self._text(snapshot, label, x, ly, ink(0.95 if on else 0.6), pt=8.5 if not on else 9.5, bold=on)
            self._text(snapshot, b.freq_text, x, h - 9, ink(0.85 if on else 0.5), pt=8.5, bold=on)
