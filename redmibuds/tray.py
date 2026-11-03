"""System tray icon (StatusNotifierItem + com.canonical.dbusmenu) for Waybar's tray and friends.

Left click opens Buds Manager; the menu shows the battery and switches the noise mode and EQ preset.
Talks to buds-manager-daemon over its socket (long-polling for changes), and runs the `buds` CLI for actions,
so it never opens the earbuds' channel by itself.
"""
import os
import subprocess
import threading
import time

from gi.repository import Gio, GLib

from . import remote
from . import protocol as p

BUDS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDS = os.path.join(BUDS_DIR, "buds")
MANAGER = os.path.join(BUDS_DIR, "buds-manager")

MODES = [("off", "Apagado"), ("anc", "Cancelación de ruido"), ("transparency", "Transparencia")]
EQS = [("balanced", "Equilibrado"), ("standard", "Estándar"), ("voice", "Voz"), ("bass", "Más graves"),
       ("treble", "Más agudos"), ("volume", "Más volumen"), ("custom", "Personalizado")]
MODE_ICON = {"anc": "audio-headphones", "transparency": "audio-headphones", "off": "audio-headphones"}

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  </interface>
</node>"""


def _batt_text(batt):
    parts = []
    for k, label in (("left", "Izq"), ("right", "Der"), ("case", "Estuche")):
        b = (batt or {}).get(k)
        if b:
            parts.append(f"{label} {b['level']} %" + (" ⚡" if b.get("charging") else ""))
    return " · ".join(parts)


class Tray:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.info = {"state": None, "reason_text": "Conectando…", "battery": None, "sleep": None}
        self._scroll_at = 0
        self.revision = 1
        self.actions = {}          # menu id -> callable
        self._layout = None
        self._build()

        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.bus.register_object("/StatusNotifierItem", sni, self._sni_call, self._sni_prop, None)
        self.bus.register_object("/MenuBar", menu, self._menu_call, self._menu_prop, None)
        Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # (re-)register whenever a tray host (Waybar) appears
        Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._register(), None)
        threading.Thread(target=self._poll, daemon=True).start()

    # ---------- data ----------

    def _poll(self):
        while True:
            try:
                r = remote.call({"op": "state"}, timeout=5)
                sl = r.get("sleep") or {}
                info = {"state": r.get("state") if r.get("holding") else None,
                        "reason_text": r.get("reason_text"), "battery": r.get("battery"),
                        "sleep": (sl.get("remaining") + 59) // 60 if sl.get("remaining") else None}
            except (OSError, ValueError, p.BudsError):
                info = {"state": None, "battery": None,
                        "reason_text": "Servicio apagado: actívalo en Buds Manager → Conexión"}
                GLib.idle_add(self._set, info)
                time.sleep(10)
                continue
            GLib.idle_add(self._set, info)
            try:
                # while the sleep timer runs, refresh each minute so the menu shows the time left
                remote.call({"op": "wait_change", "wait": 60 if not info["sleep"] else 30}, timeout=75)
            except (OSError, ValueError, p.BudsError):
                time.sleep(5)

    def _set(self, info):
        if info == self.info:
            return False
        self.info = info
        self._build()
        self.revision += 1
        self._emit("/MenuBar", "com.canonical.dbusmenu", "LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))
        self._emit("/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewToolTip", None)
        self._emit("/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewIcon", None)
        return False

    def _tooltip(self):
        st = self.info["state"]
        if st:
            mode = dict(MODES).get(st.get("mode"), "—")
            extra = f"\nTemporizador: {self.info['sleep']} min" if self.info.get("sleep") else ""
            return "REDMI Buds", f"{mode}\n{_batt_text(st.get('battery'))}{extra}"
        body = self.info.get("reason_text") or ""
        if self.info.get("battery"):
            body += "\n" + _batt_text(self.info["battery"])
        return "REDMI Buds", body

    # ---------- menu ----------

    def _build(self):
        """Builds the menu tree: list of (id, props, children)."""
        self.actions = {}
        nid = iter(range(1, 1000))
        st = self.info["state"]

        def item(label, action=None, enabled=True, toggle=None, checked=False, children=None, kind=None):
            i = next(nid)
            props = {"label": GLib.Variant("s", label), "enabled": GLib.Variant("b", enabled),
                     "visible": GLib.Variant("b", True)}
            if kind:
                props["type"] = GLib.Variant("s", kind)
            if toggle:
                props["toggle-type"] = GLib.Variant("s", toggle)
                props["toggle-state"] = GLib.Variant("i", 1 if checked else 0)
            if children is not None:
                props["children-display"] = GLib.Variant("s", "submenu")
            if action:
                self.actions[i] = action
            return (i, props, children or [])

        def sep():
            return item("", kind="separator")

        items = []
        batt = _batt_text((st or {}).get("battery") or self.info.get("battery"))
        if batt:
            items.append(item("🔋 " + batt, enabled=False))
        if st:
            items.append(sep())
            for key, label in MODES:
                items.append(item(label, lambda key=key: self._run(BUDS, "mode", key), toggle="radio",
                                  checked=st.get("mode") == key))
            items.append(sep())
            eq_items = [item(label, lambda key=key: self._run(BUDS, "eq", key), toggle="radio",
                             checked=st.get("eq_preset") == key) for key, label in EQS]
            items.append(item("Ecualizador", children=eq_items))
        else:
            items.append(item(self.info.get("reason_text") or "Sin conexión", enabled=False))
        items.append(sep())
        left = self.info.get("sleep")
        timer_items = [item(f"{m} min", lambda m=m: self._sleep(m)) for m in (15, 30, 45, 60, 90)]
        if left:
            timer_items += [sep(), item("Cancelar", lambda: self._sleep(None))]
        items.append(item(f"Dormir · faltan {left} min" if left else "Temporizador para dormir",
                          children=timer_items))
        items.append(sep())
        items.append(item("Abrir Buds Manager", lambda: self._run(MANAGER)))
        self._layout = (0, {"children-display": GLib.Variant("s", "submenu")}, items)

    def _scroll_mode(self, step):
        cur = (self.info.get("state") or {}).get("mode")
        keys = [k for k, _ in MODES]
        if cur not in keys:
            return
        self._run(BUDS, "mode", keys[(keys.index(cur) + step) % len(keys)], "--notify")

    def _sleep(self, minutes):
        def go():
            try:
                remote.call({"op": "sleep_timer", **({"minutes": minutes} if minutes else {"cancel": True})})
            except (OSError, ValueError, p.BudsError):
                return
            r = remote.call({"op": "state"}, timeout=5)
            sl = r.get("sleep") or {}
            info = dict(self.info, sleep=(sl.get("remaining") + 59) // 60 if sl.get("remaining") else None)
            GLib.idle_add(self._set, info)
        threading.Thread(target=go, daemon=True).start()

    def _run(self, *cmd):
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _find(self, node, i):
        if node[0] == i:
            return node
        for c in node[2]:
            f = self._find(c, i)
            if f:
                return f
        return None

    def _variant(self, node, depth):
        nid, props, children = node
        kids = [] if depth == 0 else [GLib.Variant("(ia{sv}av)", self._tuple(c, depth - 1)) for c in children]
        return (nid, props, kids)

    def _tuple(self, node, depth):
        return self._variant(node, depth)

    def _all(self, node):
        yield node
        for c in node[2]:
            yield from self._all(c)

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            parent, depth, _names = params.unpack()
            node = self._find(self._layout, parent) or self._layout
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._variant(node, depth))))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = [(n[0], n[1]) for n in self._all(self._layout) if not ids or n[0] in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            node = self._find(self._layout, i)
            val = (node[1] if node else {}).get(name, GLib.Variant("s", ""))
            inv.return_value(GLib.Variant("(v)", (val,)))
        elif method == "Event":
            i, event, _data, _ts = params.unpack()
            if event == "clicked" and i in self.actions:
                self.actions[i]()
            inv.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            for i, event, _d, _t in events:
                if event == "clicked" and i in self.actions:
                    self.actions[i]()
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _menu_prop(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    # ---------- StatusNotifierItem ----------

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            self._run(MANAGER)
        elif method == "Scroll":
            delta, orientation = params.unpack()
            # one notch can arrive as several events: at most one mode change per 0.7 s
            if orientation == "vertical" and delta and time.time() - self._scroll_at > 0.7:
                self._scroll_at = time.time()
                self._scroll_mode(1 if delta < 0 else -1)
        inv.return_value(None)

    def _sni_prop(self, conn, sender, path, iface, prop):
        title, body = self._tooltip()
        st = self.info["state"]
        icon = MODE_ICON.get((st or {}).get("mode"), "audio-headphones")
        return {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "buds-manager"),
            "Title": GLib.Variant("s", "Buds Manager"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", icon),
            "IconThemePath": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (icon, [], title, body)),
            "Menu": GLib.Variant("o", "/MenuBar"),
            "ItemIsMenu": GLib.Variant("b", False),
        }.get(prop)

    def _emit(self, path, iface, signal, params):
        try:
            self.bus.emit_signal(None, path, iface, signal, params)
        except GLib.Error:
            pass

    def _register(self):
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                      "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)), None,
                      Gio.DBusCallFlags.NONE, -1, None, None)


def main():
    Tray()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
