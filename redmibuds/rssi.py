"""Signal strength (RSSI) of the earbuds' Bluetooth link, for the "hot or cold" finder.

Reading it needs root (`btmgmt conn-info`); install.sh adds a sudoers rule that allows only that read-only
command (71-redmi-buds-rssi.sudoers), so `sudo -n` works without a password.
"""
import re
import subprocess

RULE = "/etc/sudoers.d/71-redmi-buds-rssi"


def read(mac):
    """(rssi dBm or None, problem text or None)."""
    try:
        r = subprocess.run(["sudo", "-n", "/usr/bin/btmgmt", "conn-info", "-t", "0", mac],
                           capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    out = r.stdout + r.stderr
    m = re.search(r"RSSI (-?\d+)", out)
    if m:
        return int(m.group(1)), None
    if "password" in out.lower() or "sudo" in out.lower():
        return None, "falta el permiso (instala la regla con ./install.sh)"
    if "Not Connected" in out or "status 0x02" in out:
        return None, "los audífonos no están conectados a esta PC"
    return None, out.strip().splitlines()[-1] if out.strip() else "sin respuesta"
