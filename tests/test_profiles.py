import unittest
from unittest import mock

import _env  # noqa: F401
from redmibuds import profiles as P

PROFS = [{"app": "spotify", "when": "playing", "eq": "bass"},
         {"app": "zoom", "when": "focused", "mode": "transparency", "eq": "voice"},
         {"app": "vlc", "when": "focused", "pc_eq": "Películas"}]


class Pick(unittest.TestCase):
    def test_focused_beats_playing(self):
        self.assertEqual(P.pick(PROFS, "zoom", ["spotify spotify"])["app"], "zoom")

    def test_playing(self):
        self.assertEqual(P.pick(PROFS, "kitty", ["spotify spotify"])["app"], "spotify")

    def test_case_insensitive_substring(self):
        self.assertEqual(P.pick(PROFS, "us.zoom.Zoom", [])["app"], "zoom")

    def test_none(self):
        self.assertIsNone(P.pick(PROFS, "kitty", []))


class Transitions(unittest.TestCase):
    def setUp(self):
        self.state = {"mode": "anc", "eq": "balanced", "pc_eq": "off"}
        self.applied = []
        self.ap = P.AppProfiles(self._apply, lambda: dict(self.state))

    def _apply(self, changes):
        self.applied.append(changes)
        self.state.update(changes)

    def step(self, focus, playing):
        self.ap._focus = focus
        with mock.patch.object(P, "load_config", return_value={"app_profiles": PROFS}), \
                mock.patch.object(P, "playing_apps", return_value=playing):
            self.ap._step()

    def test_apply_and_restore(self):
        self.step("kitty", ["spotify"])
        self.assertEqual(self.state["eq"], "bass")
        self.step("kitty", [])
        self.assertEqual(self.state, {"mode": "anc", "eq": "balanced", "pc_eq": "off"})

    def test_switch_between_profiles_restores_base(self):
        self.step("kitty", ["spotify"])
        self.step("zoom", ["spotify"])
        self.assertEqual((self.state["mode"], self.state["eq"]), ("transparency", "voice"))
        self.step("kitty", ["spotify"])
        self.assertEqual((self.state["mode"], self.state["eq"]), ("anc", "bass"))
        self.step("kitty", [])
        self.assertEqual((self.state["mode"], self.state["eq"]), ("anc", "balanced"))

    def test_manual_change_is_kept(self):
        self.step("kitty", ["spotify"])
        self.state["eq"] = "treble"            # changed by hand while the profile is active
        self.step("kitty", [])
        self.assertEqual(self.state["eq"], "treble")

    def test_pc_eq_profile(self):
        self.step("vlc", [])
        self.assertEqual(self.state["pc_eq"], "Películas")
        self.step("kitty", [])
        self.assertEqual(self.state["pc_eq"], "off")

    def test_no_channel_keeps_memory(self):
        self.step("kitty", ["spotify"])
        self.ap.current = lambda: None         # the phone took the earbuds
        self.step("kitty", [])
        self.assertEqual(self.ap.active["app"], "spotify")


if __name__ == "__main__":
    unittest.main()
