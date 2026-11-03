"""Battery history group for Buds Manager: chart (GSK), time-left estimate and long-term discharge rate."""
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import battlog  # noqa: E402

# Categorical slots 1-2 (validated: light on #ffffff, dark on #2e2e32 — all checks pass)
SERIES = {"left": ("Izquierdo", "#2a78d6", "#3987e5"), "right": ("Derecho", "#eb6834", "#d95926")}
RANGES = [("48h", "48 h", 48 * 3600), ("7d", "7 días", 7 * 24 * 3600)]


def _dark():
    return Adw.StyleManager.get_default().get_dark()


def _series_rgba(side):
    _, light, dark = SERIES[side]
    c = Gdk.RGBA()
    c.parse(dark if _dark() else light)
    return c


def _ink(fg, alpha):
    c = fg.copy()
    c.alpha = alpha
    return c


def _rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def _circle(snapshot, x, y, r, color):
    b = Gsk.PathBuilder.new()
    b.add_circle(Graphene.Point().init(x, y), r)
    snapshot.append_fill(b.to_path(), Gsk.FillRule.WINDING, color)


def _stroke(snapshot, points, color, width=2.0):
    b = Gsk.PathBuilder.new()
    b.move_to(*points[0])
    for p in points[1:]:
        b.line_to(*p)
    st = Gsk.Stroke.new(width)
    st.set_line_join(Gsk.LineJoin.ROUND)
    st.set_line_cap(Gsk.LineCap.ROUND)
    snapshot.append_stroke(b.to_path(), st, color)


class Dot(Gtk.Widget):
    """Legend swatch."""
    def __init__(self, side):
        super().__init__(valign=Gtk.Align.CENTER)
        self.side = side
        self.set_size_request(10, 10)

    def do_snapshot(self, snapshot):
        _circle(snapshot, 5, 5, 5, _series_rgba(self.side))


class BatteryChart(Gtk.Widget):
    PAD_L, PAD_R, PAD_T, PAD_B = 38, 66, 10, 22

    def __init__(self):
        super().__init__(hexpand=True)
        self.set_size_request(-1, 170)
        self.rows = []
        self.span = RANGES[0][2]
        self.hover_x = None
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: self._hover(x))
        motion.connect("leave", lambda *_: self._hover(None))
        self.add_controller(motion)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    def set_rows(self, rows, span):
        self.rows, self.span = rows, span
        last = rows[-1] if rows else None
        desc = "Sin datos de batería" if not last else (
            f"Batería: izquierdo {last['left']} %, derecho {last['right']} %")
        self.update_property([Gtk.AccessibleProperty.LABEL], [desc])
        self.queue_draw()

    def _hover(self, x):
        self.hover_x = x
        self.queue_draw()

    def _layout(self, text):
        """Pango layout in the chart's small type (~9 pt, like the caption style)."""
        from gi.repository import Pango
        layout = self.create_pango_layout(text)
        fd = (self.get_pango_context().get_font_description() or Pango.FontDescription()).copy()
        fd.set_size(9 * Pango.SCALE)
        layout.set_font_description(fd)
        return layout

    def _text(self, snapshot, text, x, y, color, align="left"):
        layout = self._layout(text)
        w, h = layout.get_pixel_size()
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y - h / 2))
        snapshot.append_layout(layout, color)
        snapshot.restore()
        return w, h

    # ---- geometry ----
    def _x(self, t, w, t0):
        return self.PAD_L + (t - t0) / self.span * (w - self.PAD_L - self.PAD_R)

    def _y(self, level, h):
        return self.PAD_T + (100 - level) / 100 * (h - self.PAD_T - self.PAD_B)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        fg = self.get_color()
        now = time.time()
        t0 = now - self.span
        plot_r = w - self.PAD_R

        # grid + y labels (recessive)
        for lvl in (0, 50, 100):
            y = round(self._y(lvl, h))
            snapshot.append_color(_ink(fg, 0.10), _rect(self.PAD_L, y, plot_r - self.PAD_L, 1))
            self._text(snapshot, f"{lvl} %", self.PAD_L - 6, y, _ink(fg, 0.55), "right")
        start = "hace 48 h" if self.span <= 48 * 3600 else "hace 7 días"
        self._text(snapshot, start, self.PAD_L, h - 9, _ink(fg, 0.55))
        self._text(snapshot, "ahora", plot_r, h - 9, _ink(fg, 0.55), "right")

        rows = [r for r in self.rows if r["time"] >= t0 - 24 * 3600]
        if not rows:
            self._text(snapshot, "Aún no hay datos: se registran mientras el servicio está activo",
                       (self.PAD_L + plot_r) / 2, h / 2, _ink(fg, 0.6), "center")
            return

        # charging bands (both earbuds charging)
        band_start = None
        for r in rows + [{"time": now, "left_charging": 0, "right_charging": 0}]:
            charging = r.get("left_charging") and r.get("right_charging")
            if charging and band_start is None:
                band_start = r["time"]
            elif not charging and band_start is not None:
                x0 = max(self.PAD_L, self._x(band_start, w, t0))
                x1 = min(plot_r, self._x(r["time"], w, t0))
                if x1 > x0:
                    snapshot.append_color(_ink(fg, 0.06), _rect(x0, self.PAD_T, x1 - x0, h - self.PAD_T - self.PAD_B))
                band_start = None

        # series: step lines (a level holds until the next reading), 2px, clipped to the plot
        snapshot.push_clip(_rect(self.PAD_L, 0, plot_r - self.PAD_L, h))
        ends = []
        for side, (name, _l, _d) in SERIES.items():
            pts = [(r["time"], r[side]) for r in rows if r.get(side) is not None]
            if not pts:
                continue
            line = [(self._x(pts[0][0], w, t0), self._y(pts[0][1], h))]
            for (_t, lvl), (t2, lvl2) in zip(pts, pts[1:]):
                line.append((self._x(t2, w, t0), self._y(lvl, h)))
                line.append((self._x(t2, w, t0), self._y(lvl2, h)))
            line.append((plot_r, self._y(pts[-1][1], h)))
            _stroke(snapshot, line, _series_rgba(side))
            ends.append((side, name, pts[-1][1]))
        snapshot.pop()

        # direct labels at the line ends (text ink; the dot carries identity); nudge apart if close
        ys = {s: self._y(lvl, h) for s, _, lvl in ends}
        if len(ends) == 2 and abs(ys["left"] - ys["right"]) < 14:
            mid = (ys["left"] + ys["right"]) / 2
            hi, lo = sorted(ys, key=ys.get)
            ys[hi], ys[lo] = mid - 7, mid + 7
        for side, name, lvl in ends:
            _circle(snapshot, plot_r + 9, ys[side], 4, _series_rgba(side))
            self._text(snapshot, f"{name[:3]} {lvl} %", plot_r + 17, ys[side], _ink(fg, 0.85))

        # hover: crosshair + values at that moment
        if self.hover_x is None or not (self.PAD_L <= self.hover_x <= plot_r):
            return
        t = t0 + (self.hover_x - self.PAD_L) / (plot_r - self.PAD_L) * self.span
        before = [r for r in rows if r["time"] <= t]
        if not before:
            return
        r = before[-1]
        snapshot.append_color(_ink(fg, 0.35), _rect(round(self.hover_x), self.PAD_T, 1, h - self.PAD_T - self.PAD_B))
        lt = time.localtime(t)
        day = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")[lt.tm_wday]
        lines = [(None, f"{day} {time.strftime('%H:%M', lt)}")]
        for side, (name, _l, _d) in SERIES.items():
            if r.get(side) is not None:
                lines.append((side, f"{name} {r[side]} %" + (" ⚡" if r.get(f"{side}_charging") else "")))
        widths = [self._layout(txt).get_pixel_size()[0] for _, txt in lines]
        bw, bh = max(widths) + 32, 8 + 17 * len(lines)
        bx = self.hover_x + 10 if self.hover_x + 10 + bw < w else self.hover_x - 10 - bw
        by = self.PAD_T + 2
        bg = Gdk.RGBA()
        bg.parse("#2e2e32" if _dark() else "#ffffff")
        snapshot.append_color(_ink(fg, 0.15), _rect(bx - 1, by - 1, bw + 2, bh + 2))
        snapshot.append_color(bg, _rect(bx, by, bw, bh))
        for i, (side, txt) in enumerate(lines):
            ty = by + 13 + 17 * i
            if side is None:
                self._text(snapshot, txt, bx + 8, ty, _ink(fg, 0.6))
            else:
                _circle(snapshot, bx + 12, ty, 4, _series_rgba(side))
                self._text(snapshot, txt, bx + 22, ty, _ink(fg, 0.9))


class BatteryHistory(Adw.PreferencesGroup):
    def __init__(self):
        super().__init__(title="Historial de batería",
                         description="Gris: cargando en el estuche")
        self.range = RANGES[0]
        toggles = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for key, label, _ in RANGES:
            toggles.add(Adw.Toggle(name=key, label=label))
        toggles.set_active_name(RANGES[0][0])
        toggles.connect("notify::active-name", lambda g, _: self._set_range(g.get_active_name()))
        self.set_header_suffix(toggles)

        legend = Gtk.Box(spacing=16, margin_bottom=6)
        for side, (name, _l, _d) in SERIES.items():
            item = Gtk.Box(spacing=6)
            item.append(Dot(side))
            item.append(Gtk.Label(label=name, css_classes=["caption"]))
            legend.append(item)
        self.chart = BatteryChart()
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=10,
                        margin_start=8, margin_end=8)
        inner.append(legend)
        inner.append(self.chart)
        frame.append(inner)
        self.add(frame)

        rows = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, margin_top=12)
        self.left_row = Adw.ActionRow(title="Tiempo restante estimado", subtitle="—")
        self.rate_row = Adw.ActionRow(title="Ritmo de descarga", subtitle="—")
        rows.append(self.left_row)
        rows.append(self.rate_row)
        self.add(rows)
        self.refresh()
        GLib.timeout_add_seconds(60, lambda: (self.refresh(), True)[1])

    def _set_range(self, key):
        self.range = next(r for r in RANGES if r[0] == key)
        self.refresh()

    def refresh(self):
        span = self.range[2]
        self.chart.set_rows(battlog.load(since=time.time() - span - 24 * 3600), span)
        est = battlog.estimate()
        if est:
            name = SERIES[est["side"]][0].lower()
            self.left_row.set_subtitle(f"≈ {battlog.fmt_hours(est['hours'])} (el {name} se acaba primero)")
        else:
            self.left_row.set_subtitle("Se calcula después de unos 20 min de uso fuera del estuche")
        months = battlog.monthly_rates()
        if not months:
            self.rate_row.set_subtitle("Aún sin suficientes datos")
        elif len(months) == 1:
            self.rate_row.set_subtitle(f"{months[0][1]:.1f} %/h este mes · compáralo en unos meses para ver "
                                       "el desgaste")
        else:
            (m0, r0), (m1, r1) = months[0], months[-1]
            change = (r1 / r0 - 1) * 100 if r0 else 0
            self.rate_row.set_subtitle(f"{r1:.1f} %/h este mes · {r0:.1f} %/h en {m0} "
                                       f"({change:+.0f} %{' — se gasta más rápido' if change > 15 else ''})")
