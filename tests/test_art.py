import unittest

import _env  # noqa: F401
from redmibuds import art


class Illustrations(unittest.TestCase):
    def test_every_token_is_filled(self):
        import re
        for name in ("earbud-left", "earbud-right", "case"):
            svg = re.sub(r"<!--.*?-->", "", art.svg_for(name), flags=re.S)
            self.assertNotRegex(svg, r"@\w+@")

    def test_side_letter_stays_readable(self):
        import re
        body = {s: re.sub(r"<!--.*?-->", "", art.svg_for(f"earbud-{s}"), flags=re.S) for s in ("left", "right")}
        self.assertIn(">L<", body["left"])
        self.assertIn(">R<", body["right"])
        self.assertIn("scale(-1,1)", body["right"])         # drawn as the left bud, mirrored for the right
        self.assertNotIn("scale(-1,1)", body["left"])

    def test_renders(self):
        for name in ("earbud-left", "earbud-right", "case"):
            pb = art.icon_pixbuf(name, 64)
            self.assertEqual((pb.get_width(), pb.get_height()), (64, 64))

    def test_bar_icon_is_trimmed_square(self):
        pb = art.bar_icon("case", 24)
        self.assertEqual((pb.get_width(), pb.get_height()), (24, 24))


if __name__ == "__main__":
    unittest.main()
