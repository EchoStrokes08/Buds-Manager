import unittest

import _env  # noqa: F401
from redmibuds import audio
from redmibuds.calls import is_call


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
