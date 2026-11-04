import unittest

import _env  # noqa: F401
from redmibuds import audio
from redmibuds.calls import MAX_RELEASE_DELAY, RELEASE_DELAY, is_call, next_release_delay


def rec(app, binary="", role="", name=""):
    return {"app": app, "binary": binary, "role": role, "name": name}


class CallApps(unittest.TestCase):
    def test_call_apps(self):
        for app in ("Discord", "Zoom", "Telegram Desktop", "Firefox"):
            self.assertTrue(is_call(rec(app)), app)
        self.assertTrue(is_call(rec("Algo", role="Communication")))
        self.assertTrue(is_call(rec("MiApp"), extra=["miapp"]))

    def test_not_call_apps(self):
        self.assertFalse(is_call(rec("OBS", "obs")))
        self.assertFalse(is_call(rec("Grabadora", "parecord")))
        self.assertFalse(is_call(rec("pipewire", name="buds_mic_denoised_input")))


class ReleaseDelay(unittest.TestCase):
    def test_first_call_uses_base_delay(self):
        self.assertEqual(next_release_delay(RELEASE_DELAY, None), RELEASE_DELAY)

    def test_flapping_doubles_up_to_max(self):
        d = RELEASE_DELAY
        for _ in range(10):
            d = next_release_delay(d, 5)
        self.assertEqual(d, MAX_RELEASE_DELAY)
        self.assertEqual(next_release_delay(RELEASE_DELAY, 5), RELEASE_DELAY * 2)

    def test_calm_resets(self):
        self.assertEqual(next_release_delay(MAX_RELEASE_DELAY, 600), RELEASE_DELAY)


class BudsNodes(unittest.TestCase):
    def test_is_buds_node(self):
        mac = "B8:53:84:DD:52:9E"
        self.assertTrue(audio.is_buds_node("bluez_output.B8_53_84_DD_52_9E.1", mac))
        self.assertTrue(audio.is_buds_node("bluez_input.B8:53:84:DD:52:9E", mac))
        self.assertTrue(audio.is_buds_node("buds_pceq.B8_53_84_DD_52_9E", mac))
        self.assertFalse(audio.is_buds_node("alsa_output.pci-0000_04_00.6.analog-stereo", mac))
        self.assertFalse(audio.is_buds_node("bluez_output.AA_BB_CC_DD_EE_FF.1", mac))


if __name__ == "__main__":
    unittest.main()
