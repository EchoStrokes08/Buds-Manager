import json
import unittest

import _env
from redmibuds import automations as A
from redmibuds import pceq


class Gains(unittest.TestCase):
    def test_pregain_and_balance(self):
        s = pceq.settings({"pc_eq": {"bands": [6] + [0] * 9, "balance": 50}})
        left, right = pceq.gains(s)
        self.assertAlmostEqual(right, 10 ** (-6 / 20))     # boost of +6 → −6 dB pre-gain
        self.assertAlmostEqual(left, right * 0.5)

    def test_clamps(self):
        s = pceq.settings({"pc_eq": {"bands": [30, -30], "balance": 500}})
        self.assertEqual(s["bands"][:2], [12.0, -12.0])
        self.assertEqual(len(s["bands"]), 10)
        self.assertEqual(s["balance"], 100)

    def test_config_has_both_chains(self):
        conf = pceq._config("B8:53:84:DD:52:9E", "bluez_output.X.1", pceq.settings({}))
        self.assertIn('node.name = "buds_pceq.B8_53_84_DD_52_9E"', conf)
        self.assertIn('target.object = "bluez_output.X.1"', conf)
        for ch in "LR":
            self.assertIn(f'output = "eq{ch}_9:Out" input = "eq{ch}_10:In"', conf)

    def test_params_cover_every_control(self):
        names = [k for k, _ in pceq.params(pceq.settings({}))]
        self.assertEqual(len(names), 22)
        self.assertIn("eqR_10:Gain", names)


class NamedCurves(unittest.TestCase):
    def setUp(self):
        _env.reset_config()
        cfg = A.load_config()
        cfg["pc_eq_presets"] = {"Podcasts": {"bands": [-4, -3, -1, 0, 2, 3, 3, 1, 0, -2], "balance": 0}}
        A.save_config(cfg)

    def test_off_preset_and_unsaved(self):
        self.assertEqual(pceq.current_value(), "off")
        pceq.set_value("Podcasts")
        self.assertEqual(pceq.current_value(), "Podcasts")
        cfg = A.load_config()
        cfg["pc_eq"]["bands"][0] = 5
        A.save_config(cfg)
        unsaved = pceq.current_value()
        self.assertTrue(unsaved.startswith("@"))
        pceq.set_value("off")
        pceq.set_value(unsaved)                             # an unsaved curve can be restored too
        self.assertEqual(json.loads(unsaved[1:])["bands"], [round(b) for b in pceq.settings()["bands"]])

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            pceq.set_value("No existe")


if __name__ == "__main__":
    unittest.main()
