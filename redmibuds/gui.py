"""Buds Manager: GTK4 / libadwaita front-end for the Redmi Buds tool."""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import device as d  # noqa: E402
from . import protocol as p  # noqa: E402
from .bluetooth import Bluez  # noqa: E402
from . import audio  # noqa: E402
from . import remote  # noqa: E402
from . import gestures as gest  # noqa: E402
from .cli import hold_enabled, set_hold  # noqa: E402
from .cli import INITIAL_BACKUP, STATE_DIR, find_mac, refresh_waybar, save_backup, _freq_label  # noqa: E402

APP_ID = "io.github.oscar.BudsManager"
POLL_ACTIVE = 8      # seconds between refreshes while the window has focus
POLL_IDLE = 45

MODE_INFO = [("off", "Apagado", "audio-headphones-symbolic"),
             ("anc", "Cancelación", "microphone-sensitivity-muted-symbolic"),
             ("transparency", "Transparencia", "microphone-sensitivity-high-symbolic")]
ANC_LABELS = [("light", "Ligera"), ("deep", "Profunda")]
TRANSP_LABELS = [("regular", "Normal"), ("ambient", "Ambiente"), ("voice", "Realzar voces")]
COMMUTE_LABELS = [("off", "Apagado"), ("airplane", "Avión"), ("transit", "Transporte público"), ("train", "Tren")]
SWIPE_LABELS = [("volume", "Ajustar volumen"), ("none", "Nada")]
EQ_LABELS = [("balanced", "Equilibrado"), ("standard", "Estándar"), ("voice", "Voz"), ("bass", "Más graves"),
             ("treble", "Más agudos"), ("volume", "Más volumen"), ("custom", "Personalizado")]
TAP_LABELS = [("single", "Un toque"), ("double", "Doble toque"), ("triple", "Triple toque")]
TAP_ACTION_LABELS = [("none", "Nada"), ("play-pause", "Reproducir / pausar"), ("previous", "Pista anterior"),
                     ("next", "Pista siguiente"), ("volume-up", "Subir volumen"), ("volume-down", "Bajar volumen")]
LONG_ACTION_LABELS = [("anc-cycle", "Cambiar modo de ruido"), ("assistant", "Asistente de voz"), ("none", "Nada")]
CYCLE_LABELS = [("anc-transparency", "Cancelación ↔ Transparencia"), ("anc-off", "Cancelación ↔ Apagado"),
                ("transparency-off", "Transparencia ↔ Apagado"), ("all", "Los tres modos")]
EQ_PRESETS_CURVES = {  # quick shapes for the custom curve
    "Plana": [0] * 10,
    "Graves potentes": [5, 4, 3, 1, 0, 0, 0, 0, 0, 0],
    "Voces claras": [-2, -1, 0, 1, 3, 3, 2, 1, 0, -1],
    "En V": [4, 3, 1, 0, -1, -1, 0, 2, 3, 3],
    "Brillante": [0, 0, 0, 0, 0, 1, 2, 3, 4, 4],
}

CSS = """
.battery-card { padding: 18px 12px; }
.battery-level { font-size: 26px; font-weight: 800; font-feature-settings: "tnum"; }
.battery-name { font-size: 13px; opacity: .7; }
.eq-band-label { font-size: 11px; opacity: .7; font-feature-settings: "tnum"; }
.hero-title { font-size: 20px; font-weight: 800; }
.mode-toggles { padding: 4px; }
.mode-toggles button { min-height: 52px; }
.wear-label { font-size: 11px; opacity: .55; }
row.bt-connected .title { font-weight: 700; }
/* narrow layout (Adw.Breakpoint below 520sp) */
window.narrow .battery-card { padding: 12px 4px; }
window.narrow .battery-level { font-size: 21px; }
window.narrow .mode-toggles button { min-height: 44px; }
"""


def _string_list(pairs):
    return Gtk.StringList.new([label for _, label in pairs])


class Worker(threading.Thread):
    """Serialises all Bluetooth traffic. Each batch of jobs runs in one short session,
    then the full state is read back and handed to the UI."""

    def __init__(self, on_state, on_error, on_busy):
        super().__init__(daemon=True)
        self.daemon_status = None
        self.q = queue.Queue()
        self.on_state, self.on_error, self.on_busy = on_state, on_error, on_busy

    def submit(self, label, fn=None):
        self.q.put((label, fn))

    def run(self):
        while True:
            jobs = [self.q.get()]
            time.sleep(0.05)
            while True:
                try:
                    jobs.append(self.q.get_nowait())
                except queue.Empty:
                    break
            GLib.idle_add(self.on_busy, True)
            ds = remote.daemon_status()
            self.daemon_status = ds
            if ds and not ds.get("holding"):
                batt = None
                if ds.get("reason") == "busy":
                    try:
                        batt = remote.call({"op": "state"}, timeout=3).get("battery")
                    except (OSError, ValueError, p.BudsError):
                        pass
                GLib.idle_add(self.on_state, None, find_mac(), "absent" if ds.get("reason") == "absent" else "busy",
                              batt)
                GLib.idle_add(self.on_busy, False)
                continue
            mac = ds["mac"] if ds else find_mac()
            if not mac:
                GLib.idle_add(self.on_state, None, None)
                GLib.idle_add(self.on_busy, False)
                continue
            try:
                with remote.session(mac) as client:
                    if any(fn for _, fn in jobs) and not os.path.exists(INITIAL_BACKUP):
                        save_backup(d.read_state(client), INITIAL_BACKUP)
                    for label, fn in jobs:
                        if fn is None:
                            continue
                        try:
                            fn(client)
                        except p.BudsError as e:
                            GLib.idle_add(self.on_error, f"{label}: {e}")
                    st = d.read_state(client)
                if any(fn for _, fn in jobs) and not ds:
                    refresh_waybar()
                GLib.idle_add(self.on_state, st, mac)
            except Exception as e:  # noqa: BLE001 - surface every failure to the user
                if not isinstance(e, p.BudsError):
                    traceback.print_exc()
                if "Connection refused" in str(e) or "Errno 111" in str(e):
                    try:
                        batt = d.basic_battery(mac)
                    except p.BudsError:
                        batt = None
                    GLib.idle_add(self.on_state, None, mac, "busy", batt)
                else:
                    GLib.idle_add(self.on_error, str(e))
                    GLib.idle_add(self.on_state, None, mac)
            GLib.idle_add(self.on_busy, False)


def watch_daemon(on_change):
    """Calls on_change() (on the main loop) whenever the daemon reports new state."""
    def loop():
        while True:
            try:
                remote.call({"op": "wait_change", "wait": 25}, timeout=40)
                GLib.idle_add(on_change)
            except (OSError, ValueError, p.BudsError):
                time.sleep(5)
    threading.Thread(target=loop, daemon=True).start()


class BatteryCard(Gtk.Box):
    def __init__(self, name, icon):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.add_css_class("card")
        self.add_css_class("battery-card")
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(28)
        self.append(img)
        self.level = Gtk.Label(label="—")
        self.level.add_css_class("battery-level")
        self.append(self.level)
        self.bar = Gtk.LevelBar(min_value=0, max_value=100)
        self.bar.add_offset_value("low", 20)
        self.bar.add_offset_value("high", 50)
        self.bar.add_offset_value("full", 100)
        self.append(self.bar)
        lbl = Gtk.Label(label=name)
        lbl.add_css_class("battery-name")
        self.append(lbl)
        self.wear = Gtk.Label(label="", visible=False)
        self.wear.add_css_class("wear-label")
        self.append(self.wear)

    def set_wear(self, w):
        self.wear.set_visible(bool(w))
        self.wear.set_label(WEAR_LABEL.get(w, "") if w else "")

    def update(self, b):
        if not b:
            self.level.set_label("—")
            self.bar.set_value(0)
            self.set_opacity(0.5)
            return
        self.set_opacity(1)
        self.level.set_label(f"{b['level']}%" + (" ⚡" if b["charging"] else ""))
        self.bar.set_value(b["level"])


class DiscHeader(Gtk.Box):
    """Status header (icon, title, wrapped description, button) that grows with its text."""

    def __init__(self, button):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12)
        self.icon = Gtk.Image(pixel_size=96, icon_name="bluetooth-disconnected-symbolic")
        self.icon.add_css_class("dim-label")
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, label="Audífonos no conectados")
        self.title.add_css_class("title-1")
        self.desc = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                              label="Saca los Redmi Buds del estuche o pulsa Conectar.")
        self.batt = Gtk.Label(visible=False)
        self.batt.add_css_class("title-3")
        for w in (self.icon, self.title, self.batt, self.desc, button):
            self.append(w)

    def set_battery(self, text):
        self.batt.set_label(text or "")
        self.batt.set_visible(bool(text))

    def set_icon_name(self, name):
        self.icon.set_from_icon_name(name)

    def set_title(self, text):
        self.title.set_label(text)

    def set_description(self, text):
        self.desc.set_label(text)


class BtPanel(Gtk.Box):
    """Paired + nearby Bluetooth devices with connect / disconnect / pair / forget."""

    def __init__(self, win, extra_group=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.win = win
        self.busy = set()
        self._rows = []
        if extra_group:
            self.append(extra_group)

        self.mine = Adw.PreferencesGroup(title="Mis dispositivos")
        self.append(self.mine)

        self.nearby = Adw.PreferencesGroup(title="Dispositivos cercanos")
        suffix = Gtk.Box(spacing=6)
        self.scan_spinner = Adw.Spinner(visible=False)
        suffix.append(self.scan_spinner)
        self.scan_btn = Gtk.Button(label="Buscar", valign=Gtk.Align.CENTER)
        self.scan_btn.add_css_class("suggested-action")
        self.scan_btn.connect("clicked", lambda *_: win.toggle_scan())
        suffix.append(self.scan_btn)
        self.nearby.set_header_suffix(suffix)
        self.all_row = Adw.SwitchRow(title="Mostrar todo tipo de dispositivos",
                                     subtitle="Por defecto solo se muestran audífonos y bocinas")
        self.all_row.connect("notify::active", lambda *_: self.update())
        self.append(self.nearby)
        self.hint = Gtk.Label(wrap=True, xalign=0, margin_start=4,
                              label="Para emparejar unos audífonos nuevos, ponlos en modo emparejamiento "
                                    "(en los Redmi Buds: abre el estuche y mantén pulsado su botón 3 s) y pulsa Buscar.")
        self.hint.add_css_class("dim-label")
        self.append(self.hint)

    def update(self):
        bt = self.win.bt
        for group, row in self._rows:
            group.remove(row)
        self._rows = []
        if bt is None or bt.adapter is None:
            row = Adw.ActionRow(title="Bluetooth no disponible", subtitle="¿Está encendido el servicio bluetooth?")
            self.mine.add(row)
            self._rows.append((self.mine, row))
            return
        self.scan_btn.set_label("Detener" if bt.discovering else "Buscar")
        self.scan_btn.set_css_classes(["destructive-action" if bt.discovering else "suggested-action"])
        self.scan_spinner.set_visible(bt.discovering)

        devices = bt.list(audio_only=not self.all_row.get_active())
        paired = [x for x in devices if x[1].get("Paired")]
        new = [x for x in devices if not x[1].get("Paired")]
        for path, dev in paired:
            self._add(self.mine, self._row(path, dev))
        if not paired:
            self._add(self.mine, Adw.ActionRow(title="Ningún dispositivo emparejado"))
        self._add(self.nearby, self.all_row)
        for path, dev in new:
            self._add(self.nearby, self._row(path, dev))
        if not new:
            self._add(self.nearby, Adw.ActionRow(
                title="Buscando…" if bt.discovering else "Pulsa «Buscar» para encontrar dispositivos"))

    def _add(self, group, row):
        group.add(row)
        self._rows.append((group, row))

    def _row(self, path, dev):
        name = dev.get("Alias") or dev.get("Name") or dev.get("Address")
        connected, paired = dev.get("Connected"), dev.get("Paired")
        if connected:
            sub = "Conectado"
        elif paired:
            sub = "Emparejado · desconectado"
        else:
            rssi = dev.get("RSSI")
            sub = dev.get("Address", "") + (f" · señal {_signal(rssi)}" if rssi is not None else "")
        if "Battery Percentage" in dev:
            sub += f" · {dev['Battery Percentage']}%"
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=sub)
        icon = Gtk.Image.new_from_icon_name((dev.get("Icon") or "bluetooth") + "-symbolic")
        row.add_prefix(icon)
        if connected:
            row.add_css_class("bt-connected")

        if path in self.busy:
            row.add_suffix(Adw.Spinner())
            return row
        if paired:
            b = Gtk.Button(label="Desconectar" if connected else "Conectar", valign=Gtk.Align.CENTER)
            if not connected:
                b.add_css_class("suggested-action")
            b.connect("clicked", lambda *_: self.win.bt_action("disconnect" if connected else "connect", path, name))
            row.add_suffix(b)
            forget = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Olvidar (desemparejar)")
            forget.add_css_class("flat")
            forget.connect("clicked", lambda *_: self.win.confirm_forget(path, name))
            row.add_suffix(forget)
        else:
            b = Gtk.Button(label="Emparejar", valign=Gtk.Align.CENTER)
            b.add_css_class("suggested-action")
            b.connect("clicked", lambda *_: self.win.bt_action("pair", path, name))
            row.add_suffix(b)
        return row


def _signal(rssi):
    return "excelente" if rssi > -55 else "buena" if rssi > -70 else "regular" if rssi > -85 else "débil"


WEAR_LABEL = {"ear": "En el oído", "case": "En el estuche", "out": "Fuera del estuche"}
FIT_LABEL = {"good": "Buen sellado ✓", "poor": "Sellado deficiente: acomódalo o cambia de almohadilla",
             "testing": "Midiendo…", "none": "Sin probar"}


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Buds Manager", default_width=560, default_height=700,
                         width_request=360, height_request=300)
        self.state = None
        self.mac = None
        self._updating = False
        self._eq_timer = 0
        self.worker = Worker(self._on_state, self._on_error, self._on_busy)
        self.worker.start()
        self._bt_timer = 0
        self._scan_timer = 0
        try:
            self.bt = Bluez(self._bt_changed)
        except GLib.Error as e:
            print("BlueZ no disponible:", e.message)
            self.bt = None

        self._page_boxes = []
        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.spinner = Adw.Spinner(visible=False)
        header.pack_start(self.spinner)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Actualizar")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_start(refresh)
        header.pack_end(self._build_menu())

        self.stack = Adw.ViewStack()
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.NARROW)
        header.set_title_widget(self.switcher)
        toolbar.add_top_bar(header)
        # narrow windows (tiled, phone-sized): tabs move to a bottom bar
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        toolbar.add_bottom_bar(self.switcher_bar)
        self._narrow = False
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 520sp"))
        bp.connect("apply", lambda *_: self._set_narrow(True))
        bp.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(bp)

        self.root = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.root.add_named(Adw.StatusPage(title="Conectando…", icon_name="audio-headphones-symbolic",
                                           child=Adw.Spinner(width_request=32, height_request=32)), "loading")
        self.root.add_named(self._build_disconnected(), "disconnected")
        self.root.add_named(self.stack, "main")
        toolbar.set_content(self.root)
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        self.stack.add_titled_with_icon(self._page(self._build_sound()), "sound", "Sonido",
                                        "audio-volume-high-symbolic")
        self.stack.add_titled_with_icon(self._page(self._build_eq()), "eq", "Ecualizador",
                                        "audio-x-generic-symbolic")
        self.stack.add_titled_with_icon(self._page(self._build_controls()), "controls", "Controles",
                                        "input-touchpad-symbolic")
        self.stack.add_titled_with_icon(self._page(self._build_device()), "device", "Dispositivo",
                                        "preferences-system-devices-symbolic")
        self.stack.add_titled_with_icon(self._page(self._build_connection()), "connection", "Conexión",
                                        "bluetooth-active-symbolic")
        self._bt_changed()
        self._sync_hold()

        self._actions()
        watch_daemon(self.refresh)
        if os.environ.get("BUDS_MANAGER_PAGE"):
            self.stack.set_visible_child_name(os.environ["BUDS_MANAGER_PAGE"])
        self.refresh()
        GLib.timeout_add_seconds(1, self._tick)
        self._last_poll = time.time()

    # ---------- plumbing ----------

    def _set_narrow(self, narrow):
        self._narrow = narrow
        (self.add_css_class if narrow else self.remove_css_class)("narrow")
        m = 10 if narrow else 16
        for box in self._page_boxes:
            box.set_margin_start(m)
            box.set_margin_end(m)
        self.eq_frame.set_spacing(0 if narrow else 2)
        self.eq_frame.set_margin_start(0 if narrow else 6)
        self.eq_frame.set_margin_end(0 if narrow else 6)
        self._sync_switcher()

    def _sync_switcher(self):
        main = self.root.get_visible_child_name() == "main"
        self.switcher.set_visible(main and not self._narrow)
        self.switcher_bar.set_reveal(main and self._narrow)

    def _page(self, child):
        return Gtk.ScrolledWindow(child=Adw.Clamp(child=child, maximum_size=640),
                                  hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=18, margin_bottom=24,
                      margin_start=16, margin_end=16)
        self._page_boxes.append(box)
        return box

    def refresh(self):
        self._last_poll = time.time()
        if self.worker.q.empty():
            self.worker.submit("actualizar")

    def run(self, label, fn, toast=None):
        if self._updating:
            return
        self.worker.submit(label, fn)
        if toast:
            self.toast(toast)

    def toast(self, text, timeout=2):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout))

    def _tick(self):
        interval = POLL_ACTIVE if self.is_active() else POLL_IDLE
        if time.time() - self._last_poll >= interval:
            self.refresh()
        return True

    def _on_busy(self, busy):
        self.spinner.set_visible(busy)

    def _on_error(self, msg):
        self.toast(f"⚠ {msg}", timeout=4)

    def _on_state(self, st, mac, reason=None, battery=None):
        self.mac = mac
        if st is None:
            if reason == "busy":
                self.disc_page.set_icon_name("phone-symbolic")
                self.disc_page.set_title("Otro dispositivo está controlando los audífonos")
                if self._hold_on:
                    self.disc_page.set_description(
                        "El celular tomó el control primero. «Mantener el control en esta PC» está activado, "
                        "así que lo tomaré en cuanto el celular lo suelte: apaga y enciende su Bluetooth o "
                        "guarda los audífonos en el estuche un momento.")
                else:
                    self.disc_page.set_description(
                        "Los audífonos solo aceptan un controlador a la vez y ahora lo tiene el celular "
                        "(el servicio Bluetooth de Xiaomi lo toma al conectarse). La música sigue sonando aquí. "
                        "Activa «Mantener el control en esta PC» abajo, o desconéctalos del celular un momento.")
                self.disc_btn.set_label("Reintentar")
                parts = [f"{lbl} {battery[k]['level']}%" + (" ⚡" if battery[k]["charging"] else "")
                         for k, lbl in (("left", "Izquierdo"), ("right", "Derecho"), ("case", "Estuche"))
                         if battery and battery.get(k)]
                self.disc_page.set_battery("  ·  ".join(parts))
            else:
                self.disc_page.set_icon_name("bluetooth-disconnected-symbolic")
                self.disc_page.set_title("Audífonos no conectados")
                self.disc_page.set_description("Saca los Redmi Buds del estuche o pulsa Conectar.")
                self.disc_btn.set_label("Conectar")
                self.disc_page.set_battery(None)
            self._disc_reason = reason
            if self.state is None or mac is None or reason == "busy":
                self.root.set_visible_child_name("disconnected")
                self._sync_switcher()
            return
        self.state = st
        self._updating = True
        try:
            self._apply_state(st)
        finally:
            self._updating = False
        self.root.set_visible_child_name("main")
        self._sync_switcher()

    def _combo(self, title, pairs, on_change, subtitle=None):
        row = Adw.ComboRow(title=title, model=_string_list(pairs))
        if subtitle:
            row.set_subtitle(subtitle)
        row.pairs = pairs

        def changed(r, _):
            if not self._updating:
                on_change(pairs[r.get_selected()][0])
        row.connect("notify::selected", changed)
        return row

    @staticmethod
    def _select(row, key):
        for i, (k, _) in enumerate(row.pairs):
            if k == key:
                row.set_selected(i)
                return
        row.set_selected(Gtk.INVALID_LIST_POSITION)

    def _switch(self, title, subtitle, on_change):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.connect("notify::active", lambda r, _: None if self._updating else on_change(r.get_active()))
        return row

    def _toggle_group(self, pairs, on_change, icons=None):
        g = Adw.ToggleGroup(homogeneous=True)
        for i, (name, label) in enumerate(pairs):
            t = Adw.Toggle(name=name, label=label)
            if icons:
                t.set_icon_name(icons[i])
                t.set_tooltip(label)
            g.add(t)
        g.connect("notify::active-name", lambda w, _: None if self._updating else on_change(w.get_active_name()))
        return g

    # ---------- pages ----------

    def _build_disconnected(self):
        btn = self.disc_btn = Gtk.Button(label="Conectar", halign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", self._connect_bt)
        self.disc_page = DiscHeader(btn)
        box = self._box()
        box.append(self.disc_page)
        g = Adw.PreferencesGroup()
        self.hold_row_disc = self._hold_row()
        g.add(self.hold_row_disc)
        box.append(g)
        self.bt_panel_disc = BtPanel(self)
        box.append(self.bt_panel_disc)
        return self._page(box)

    def _connect_bt(self, *_):
        if getattr(self, "_disc_reason", None) == "busy":
            self.refresh()
            return
        mac = os.environ.get("REDMI_BUDS_MAC") or self.mac or _paired_buds_mac()
        if not mac:
            self.toast("No encuentro unos Redmi Buds emparejados")
            return
        self.toast("Conectando…")

        def go():
            subprocess.run(["bluetoothctl", "connect", mac], capture_output=True, timeout=20)
            time.sleep(1.5)
            GLib.idle_add(self.refresh)
        threading.Thread(target=go, daemon=True).start()

    def _build_sound(self):
        box = self._box()

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.title_label = Gtk.Label(label="REDMI Buds 8 Pro", xalign=0)
        self.title_label.add_css_class("hero-title")
        hero.append(self.title_label)
        cards = Gtk.Box(spacing=12, homogeneous=True)
        self.batt = {"left": BatteryCard("Izquierdo", "audio-headphones-symbolic"),
                     "right": BatteryCard("Derecho", "audio-headphones-symbolic"),
                     "case": BatteryCard("Estuche", "battery-symbolic")}
        for c in self.batt.values():
            cards.append(c)
        hero.append(cards)
        box.append(hero)

        g = Adw.PreferencesGroup(title="Control de ruido")
        self.mode_group = self._toggle_group([(k, l) for k, l, _ in MODE_INFO],
                                             lambda m: self._set_mode(m))
        self.mode_group.add_css_class("mode-toggles")
        g.add(self.mode_group)
        box.append(g)
        self.commute_row = self._combo("Viaje inmersivo", COMMUTE_LABELS, lambda v: self.run(
            "viaje inmersivo", lambda c: d.set_commute(c, v)),
            subtitle="Cancelación de ruido con sonidos relajantes para el trayecto")
        cg = Adw.PreferencesGroup()
        cg.add(self.commute_row)

        self.anc_group = Adw.PreferencesGroup(title="Intensidad de la cancelación")
        self.anc_strength = self._toggle_group(ANC_LABELS, lambda v: self.run(
            "intensidad", lambda c: d.set_strength(c, "anc", v)))
        self.anc_group.add(self.anc_strength)
        box.append(self.anc_group)

        self.transp_group = Adw.PreferencesGroup(title="Tipo de transparencia")
        self.transp_strength = self._toggle_group(TRANSP_LABELS, lambda v: self.run(
            "transparencia", lambda c: d.set_strength(c, "transparency", v)))
        self.transp_group.add(self.transp_strength)
        box.append(self.transp_group)
        box.append(cg)

        g = Adw.PreferencesGroup(title="Inteligencia")
        self.bool_rows = {}
        for key, cfg, title, sub in [
            ("adaptive_anc", p.CFG_ADAPTIVE_ANC, "Cancelación inteligente",
             "Ajusta la intensidad en tiempo real según el ruido del entorno"),
            ("customized_anc", p.CFG_CUSTOMIZED_ANC, "ANC personalizado",
             "Adapta la cancelación a la forma de tu oído"),
            ("adaptive_sound", p.CFG_ADAPTIVE_SOUND, "Sonido adaptativo",
             "Compensa el sonido según cómo quedan puestos en tu oído"),
        ]:
            row = self._switch(title, sub, lambda on, cfg=cfg, t=title: self.run(
                t, lambda c: d.set_bool(c, cfg, on, t)))
            self.bool_rows[key] = row
            g.add(row)
        box.append(g)
        return box

    def _set_mode(self, mode):
        if not mode:
            return
        self.run("modo", lambda c: d.set_mode(c, mode))
        self.anc_group.set_visible(mode == "anc")
        self.transp_group.set_visible(mode == "transparency")

    def _build_eq(self):
        box = self._box()
        g = Adw.PreferencesGroup(title="Ecualizador")
        self.eq_row = self._combo("Perfil de sonido", EQ_LABELS, lambda v: self.run(
            "ecualizador", lambda c: d.set_eq_preset(c, v)))
        g.add(self.eq_row)
        hear = Adw.ActionRow(title="Ajustar a tu oído",
                             subtitle="Prueba de tonos que crea una curva para tus oídos y tus audífonos")
        hear.add_prefix(Gtk.Image(icon_name="audio-volume-high-symbolic"))
        btn = Gtk.Button(label="Empezar", valign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.connect("clicked", self._hearing_test)
        hear.add_suffix(btn)
        hear.set_activatable_widget(btn)
        g.add(hear)
        box.append(g)

        self.curve_group = Adw.PreferencesGroup(
            title="Curva personalizada",
            description="Mueve una banda y el perfil pasa a «Personalizado». De −6 a +6 dB.")
        reset = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Curvas rápidas")
        reset.add_css_class("flat")
        menu = Gio.Menu()
        for name in EQ_PRESETS_CURVES:
            menu.append(name, f"win.curve::{name}")
        reset.set_menu_model(menu)
        self.curve_group.set_header_suffix(reset)

        frame = Gtk.Box(spacing=2, homogeneous=True, margin_top=12, margin_bottom=12, margin_start=6, margin_end=6)
        frame.add_css_class("card")
        self.eq_frame = frame
        self.sliders = []
        for f in d.DEFAULT_EQ_FREQS:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10, margin_bottom=10)
            sc = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, height_request=220,
                           draw_value=True, digits=0, value_pos=Gtk.PositionType.TOP)
            sc.set_range(-6, 6)
            sc.set_increments(1, 1)
            sc.set_round_digits(0)
            sc.set_has_origin(False)
            sc.add_mark(0, Gtk.PositionType.LEFT, None)
            sc.set_format_value_func(lambda _s, v: f"{int(round(v)):+d}" if round(v) else "0")
            sc.connect("value-changed", self._on_slider)
            col.append(sc)
            lbl = Gtk.Label(label=_freq_label(f))
            lbl.add_css_class("eq-band-label")
            sc.freq_label = lbl
            col.append(lbl)
            frame.append(col)
            self.sliders.append(sc)
        self.curve_group.add(frame)
        box.append(self.curve_group)
        return box

    def _hearing_test(self, *_):
        if not self.state:
            self.toast("Los audífonos no están disponibles", 3)
            return
        from .hearing_gui import HearingDialog
        HearingDialog(self).present(self)

    def _on_slider(self, *_):
        if self._updating:
            return
        if self._eq_timer:
            GLib.source_remove(self._eq_timer)
        self._eq_timer = GLib.timeout_add(350, self._push_curve)

    def _curve_from_sliders(self):
        freqs = [b["freq"] for b in (self.state or {}).get("eq_curve") or []] or d.DEFAULT_EQ_FREQS
        return [{"freq": f, "level": int(round(s.get_value()))} for f, s in zip(freqs, self.sliders)]

    def _push_curve(self):
        self._eq_timer = 0
        curve = self._curve_from_sliders()

        def job(c):
            d.set_eq_curve(c, curve)
            d.set_eq_preset(c, "custom")
        self.worker.submit("curva", job)
        self._updating = True
        self._select(self.eq_row, "custom")
        self._updating = False
        return False

    def _build_controls(self):
        box = self._box()
        self.gesture_rows = {}
        for tap, title in TAP_LABELS:
            g = Adw.PreferencesGroup(title=title)
            for side, side_label in (("left", "Audífono izquierdo"), ("right", "Audífono derecho")):
                row = self._combo(side_label, TAP_ACTION_LABELS,
                                  lambda a, tap=tap, side=side: self.run(
                                      "gesto", lambda c: d.set_gesture(c, tap, side, a)))
                self.gesture_rows[(tap, side)] = row
                g.add(row)
            box.append(g)

        g = Adw.PreferencesGroup(title="Mantener pulsado")
        self.cycle_rows = {}
        for side, side_label in (("left", "Izquierdo"), ("right", "Derecho")):
            row = self._combo(side_label, LONG_ACTION_LABELS, lambda a, side=side: self.run(
                "pulsación larga", lambda c: d.set_gesture(c, "long", side, a)))
            self.gesture_rows[("long", side)] = row
            g.add(row)
            crow = self._combo(f"Modos que recorre ({side_label.lower()})", CYCLE_LABELS,
                               lambda cy, side=side: self.run(
                                   "ciclo", lambda c: d.set_anc_cycle(c, side, cy)))
            self.cycle_rows[side] = crow
            g.add(crow)
        box.append(g)

        g = Adw.PreferencesGroup(title="Deslizar", description="Desliza el dedo sobre el tallo del audífono")
        for side, side_label in (("left", "Audífono izquierdo"), ("right", "Audífono derecho")):
            row = self._combo(side_label, SWIPE_LABELS, lambda a, side=side: self.run(
                "deslizar", lambda c: d.set_gesture(c, "swipe", side, a)))
            self.gesture_rows[("swipe", side)] = row
            g.add(row)
        box.append(g)
        box.append(self._build_pc_shortcuts())
        return box

    # ---------- gestures → PC shortcuts ----------

    def _build_pc_shortcuts(self):
        g = Adw.PreferencesGroup(
            title="Atajos en esta PC",
            description="Los toques mandan a la PC la acción que elegiste arriba (reproducir, siguiente…). "
                        "Aquí decides qué hace la PC al recibirla. Solo cuando el audio sale por esta PC; "
                        "el celular no cambia.")
        self.pc_status_row = Adw.ActionRow(title="Estado", subtitle="—")
        self.pc_status_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
        self.pc_status_row.add_prefix(self.pc_status_icon)
        g.add(self.pc_status_row)
        pairs = [(k, label) for k, (label, _) in gest.BUILTIN.items()] + [("custom", "Comando personalizado…")]
        self.pc_rows = {}
        for key, label in gest.KEY_LABELS:
            row = self._combo(f"Al recibir «{label}»", pairs,
                              lambda a, key=key: self._set_pc_action(key, a))
            entry = Adw.EntryRow(title="Comando (se ejecuta con sh)", show_apply_button=True, visible=False)
            entry.connect("apply", lambda e, key=key: self._set_pc_action(key, "cmd:" + e.get_text().strip()))
            row.entry = entry
            self.pc_rows[key] = row
            g.add(row)
            g.add(entry)
        self._sync_pc_shortcuts()
        return g

    def _set_pc_action(self, key, action):
        row = self.pc_rows[key]
        if action == "custom":
            row.entry.set_visible(True)
            row.entry.grab_focus()
            return
        if action == "cmd:":
            self.toast("Escribe un comando primero")
            return
        try:
            gest.set_action(key, action)
        except (OSError, ValueError) as e:
            self.toast(f"⚠ {e}", 4)
            return
        row.entry.set_visible(action.startswith("cmd:"))
        self.toast(f"«{dict(gest.KEY_LABELS)[key]}» → {gest.describe(action)}")

    def _sync_pc_shortcuts(self, st=None):
        acts = gest.get_actions()
        senders = {}
        for tap, title in TAP_LABELS:
            for side, short in (("left", "izq."), ("right", "der.")):
                a = ((st or {}).get("gestures") or {}).get(tap, {}).get(side)
                if a:
                    senders.setdefault(a, []).append(f"{title.lower()} {short}")
        was, self._updating = self._updating, True
        for key, row in self.pc_rows.items():
            act = acts[key]
            custom = act.startswith("cmd:")
            self._select(row, "custom" if custom else act)
            if custom and not row.entry.has_focus():
                row.entry.set_text(act[4:])
            row.entry.set_visible(custom)
            row.set_subtitle("Lo envía: " + ", ".join(senders[key]) if key in senders
                             else "Ningún toque lo envía ahora" if st else "")
        self._updating = was
        ds = self.worker.daemon_status
        gs = (ds or {}).get("gestures") or {}
        if not ds:
            text, icon = "Activa «Mantener el control en esta PC» (pestaña Conexión) para usar los atajos", \
                "dialog-warning-symbolic"
        elif gs.get("device"):
            text, icon = f"Escuchando {gs['device'].replace(' (AVRCP)', '')}", "object-select-symbolic"
        else:
            text, icon = (gs.get("problem") or "esperando a los audífonos").capitalize(), "dialog-information-symbolic"
        self.pc_status_row.set_subtitle(text)
        self.pc_status_icon.set_from_icon_name(icon)

    def _build_device(self):
        box = self._box()
        g = Adw.PreferencesGroup(title="Comportamiento")
        self.ear_row = self._switch("Detección de oído", "Pausa la música al quitarte un audífono",
                                    lambda on: self.run("detección", lambda c: d.set_ear_detection(c, on)))
        g.add(self.ear_row)
        for key, cfg, title, sub in [
            ("auto_answer", p.CFG_AUTO_ANSWER, "Respuesta automática",
             "Contesta llamadas al ponerte los audífonos"),
        ]:
            row = self._switch(title, sub, lambda on, cfg=cfg, t=title: self.run(
                t, lambda c: d.set_bool(c, cfg, on, t)))
            self.bool_rows[key] = row
            g.add(row)
        box.append(g)

        g = Adw.PreferencesGroup(title="Prueba de ajuste",
                                 description="Comprueba si las almohadillas sellan bien. Ponte los dos "
                                             "audífonos y quédate en silencio: sonará un tono unos segundos.")
        self.fit_btn = Gtk.Button(label="Iniciar prueba", valign=Gtk.Align.CENTER)
        self.fit_btn.add_css_class("suggested-action")
        self.fit_btn.connect("clicked", self._fit_test)
        g.set_header_suffix(self.fit_btn)
        self.fit_rows = {}
        for side, label in (("left", "Izquierdo"), ("right", "Derecho")):
            row = Adw.ActionRow(title=label, subtitle="Sin probar")
            self.fit_rows[side] = row
            g.add(row)
        box.append(g)

        g = Adw.PreferencesGroup(title="Buscar audífonos",
                                 description="Emiten un pitido fuerte. Quítatelos antes de usarlo.")
        btns = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        for i, (target, label) in enumerate((("left", "Izquierdo"), ("right", "Derecho"), ("both", "Ambos"))):
            b = Gtk.Button(label=label)
            b.add_css_class("pill")
            b.connect("clicked", lambda _b, t=target: self.run("buscar", lambda c: d.find(c, t),
                                                                toast="Sonando…"))
            btns.attach(b, i % 2, i // 2, 1, 1)
        stop = Gtk.Button(label="Detener")
        stop.add_css_class("pill")
        stop.add_css_class("destructive-action")
        stop.connect("clicked", lambda *_: self.run("buscar", lambda c: d.find(c, "stop")))
        btns.attach(stop, 1, 1, 1, 1)
        g.add(btns)
        box.append(g)

        g = Adw.PreferencesGroup(title="Información")
        self.info_rows = {}
        for key, title in (("name", "Modelo"), ("mac", "Dirección"), ("firmware", "Firmware"),
                           ("serial", "Número de serie"), ("platform", "Sistema")):
            row = Adw.ActionRow(title=title, subtitle="—", subtitle_selectable=True)
            row.add_css_class("property")
            self.info_rows[key] = row
            g.add(row)
        box.append(g)

        g = Adw.PreferencesGroup(title="Copias de seguridad")
        for title, sub, action in (
                ("Guardar configuración", "Exporta todos los ajustes a un archivo JSON", "win.backup"),
                ("Restaurar desde archivo…", "Aplica una copia guardada", "win.restore-file"),
                ("Restaurar configuración original", "La que tenían antes de usar esta app", "win.restore-initial")):
            row = Adw.ActionRow(title=title, subtitle=sub, activatable=True, action_name=action)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            g.add(row)
        box.append(g)
        return box

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("Actualizar", "win.refresh")
        menu.append("Guardar configuración", "win.backup")
        menu.append("Restaurar original", "win.restore-initial")
        menu.append("Acerca de Buds Manager", "win.about")
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True)

    def _actions(self):
        def add(name, cb, param=None):
            a = Gio.SimpleAction(name=name, parameter_type=param)
            a.connect("activate", cb)
            self.add_action(a)
        add("refresh", lambda *_: self.refresh())
        add("backup", self._backup)
        add("restore-file", self._restore_file)
        add("restore-initial", lambda *_: self._restore(INITIAL_BACKUP))
        add("about", self._about)
        add("curve", self._apply_curve_shape, GLib.VariantType.new("s"))
        self.get_application().set_accels_for_action("win.refresh", ["F5", "<Ctrl>r"])

    def _apply_curve_shape(self, _a, param):
        levels = EQ_PRESETS_CURVES[param.get_string()]
        self._updating = True
        for s, v in zip(self.sliders, levels):
            s.set_value(v)
        self._updating = False
        self._push_curve()

    # ---------- connection tab / bluetooth ----------

    def _build_connection(self):
        box = self._box()
        g = Adw.PreferencesGroup(title="Escuchar en dos dispositivos",
                                 description="Con el multipunto los audífonos quedan conectados a la vez a dos "
                                             "equipos (por ejemplo, el celular y esta PC) y cambian solos al que "
                                             "empiece a reproducir o reciba una llamada.")
        row = self._switch("Conexión multipunto", "Mantener dos dispositivos conectados al mismo tiempo",
                           lambda on: self.run("multipunto",
                                               lambda c: d.set_bool(c, p.CFG_DOUBLE_CONNECTION, on, "multipunto")))
        self.bool_rows["multipoint"] = row
        g.add(row)
        self.hold_row_main = self._hold_row()
        g.add(self.hold_row_main)
        self.bt_panel_main = BtPanel(self, extra_group=g)
        box.append(self._build_codec())
        box.append(self.bt_panel_main)
        return box

    def _hold_row(self):
        row = Adw.SwitchRow(title="Mantener el control en esta PC",
                            subtitle="Un servicio guarda el canal de ajustes para la PC. El celular sigue "
                                     "reproduciendo, pero su menú de los audífonos no podrá cambiar ajustes.")
        row.connect("notify::active", self._on_hold)
        return row

    @property
    def _hold_on(self):
        return getattr(self, "_hold_state", False)

    def _sync_hold(self):
        self._hold_state = hold_enabled()
        self._updating = True
        for r in (getattr(self, "hold_row_main", None), getattr(self, "hold_row_disc", None)):
            if r:
                r.set_active(self._hold_state)
        self._updating = False

    def _on_hold(self, row, _):
        if self._updating or row.get_active() == self._hold_on:
            return
        on = row.get_active()

        def go():
            try:
                set_hold(on)
                time.sleep(1.5)
                GLib.idle_add(self.toast, "Control en esta PC activado" if on else "Control en esta PC desactivado")
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.toast, f"⚠ no se pudo cambiar: {e}", 4)
            GLib.idle_add(self._sync_hold)
            GLib.idle_add(self.refresh)
        threading.Thread(target=go, daemon=True).start()

    def _build_codec(self):
        g = Adw.PreferencesGroup(title="Calidad de audio en esta PC",
                                 description="LDAC da la mejor calidad. El modo llamadas activa el micrófono "
                                             "de los audífonos pero baja mucho la calidad de la música.")
        self.profile_row = Adw.ComboRow(title="Códec")
        self.profile_row.pairs = []
        self.profile_row.connect("notify::selected", self._on_profile)
        g.add(self.profile_row)
        self.ldac_row = self._combo("Calidad LDAC", audio.LDAC_QUALITY, self._on_ldac,
                                    subtitle="Al cambiarla el audio se corta un par de segundos")
        g.add(self.ldac_row)
        return g

    def _update_codec(self):
        if not self.mac:
            return
        profs, active = audio.profiles(self.mac)
        self._updating = True
        try:
            if [p_ for p_, _ in profs] != [p_ for p_, _ in self.profile_row.pairs]:
                self.profile_row.pairs = profs
                self.profile_row.set_model(_string_list(profs))
            self._select(self.profile_row, active)
            self._select(self.ldac_row, audio.ldac_quality(self.mac))
            self.ldac_row.set_visible(active == "a2dp-sink" or "ldac" in (active or ""))
        finally:
            self._updating = False

    def _on_profile(self, row, _):
        if self._updating or not row.pairs or row.get_selected() >= len(row.pairs):
            return
        prof = row.pairs[row.get_selected()][0]
        try:
            audio.set_profile(self.mac, prof)
            self.toast(f"Códec: {row.pairs[row.get_selected()][1]}")
        except RuntimeError as e:
            self.toast(f"⚠ {e}", 4)
        GLib.timeout_add(800, lambda: self._update_codec() and False)

    def _on_ldac(self, quality):
        self.toast("Aplicando calidad LDAC… el audio se cortará un momento", 3)

        def go():
            audio.set_ldac_quality(self.mac, quality)
            time.sleep(3)
            GLib.idle_add(self._update_codec)
        threading.Thread(target=go, daemon=True).start()

    def _bt_changed(self):
        if not self._bt_timer:
            self._bt_timer = GLib.timeout_add(250, self._bt_update)

    def _bt_update(self):
        self._bt_timer = 0
        for panel in (getattr(self, "bt_panel_main", None), getattr(self, "bt_panel_disc", None)):
            if panel:
                panel.update()
        return False

    def toggle_scan(self):
        if not self.bt:
            return
        on = not self.bt.discovering
        self.bt.scan(on, lambda e: e and self.toast(f"⚠ {e}", 4))
        if self._scan_timer:
            GLib.source_remove(self._scan_timer)
            self._scan_timer = 0
        if on:
            def stop():
                self._scan_timer = 0
                if self.bt.discovering:
                    self.bt.scan(False)
                return False
            self._scan_timer = GLib.timeout_add_seconds(45, stop)

    def bt_action(self, action, path, name):
        verbs = {"connect": ("Conectando", "Conectado"), "disconnect": ("Desconectando", "Desconectado"),
                 "pair": ("Emparejando", "Emparejado y conectado"), "forget": ("Olvidando", "Olvidado")}
        doing, done_msg = verbs[action]
        for panel in (self.bt_panel_main, self.bt_panel_disc):
            panel.busy.add(path)
        self._bt_changed()

        def done(err):
            for panel in (self.bt_panel_main, self.bt_panel_disc):
                panel.busy.discard(path)
            self._bt_changed()
            if err:
                self.toast(f"⚠ {name}: {err}", 5)
            else:
                self.toast(f"{done_msg}: {name}")
            GLib.timeout_add(1500, lambda: self.refresh() and False)
        getattr(self.bt, action)(path, done)

    def confirm_forget(self, path, name):
        dlg = Adw.AlertDialog(heading=f"¿Olvidar {name}?",
                              body="Se eliminará el emparejamiento. Para volver a usarlo tendrás que "
                                   "ponerlo en modo emparejamiento y emparejarlo de nuevo.")
        dlg.add_response("cancel", "Cancelar")
        dlg.add_response("forget", "Olvidar")
        dlg.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda _d, r: r == "forget" and self.bt_action("forget", path, name))
        dlg.present(self)

    def _fit_test(self, *_):
        wear = (self.state or {}).get("wear") or {}
        if wear and not all(v == "ear" for v in wear.values()):
            self.toast("Ponte los dos audífonos antes de la prueba", 4)
            return
        self.fit_btn.set_sensitive(False)
        for row in self.fit_rows.values():
            row.set_subtitle("Midiendo…")

        def job(c):
            try:
                res = d.fit_test(c)
                GLib.idle_add(self.toast, "Prueba de ajuste terminada")
                GLib.idle_add(self._show_fit, res)
            finally:
                GLib.idle_add(self.fit_btn.set_sensitive, True)
        self.worker.submit("prueba de ajuste", job)

    def _show_fit(self, res):
        for side, row in self.fit_rows.items():
            row.set_subtitle(FIT_LABEL.get(res.get(side), res.get(side)))

    # ---------- backups ----------

    def _backup(self, *_):
        if not self.state:
            return
        path = os.path.join(STATE_DIR, time.strftime("backup-%Y%m%d-%H%M%S.json"))
        save_backup(self.state, path)
        self.toast(f"Guardado en {path.replace(os.path.expanduser('~'), '~')}", timeout=4)

    def _restore_file(self, *_):
        dlg = Gtk.FileDialog(title="Restaurar configuración", initial_folder=Gio.File.new_for_path(STATE_DIR))
        f = Gtk.FileFilter(name="Copias JSON")
        f.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)

        def done(dialog, res):
            try:
                gf = dialog.open_finish(res)
            except GLib.Error:
                return
            if gf:
                self._restore(gf.get_path())
        dlg.open(self, None, done)

    def _restore(self, path):
        try:
            with open(path) as fh:
                st = json.load(fh)
        except (OSError, ValueError) as e:
            self.toast(f"No se pudo leer la copia: {e}", timeout=4)
            return

        def job(c):
            failed = []
            for what, fn in d.restore_steps(c, st):
                try:
                    fn()
                except p.BudsError:
                    failed.append(what)
            if failed:
                raise p.BudsError("no se restauró: " + ", ".join(failed))
            GLib.idle_add(self.toast, "Configuración restaurada")
        self.worker.submit("restaurar", job)

    def _about(self, *_):
        Adw.AboutDialog(application_name="Buds Manager", application_icon="audio-headphones",
                        version="1.0", developer_name="Oscar",
                        comments="Control completo de los REDMI Buds 8 Pro en Linux.\n"
                                 "Protocolo MIWEAR portado de Gadgetbridge.",
                        license_type=Gtk.License.GPL_3_0).present(self)

    # ---------- state → widgets ----------

    def _apply_state(self, st):
        b = st.get("battery", {})
        for k, card in self.batt.items():
            card.update(b.get(k))
            card.set_wear((st.get("wear") or {}).get(k))
        for side, row in self.fit_rows.items():
            row.set_subtitle(FIT_LABEL.get((st.get("fit") or {}).get(side), "Sin probar"))

        mode = st.get("mode", "off")
        self.mode_group.set_active_name(mode)
        self.anc_group.set_visible(mode == "anc")
        self.transp_group.set_visible(mode == "transparency")
        s = st.get("strength") or {}
        if s.get("target") == "anc":
            self.anc_strength.set_active_name(s.get("value") or "")
        elif s.get("target") == "transparency":
            self.transp_strength.set_active_name(s.get("value") or "")

        for key, row in self.bool_rows.items():
            row.set_sensitive(key in st)
            row.set_active(bool(st.get(key)))
        self.ear_row.set_active(bool(st.get("ear_detection")))

        self._select(self.eq_row, st.get("eq_preset"))
        curve = st.get("eq_curve") or []
        if not self._eq_timer:
            for sc, band in zip(self.sliders, curve):
                sc.set_value(band["level"])
                sc.freq_label.set_label(_freq_label(band["freq"]))

        for (tap, side), row in self.gesture_rows.items():
            self._select(row, (st.get("gestures") or {}).get(tap, {}).get(side))
        self._sync_pc_shortcuts(st)
        cyc = st.get("anc_cycle") or {}
        longg = (st.get("gestures") or {}).get("long", {})
        for side, row in self.cycle_rows.items():
            self._select(row, cyc.get(side))
            row.set_visible(longg.get(side) == "anc-cycle")

        self._select(self.commute_row, st.get("commute"))
        self._update_codec()

        name = _device_name(self.mac) or "REDMI Buds"
        self.title_label.set_label(name)
        info = {"name": name, "mac": self.mac, "firmware": st.get("firmware"), "serial": st.get("serial"),
                "platform": st.get("platform")}
        for k, row in self.info_rows.items():
            row.set_subtitle(info.get(k) or "—")


_name_cache = {}


def _device_name(mac):
    if not mac:
        return None
    if mac not in _name_cache:
        out = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.strip().startswith("Name:"):
                _name_cache[mac] = line.split(":", 1)[1].strip()
    return _name_cache.get(mac)


def _paired_buds_mac():
    import re
    out = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line.strip(), re.I)
        if m and re.search(r"redmi buds|xiaomi buds", m.group(2), re.I):
            return m.group(1)
    return None


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name("Buds Manager")

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_ = Gio.SimpleAction(name="quit")
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Ctrl>q", "<Ctrl>w"])

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main():
    if not shutil.which("bluetoothctl"):
        print("Buds Manager necesita bluetoothctl (paquete bluez-utils)")
    raise SystemExit(App().run(None))
