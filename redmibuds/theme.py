"""Look of Buds Manager: the Ayu theme (default) or the system's libadwaita theme, plus custom widgets
(battery rings, noise-mode tiles, status pill) drawn with GSK so they follow whichever theme is active.

config.json: "theme": "ayu" | "system"
"""
import math
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

# Ayu dark, as in the Waybar rice (fondo, texto, naranja, amarillo, azul, verde, rojo, gris)
AYU = {"bg": "#0e1419", "card": "#141c23", "raised": "#1a242d", "fg": "#e5e1cf", "dim": "#8a96a3",
       "accent": "#f19618", "yellow": "#e6c446", "blue": "#36a3d9", "green": "#b8cc52", "red": "#ff3333"}

AYU_CSS = f"""
:root {{
  --window-bg-color: {AYU['bg']};
  --window-fg-color: {AYU['fg']};
  --view-bg-color: {AYU['bg']};
  --view-fg-color: {AYU['fg']};
  --headerbar-bg-color: {AYU['bg']};
  --headerbar-fg-color: {AYU['fg']};
  --headerbar-backdrop-color: {AYU['bg']};
  --headerbar-border-color: transparent;
  --headerbar-shade-color: rgb(0 0 0 / 0.35);
  --card-bg-color: {AYU['card']};
  --card-fg-color: {AYU['fg']};
  --card-shade-color: rgb(0 0 0 / 0.45);
  --dialog-bg-color: {AYU['card']};
  --dialog-fg-color: {AYU['fg']};
  --popover-bg-color: {AYU['raised']};
  --popover-fg-color: {AYU['fg']};
  --popover-shade-color: rgb(0 0 0 / 0.5);
  --thumbnail-bg-color: {AYU['card']};
  --sidebar-bg-color: {AYU['bg']};
  --sidebar-fg-color: {AYU['fg']};
  --accent-bg-color: {AYU['accent']};
  --accent-fg-color: {AYU['bg']};
  --accent-color: #f6ab45;
  --success-bg-color: #8fa33a;
  --success-color: {AYU['green']};
  --warning-bg-color: #c9a62a;
  --warning-color: {AYU['yellow']};
  --error-bg-color: #d42a2a;
  --error-color: #ff6a6a;
  --destructive-bg-color: #d42a2a;
  --destructive-color: #ff6a6a;
  --border-color: rgb(229 225 207 / 0.08);
  --shade-color: rgb(0 0 0 / 0.4);
}}
.boxed-list, .card {{ border-radius: 16px; }}
.boxed-list {{ box-shadow: 0 0 0 1px rgb(229 225 207 / 0.06), 0 2px 6px rgb(0 0 0 / 0.25); }}
headerbar {{ box-shadow: none; }}
viewswitcher button:checked {{ background: alpha({AYU['accent']}, 0.14); color: {AYU['accent']}; }}
viewswitcherbar viewswitcher button:checked {{ background: alpha({AYU['accent']}, 0.14); color: {AYU['accent']}; }}
.hero-card {{
  background: linear-gradient(160deg, #18232d 0%, {AYU['card']} 55%, #111920 100%);
  box-shadow: 0 0 0 1px rgb(229 225 207 / 0.06), 0 8px 24px rgb(0 0 0 / 0.35);
}}
"""

# Shared extras (both themes): layout and the custom widgets
BASE_CSS = """
preferencesgroup label.heading { font-size: 15px; font-weight: 800; letter-spacing: -0.01em; }
preferencesgroup > box > box label.dim-label { font-size: 12.5px; }
.hero-card { border-radius: 22px; padding: 18px 10px 16px 10px; background: var(--card-bg-color);
             box-shadow: 0 0 0 1px alpha(currentColor, 0.06), 0 4px 14px var(--card-shade-color); }
.hero-title { font-size: 22px; font-weight: 800; letter-spacing: -0.01em; }
.hero-sub { font-size: 12px; opacity: .65; }
.status-pill { border-radius: 999px; padding: 4px 12px; font-size: 12px; font-weight: 700;
               background: alpha(currentColor, 0.08); }
.status-pill .dot { color: var(--success-color); }
levelbar.batt-bar trough { min-height: 5px; border-radius: 99px; background: alpha(currentColor, 0.12); padding: 0; }
levelbar.batt-bar block.filled { border-radius: 99px; background: var(--accent-bg-color); min-height: 5px; }
levelbar.batt-bar.low block.filled { background: var(--warning-bg-color); }
levelbar.batt-bar.critical block.filled { background: var(--error-bg-color); }
levelbar.batt-bar block.empty { background: transparent; }
.ring-pct { font-size: 24px; font-weight: 800; font-feature-settings: "tnum"; letter-spacing: -0.02em; }
.ring-name { font-size: 13px; font-weight: 700; opacity: .9; }
.ring-sub { font-size: 11px; opacity: .6; }
.mode-tile { border-radius: 18px; padding: 14px 6px 12px 6px; min-height: 92px;
             background: var(--card-bg-color); color: var(--window-fg-color);
             box-shadow: 0 0 0 1px alpha(currentColor, 0.07);
             transition: all 180ms ease-out; }
.mode-tile:hover { background: color-mix(in srgb, var(--card-bg-color) 88%, var(--accent-bg-color)); }
.mode-tile:checked { background: linear-gradient(160deg, color-mix(in srgb, var(--accent-bg-color) 92%, white),
                                                  var(--accent-bg-color));
                     color: var(--accent-fg-color);
                     box-shadow: 0 0 0 1px var(--accent-bg-color),
                                 0 8px 22px alpha(var(--accent-bg-color), 0.35); }
.mode-tile label { font-weight: 700; font-size: 13px; }
window.narrow .mode-tile { min-height: 78px; padding: 10px 2px; }
window.narrow .hero-card { padding: 14px 4px 12px 4px; }
window.narrow .ring-pct { font-size: 20px; }
"""


def _rgba(hexcolor, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(hexcolor)
    c.alpha = alpha
    return c


def _with_alpha(c, alpha):
    c = c.copy()
    c.alpha = alpha
    return c


def accent_rgba(widget):
    """The accent of the active theme (Ayu orange, or the system accent)."""
    if current() == "ayu":
        return _rgba(AYU["accent"])
    return Adw.StyleManager.get_default().get_accent_color_rgba()


def status_rgba(widget, kind):
    if current() == "ayu":
        return _rgba({"warning": AYU["yellow"], "error": AYU["red"], "success": AYU["green"]}[kind])
    return _rgba({"warning": "#e5a50a", "error": "#e01b24", "success": "#2ec27e"}[kind])


# ---------- theme switching ----------

_providers = {}
_current = "ayu"


def current():
    return _current


def apply(name):
    """Installs the theme for the whole app (call after the display exists)."""
    global _current
    display = Gdk.Display.get_default()
    for key in ("ayu", "base"):
        if key not in _providers:
            p = Gtk.CssProvider()
            p.load_from_string(AYU_CSS if key == "ayu" else BASE_CSS)
            _providers[key] = p
    Gtk.StyleContext.add_provider_for_display(display, _providers["base"], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    sm = Adw.StyleManager.get_default()
    if name == "ayu":
        sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        Gtk.StyleContext.add_provider_for_display(display, _providers["ayu"],
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    else:
        sm.set_color_scheme(Adw.ColorScheme.DEFAULT)
        Gtk.StyleContext.remove_provider_for_display(display, _providers["ayu"])
    _current = name


# ---------- widgets ----------

from .art import icon_pixbuf  # noqa: E402

_textures = {}


def icon_texture(name, px):
    """Detailed device illustration (data/icons/<name>.svg) rendered at px×px, cached."""
    key = (name, px)
    if key not in _textures:
        _textures[key] = Gdk.Texture.new_for_pixbuf(icon_pixbuf(name, px))
    return _textures[key]


class BatteryRing(Gtk.Widget):
    """Circular battery gauge: track + arc (accent; warning ≤ 20 %, error ≤ 10 %) around the device icon
    (left / right earbud or case). A ⚡ badge shows while charging."""

    SIZE = 98

    def __init__(self, kind):
        super().__init__(halign=Gtk.Align.CENTER)
        self.kind = kind
        self.set_size_request(self.SIZE, self.SIZE)
        self.level = None
        self.charging = False
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    def set_level(self, level, charging=False):
        self.level, self.charging = level, charging
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{level} %" + (" cargando" if charging else "") if level is not None else "sin dato"])
        self.queue_draw()

    def _color(self):
        if self.level is None:
            return _with_alpha(self.get_color(), 0.3)
        if self.level <= 10 and not self.charging:
            return status_rgba(self, "error")
        if self.level <= 20 and not self.charging:
            return status_rgba(self, "warning")
        return accent_rgba(self)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        size = min(w, h)
        cx, cy = w / 2, h / 2
        stroke_w = max(6.0, size * 0.075)
        r = size / 2 - stroke_w / 2 - 1
        fg = self.get_color()

        track = Gsk.PathBuilder.new()
        track.add_circle(Graphene.Point().init(cx, cy), r)
        snapshot.append_stroke(track.to_path(), Gsk.Stroke.new(stroke_w), _with_alpha(fg, 0.10))
        if self.level:
            frac = max(0.0, min(1.0, self.level / 100))
            st = Gsk.Stroke.new(stroke_w)
            st.set_line_cap(Gsk.LineCap.ROUND)
            b = Gsk.PathBuilder.new()
            if frac >= 0.999:
                b.add_circle(Graphene.Point().init(cx, cy), r)
            else:
                end = -math.pi / 2 + frac * 2 * math.pi
                b.move_to(cx, cy - r)
                b.svg_arc_to(r, r, 0, frac > 0.5, True, cx + r * math.cos(end), cy + r * math.sin(end))
            snapshot.append_stroke(b.to_path(), st, self._color())

        # device illustration in the middle (crisp: rendered at the monitor's scale)
        icon = size * 0.66
        ix, iy = cx - icon / 2, cy - icon / 2
        px = int(math.ceil(icon * self.get_scale_factor() * 1.5))
        tex = icon_texture("case" if self.kind == "case" else f"earbud-{self.kind}", px)
        snapshot.save()
        if self.level is None:
            snapshot.push_opacity(0.4)
        snapshot.translate(Graphene.Point().init(ix, iy))
        snapshot.append_texture(tex, Graphene.Rect().init(0, 0, icon, icon))
        if self.level is None:
            snapshot.pop()
        snapshot.restore()

        # charging badge on the ring (bottom-right)
        if self.charging:
            bx, by = cx + r * math.cos(math.pi / 4), cy + r * math.sin(math.pi / 4)
            br = size * 0.13
            badge = Gsk.PathBuilder.new()
            badge.add_circle(Graphene.Point().init(bx, by), br + 2)
            snapshot.append_fill(badge.to_path(), Gsk.FillRule.WINDING, _badge_bg(self))
            inner = Gsk.PathBuilder.new()
            inner.add_circle(Graphene.Point().init(bx, by), br)
            snapshot.append_fill(inner.to_path(), Gsk.FillRule.WINDING, accent_rgba(self))
            bolt = Gsk.PathBuilder.new()                       # lightning bolt
            k = br * 0.9
            for i, (px, py) in enumerate([(0.15, -0.75), (-0.45, 0.10), (-0.02, 0.10), (-0.18, 0.75),
                                          (0.45, -0.12), (0.02, -0.12)]):
                (bolt.move_to if i == 0 else bolt.line_to)(bx + px * k, by + py * k)
            bolt.close()
            fgc = _rgba(AYU["bg"]) if current() == "ayu" else _rgba("#ffffff")
            snapshot.append_fill(bolt.to_path(), Gsk.FillRule.WINDING, fgc)


def _badge_bg(widget):
    """Ring of card color around the charging badge, so it reads as cut out of the gauge."""
    if current() == "ayu":
        return _rgba("#16202a")
    return _rgba("#2e2e32" if Adw.StyleManager.get_default().get_dark() else "#ffffff")


class DeviceArt(Gtk.Widget):
    """Large device illustration (left / right earbud or case), crisp at any scale."""

    def __init__(self, kind, size=112):
        super().__init__(halign=Gtk.Align.CENTER)
        self.kind, self.size = kind, size
        self.dim = False
        self.set_size_request(size, size)

    def set_dim(self, dim):
        self.dim = dim
        self.queue_draw()

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        s = min(w, h)
        x, y = (w - s) / 2, (h - s) / 2
        px = int(math.ceil(s * self.get_scale_factor() * 1.25))
        tex = icon_texture("case" if self.kind == "case" else f"earbud-{self.kind}", px)
        snapshot.save()
        if self.dim:
            snapshot.push_opacity(0.35)
        snapshot.translate(Graphene.Point().init(x, y))
        snapshot.append_texture(tex, Graphene.Rect().init(0, 0, s, s))
        if self.dim:
            snapshot.pop()
        snapshot.restore()


class BatteryCard(Gtk.Box):
    """Device illustration, big percentage, thin battery bar, name and status (same API as before)."""

    def __init__(self, name, wear_labels, kind):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.wear_labels = wear_labels
        self.art = DeviceArt(kind)
        self.append(self.art)
        self.pct = Gtk.Label(css_classes=["ring-pct"], margin_top=4)
        self.pct.set_markup("—")
        self.append(self.pct)
        self.bar = Gtk.LevelBar(min_value=0, max_value=100, css_classes=["batt-bar"], halign=Gtk.Align.CENTER,
                                width_request=64, margin_top=2, margin_bottom=6)
        self.bar.remove_offset_value("low")
        self.bar.remove_offset_value("high")
        self.bar.remove_offset_value("full")
        self.append(self.bar)
        self.name = Gtk.Label(label=name, css_classes=["ring-name"])
        self.append(self.name)
        self.wear = Gtk.Label(label=" ", css_classes=["ring-sub"])
        self.append(self.wear)

    def set_wear(self, w):
        self.wear.set_label(self.wear_labels.get(w, "") if w else " ")

    def update(self, b):
        for c in ("low", "critical"):
            self.bar.remove_css_class(c)
        if not b:
            self.art.set_dim(True)
            self.pct.set_markup("—")
            self.bar.set_value(0)
            return
        self.art.set_dim(False)
        lvl = b["level"]
        bolt = "<span size='70%'> ⚡</span>" if b["charging"] else ""
        self.pct.set_markup(f"{lvl}<span size='60%' alpha='60%'> %</span>{bolt}")
        self.bar.set_value(lvl)
        if not b["charging"]:
            if lvl <= 10:
                self.bar.add_css_class("critical")
            elif lvl <= 20:
                self.bar.add_css_class("low")
        if b["charging"] and not self.wear.get_label().strip():
            self.wear.set_label("Cargando")


class ModeGlyph(Gtk.Widget):
    """Noise-mode glyphs: ○ off, ◐ cancelling, ◎ transparency (follow the label color)."""

    def __init__(self, mode):
        super().__init__(halign=Gtk.Align.CENTER)
        self.mode = mode
        self.set_size_request(34, 34)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 3
        fg = self.get_color()
        st = Gsk.Stroke.new(2.4)
        b = Gsk.PathBuilder.new()
        b.add_circle(Graphene.Point().init(cx, cy), r)
        snapshot.append_stroke(b.to_path(), st, fg)
        if self.mode == "anc":                     # half filled: sound blocked
            hb = Gsk.PathBuilder.new()
            hb.move_to(cx, cy - r)
            hb.svg_arc_to(r, r, 0, False, False, cx, cy + r)
            hb.close()
            snapshot.append_fill(hb.to_path(), Gsk.FillRule.WINDING, fg)
        elif self.mode == "transparency":          # inner ring: sound passes through
            ib = Gsk.PathBuilder.new()
            ib.add_circle(Graphene.Point().init(cx, cy), r * 0.45)
            snapshot.append_stroke(ib.to_path(), Gsk.Stroke.new(2.4), fg)
            dot = Gsk.PathBuilder.new()
            dot.add_circle(Graphene.Point().init(cx, cy), 2.2)
            snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, fg)


class ModeTiles(Gtk.Box):
    """Three big toggle tiles; same small API as Adw.ToggleGroup (active-name)."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, modes):
        super().__init__(spacing=10, homogeneous=True)
        self.buttons = {}
        first = None
        for key, label in modes:
            btn = Gtk.ToggleButton(css_classes=["mode-tile"])
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
            inner.append(ModeGlyph(key))
            inner.append(Gtk.Label(label=label))
            btn.set_child(inner)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [label])
            btn.connect("toggled", self._toggled, key)
            self.buttons[key] = btn
            self.append(btn)
        self._quiet = False

    def _toggled(self, btn, key):
        if btn.get_active() and not self._quiet:
            self.emit("changed", key)

    def set_active_name(self, key):
        self._quiet = True
        if key in self.buttons:
            self.buttons[key].set_active(True)
        else:
            for b in self.buttons.values():
                b.set_active(False)
        self._quiet = False

    def get_active_name(self):
        return next((k for k, b in self.buttons.items() if b.get_active()), None)


class StatusPill(Gtk.Box):
    """Small pill: ● LDAC (or other short status)."""

    def __init__(self):
        super().__init__(spacing=6, css_classes=["status-pill"], valign=Gtk.Align.CENTER)
        self.dot = Gtk.Label(label="●", css_classes=["dot"])
        self.text = Gtk.Label(label="")
        self.append(self.dot)
        self.append(self.text)

    def set_text(self, text, ok=True):
        self.set_visible(bool(text))
        self.text.set_label(text or "")
        self.dot.set_opacity(1 if ok else 0.35)


def card_rgba(widget):
    """Card background of the active theme (used to cut rings around drawn marks)."""
    if current() == "ayu":
        return _rgba(AYU["card"])
    return _rgba("#2e2e32" if Adw.StyleManager.get_default().get_dark() else "#ffffff")
