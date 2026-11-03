import os
import tempfile
import time
import unittest
from unittest import mock

import _env  # noqa: F401
from redmibuds import battlog as B


def batt(l, r, lc=False, rc=False):
    return {"left": {"level": l, "charging": lc}, "right": {"level": r, "charging": rc},
            "case": {"level": 80, "charging": False}}


class BatteryLog(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        os.remove(self.path)
        self.log = B.BatteryLog(self.path)
        self.now = time.time()

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def at(self, t, *a, **k):
        with mock.patch.object(B.time, "time", lambda: t):
            return self.log.record(batt(*a, **k))

    def test_only_changes_are_written(self):
        self.assertTrue(self.at(self.now - 60, 90, 90))
        self.assertFalse(self.at(self.now, 90, 90))
        self.assertTrue(self.at(self.now, 85, 90))
        self.assertEqual(len(B.load(self.path, since=0)), 2)

    def test_estimate_current_run(self):
        # 100 → 90 in 39 min ≈ 15.4 %/h → 90 % lasts ≈ 5.8 h; the right one drains slower
        self.at(self.now - 2400, 100, 100)
        self.at(self.now - 1200, 95, 100)
        self.at(self.now - 60, 90, 95)
        e = B.estimate(B.load(self.path, since=0), self.now)
        self.assertEqual(e["side"], "left")
        self.assertAlmostEqual(e["rate"], 15.38, places=1)
        self.assertAlmostEqual(e["hours"], 5.85, places=1)

    def test_no_estimate_while_charging(self):
        self.at(self.now - 3000, 100, 100)
        self.at(self.now - 600, 80, 80)
        self.at(self.now - 60, 80, 80, True, True)
        self.assertIsNone(B.estimate(B.load(self.path, since=0), self.now))

    def test_short_run_uses_past_average(self):
        t0 = self.now - 10 * 3600
        for i, lvl in enumerate((100, 90, 80, 70)):       # 20 %/h run
            self.at(t0 + i * 1800, lvl, lvl)
        self.at(t0 + 7200, 70, 70, True, True)
        self.at(self.now - 300, 100, 100)                 # current run: too short to measure
        self.at(self.now - 60, 100, 99)
        e = B.estimate(B.load(self.path, since=0), self.now)
        self.assertAlmostEqual(e["rate"], 20, places=0)

    def test_fmt_hours(self):
        self.assertEqual(B.fmt_hours(5.85), "5 h 51 min")
        self.assertEqual(B.fmt_hours(0.5), "30 min")


if __name__ == "__main__":
    unittest.main()
