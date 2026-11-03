import unittest

import _env  # noqa: F401
from redmibuds import art


class Illustrations(unittest.TestCase):
    def test_every_color_fills_every_token(self):
        import re
        for color in art.BUDS_COLORS:
            for name in ("earbud-left", "earbud-right", "case"):
                svg = re.sub(r"<!--.*?-->", "", art.svg_for(name, color), flags=re.S)
                self.assertNotRegex(svg, r"@\w+@")

    def test_side_letter_stays_readable(self):
        self.assertIn(">R<", art.svg_for("earbud-right", "black"))
        self.assertIn(">L<", art.svg_for("earbud-left", "black"))
        self.assertIn("scale(-1,1)", art.svg_for("earbud-right", "black"))

    def test_renders(self):
        for name in ("earbud-left", "earbud-right", "case"):
            pb = art.icon_pixbuf(name, 64, "black")
            self.assertEqual((pb.get_width(), pb.get_height()), (64, 64))

    def test_bar_icon_is_trimmed_square(self):
        pb = art.bar_icon("case", 24)
        self.assertEqual((pb.get_width(), pb.get_height()), (24, 24))


if __name__ == "__main__":
    unittest.main()
