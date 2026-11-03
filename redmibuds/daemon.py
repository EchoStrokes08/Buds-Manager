"""Background service that keeps the earbuds' control channel (RFCOMM 28) for this PC.

The earbuds accept a single controller. A Xiaomi phone grabs it as soon as it connects and never lets
go, so whoever connects first wins. This daemon connects as soon as the earbuds reach the PC, retries
quickly while someone else holds the channel, keeps the session alive and serves every local client
(CLI, Waybar, Buds Manager) over a Unix socket. Pushed notifications (battery, mode changed with a
gesture…) refresh the cached state and Waybar immediately.
"""
import json
import os
import socket
import socketserver
import subprocess
import threading
import time

from . import device as d
from . import protocol as p
from .automations import Automations
from .gestures import GestureListener
from .remote import SOCK_PATH, _msg_to_json

RETRY_BUSY = 1.0       # seconds between attempts while the phone holds the channel
RETRY_ABSENT = 3.0     # seconds between checks while the earbuds are not connected to the PC
KEEPALIVE = 60         # re-read state at least this often (also detects a dead link)

REASONS = {
    "absent": "Los audífonos no están conectados a esta PC",
    "busy": "El celular tiene el control; lo tomaré en cuanto lo suelte",
    "error": "No se pudo abrir el canal de control",
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Holder:
    def __init__(self):
        self.client = None
        self.mac = None
        self.lock = threading.RLock()       # guards every use of self.client
        self.cond = threading.Condition()   # signals new notifications / state
        self.notifs = []                    # (index, Message)
        self.count = 0
        self.state = None
        self.reason = "absent"
        self._refresh = threading.Event()
        self._late_refresh = 0
        self.waiting = 0
        self.basic = None                   # battery from channel 17 while the phone has control
        self.auto = Automations()
        from .battlog import BatteryLog, estimate, prune
        self.battlog, self._estimate_fn = BatteryLog(), estimate
        self.estimate = None
        try:
            prune()
        except OSError:
            pass
        self._update_estimate()
        self._basic_at = 0                    # client requests queued for the lock; the pump yields to them

    # --- connection management ---

    def run(self):
        from .cli import find_mac
        while True:
            mac = find_mac()
            if not mac:
                self._set_reason("absent")
                time.sleep(RETRY_ABSENT)
                continue
            c = p.Client(mac)
            try:
                c.open(attempts=1, lock_timeout=5)
            except p.BudsError as e:
                busy = "refused" in str(e).lower() or "111" in str(e)
                self._set_reason("busy" if busy else "error")
                if busy and time.time() - self._basic_at > 60:
                    self._basic_at = time.time()
                    try:
                        basic = d.basic_battery(mac)
                    except p.BudsError:
                        basic = None
                    if basic != self.basic:
                        self.basic = basic
                        self._notify_waybar()
                    if basic:
                        self._record_battery(basic)
                        self.auto.on_state(mac, {"battery": basic})
                time.sleep(RETRY_BUSY if busy else RETRY_ABSENT)
                continue
            log("canal de control tomado", mac)
            with self.lock:
                self.client, self.mac = c, mac
            self.reason = None
            try:
                self._serve_session()
            except (OSError, p.BudsError) as e:
                log("sesión terminada:", e)
            finally:
                with self.lock:
                    self.client = None
                    c.close()
                self.state = None
                self.auto.on_state(mac, None)
                self._set_reason("absent")
                self._notify_waybar()

    def _record_battery(self, batt):
        try:
            if self.battlog.record(batt):
                self._update_estimate()
        except OSError as e:
            log("historial de batería:", e)

    def _update_estimate(self):
        try:
            self.estimate = self._estimate_fn()
        except Exception as e:  # noqa: BLE001
            log("estimación de batería:", e)
            self.estimate = None

    def _set_reason(self, reason):
        if reason != "busy":
            self.basic, self._basic_at = None, 0
        if reason != self.reason:
            log("estado:", REASONS.get(reason, reason))
            self.reason = reason
            self._notify_waybar()

    def _serve_session(self):
        self._read_state()
        last_read = time.time()
        while True:
            if self.waiting:
                time.sleep(0.02)
                continue
            with self.lock:
                self.client.wait_notifications(0.3)
                new = self.client.notifications[:]
                self.client.notifications.clear()
            if new:
                with self.cond:
                    for m in new:
                        self.notifs.append((self.count, m))
                        self.count += 1
                    self.notifs = self.notifs[-200:]
                    self.cond.notify_all()
                self._refresh.set()
            late = self._late_refresh and time.time() >= self._late_refresh
            if self._refresh.is_set() or late or time.time() - last_read > KEEPALIVE:
                self._refresh.clear()
                if late:
                    self._late_refresh = 0
                time.sleep(0.3)   # let a burst of notifications settle
                self._read_state()
                last_read = time.time()
            else:
                time.sleep(0.05)

    def _read_state(self):
        t0 = time.time()
        with self.lock:
            st = d.read_state(self.client)
            if not st.get("battery") and not st.get("mode"):
                # one lost burst happens now and then (e.g. while the phone streams); don't drop the session
                log("lectura sin respuesta; reintento")
                time.sleep(0.5)
                st = d.read_state(self.client)
        if time.time() - t0 > 1.5:
            missing = sorted({"info", "run_info", *[f"config_{c:02x}" for c in p.ALL_CONFIGS]} - set(st["raw"]))
            log(f"lectura lenta ({time.time() - t0:.1f}s), sin respuesta: {missing}")
        if not st.get("battery") and not st.get("mode"):
            raise p.BudsError("sin respuesta de los audífonos")
        changed = _public(st) != _public(self.state)
        self.state = st
        self._record_battery(st.get("battery"))
        self.auto.on_state(self.mac, st)
        if changed:
            with self.cond:
                self.cond.notify_all()
            self._notify_waybar()

    @staticmethod
    def _notify_waybar():
        subprocess.run(["pkill", "-RTMIN+8", "waybar"], stderr=subprocess.DEVNULL)

    # --- requests from local clients ---

    def handle(self, req):
        op = req.get("op")
        if op == "status":
            g = getattr(self, "gestures", None)
            return {"running": True, "holding": self.client is not None, "mac": self.mac,
                    "gestures": {"device": g.device, "problem": g.problem} if g else None,
                    "reason": self.reason, "reason_text": REASONS.get(self.reason), "notif_count": self.count}
        if op == "sleep_timer":
            t = self.sleep
            if req.get("cancel"):
                t.cancel()
            elif req.get("minutes"):
                t.start(float(req["minutes"]))
            if req.get("cancel") or req.get("minutes"):
                with self.cond:
                    self.cond.notify_all()       # tray / app refresh right away
            return t.status()
        if op == "state":
            return {"sleep": self.sleep.status() if getattr(self, "sleep", None) else None,
                    "estimate": self.estimate,
                    "exposure": self.exposure.status() if getattr(self, "exposure", None) else None,
                    "state": self.state, "holding": self.client is not None, "mac": self.mac,
                    "reason": self.reason, "reason_text": REASONS.get(self.reason),
                    "battery": self.basic if self.reason == "busy" else None}
        if op == "wait_change":
            # Long poll used by the GUI: returns when state changes or after `wait` seconds.
            with self.cond:
                self.cond.wait(float(req.get("wait", 30)))
            return {"ok": True}
        self.waiting += 1
        try:
            self.lock.acquire()
        finally:
            self.waiting -= 1
        try:
            if self.client is None:
                return {"error": REASONS.get(self.reason) or "sin conexión"}
            if op == "request_many":
                items = [(op_, bytes.fromhex(pl)) for op_, pl in req["items"]]
                msgs = self.client.request_many(items, req.get("timeout", 4.0))
                if any(o in (p.SET_CONFIG, p.ANC) for o, _ in items):
                    self._refresh.set()
                    self._late_refresh = time.time() + 1.5   # the earbuds report some changes late
                return {"messages": [_msg_to_json(m) for m in msgs]}
        finally:
            self.lock.release()
        if op == "notifications":
            deadline = time.time() + float(req.get("wait", 0))
            since = int(req.get("since", 0))
            with self.cond:
                while time.time() < deadline and not any(i >= since for i, _ in self.notifs):
                    self.cond.wait(deadline - time.time())
                out = [m for i, m in self.notifs if i >= since]
                return {"messages": [_msg_to_json(m) for m in out], "count": self.count}
        return {"error": f"operación desconocida: {op}"}


def _public(st):
    if st is None:
        return None
    return {k: v for k, v in st.items() if k != "raw"}


def main():
    import faulthandler, signal, sys
    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    holder = Holder()
    holder.gestures = GestureListener()
    holder.gestures.start()
    from .calls import CallMic
    from .cli import find_mac
    holder.callmic = CallMic(lambda: holder.mac or find_mac())
    holder.callmic.start()

    def apply_profile(changes):
        with holder.lock:
            if holder.client is None:
                return
            if "mode" in changes:
                d.set_mode(holder.client, changes["mode"])
            if "eq" in changes:
                d.set_eq_preset(holder.client, changes["eq"])
        holder._refresh.set()

    def current_settings():
        st = holder.state
        if holder.client is None or not st:
            return None
        return {"mode": st.get("mode"), "eq": st.get("eq_preset")}

    from .sleeptimer import SleepTimer

    def set_mode(mode):
        with holder.lock:
            if holder.client is not None:
                d.set_mode(holder.client, mode)
        holder._refresh.set()
    holder.sleep = SleepTimer(set_mode)

    from .exposure import Exposure
    holder.exposure = Exposure(lambda: holder.mac or find_mac(),
                               lambda: (holder.state or {}).get("wear") if holder.client else None)
    holder.exposure.start()

    from .profiles import AppProfiles
    holder.profiles = AppProfiles(apply_profile, current_settings)
    holder.profiles.start()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self._handle()
            except (BrokenPipeError, ConnectionResetError):
                pass    # the client (CLI, Waybar, GUI) gave up before the answer; nothing to do

        def _handle(self):
            line = self.rfile.readline()
            if not line:
                return
            try:
                resp = holder.handle(json.loads(line))
            except (p.BudsError, OSError) as e:
                resp = {"error": str(e)}
            except Exception as e:  # noqa: BLE001 - never kill the daemon for one bad request
                resp = {"error": f"error interno: {e!r}"}
            self.wfile.write(json.dumps(resp).encode() + b"\n")

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            import sys
            log("error atendiendo a un cliente:", repr(sys.exc_info()[1]))

    # Refuse to start twice; remove a stale socket left by a crash.
    if os.path.exists(SOCK_PATH):
        try:
            s = socket.socket(socket.AF_UNIX)
            s.connect(SOCK_PATH)
            s.close()
            raise SystemExit("buds daemon ya está en marcha")
        except OSError:
            os.unlink(SOCK_PATH)

    srv = Server(SOCK_PATH, Handler)
    os.chmod(SOCK_PATH, 0o600)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("servicio iniciado en", SOCK_PATH)
    try:
        holder.run()
    finally:
        srv.server_close()
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
