"""Device illustrations (data/icons/*.svg) in the earbuds' official colors. GdkPixbuf only (no GTK),
so the CLI can render them too (Waybar icons)."""
import os

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons")
# The three official colors of the REDMI Buds 8 Pro (config "buds_color").
BUDS_COLORS = {
    "black": {"label": "Negro obsidiana",
              "hi": "#5d626a", "mid": "#2b2e33", "lo": "#0e1012", "lidhi": "#555a61", "lidlo": "#25282c",
              "tiphi": "#4a4e54", "tipmid": "#26292d", "tiplo": "#101214",
              "ch1": "#0d0f12", "ch2": "#d9dde2", "ch3": "#3a3f46", "ch4": "#f4f6f8", "ch5": "#2a2e34", "ch6": "#08090b",
              "edge": "#ffffff", "edgea": "0.18", "rima": "0.45",
              "specexp": "34", "speck": "0.6", "diffk": "1.2", "diffb": "0.15", "chroma": "0.75", "tipspec": "0.35", "refl": "0.30", "casespecexp": "14", "casespeck": "0.40"},
    "white": {"label": "Blanco nube",
              "hi": "#f6f6f4", "mid": "#d9d8d3", "lo": "#9b9a94", "lidhi": "#fbfbfa", "lidlo": "#dedcd7",
              "tiphi": "#d2d4d6", "tipmid": "#a3a7ab", "tiplo": "#6b7075",
              "ch1": "#3a3f45", "ch2": "#f4f6f8", "ch3": "#7b828a", "ch4": "#ffffff", "ch5": "#5f666e", "ch6": "#22262b",
              "edge": "#6c7178", "edgea": "0.5", "rima": "0.5",
              "specexp": "28", "speck": "0.35", "diffk": "0.45", "diffb": "0.6", "chroma": "0.55", "tipspec": "0.25", "refl": "0.45", "casespecexp": "10", "casespeck": "0.22"},
    "blue": {"label": "Azul glaciar",
             "hi": "#e6f0f8", "mid": "#a9c2d6", "lo": "#5f7d96", "lidhi": "#eef5fb", "lidlo": "#b9cfe0",
             "tiphi": "#b9c6d1", "tipmid": "#7e8e9c", "tiplo": "#4c5966",
             "ch1": "#34404b", "ch2": "#f1f6fa", "ch3": "#76889a", "ch4": "#ffffff", "ch5": "#56687a", "ch6": "#1f2730",
             "edge": "#5a6b7a", "edgea": "0.5", "rima": "0.5",
              "specexp": "30", "speck": "0.45", "diffk": "0.55", "diffb": "0.5", "chroma": "0.6", "tipspec": "0.25", "refl": "0.40", "casespecexp": "12", "casespeck": "0.28"},
}


def buds_color():
    from .automations import load_config
    c = load_config().get("buds_color", "black")
    return c if c in BUDS_COLORS else "black"


def svg_for(name, color=None):
    """SVG text of an illustration with the palette of `color` filled in.
    name: "case", "earbud-left" or "earbud-right" (the right one is the left body mirrored, letter upright)."""
    pal = BUDS_COLORS[color or buds_color()]
    side = None
    if name.startswith("earbud"):
        side = "right" if name.endswith("right") else "left"
        name = "earbud"
    with open(os.path.join(ICON_DIR, f"{name}.svg")) as f:
        text = f.read()
    if side:
        right = side == "right"
        text = (text.replace("@flip@", "translate(128,0) scale(-1,1)" if right else "translate(0,0)")
                    .replace("@lx@", "101.5" if right else "26.5")
                    .replace("@shx@", "101.5" if right else "26.5")
                    .replace("@letter@", "R" if right else "L"))
    for k, v in pal.items():
        text = text.replace(f"@{k}@", v)
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


def bar_icon(name, px, color="white"):
    """Compact icon for a status bar: no contact shadow, margins trimmed, light palette for contrast."""
    svg = svg_for(name, color)
    import re
    svg = re.sub(r"^\s*<g id=\"shadow\">.*?</g>", "", svg, count=1, flags=re.S | re.M)     # the contact shadow
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(px * 2, px * 2)
    loader.write(svg.encode())
    loader.close()
    pb = _crop(loader.get_pixbuf())
    return pb.scale_simple(px, px, GdkPixbuf.InterpType.HYPER)


def icon_pixbuf(name, px, color=None):
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(px, px)
    loader.write(svg_for(name, color).encode())
    loader.close()
    return loader.get_pixbuf()


