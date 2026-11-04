"""Device illustrations (data/icons/*.svg), drawn in Obsidian Black. GdkPixbuf only (no GTK),
so the CLI can render them too (Waybar icons)."""
import os

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons")


def svg_for(name):
    """SVG text of an illustration. name: "case", "earbud-left" or "earbud-right"
    (the right one is the left drawing mirrored, letter upright)."""
    side = None
    if name.startswith("earbud"):
        side = "right" if name.endswith("right") else "left"
        name = "earbud"
    with open(os.path.join(ICON_DIR, f"{name}.svg")) as f:
        text = f.read()
    if side:
        right = side == "right"
        text = (text.replace("@flip@", "translate(128,0) scale(-1,1)" if right else "translate(0,0)")
                    .replace("@letter@", "R" if right else "L"))
    return text


def _crop(pb, pad=1):
    """Trims transparent margins (alpha < 24) and returns a square pixbuf centered on the drawing."""
    w, h, rs, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
    data = pb.get_pixels()
    xs, ys = [], []
    for y in range(h):
        row = y * rs
        for x in range(w):
            if data[row + x * n + 3] >= 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return pb
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    side = max(x1 - x0, y1 - y0) + 1 + 2 * pad
    out = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
    out.fill(0)
    ox, oy = (side - (x1 - x0 + 1)) // 2, (side - (y1 - y0 + 1)) // 2
    pb.copy_area(x0, y0, x1 - x0 + 1, y1 - y0 + 1, out, ox, oy)
    return out


# Light panel behind the bar icons so they read on the dark (Ayu) Waybar: Ayu "texto" (theme.AYU["fg"])
# with a slightly darker rim of the same hue.
BAR_PANEL = {"fill": "#e5e1cf", "rim": "#bdb8a4"}


def _bar_panel(px):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {px} {px}" width="{px}" height="{px}">'
           f'<rect x="0.5" y="0.5" width="{px - 1}" height="{px - 1}" rx="{px * 0.26:.1f}" '
           f'fill="{BAR_PANEL["fill"]}" stroke="{BAR_PANEL["rim"]}" stroke-width="1"/></svg>')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


def bar_icon(name, px, panel=True):
    """Compact icon for a status bar: no contact shadow, margins trimmed, on a light rounded panel."""
    svg = svg_for(name)
    import re
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)       # comments may mention <g id="shadow"> too
    svg = re.sub(r"<g id=\"shadow\">.*?</g>", "", svg, count=1, flags=re.S)     # the contact shadow
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(px * 2, px * 2)
    loader.write(svg.encode())
    loader.close()
    pb = _crop(loader.get_pixbuf())
    if not panel:
        return pb.scale_simple(px, px, GdkPixbuf.InterpType.HYPER)
    inner = round(px * 0.8)                       # drawing inset inside the panel
    pb = pb.scale_simple(inner, inner, GdkPixbuf.InterpType.HYPER)
    out = _bar_panel(px)
    off = (px - inner) // 2
    pb.composite(out, off, off, inner, inner, off, off, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return out


def icon_pixbuf(name, px):
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(px, px)
    loader.write(svg_for(name).encode())
    loader.close()
    return loader.get_pixbuf()


