import threading
import time
import unittest
from unittest import mock

import _env
from redmibuds import sleeptimer as T


class SleepTimer(unittest.TestCase):
    def setUp(self):
        self.clock = _env.FakeClock()
        self.vols, self.cmds, self.modes = [], [], []
        fake_time = type("FakeTime", (), {"time": staticmethod(self.clock.time),
                                          "sleep": staticmethod(self._sleep)})
        self.patches = [
            mock.patch.object(T, "time", fake_time),
            mock.patch.object(T, "_sink_volume", lambda: 0.6),
            mock.patch.object(T, "_set_sink_volume", lambda v: self.vols.append(round(v, 3))),
            mock.patch.object(T.subprocess, "run", lambda c, **k: self.cmds.append(c)),
            mock.patch.object(T, "notify", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        self.lock = threading.Lock()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _sleep(self, s):
        with self.lock:
            self.clock.sleep(s)
        time.sleep(0)       # real yield so the test thread can act between steps

    def _wait_done(self, timer):
        deadline = time.monotonic() + 5
        while timer.end and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_full_cycle(self):
        t = T.SleepTimer(self.modes.append)
        t.start(30)
        self._wait_done(t)
        self.assertEqual(self.cmds, [["playerctl", "-a", "pause"]])
        self.assertEqual(self.modes, ["off"])
        self.assertEqual(self.vols[0], 0.6)                 # fade starts at the current volume…
        self.assertLess(min(self.vols), 0.6 * 0.3)         # …goes down to ~25 %…
        self.assertEqual(self.vols[-1], 0.6)                # …and is restored at the end
        self.assertEqual(t.remaining(), 0)

    def test_cancel_restores_volume(self):
        t = T.SleepTimer(self.modes.append)
        with mock.patch.object(T, "FADE", 0):                # no fade: it just waits
            t.start(30)
            t.cancel()
        time.sleep(0.05)
        self.assertEqual(self.cmds, [])
        self.assertEqual(self.modes, [])
        self.assertEqual(t.remaining(), 0)

    def test_restart_while_first_is_finishing(self):
        """A new timer started right while the old one pauses must not be clobbered by the old thread."""
        t = T.SleepTimer(self.modes.append)
        first = {"done": False}

        def run(cmd, **_):
            self.cmds.append(cmd)
            if not first["done"]:                            # the old timer is pausing right now
                first["done"] = True
                t.start(60)
        with mock.patch.object(T.subprocess, "run", run):
            t.start(30)
            deadline = time.monotonic() + 5
            while len(self.cmds) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self._wait_done(t)
        self.assertEqual(len(self.cmds), 2)                  # both timers paused once
        self.assertEqual(self.modes, ["off"])                # but only the live one finished the job


if __name__ == "__main__":
    unittest.main()
