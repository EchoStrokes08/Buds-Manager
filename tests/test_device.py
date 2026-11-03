import unittest

import _env  # noqa: F401
from redmibuds import device as d


class Decoding(unittest.TestCase):
    def test_battery(self):
        self.assertEqual(d.decode_battery(0x55), {"level": 85, "charging": False})
        self.assertEqual(d.decode_battery(0x80 | 90), {"level": 90, "charging": True})
        self.assertIsNone(d.decode_battery(0xFF))

    def test_wear(self):
        self.assertEqual(d.decode_wear(0x0C), {"left": "ear", "right": "ear"})
        self.assertEqual(d.decode_wear(0x03), {"left": "case", "right": "case"})
        self.assertEqual(d.decode_wear(0x00), {"left": "out", "right": "out"})
        self.assertEqual(d.decode_wear(0x09), {"left": "case", "right": "ear"})

    def test_eq_level_roundtrip(self):
        for lvl in range(-6, 7):
            self.assertEqual(d.decode_eq_level(d.encode_eq_level(lvl)), lvl)
        with self.assertRaises(ValueError):
            d.encode_eq_level(7)

    def test_name_of(self):
        self.assertEqual(d.name_of(d.MODES, 0x02), "transparency")
        self.assertEqual(d.name_of(d.MODES, 0x7F), "0x7f")


if __name__ == "__main__":
    unittest.main()
