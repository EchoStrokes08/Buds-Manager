import os
import shutil
import subprocess
import unittest

import _env

ROOT = _env.ROOT


class Packaging(unittest.TestCase):
    def test_install_script_syntax(self):
        subprocess.run(["bash", "-n", os.path.join(ROOT, "install.sh")], check=True)

    def test_desktop_file(self):
        with open(os.path.join(ROOT, "io.github.echostrokes08.BudsManager.desktop")) as f:
            text = f.read()
        self.assertIn("Exec=buds-manager", text)               # install.sh rewrites it to the full path
        self.assertIn("StartupWMClass=io.github.echostrokes08.BudsManager", text)

    def test_units_have_no_personal_paths(self):
        for unit in ("buds-manager-daemon.service", "buds-tray.service"):
            with open(os.path.join(ROOT, unit)) as f:
                text = f.read()
            self.assertIn("ExecStart=%h/.local/bin/", text)
            self.assertNotIn("/home/", text)

    @unittest.skipUnless(shutil.which("visudo"), "visudo not available")
    def test_sudoers_rule(self):
        subprocess.run(["visudo", "-cf", os.path.join(ROOT, "71-redmi-buds-rssi.sudoers")], check=True,
                       capture_output=True)

    def test_modules_compile(self):
        import compileall
        self.assertTrue(compileall.compile_dir(os.path.join(ROOT, "redmibuds"), quiet=1, force=False))


if __name__ == "__main__":
    unittest.main()
