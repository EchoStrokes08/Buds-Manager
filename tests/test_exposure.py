import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: F401
from redmibuds import exposure as E


class Math(unittest.TestCase):
    def test_who_reference(self):
        self.assertAlmostEqual(E.safe_hours(80), 40)
        self.assertAlmostEqual(E.safe_hours(83), 20)      # +3 dB halves the time
        self.assertAlmostEqual(E.safe_hours(77), 80)

    def test_cubic_volume(self):
        self.assertAlmostEqual(E.level_db(1.0, 95), 95)
        self.assertAlmostEqual(E.level_db(0.5, 95), 95 - 18.06, places=1)


class Alerts(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        os.remove(self.path)
        self.sent = []
        self.p = mock.patch.object(E, "notify", lambda *a: self.sent.append(a[0]))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        if os.path.exists(self.path):
            os.remove(self.path)

    def listen(self, ex, minutes, level):
        for _ in range(minutes):
            ex._add(1, (1 / 60) / E.safe_hours(level))

    def test_80_then_100(self):
        ex = E.Exposure(lambda: None, lambda: None, self.path)
        self.listen(ex, 64, 95)                            # 1.2 h is the weekly dose at 95 dB
        self.assertEqual(len(self.sent), 1)
        self.listen(ex, 15, 95)
        self.assertEqual(len(self.sent), 2)
        self.listen(ex, 10, 95)
        self.assertEqual(len(self.sent), 2)                # no repeats

    def test_no_repeat_after_restart(self):
        ex = E.Exposure(lambda: None, lambda: None, self.path)
        self.listen(ex, 64, 95)
        ex2 = E.Exposure(lambda: None, lambda: None, self.path)
        self.listen(ex2, 1, 95)
        self.assertEqual(len(self.sent), 1)

    def test_summary(self):
        ex = E.Exposure(lambda: None, lambda: None, self.path)
        self.listen(ex, 30, 80)
        s = E.summary(self.path)
        self.assertEqual(s["today_min"], 30)
        self.assertAlmostEqual(s["week"], 0.5 / 40, places=4)


if __name__ == "__main__":
    unittest.main()
