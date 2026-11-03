import unittest
from unittest import mock

import _env
from redmibuds import audio
from redmibuds import automations as A

MAC = "B8:53:84:DD:52:9E"
BT = "bluez_output.B8_53_84_DD_52_9E.1"
EQ = "buds_pceq.B8_53_84_DD_52_9E"
PC = "alsa_output.pc"


def st(left, right):
    return {"wear": {"left": left, "right": right}}


class Base(unittest.TestCase):
    def setUp(self):
        _env.reset_config()
        A._save_moved(None)
        self.env = {"default": BT, "sinks": [PC, BT], "configured": None}
        self.sent = []
        e = self.env
        self.patches = [
            mock.patch.object(audio, "default_sink", lambda: e["default"]),
            mock.patch.object(audio, "set_default_sink", lambda n: e.update(default=n)),
            mock.patch.object(audio, "pc_sink", lambda: PC),
            mock.patch.object(audio, "_json", lambda *a: [{"name": n} for n in e["sinks"]]),
            mock.patch.object(audio, "set_configured_sink", lambda n: e.update(configured=n)),
            mock.patch.object(A, "notify", lambda *a, **k: self.sent.append(a)),
            mock.patch.object(A, "_run", lambda *a: "Connected: no"),
            mock.patch.object(A, "_playing_players", lambda: []),
        ]
        for p in self.patches:
            p.start()
        self.cfg = A.load_config()
        self.cfg.update(auto_pause=False, auto_resume=False, battery_alerts=False, case_reminder=False)
        self.cfg_patch = mock.patch.object(A, "load_config", lambda: self.cfg)
        self.cfg_patch.start()

    def tearDown(self):
        self.cfg_patch.stop()
        for p in self.patches:
            p.stop()
        A._save_moved(None)


class OutputFollowsEarbuds(Base):
    def test_out_and_back(self):
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a.on_state(MAC, st("ear", "case"))
        self.assertEqual(self.env["default"], BT)            # one still in: nothing changes
        a.on_state(MAC, st("case", "case"))
        self.assertEqual(self.env["default"], PC)
        a.on_state(MAC, st("ear", "case"))
        self.assertEqual(self.env["default"], BT)

    def test_survives_daemon_restart(self):
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a.on_state(MAC, st("case", "case"))
        A.Automations().on_state(MAC, st("ear", "ear"))     # new daemon, first reading: earbuds in
        self.assertEqual(self.env["default"], BT)
        self.assertIsNone(A._load_moved())

    def test_disconnect_restores_preferred_output(self):
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a.on_state(MAC, st("out", "out"))
        a.on_state(MAC, None)
        self.assertEqual(self.env["configured"], BT)

    def test_returns_to_pc_equalizer(self):
        self.env.update(default=EQ, sinks=[PC, BT, EQ])
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a.on_state(MAC, st("case", "out"))
        a.on_state(MAC, st("ear", "case"))
        self.assertEqual(self.env["default"], EQ)

    def test_user_choice_meanwhile_is_kept(self):
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a.on_state(MAC, st("case", "case"))
        self.env["default"] = "hdmi_output"                  # picked by hand
        a.on_state(MAC, st("ear", "ear"))
        self.assertEqual(self.env["default"], "hdmi_output")


class CaseReminder(Base):
    def setUp(self):
        super().setUp()
        self.cfg["case_reminder"] = True
        self.cfg["auto_output"] = False

    def test_no_alert_when_worn_or_in_case(self):
        a = A.Automations()
        a.on_state(MAC, st("ear", "ear"))
        a._left_out_on_disconnect = A.Automations._left_out_on_disconnect.__get__(a)
        with mock.patch.object(A, "time", type("FT", (), {"sleep": staticmethod(lambda s: None),
                                                                   "time": staticmethod(__import__("time").time),
                                                                   "strftime": staticmethod(__import__("time").strftime)})):
            a.on_state(MAC, None)
            a.on_state(MAC, st("case", "case"))
            a.on_state(MAC, None)
        self.assertEqual(self.sent, [])

    def test_reminder_once_after_10_min(self):
        a = A.Automations()
        a.on_state(MAC, st("case", "out"))
        a.out_since -= A.LEFT_OUT_AFTER + 1
        a.on_state(MAC, st("case", "out"))
        a.on_state(MAC, st("case", "out"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("derecho", self.sent[0][1])

    def test_disconnected_outside(self):
        a = A.Automations()
        a.on_state(MAC, st("out", "out"))
        with mock.patch("threading.Thread", lambda target, daemon: type("T", (), {"start": lambda s: target()})()), \
                mock.patch.object(A, "time", type("FT", (), {"sleep": staticmethod(lambda s: None),
                                                                   "time": staticmethod(__import__("time").time),
                                                                   "strftime": staticmethod(__import__("time").strftime)})):
            a.on_state(MAC, None)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Los dos", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
