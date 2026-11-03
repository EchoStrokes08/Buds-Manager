"""Minimal async BlueZ (org.bluez over system D-Bus) helper for the GUI."""
from gi.repository import Gio, GLib

BUS = "org.bluez"
AGENT_PATH = "/io/github/oscar/BudsManager/agent"
AGENT_XML = """
<node>
  <interface name="org.bluez.Agent1">
    <method name="Release"/>
    <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
    <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
    <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
    <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
    <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
    <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="Cancel"/>
  </interface>
</node>
"""


def _err(e):
    if isinstance(e, GLib.Error):
        Gio.DBusError.strip_remote_error(e)
        msg = e.message
    else:
        msg = str(e)
    friendly = {
        "Page Timeout": "el dispositivo no respondió (¿está encendido y cerca?)",
        "Authentication Failed": "falló la autenticación",
        "Already Exists": "ya está emparejado",
        "br-connection-page-timeout": "el dispositivo no respondió (¿está encendido y cerca?)",
        "br-connection-profile-unavailable": "el dispositivo no ofrece audio ahora mismo",
    }
    for k, v in friendly.items():
        if k in msg:
            return v
    return msg


class Bluez:
    """Tracks Bluetooth devices and runs actions. Callbacks run on the GTK main loop."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        self.devices = {}   # path -> dict of Device1 props
        self.adapter = None
        self.discovering = False
        self._agent_id = None
        self.bus.signal_subscribe(BUS, "org.freedesktop.DBus.ObjectManager", None, "/", None,
                                  Gio.DBusSignalFlags.NONE, self._on_objects, None)
        self.bus.signal_subscribe(BUS, "org.freedesktop.DBus.Properties", "PropertiesChanged", None, None,
                                  Gio.DBusSignalFlags.NONE, self._on_props, None)
        self.reload()
        self._register_agent()

    # --- state ---

    def reload(self):
        try:
            res = self.bus.call_sync(BUS, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                                     None, None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            self.devices, self.adapter = {}, None
            return
        self.devices = {}
        for path, ifaces in res.unpack()[0].items():
            if "org.bluez.Adapter1" in ifaces and self.adapter is None:
                self.adapter = path
                self.discovering = bool(ifaces["org.bluez.Adapter1"].get("Discovering"))
            if "org.bluez.Device1" in ifaces:
                self.devices[path] = dict(ifaces["org.bluez.Device1"])
        self.on_change()

    def _on_objects(self, _c, _s, _p, _i, signal, params, _u):
        if signal == "InterfacesAdded":
            path, ifaces = params.unpack()
            if "org.bluez.Device1" in ifaces:
                self.devices[path] = dict(ifaces["org.bluez.Device1"])
                self.on_change()
        elif signal == "InterfacesRemoved":
            path, ifaces = params.unpack()
            if "org.bluez.Device1" in ifaces and self.devices.pop(path, None) is not None:
                self.on_change()

    def _on_props(self, _c, _s, path, _i, _sig, params, _u):
        iface, changed, _inv = params.unpack()
        if iface == "org.bluez.Device1" and path in self.devices:
            self.devices[path].update(changed)
            self.on_change()
        elif iface == "org.bluez.Adapter1" and "Discovering" in changed:
            self.discovering = bool(changed["Discovering"])
            self.on_change()

    def list(self, audio_only=True):
        out = []
        for path, d in self.devices.items():
            icon = d.get("Icon", "")
            if audio_only and not (icon.startswith("audio") or d.get("Paired")):
                continue
            if not d.get("Name") and not d.get("Paired"):
                continue
            out.append((path, d))
        out.sort(key=lambda x: (not x[1].get("Connected"), not x[1].get("Paired"),
                                -(x[1].get("RSSI") or -999), x[1].get("Alias", "")))
        return out

    # --- actions ---

    def _call(self, path, iface, method, args, done, timeout=30000):
        def cb(bus, res):
            try:
                bus.call_finish(res)
                done(None)
            except GLib.Error as e:
                done(_err(e))
        self.bus.call(BUS, path, iface, method, args, None, Gio.DBusCallFlags.NONE, timeout, None, cb)

    def _set_prop(self, path, iface, name, value):
        self.bus.call(BUS, path, "org.freedesktop.DBus.Properties", "Set",
                      GLib.Variant("(ssv)", (iface, name, value)), None, Gio.DBusCallFlags.NONE, 5000,
                      None, None)

    def scan(self, on, done=lambda e: None):
        if not self.adapter:
            return done("no hay adaptador Bluetooth")
        if on:
            self._call(self.adapter, "org.bluez.Adapter1", "SetDiscoveryFilter",
                       GLib.Variant("(a{sv})", ({"Transport": GLib.Variant("s", "auto")},)), lambda e: None)
        self._call(self.adapter, "org.bluez.Adapter1", "StartDiscovery" if on else "StopDiscovery", None,
                   lambda e: done(None if e and ("InProgress" in e or "Not" in e) else e))

    def connect(self, path, done):
        self._call(path, "org.bluez.Device1", "Connect", None, done)

    def disconnect(self, path, done):
        self._call(path, "org.bluez.Device1", "Disconnect", None, done)

    def pair(self, path, done):
        def paired(e):
            if e:
                return done(e)
            self._set_prop(path, "org.bluez.Device1", "Trusted", GLib.Variant("b", True))
            self.connect(path, done)
        if self.discovering:
            self.scan(False)
        self._call(path, "org.bluez.Device1", "Pair", None, paired, timeout=60000)

    def forget(self, path, done):
        self._call(self.adapter, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (path,)), done)

    # --- pairing agent: headphones use "just works", so accept automatically ---

    def _register_agent(self):
        try:
            info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
            self._agent_id = self.bus.register_object(AGENT_PATH, info.interfaces[0], self._agent_call)
            self.bus.call_sync(BUS, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                               GLib.Variant("(os)", (AGENT_PATH, "NoInputNoOutput")), None,
                               Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as e:
            print("agente Bluetooth no registrado:", e.message)

    def _agent_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method == "RequestPinCode":
            invocation.return_value(GLib.Variant("(s)", ("0000",)))
        elif method == "RequestPasskey":
            invocation.return_value(GLib.Variant("(u)", (0,)))
        else:
            invocation.return_value(None)
