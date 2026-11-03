"""`buds` command line interface."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import device as d
from . import protocol as p
from . import remote

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "redmi-buds")
INITIAL_BACKUP = os.path.join(STATE_DIR, "backup-initial.json")

MODE_LABEL = {"off": "Apagado", "anc": "Cancelación de ruido", "transparency": "Transparencia"}
MODE_ICON = {"off": "󰋋", "anc": "󰟎", "transparency": "󰋎"}
NEXT_MODE = {"off": "anc", "anc": "transparency", "transparency": "off"}


# ---- device discovery ----

def _bluetoothctl(*args):
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def find_mac():
    mac = os.environ.get("REDMI_BUDS_MAC")
    if mac:
        return mac
    for line in _bluetoothctl("devices", "Connected").splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line.strip(), re.I)
        if m and re.search(r"redmi buds|xiaomi buds", m.group(2), re.I):
            return m.group(1)
    return None


# ---- output helpers ----

def fmt_batt(b):
    if not b:
        return "—"
    return f"{b['level']}%" + (" ⚡" if b["charging"] else "")


def onoff(v):
    return "sí" if v else "no" if v is not None else "?"


def print_status(st):
    b = st.get("battery", {})
    print(f"Batería     izq {fmt_batt(b.get('left'))}   der {fmt_batt(b.get('right'))}   estuche {fmt_batt(b.get('case'))}")
    print(f"Modo        {MODE_LABEL.get(st.get('mode'), st.get('mode', '?'))}")
    if "strength" in st:
        s = st["strength"]
        print(f"Intensidad  {s['value']} ({s['target']})")
    print(f"ANC adaptativo {onoff(st.get('adaptive_anc'))}   ANC personalizado {onoff(st.get('customized_anc'))}")
    print(f"Ecualizador {st.get('eq_preset', '?')}   sonido adaptativo {onoff(st.get('adaptive_sound'))}")
    if st.get("eq_curve"):
        curve = "  ".join(f"{_freq_label(x['freq'])}:{x['level']:+d}" for x in st["eq_curve"])
        print(f"Curva       {curve}")
    if st.get("gestures"):
        for tap, g in st["gestures"].items():
            print(f"Gesto {tap:<7} izq {g['left']:<12} der {g['right']}")
    if st.get("anc_cycle"):
        c = st["anc_cycle"]
        print(f"Ciclo pulsación larga  izq {c['left']}   der {c['right']}")
    print(f"Detección de oído {onoff(st.get('ear_detection'))}   respuesta automática {onoff(st.get('auto_answer'))}"
          f"   multipunto {onoff(st.get('multipoint'))}")
    sp = st.get("spatial") or {}
    print(f"Audio espacial {onoff(sp.get('on'))} (seguimiento de cabeza {onoff(sp.get('head_tracking'))}, "
          f"motor {st.get('spatial_engine', '?')})   viaje inmersivo {st.get('commute', '?')}")
    print(f"Firmware    {st.get('firmware', '?')}   número de serie {st.get('serial', '?')}")


def _freq_label(f):
    return f"{f // 1000}k" if f >= 1000 else str(f)


def _parse_freq(s):
    s = s.lower().removesuffix("hz")
    return int(float(s[:-1]) * 1000) if s.endswith("k") else int(s)


def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Redmi Buds", "-i", "audio-headphones",
                        "-h", "string:x-canonical-private-synchronous:redmi-buds", summary, body])


def refresh_waybar():
    subprocess.run(["pkill", "-RTMIN+8", "waybar"], stderr=subprocess.DEVNULL)


def save_backup(st, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)


# ---- commands ----

def ensure_initial_backup(client):
    if not os.path.exists(INITIAL_BACKUP):
        save_backup(d.read_state(client), INITIAL_BACKUP)
        print(f"(guardé tu configuración original en {INITIAL_BACKUP})", file=sys.stderr)


def cmd_status(client, args):
    st = d.read_state(client)
    if not args.raw:
        st.pop("raw", None)
    if args.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
    else:
        print_status(st)
        if args.raw:
            for k, v in st["raw"].items():
                print(f"  {k}: {v}")


def cmd_battery(client, args):
    b = d.read_state(client).get("battery", {})
    print(f"izq {fmt_batt(b.get('left'))}  der {fmt_batt(b.get('right'))}  estuche {fmt_batt(b.get('case'))}")


def cmd_mode(client, args):
    mode = args.mode
    if mode in (None, "next", "toggle", "flip"):
        current = d.read_state(client).get("mode", "off")
        if mode is None:
            print(MODE_LABEL.get(current, current))
            return
        if mode == "next":
            mode = NEXT_MODE.get(current, "anc")
        elif mode == "flip":
            mode = "transparency" if current == "anc" else "anc"
        else:
            mode = "off" if current == "anc" else "anc"
    ensure_initial_backup(client)
    d.set_mode(client, mode)
    print(MODE_LABEL[mode])
    if args.notify:
        notify(MODE_LABEL[mode])
    refresh_waybar()


def cmd_strength(client, args):
    ensure_initial_backup(client)
    d.set_strength(client, args.target, args.value)
    print(f"{args.target}: {args.value}")


def _bool_cmd(cfg, what):
    def run(client, args):
        ensure_initial_backup(client)
        if cfg == "ear":
            d.set_ear_detection(client, args.state == "on")
        else:
            d.set_bool(client, cfg, args.state == "on", what)
        print(f"{what}: {args.state}")
    return run


def cmd_eq(client, args):
    if args.preset is None:
        st = d.read_state(client)
        print(st.get("eq_preset", "?"))
        for x in st.get("eq_curve", []):
            print(f"  {_freq_label(x['freq']):>4} Hz  {x['level']:+d}")
        return
    ensure_initial_backup(client)
    d.set_eq_preset(client, args.preset)
    print(f"ecualizador: {args.preset}")


def _current_curve(client):
    curve = d.read_state(client).get("eq_curve") or [{"freq": f, "level": 0} for f in d.DEFAULT_EQ_FREQS]
    return curve


def cmd_eq_band(client, args):
    ensure_initial_backup(client)
    curve = _current_curve(client)
    freq = _parse_freq(args.freq)
    band = min(curve, key=lambda x: abs(x["freq"] - freq))
    band["level"] = args.level
    d.set_eq_preset(client, "custom")
    d.set_eq_curve(client, curve)
    print("  ".join(f"{_freq_label(x['freq'])}:{x['level']:+d}" for x in curve))


def cmd_eq_curve(client, args):
    ensure_initial_backup(client)
    curve = _current_curve(client)
    if len(args.levels) != len(curve):
        raise SystemExit(f"hacen falta {len(curve)} niveles (uno por banda)")
    for band, level in zip(curve, args.levels):
        band["level"] = level
    d.set_eq_preset(client, "custom")
    d.set_eq_curve(client, curve)
    print("  ".join(f"{_freq_label(x['freq'])}:{x['level']:+d}" for x in curve))


def cmd_gesture(client, args):
    table = d.action_table(args.tap)
    if args.action not in table:
        raise SystemExit(f"acciones válidas para {args.tap}: {', '.join(table)}")
    ensure_initial_backup(client)
    d.set_gesture(client, args.tap, args.side, args.action)
    print(f"{args.tap} ({args.side}): {args.action}")


def cmd_cycle(client, args):
    ensure_initial_backup(client)
    d.set_anc_cycle(client, args.side, args.cycle)
    print(f"pulsación larga ({args.side}): {args.cycle}")


def cmd_spatial(client, args):
    ensure_initial_backup(client)
    if args.state in d.SPATIAL_ENGINES:
        d.set_spatial_engine(client, args.state)
    else:
        d.set_spatial(client, args.state != "off", args.state == "head")
    print(f"audio espacial: {args.state}")


def cmd_commute(client, args):
    ensure_initial_backup(client)
    d.set_commute(client, args.scene)
    print(f"viaje inmersivo: {args.scene}")
    refresh_waybar()


def cmd_find(client, args):
    d.find(client, args.target)
    if args.target != "stop":
        print("sonando… (detén con: buds find stop)")


def cmd_backup(client, args):
    st = d.read_state(client)
    path = args.file or os.path.join(STATE_DIR, time.strftime("backup-%Y%m%d-%H%M%S.json"))
    save_backup(st, path)
    print(path)


def cmd_restore(client, args):
    path = args.file or INITIAL_BACKUP
    with open(path) as f:
        st = json.load(f)
    steps = d.restore_steps(client, st)
    for what, fn in steps:
        try:
            fn()
            print(f"✓ {what}")
        except p.BudsError as e:
            print(f"✗ {what}: {e}")
    refresh_waybar()


WAYBAR_ICON_DIR = os.path.join(STATE_DIR, "waybar")
PART_LABEL = {"left": "Izquierdo", "right": "Derecho", "case": "Estuche"}


def make_waybar_icons(px=48):
    """Renders left / right / case icons (in the earbuds' color) for Waybar's image modules."""
    from .art import bar_icon
    os.makedirs(WAYBAR_ICON_DIR, exist_ok=True)
    ear = bar_icon("earbud", px)
    ear.savev(os.path.join(WAYBAR_ICON_DIR, "left.png"), "png", [], [])
    ear.flip(True).savev(os.path.join(WAYBAR_ICON_DIR, "right.png"), "png", [], [])
    bar_icon("case", px).savev(os.path.join(WAYBAR_ICON_DIR, "case.png"), "png", [], [])
    refresh_waybar()


def _battery_snapshot():
    """(battery dict or None, mode or None, status text) without ever raising."""
    try:
        ds = remote.call({"op": "state"}, timeout=3)
    except (OSError, ValueError, p.BudsError):
        ds = None
    if ds is not None:
        st = ds.get("state")
        if st:
            return st.get("battery") or {}, st.get("mode"), MODE_LABEL.get(st.get("mode"), "")
        if ds.get("reason") == "busy":
            return ds.get("battery") or {}, None, ds.get("reason_text") or ""
        return None, None, ds.get("reason_text") or "Desconectados"
    mac = find_mac()
    if not mac:
        return None, None, "Desconectados"
    try:
        with p.Client(mac) as client:
            st = d.read_state(client)
        return st.get("battery") or {}, st.get("mode"), MODE_LABEL.get(st.get("mode"), "")
    except Exception as e:  # noqa: BLE001
        return None, None, str(e)


def cmd_waybar_part(part, icon=False):
    b, mode, status = _battery_snapshot()
    x = (b or {}).get(part)
    if icon:                      # image module: path, or nothing to hide the icon
        path = os.path.join(WAYBAR_ICON_DIR, f"{part}.png")
        if x and not os.path.exists(path):
            make_waybar_icons()
        print(path if x else "")
        return
    if not x:
        print(json.dumps({"text": "", "class": "empty"}))
        return
    classes = [part, mode or "busy"]
    if x["charging"]:
        classes.append("charging")
    elif x["level"] <= 10:
        classes.append("critical")
    elif x["level"] <= 20:
        classes.append("low")
    tip = (f"REDMI Buds — {status}\n" +
           "   ".join(f"{PART_LABEL[k]} {fmt_batt(b.get(k))}" for k in ("left", "right", "case")) +
           "\nClic: cambiar modo · Clic derecho: ANC/apagado · Clic central: Buds Manager")
    print(json.dumps({"text": f"{x['level']}%", "tooltip": tip, "class": classes,
                      "percentage": x["level"]}, ensure_ascii=False))


def cmd_waybar(args):
    """Never fails: prints a JSON line for Waybar's custom module."""
    if getattr(args, "part", None):
        return cmd_waybar_part(args.part, icon=args.icon)
    ds = None
    try:
        ds = remote.call({"op": "state"}, timeout=3)
    except (OSError, ValueError, p.BudsError):
        pass
    if ds is not None and ds.get("state") is None:
        if ds.get("reason") == "absent":
            print(json.dumps({"text": "", "class": "disconnected", "tooltip": "Redmi Buds desconectados"}))
        else:
            b = ds.get("battery") or {}
            levels = [x["level"] for x in (b.get("left"), b.get("right")) if x]
            text = f"󰋋 {min(levels)}%" if levels else "󰋋"
            tip = f"Redmi Buds: {ds.get('reason_text')}"
            if b:
                tip += (f"\nIzquierdo {fmt_batt(b.get('left'))}   Derecho {fmt_batt(b.get('right'))}"
                        f"   Estuche {fmt_batt(b.get('case'))}")
            print(json.dumps({"text": text, "class": "busy", "tooltip": tip}, ensure_ascii=False))
        return
    mac = None if ds else find_mac()
    if not ds and not mac:
        print(json.dumps({"text": "", "class": "disconnected", "tooltip": "Redmi Buds desconectados"}))
        return
    try:
        if ds:
            st = ds["state"]
        else:
            with p.Client(mac) as client:
                st = d.read_state(client)
    except Exception as e:
        busy = "Connection refused" in str(e)
        tip = ("Redmi Buds: el celular está usando el canal de control" if busy else f"Redmi Buds: {e}")
        print(json.dumps({"text": "󰋋", "class": "busy" if busy else "error", "tooltip": tip}, ensure_ascii=False))
        return
    b = st.get("battery", {})
    levels = [x["level"] for x in (b.get("left"), b.get("right")) if x]
    low = min(levels) if levels else None
    mode = st.get("mode", "off")
    text = f"{MODE_ICON.get(mode, '󰋋')} {low}%" if low is not None else MODE_ICON.get(mode, "󰋋")
    tooltip = (f"Redmi Buds 8 Pro — {MODE_LABEL.get(mode, mode)}\n"
               f"Izquierdo {fmt_batt(b.get('left'))}   Derecho {fmt_batt(b.get('right'))}   Estuche {fmt_batt(b.get('case'))}\n"
               f"Ecualizador: {st.get('eq_preset', '?')}\n"
               "Clic: cambiar modo · Clic derecho: ANC/apagado")
    classes = [mode] + (["low"] if low is not None and low <= 20 else [])
    print(json.dumps({"text": text, "tooltip": tooltip, "class": classes, "alt": mode,
                      "percentage": low or 0}, ensure_ascii=False))


SERVICE = "buds-manager-daemon.service"


def hold_enabled():
    r = subprocess.run(["systemctl", "--user", "is-enabled", SERVICE], capture_output=True, text=True)
    return r.stdout.strip() == "enabled"


def set_hold(on):
    subprocess.run(["systemctl", "--user", "enable" if on else "disable", "--now", SERVICE],
                   capture_output=True, text=True, check=True)


def cmd_hold(args):
    if args.state != "status":
        set_hold(args.state == "on")
    st = remote.daemon_status()
    for _ in range(15):
        if st or args.state == "off":
            break
        time.sleep(0.2)
        st = remote.daemon_status()
    if not st:
        print("control en esta PC: desactivado")
    elif st["holding"]:
        print(f"control en esta PC: activo (canal tomado, {st['mac']})")
    else:
        print(f"control en esta PC: esperando — {st['reason_text']}")


AUTO_KEYS = {"salida": "auto_output", "pausa": "auto_pause", "reanudar": "auto_resume", "bateria": "battery_alerts",
             "estuche": "case_reminder", "ruido": "call_denoise"}
AUTO_LABEL = {"auto_output": "pasar el audio a las bocinas al quitártelos (y volver)",
              "auto_pause": "pausar al quitarte un audífono",
              "auto_resume": "reanudar al ponértelo",
              "battery_alerts": "avisos de batería baja / carga completa",
              "case_reminder": "avisar si quedan fuera del estuche",
              "call_denoise": "quitar ruido de fondo del micrófono en llamadas (RNNoise)"}


def cmd_auto(args):
    from . import automations as a
    cfg = a.load_config()
    if args.what:
        if args.state is None:
            sys.exit("indica on u off: buds auto pausa off")
        cfg[AUTO_KEYS[args.what]] = args.state == "on"
        a.save_config(cfg)
    for key, label in AUTO_LABEL.items():
        print(f"{'sí' if cfg[key] else 'no':3} {label}")
    from .calls import MODES
    print(f"micrófono de los audífonos: {MODES.get(cfg['call_mic'], cfg['call_mic']).lower()}  (buds mic)")


def cmd_exposicion(args):
    from . import exposure as ex
    s = ex.summary()
    print(f"Esta semana: {s['week'] * 100:.0f} % de la dosis segura de la OMS (estimado)")
    print(f"Hoy: {int(s['today_min']) // 60} h {int(s['today_min']) % 60:02d} min con los audífonos puestos")
    for day, dose in s["days"]:
        print(f"  {day}  {'█' * min(40, round(dose * 40))} {dose * 100:.0f} %")
    print("Estimación con el volumen del sistema; calibra ref_spl en config.json (dB al 100 %).")


def cmd_dormir(args):
    try:
        if args.minutes in ("off", "cancelar", "0"):
            st = remote.call({"op": "sleep_timer", "cancel": True})
        elif args.minutes:
            st = remote.call({"op": "sleep_timer", "minutes": float(args.minutes)})
        else:
            st = remote.call({"op": "sleep_timer"})
    except (OSError, ValueError, p.BudsError):
        sys.exit("El temporizador necesita el servicio: buds hold on")
    r = st["remaining"]
    print(f"temporizador: faltan {r // 60} min {r % 60:02d} s" if r else "temporizador: apagado")


def cmd_mic(args):
    from . import automations as a
    from .calls import MODES
    cfg = a.load_config()
    if args.mode:
        cfg["call_mic"] = args.mode
        a.save_config(cfg)
    print("micrófono de los audífonos:", MODES[cfg["call_mic"]].lower())
    if not remote.daemon_status():
        print("Aviso: el servicio está apagado (buds hold on); sin él manda WirePlumber.")


def cmd_atajos(args):
    from . import gestures as g
    if args.key:
        if args.action is None:
            sys.exit("indica la acción: buds atajos next mic-mute   (o \"cmd:<comando>\")")
        try:
            g.set_action(args.key, args.action)
        except ValueError as e:
            sys.exit(f"error: {e}")
    acts = g.get_actions()
    print("Tecla que envían los audífonos → qué hace esta PC")
    for key, label in g.KEY_LABELS:
        print(f"  {key:12} {label:22} → {g.describe(acts[key])}")
    st = remote.daemon_status()
    gs = (st or {}).get("gestures")
    if not st:
        print("\nAviso: el servicio está apagado (buds hold on); los atajos solo funcionan con él.")
    elif gs and gs.get("problem"):
        print(f"\nAhora mismo no escucha: {gs['problem']}")
    elif gs and gs.get("device"):
        print(f"\nEscuchando: {gs['device']}")
    if not args.key:
        print("\nAcciones: " + ", ".join(g.BUILTIN) + ", cmd:<comando>")


def build_parser():
    ap = argparse.ArgumentParser(prog="buds", description="Controla tus Redmi Buds desde Linux")
    ap.add_argument("--mac", help="MAC de los auriculares (por defecto: autodetección o $REDMI_BUDS_MAC)")
    ap.add_argument("--debug", action="store_true", help="muestra los mensajes en crudo")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="todo el estado")
    s.add_argument("--json", action="store_true")
    s.add_argument("--raw", action="store_true", help="incluye los bytes sin decodificar")
    s.set_defaults(fn=cmd_status)

    sub.add_parser("battery", help="batería").set_defaults(fn=cmd_battery)

    s = sub.add_parser("mode", aliases=["anc"], help="modo: off | anc | transparency | next | toggle (anc↔off) | flip (anc↔transparencia)")
    s.add_argument("mode", nargs="?", choices=[*d.MODES, "next", "toggle", "flip"])
    s.add_argument("--notify", action="store_true", help="muestra una notificación")
    s.set_defaults(fn=cmd_mode)

    s = sub.add_parser("strength", help="intensidad del ANC o de la transparencia")
    s.add_argument("target", choices=list(d.STRENGTH_TARGET))
    s.add_argument("value", choices=sorted({*d.ANC_STRENGTH, *d.TRANSPARENCY_STRENGTH}))
    s.set_defaults(fn=cmd_strength)

    for name, cfg, what in [("adaptive-anc", p.CFG_ADAPTIVE_ANC, "ANC adaptativo"),
                            ("personalized-anc", p.CFG_CUSTOMIZED_ANC, "ANC personalizado"),
                            ("adaptive-sound", p.CFG_ADAPTIVE_SOUND, "sonido adaptativo"),
                            ("auto-answer", p.CFG_AUTO_ANSWER, "respuesta automática"),
                            ("multipoint", p.CFG_DOUBLE_CONNECTION, "multipunto"),
                            ("ear-detection", "ear", "detección de oído")]:
        s = sub.add_parser(name, help=what)
        s.add_argument("state", choices=["on", "off"])
        s.set_defaults(fn=_bool_cmd(cfg, what))

    s = sub.add_parser("eq", help=f"preset de ecualizador: {', '.join(d.EQ_PRESETS)}")
    s.add_argument("preset", nargs="?", choices=list(d.EQ_PRESETS))
    s.set_defaults(fn=cmd_eq)

    s = sub.add_parser("eq-band", help="ajusta una banda: buds eq-band 62 +4")
    s.add_argument("freq")
    s.add_argument("level", type=int)
    s.set_defaults(fn=cmd_eq_band)

    s = sub.add_parser("eq-curve", help="las 10 bandas de una vez: buds eq-curve 4 3 2 0 0 0 1 2 2 1")
    s.add_argument("levels", type=int, nargs="+")
    s.set_defaults(fn=cmd_eq_curve)

    s = sub.add_parser("gesture", help="asigna acción a un gesto")
    s.add_argument("tap", choices=list(d.TAPS))
    s.add_argument("side", choices=["left", "right", "both"])
    s.add_argument("action", help=f"toques: {', '.join(d.TAP_ACTIONS)}; long: {', '.join(d.LONG_ACTIONS)}; "
                                  f"swipe: {', '.join(d.SWIPE_ACTIONS)}")
    s.set_defaults(fn=cmd_gesture)

    s = sub.add_parser("cycle", help="modos que recorre la pulsación larga")
    s.add_argument("side", choices=["left", "right", "both"])
    s.add_argument("cycle", choices=list(d.ANC_CYCLES))
    s.set_defaults(fn=cmd_cycle)

    s = sub.add_parser("spatial", help="audio espacial: on | head (con seguimiento de cabeza) | off | dolby | xiaomi")
    s.add_argument("state", choices=["on", "head", "off", *d.SPATIAL_ENGINES])
    s.set_defaults(fn=cmd_spatial)

    s = sub.add_parser("commute", help="viaje inmersivo: off | airplane | transit | train")
    s.add_argument("scene", choices=list(d.COMMUTE))
    s.set_defaults(fn=cmd_commute)

    s = sub.add_parser("find", help="hace sonar los auriculares")
    s.add_argument("target", nargs="?", default="both", choices=[*d.FIND_TARGET, "stop"])
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("backup", help="guarda la configuración actual en JSON")
    s.add_argument("file", nargs="?")
    s.set_defaults(fn=cmd_backup)

    s = sub.add_parser("restore", help="restaura una copia (por defecto, la original)")
    s.add_argument("file", nargs="?")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("waybar", help="salida JSON para Waybar (--part left|right|case: un porcentaje; "
                                      "--icon: ruta del ícono para un módulo image)")
    s.add_argument("--part", choices=["left", "right", "case"])
    s.add_argument("--icon", action="store_true")
    s.set_defaults(fn=None)
    sub.add_parser("waybar-icons", help="regenera los íconos de Waybar (al cambiar el color)").set_defaults(fn=None)
    sub.add_parser("daemon", help="servicio que mantiene el control en esta PC (lo usa systemd)").set_defaults(fn=None)
    s = sub.add_parser("hold", help="mantener el control de los audífonos en esta PC: on | off | status")
    s.add_argument("state", nargs="?", default="status", choices=["on", "off", "status"])
    s.set_defaults(fn=None)
    s = sub.add_parser("auto", help="automatizaciones: buds auto [salida|pausa|reanudar|bateria|estuche|ruido] [on|off]")
    s.add_argument("what", nargs="?", choices=list(AUTO_KEYS))
    s.add_argument("state", nargs="?", choices=["on", "off"])
    s.set_defaults(fn=None)
    sub.add_parser("exposicion", aliases=["exposure"], help="dosis de sonido de la semana (estimada)"
                   ).set_defaults(fn=None)
    s = sub.add_parser("dormir", aliases=["sleep"], help="temporizador para dormir: buds dormir 30 | off")
    s.add_argument("minutes", nargs="?")
    s.set_defaults(fn=None)
    s = sub.add_parser("mic", help="cuándo usar el micrófono de los audífonos: calls (solo llamadas) | any | never")
    s.add_argument("mode", nargs="?", choices=["calls", "any", "never"])
    s.set_defaults(fn=None)
    s = sub.add_parser("atajos", aliases=["shortcuts"],
                       help="qué hace la PC con cada gesto: buds atajos [tecla] [acción | cmd:<comando>]")
    s.add_argument("key", nargs="?", metavar="tecla", choices=["play-pause", "next", "previous", "volume-up", "volume-down"])
    s.add_argument("action", nargs="?", metavar="acción")
    s.set_defaults(fn=None)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        args.cmd, args.fn, args.json, args.raw = "status", cmd_status, False, False
    if args.cmd == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
    if args.cmd == "hold":
        return cmd_hold(args)
    if args.cmd == "auto":
        return cmd_auto(args)
    if args.cmd in ("exposicion", "exposure"):
        return cmd_exposicion(args)
    if args.cmd in ("dormir", "sleep"):
        return cmd_dormir(args)
    if args.cmd == "mic":
        return cmd_mic(args)
    if args.cmd in ("atajos", "shortcuts"):
        return cmd_atajos(args)
    if args.cmd == "waybar-icons":
        return make_waybar_icons()
    if args.cmd == "waybar":
        if args.mac:
            os.environ["REDMI_BUDS_MAC"] = args.mac
        return cmd_waybar(args)

    mac = args.mac or find_mac()
    if not mac:
        sys.exit("No encuentro unos Redmi Buds conectados (usa --mac o REDMI_BUDS_MAC).")
    try:
        with remote.session(mac, debug=args.debug) as client:
            args.fn(client, args)
    except p.BudsError as e:
        if getattr(args, "notify", False):
            notify("Redmi Buds", str(e))
        sys.exit(f"error: {e}")
