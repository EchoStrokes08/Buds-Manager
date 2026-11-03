"""Imported first by every test: private config/state dirs and the repo on sys.path.

The modules compute their file paths at import time, so this must run before importing redmibuds.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BUDS_QUIET", "1")

if not os.environ.get("BUDS_TEST_TMP"):
    tmp = tempfile.mkdtemp(prefix="buds-tests-")
    os.environ["BUDS_TEST_TMP"] = tmp
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
    os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(tmp, "run")
    for d in ("config", "state", "run"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)


def reset_config():
    """Removes the test config so each test starts from the defaults."""
    from redmibuds import automations
    try:
        os.remove(automations.CONFIG_PATH)
    except OSError:
        pass


class FakeClock:
    """Replaces time.time/time.sleep: sleeping just advances the clock (threads finish instantly)."""

    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s
