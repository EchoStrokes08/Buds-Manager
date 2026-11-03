"""PC-side audio settings for the earbuds (PipeWire / WirePlumber): codec profile and LDAC quality."""
import os
import re
import subprocess
import time

WP_DROPIN_DIR = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d")
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
RECONNECT = "reconnect-music"     # pseudo-profile shown when the music link (A2DP) dropped
LDAC_QUALITY = [("auto", "Automática"), ("hq", "Alta · 990 kbps"),
                ("sq", "Estándar · 660 kbps"), ("mq", "Estable · 330 kbps")]


def card_name(mac):
    return "bluez_card." + mac.replace(":", "_")


def _pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def profiles(mac):
    """Returns (list of (name, description), active name) for the earbuds' card, or ([], None)."""
    r = _pactl("list", "cards")
    if not r or r.returncode:
        return [], None
    block = None
    for chunk in r.stdout.split("\nCard #"):
        if f"Name: {card_name(mac)}" in chunk:
            block = chunk
            break
    if not block:
        return [], None
    out, active = [], None
    in_profiles = False
    for line in block.splitlines():
        s = line.strip()
        if s == "Profiles:":
            in_profiles = True
            continue
        if in_profiles:
            m = re.match(r"([\w-]+): (.*?) \(sinks:.*available: (\w+)\)", s)
            if m:
                if m.group(1) != "off" and m.group(3) == "yes":
                    out.append((m.group(1), _profile_label(m.group(1), m.group(2))))
                continue
            in_profiles = False
        if s.startswith("Active Profile:"):
            active = s.split(":", 1)[1].strip()
    if out and not any(is_music(p) for p, _ in out):
        out.insert(0, (RECONNECT, "Música · reconectar (se perdió el enlace)"))
    return out, active


def _profile_label(name, desc):
    m = re.search(r"codec ([\w-]+)", desc)
    codec = m.group(1) if m else ""
    if name.startswith("a2dp"):
        return f"Música · {codec}" if codec else "Música"
    if name.startswith("headset"):
        return f"Llamadas con micrófono · {codec}" if codec else "Llamadas con micrófono"
    return desc


def is_music(profile):
    return bool(profile) and profile.startswith("a2dp")


def connect_music(mac):
    """Re-opens the A2DP link. Restarting WirePlumber (or a hiccup while the phone is also connected)
    can drop it, leaving only the call profiles, and the earbuds don't reopen it by themselves."""
    path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
    r = subprocess.run(["busctl", "call", "org.bluez", path, "org.bluez.Device1", "ConnectProfile", "s",
                        A2DP_SINK_UUID], capture_output=True, text=True, timeout=20)
    if r.returncode and "AlreadyConnected" not in r.stderr:
        raise RuntimeError(r.stderr.strip() or "no se pudo reconectar la música")


def ensure_music(mac, prefer=None, timeout=8):
    """Makes the earbuds play music again: reconnects A2DP if needed and selects `prefer`
    (or the best music profile). Returns the profile set."""
    deadline = time.time() + timeout
    tried = False
    while True:
        profs, active = profiles(mac)
        music = [p for p, _ in profs if is_music(p)]
        if music:
            target = prefer if prefer in music else ("a2dp-sink" if "a2dp-sink" in music else music[0])
            if active != target:
                set_profile(mac, target)
            return target
        if time.time() > deadline:
            raise RuntimeError("los audífonos no ofrecen el perfil de música")
        if not tried and profs:          # card is back but without A2DP: ask BlueZ to reopen it
            connect_music(mac)
            tried = True
        time.sleep(0.5)


def set_profile(mac, profile):
    if profile == RECONNECT:
        ensure_music(mac)
        return
    r = _pactl("set-card-profile", card_name(mac), profile)
    if not r or r.returncode:
        raise RuntimeError((r.stderr.strip() if r else "") or "pactl falló")


def _dropin(mac):
    return os.path.join(WP_DROPIN_DIR, f"51-buds-manager-{mac.replace(':', '').lower()}.conf")


def ldac_quality(mac):
    try:
        with open(_dropin(mac)) as f:
            m = re.search(r'bluez5\.a2dp\.ldac\.quality\s*=\s*"(\w+)"', f.read())
            return m.group(1) if m else "auto"
    except OSError:
        return "auto"


def set_ldac_quality(mac, quality):
    """Writes a WirePlumber rule and restarts WirePlumber (audio drops for ~2 s)."""
    os.makedirs(WP_DROPIN_DIR, exist_ok=True)
    path = _dropin(mac)
    if quality == "auto":
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "w") as f:
            f.write(f"""# Generado por Buds Manager: calidad LDAC de los audífonos {mac}
monitor.bluez.rules = [
  {{
    matches = [ {{ device.name = "{card_name(mac)}" }} ]
    actions = {{ update-props = {{ bluez5.a2dp.ldac.quality = "{quality}" }} }}
  }}
]
""")
    _, before = profiles(mac)
    subprocess.run(["systemctl", "--user", "restart", "wireplumber"], timeout=15)
    if before is None or is_music(before):
        # the restart drops the A2DP link; bring the music back on the same codec
        time.sleep(2)
        ensure_music(mac, prefer=before, timeout=12)
