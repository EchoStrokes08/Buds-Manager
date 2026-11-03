"""Talk to the earbuds through the background daemon when it holds the control channel,
or directly otherwise. Both expose the same interface the `device` module uses."""
import contextlib
import json
import os
import socket

from . import protocol as p

SOCK_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "redmi-buds.sock")


def _msg_to_json(m):
    if m is None:
        return None
    return {"type": m.type, "opcode": m.opcode, "seq": m.seq, "payload": m.payload.hex(), "status": m.status}


def _msg_from_json(d):
    if d is None:
        return None
    return p.Message(d["type"], d["opcode"], d["seq"], bytes.fromhex(d["payload"]), d["status"])


def call(req, timeout=30):
    """One JSON request/response to the daemon. Raises OSError if it is not running."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK_PATH)
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("el servicio cerró la conexión")
            buf += chunk
    finally:
        s.close()
    resp = json.loads(buf)
    if resp.get("error"):
        raise p.BudsError(resp["error"])
    return resp


def daemon_status():
    """None if the daemon is not running, else {"holding": bool, "mac": str|None, "reason": str|None}."""
    try:
        return call({"op": "status"}, timeout=3)
    except (OSError, ValueError, p.BudsError):
        return None


class RemoteClient:
    """Same surface as protocol.Client, executed by the daemon over its held session."""

    def __init__(self, mac):
        self.mac = mac
        self.notifications = []
        self._since = call({"op": "status"})["notif_count"]

    def request_many(self, items, timeout=4.0):
        resp = call({"op": "request_many", "items": [[op, pl.hex()] for op, pl in items], "timeout": timeout})
        return [_msg_from_json(m) for m in resp["messages"]]

    def request(self, opcode, payload=b"", timeout=3.0):
        return self.request_many([(opcode, payload)], timeout)[0]

    def wait_notifications(self, seconds):
        resp = call({"op": "notifications", "since": self._since, "wait": seconds}, timeout=seconds + 10)
        self._since = resp["count"]
        self.notifications.extend(_msg_from_json(m) for m in resp["messages"])
        return self.notifications


@contextlib.contextmanager
def session(mac, debug=False):
    """Yields a client: the daemon's session if it holds the channel, a direct one otherwise."""
    st = daemon_status()
    if st and st.get("holding") and (st.get("mac") or "").upper() == mac.upper():
        yield RemoteClient(mac)
        return
    if st and st.get("running"):
        # The daemon is trying to grab the channel; a direct connection would fight it.
        raise p.BudsError(st.get("reason_text") or "el canal de control está ocupado")
    with p.Client(mac, debug=debug) as c:
        yield c
