"""High-level read/write of earbud settings on top of protocol.Client."""
import struct
import time

from . import protocol as p

MODES = {"off": 0x00, "anc": 0x01, "transparency": 0x02}

# The 8 Pro app only offers light/deep (+ "smart" = CFG_ADAPTIVE_ANC); older models used 0x00-0x03.
ANC_STRENGTH = {"light": 0x01, "deep": 0x12}
ANC_STRENGTH_READ = {0x00: "light", 0x01: "light", 0x02: "deep", 0x03: "deep", 0x12: "deep", 0x13: "deep"}
TRANSPARENCY_STRENGTH = {"regular": 0x00, "voice": 0x01, "ambient": 0x02}
STRENGTH_TARGET = {"anc": 0x01, "transparency": 0x02}

EQ_PRESETS = {"standard": 0x00, "voice": 0x01, "bass": 0x05, "treble": 0x06,
              "volume": 0x07, "custom": 0x0A, "balanced": 0x15}

TAPS = {"double": 0x01, "triple": 0x02, "long": 0x03, "single": 0x04, "swipe": 0x05}
TAP_ACTIONS = {"none": 0x08, "play-pause": 0x01, "previous": 0x02, "next": 0x03,
               "volume-up": 0x04, "volume-down": 0x05}
LONG_ACTIONS = {"none": 0x08, "assistant": 0x00, "anc-cycle": 0x06}
SWIPE_ACTIONS = {"volume": 0x0B, "none": 0x08}
COMMUTE = {"off": 0x00, "airplane": 0x01, "transit": 0x02, "train": 0x03}
SPATIAL_ENGINES = {"xiaomi": 0x00, "dolby": 0x01}


def action_table(tap):
    return {"long": LONG_ACTIONS, "swipe": SWIPE_ACTIONS}.get(tap, TAP_ACTIONS)

ANC_CYCLES = {"anc-off": 0x03, "transparency-off": 0x05, "anc-transparency": 0x06, "all": 0x07}

FIND_TARGET = {"left": 0x01, "right": 0x02, "both": 0x03}

FIT_RESULT = {0x00: "none", 0x01: "good", 0x02: "poor", 0x03: "testing"}

DEFAULT_EQ_FREQS = [62, 125, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000]


def name_of(table, code):
    for k, v in table.items():
        if v == code:
            return k
    return f"0x{code:02x}"


def decode_battery(b):
    if b == 0xFF:
        return None
    return {"level": b & 0x7F, "charging": bool(b & 0x80)}


def decode_eq_level(b):
    return -(b & 0x7F) if b & 0x80 else b


def basic_battery(mac):
    """Battery via the channel-17 info service; works while another device has control."""
    b = p.read_basic(mac).get(0x03)
    if not b or len(b) < 3:
        return None
    return {"left": decode_battery(b[0]), "right": decode_battery(b[1]), "case": decode_battery(b[2])}


def decode_wear(b):
    def side(case_bit, ear_bit):
        return "ear" if b & ear_bit else "case" if b & case_bit else "out"
    return {"left": side(0x01, 0x04), "right": side(0x02, 0x08)}


def encode_eq_level(level):
    if not -6 <= level <= 6:
        raise ValueError("el nivel del ecualizador va de -6 a +6")
    return (0x80 | -level) if level < 0 else level


def _tlv(payload):
    i = 0
    while i + 1 < len(payload):
        n = payload[i]
        yield payload[i + 1], payload[i + 2:i + 1 + n]
        i += n + 1


def read_state(client):
    """Query everything the protocol knows about. Returns a JSON-serialisable dict."""
    # Like the Xiaomi app, ask for several configs per GET_CONFIG (16-bit ids); the earbuds drop
    # requests when too many are in flight.
    groups = [p.ALL_CONFIGS[i:i + 8] for i in range(0, len(p.ALL_CONFIGS), 8)]
    items = [(p.GET_DEVICE_INFO, b"\xff" * 4), (p.GET_DEVICE_RUN_INFO, b"\xff" * 4)]
    items += [(p.GET_CONFIG, b"".join(bytes([0x00, c]) for c in g)) for g in groups]
    info, run, *multi = client.request_many(items)
    found = {}
    for m in multi:
        pl = m.payload if m else b""
        i = 0
        while i < len(pl):
            n = pl[i]
            item = pl[i:i + 1 + n]
            if n >= 2 and len(item) == n + 1:
                found[item[2]] = item       # same layout as a single-config answer: len, 00, id, data
            i += n + 1
    configs = [found.get(c) for c in p.ALL_CONFIGS]

    st = {"raw": {}}
    if info:
        st["raw"]["info"] = info.payload.hex()
        for k, v in _tlv(info.payload):
            if k == 0x00:
                st["platform"] = v.decode(errors="replace")
            elif k == 0x01 and len(v) >= 4:
                st["firmware"] = ".".join(f"{x >> 4}.{x & 15}" for x in v[:2])
            elif k == 0x03:
                st["vid_pid"] = v.hex()
            elif k == 0x07 and len(v) >= 3:
                st["battery"] = {"left": decode_battery(v[0]), "right": decode_battery(v[1]),
                                 "case": decode_battery(v[2])}
    if run:
        st["raw"]["run_info"] = run.payload.hex()
        for k, v in _tlv(run.payload):
            if k == 0x09 and v:
                st["mode"] = name_of(MODES, v[0])
            elif k == 0x0A and v:
                st["ear_detection"] = v[0] == 0x00

    for cfg, m in zip(p.ALL_CONFIGS, configs):
        if not m or len(m) < 3:
            continue
        pl = m
        st["raw"][f"config_{cfg:02x}"] = pl.hex()
        d = pl[3:]
        if cfg == p.CFG_GESTURES:
            g = {}
            for i in range(0, len(d) - 2, 3):
                tap = name_of(TAPS, d[i])
                table = action_table(tap)
                g[tap] = {"left": name_of(table, d[i + 1]), "right": name_of(table, d[i + 2])}
            st["gestures"] = g
        elif cfg == p.CFG_LONG_GESTURES and len(d) >= 2:
            st["anc_cycle"] = {"left": name_of(ANC_CYCLES, d[0]), "right": name_of(ANC_CYCLES, d[1])}
        elif cfg == p.CFG_EFFECT_STRENGTH and len(d) >= 2:
            target = name_of(STRENGTH_TARGET, d[0])
            if target == "anc":
                value = ANC_STRENGTH_READ.get(d[1], f"0x{d[1]:02x}")
            else:
                value = name_of(TRANSPARENCY_STRENGTH, d[1])
            st["strength"] = {"target": target, "value": value}
        elif cfg == p.CFG_EQ_PRESET and d:
            st["eq_preset"] = name_of(EQ_PRESETS, d[0])
        elif cfg == p.CFG_EQ_CURVE and len(pl) > 9:
            bands = []
            for i in range(pl[9]):
                off = 10 + 3 * i
                if off + 2 >= len(pl):
                    break
                freq = struct.unpack(">H", pl[off:off + 2])[0]
                bands.append({"freq": freq, "level": decode_eq_level(pl[off + 2])})
            st["eq_curve"] = bands
        elif cfg == p.CFG_AUTO_ANSWER and d:
            st["auto_answer"] = d[0] == 0x01
        elif cfg == p.CFG_DOUBLE_CONNECTION and d:
            st["multipoint"] = d[0] == 0x01
        elif cfg == p.CFG_ADAPTIVE_ANC and d:
            st["adaptive_anc"] = d[0] == 0x01
        elif cfg == p.CFG_ADAPTIVE_SOUND and d:
            st["adaptive_sound"] = d[0] == 0x01
        elif cfg == p.CFG_CUSTOMIZED_ANC and d:
            st["customized_anc"] = d[0] == 0x01
        elif cfg == p.CFG_WEAR and d:
            st["wear"] = decode_wear(d[0])
        elif cfg == p.CFG_FIT_RESULT and len(d) >= 2:
            st["fit"] = {"left": FIT_RESULT.get(d[0], f"0x{d[0]:02x}"), "right": FIT_RESULT.get(d[1], f"0x{d[1]:02x}")}
        elif cfg == p.CFG_SPATIAL_STATE and d:
            st["spatial"] = {"on": bool(d[0] & 0x01), "head_tracking": bool(d[0] & 0x08)}
        elif cfg == p.CFG_COMMUTE and d:
            st["commute"] = name_of(COMMUTE, d[0])
        elif cfg == p.CFG_SPATIAL_ENGINE and d:
            st["spatial_engine"] = name_of(SPATIAL_ENGINES, d[0])
        elif cfg == p.CFG_SERIAL and d:
            st["serial"] = d.decode(errors="replace").strip("\x00")
    return st


def _check(m, what):
    if m is None:
        raise p.BudsError(f"sin respuesta al cambiar {what}")
    if m.status not in (0, None):
        raise p.BudsError(f"los auriculares rechazaron el cambio de {what} (estado {m.status})")
    return m


def _set_config(client, payload, what):
    return _check(client.request(p.SET_CONFIG, bytes(payload)), what)


def set_mode(client, mode):
    _check(client.request(p.ANC, bytes([0x02, 0x04, MODES[mode]])), "modo")


def set_strength(client, target, value):
    table = ANC_STRENGTH if target == "anc" else TRANSPARENCY_STRENGTH
    _set_config(client, [0x04, 0x00, p.CFG_EFFECT_STRENGTH, STRENGTH_TARGET[target], table[value]], "intensidad")


def set_bool(client, cfg, on, what):
    _set_config(client, [0x03, 0x00, cfg, 0x01 if on else 0x00], what)


def set_ear_detection(client, on):
    _check(client.request(p.ANC, bytes([0x02, 0x06, 0x00 if on else 0x01])), "detección de oído")


def set_eq_preset(client, preset):
    _set_config(client, [0x03, 0x00, p.CFG_EQ_PRESET, EQ_PRESETS[preset]], "preset de ecualizador")


def set_eq_curve(client, bands):
    """bands: list of {"freq": int, "level": int(-6..6)}."""
    body = bytes([0x00, p.CFG_EQ_CURVE, 0x05, 0x01, 0x01, len(bands)])
    for b in bands:
        body += struct.pack(">HB", b["freq"], encode_eq_level(b["level"]))
    _set_config(client, bytes([len(body)]) + body, "curva de ecualizador")


def set_gesture(client, tap, side, action):
    code = action_table(tap)[action]
    left = code if side in ("left", "both") else 0xFF
    right = code if side in ("right", "both") else 0xFF
    _set_config(client, [0x05, 0x00, p.CFG_GESTURES, TAPS[tap], left, right], "gesto")


def set_spatial(client, on, head_tracking=False):
    v = 0x02 | (0x01 if on else 0) | (0x08 if on and head_tracking else 0)
    _set_config(client, [0x03, 0x00, p.CFG_SPATIAL, v], "audio espacial")


def set_spatial_engine(client, engine):
    _set_config(client, [0x03, 0x00, p.CFG_SPATIAL_ENGINE, SPATIAL_ENGINES[engine]], "motor de audio espacial")


def set_commute(client, scene):
    _set_config(client, [0x03, 0x00, p.CFG_COMMUTE, COMMUTE[scene]], "viaje inmersivo")


def set_anc_cycle(client, side, cycle):
    code = ANC_CYCLES[cycle]
    left = code if side in ("left", "both") else 0xFF
    right = code if side in ("right", "both") else 0xFF
    _set_config(client, [0x04, 0x00, p.CFG_LONG_GESTURES, left, right], "ciclo de pulsación larga")


def find(client, target):
    if target == "stop":
        _set_config(client, [0x04, 0x00, p.CFG_FIND, 0x00, FIND_TARGET["both"]], "buscar")
    else:
        _set_config(client, [0x04, 0x00, p.CFG_FIND, 0x01, FIND_TARGET[target]], "buscar")


def fit_test(client, timeout=25):
    """Plays the seal-test tone and returns {"left": ..., "right": ...} (FIT_RESULT names)."""
    client.notifications.clear()
    _set_config(client, [0x03, 0x00, p.CFG_FIT_TEST, 0x01], "prueba de ajuste")
    deadline = time.time() + timeout
    while time.time() < deadline:
        client.wait_notifications(0.5)
        for m in client.notifications:
            pl = m.payload
            if m.opcode == p.NOTIFY_CONFIG and len(pl) >= 5 and pl[2] == p.CFG_FIT_RESULT and 0x03 not in pl[3:5]:
                return {"left": FIT_RESULT.get(pl[3], f"0x{pl[3]:02x}"),
                        "right": FIT_RESULT.get(pl[4], f"0x{pl[4]:02x}")}
        client.notifications.clear()
    raise p.BudsError("la prueba de ajuste no devolvió resultado")


BOOL_SETTINGS = [  # state key, config id, label
    ("adaptive_anc", p.CFG_ADAPTIVE_ANC, "ANC adaptativo"),
    ("customized_anc", p.CFG_CUSTOMIZED_ANC, "ANC personalizado"),
    ("adaptive_sound", p.CFG_ADAPTIVE_SOUND, "sonido adaptativo"),
    ("auto_answer", p.CFG_AUTO_ANSWER, "respuesta automática"),
    ("multipoint", p.CFG_DOUBLE_CONNECTION, "multipunto"),
]


def restore_steps(client, st):
    """List of (label, callable) that re-apply a saved state dict."""
    steps = []
    if st.get("commute") in COMMUTE:
        steps.append(("viaje inmersivo", lambda: set_commute(client, st["commute"])))
    if st.get("spatial_engine") in SPATIAL_ENGINES:
        steps.append(("motor espacial", lambda: set_spatial_engine(client, st["spatial_engine"])))
    if isinstance(st.get("spatial"), dict):
        sp = st["spatial"]
        steps.append(("audio espacial", lambda: set_spatial(client, sp.get("on"), sp.get("head_tracking"))))
    if st.get("mode") in MODES:
        steps.append(("modo", lambda: set_mode(client, st["mode"])))
    s = st.get("strength")
    if s and s.get("target") in STRENGTH_TARGET:
        table = ANC_STRENGTH if s["target"] == "anc" else TRANSPARENCY_STRENGTH
        if s.get("value") in table and st.get("mode") == s["target"]:
            steps.append(("intensidad", lambda: set_strength(client, s["target"], s["value"])))
    if st.get("eq_curve"):
        steps.append(("curva", lambda: set_eq_curve(client, st["eq_curve"])))
    if st.get("eq_preset") in EQ_PRESETS:
        steps.append(("preset", lambda: set_eq_preset(client, st["eq_preset"])))
    for tap, g in (st.get("gestures") or {}).items():
        table = action_table(tap)
        if tap in TAPS:
            for side in ("left", "right"):
                if g.get(side) in table:
                    steps.append((f"gesto {tap} {side}",
                                  lambda tap=tap, side=side, a=g[side]: set_gesture(client, tap, side, a)))
    c = st.get("anc_cycle") or {}
    for side in ("left", "right"):
        if c.get(side) in ANC_CYCLES:
            steps.append((f"ciclo {side}", lambda side=side: set_anc_cycle(client, side, c[side])))
    for key, cfg, what in BOOL_SETTINGS:
        if key in st:
            steps.append((what, lambda cfg=cfg, v=st[key], what=what: set_bool(client, cfg, v, what)))
    if "ear_detection" in st:
        steps.append(("detección de oído", lambda: set_ear_detection(client, st["ear_detection"])))
    return steps
