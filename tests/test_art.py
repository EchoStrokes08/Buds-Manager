import unittest

import _env  # noqa: F401
from redmibuds import art


class Illustrations(unittest.TestCase):
    def test_every_color_fills_every_token(self):
        for color in art.BUDS_COLORS:
            for name in ("earbud", "case"):
                svg = art.svg_for(name, color)
                self.assertNotRegex(svg.split("-->", 1)[-1] if "@tokens@" in svg else svg, r'"@\w+@"')

    def test_renders(self):
        for name in ("earbud", "case"):
            pb = art.icon_pixbuf(name, 64, "black")
            self.assertEqual((pb.get_width(), pb.get_height()), (64, 64))

    def test_bar_icon_is_trimmed_square(self):
        pb = art.bar_icon("case", 24)
        self.assertEqual((pb.get_width(), pb.get_height()), (24, 24))


if __name__ == "__main__":
    unittest.main()
