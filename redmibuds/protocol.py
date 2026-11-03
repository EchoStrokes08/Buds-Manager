"""Xiaomi "MIWEAR" earbuds protocol over RFCOMM.

Port of Gadgetbridge's RedmiBudsProtocol (GPL-3.0):
https://codeberg.org/Freeyourgadget/Gadgetbridge
"""
import errno
import fcntl
import os
import socket
import struct
import time

HEADER = b"\xfe\xdc\xba"
TRAILER = 0xEF

# Message types
PHONE_REQUEST = 0xC4
RESPONSE = 0x04
EARBUDS_REQUEST = 0xC0
EARBUDS_NOTIFY = 0xC7

# Opcodes
GET_DEVICE_INFO = 0x02
ANC = 0x08
GET_DEVICE_RUN_INFO = 0x09
REPORT_STATUS = 0x0E
AUTH_CHALLENGE = 0x50
AUTH_CONFIRM = 0x51
SET_CONFIG = 0xF2
GET_CONFIG = 0xF3
NOTIFY_CONFIG = 0xF4

# Config ids (GET_CONFIG / SET_CONFIG)
CFG_GESTURES = 0x02
CFG_AUTO_ANSWER = 0x03
CFG_DOUBLE_CONNECTION = 0x04
CFG_EAR_DETECTION = 0x06
CFG_EQ_PRESET = 0x07
CFG_FIND = 0x09
CFG_FIT_TEST = 0x05     # write 01 to start; result arrives as NOTIFY_CONFIG of CFG_FIT_RESULT
CFG_FIT_RESULT = 0x06
CFG_WEAR = 0x0C         # bit0/1: left/right in case, bit2/3: left/right in ear
CFG_LONG_GESTURES = 0x0A
CFG_EFFECT_STRENGTH = 0x0B
CFG_ADAPTIVE_ANC = 0x25
CFG_SERIAL = 0x27
CFG_SPATIAL = 0x1D        # write: 0x02 | on(0x01) | head tracking(0x08)
CFG_SPATIAL_STATE = 0x1E  # read back of CFG_SPATIAL
CFG_COMMUTE = 0x67        # "Immersive commute": 0 off, 1 airplane, 2 transit, 3 train
CFG_SPATIAL_ENGINE = 0x68 # 0 Xiaomi dimensional audio, 1 Dolby Audio
CFG_ADAPTIVE_SOUND = 0x29
CFG_EQ_CURVE = 0x37
CFG_CUSTOMIZED_ANC = 0x3B

ALL_CONFIGS = [CFG_GESTURES, CFG_AUTO_ANSWER, CFG_DOUBLE_CONNECTION, CFG_EAR_DETECTION,
               CFG_EQ_PRESET, CFG_LONG_GESTURES, CFG_EFFECT_STRENGTH, CFG_ADAPTIVE_ANC,
               CFG_ADAPTIVE_SOUND, CFG_EQ_CURVE, CFG_CUSTOMIZED_ANC, CFG_SERIAL, CFG_WEAR,
               CFG_FIT_RESULT, CFG_SPATIAL_STATE, CFG_COMMUTE, CFG_SPATIAL_ENGINE]

DEFAULT_CHANNEL = 28


def _is_request(msg_type):
    return bool(msg_type & 0x40)


def encode(msg_type, opcode, seq, payload=b""):
    body = (b"" if _is_request(msg_type) else b"\x00") + bytes([seq]) + payload
    return HEADER + bytes([msg_type, opcode]) + struct.pack(">H", len(body)) + body + bytes([TRAILER])


class Message:
    def __init__(self, msg_type, opcode, seq, payload, status=None):
        self.type, self.opcode, self.seq, self.payload, self.status = msg_type, opcode, seq, payload, status

    def __repr__(self):
        return f"Message(type={self.type:#04x}, op={self.opcode:#04x}, seq={self.seq}, status={self.status}, payload={self.payload.hex()})"


def split_frames(buf):
    """Returns (messages, remaining_bytes)."""
    out = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            return out, b""
        if len(buf) - i < 7:
            return out, buf[i:]
        length = struct.unpack(">H", buf[i + 5:i + 7])[0]
        end = i + 7 + length + 1
        if len(buf) < end:
            return out, buf[i:]
        frame, buf = buf[i:end], buf[end:]
        msg_type, opcode = frame[3], frame[4]
        if _is_request(msg_type):
            out.append(Message(msg_type, opcode, frame[7], frame[8:-1]))
        else:
            out.append(Message(msg_type, opcode, frame[8], frame[9:-1], status=frame[7]))


# ---- Authentication: Bluetooth SAFER+ variant, 128-bit key, 8 rounds ----

_SEQ = bytes([0x11, 0x22, 0x33, 0x33, 0x22, 0x11, 0x11, 0x22, 0x33, 0x33, 0x22, 0x11, 0x11, 0x22, 0x33, 0x33])
_COEF = [
    [2, 1, 1, 1, 4, 2, 1, 1, 2, 2, 4, 2, 4, 4, 16, 8], [2, 1, 1, 1, 4, 2, 1, 1, 1, 1, 2, 1, 2, 2, 8, 4],
    [1, 1, 4, 2, 2, 2, 4, 2, 16, 8, 4, 4, 2, 1, 1, 1], [1, 1, 4, 2, 1, 1, 2, 1, 8, 4, 2, 2, 2, 1, 1, 1],
    [16, 8, 2, 2, 4, 2, 4, 4, 1, 1, 4, 2, 1, 1, 2, 1], [8, 4, 1, 1, 2, 1, 2, 2, 1, 1, 4, 2, 1, 1, 2, 1],
    [2, 2, 4, 2, 4, 4, 16, 8, 2, 1, 1, 1, 4, 2, 1, 1], [1, 1, 2, 1, 2, 2, 8, 4, 2, 1, 1, 1, 4, 2, 1, 1],
    [4, 2, 4, 4, 16, 8, 2, 2, 1, 1, 2, 1, 1, 1, 4, 2], [2, 1, 2, 2, 8, 4, 1, 1, 1, 1, 2, 1, 1, 1, 4, 2],
    [4, 4, 16, 8, 1, 1, 2, 1, 4, 2, 1, 1, 4, 2, 2, 2], [2, 2, 8, 4, 1, 1, 2, 1, 4, 2, 1, 1, 2, 1, 1, 1],
    [1, 1, 2, 1, 1, 1, 4, 2, 4, 4, 16, 8, 2, 2, 4, 2], [1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 8, 4, 1, 1, 2, 1],
    [4, 2, 1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 16, 8, 4, 4], [4, 2, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 8, 4, 2, 2],
]
_PATTERN = 0x9999
_BIAS = [[(lambda v: 0 if v == 256 else v)(pow(45, pow(45, 17 * (i + 2) + (j + 1), 257), 257))
          for j in range(16)] for i in range(16)]
_EXP = [0 if i == 128 else pow(45, i, 257) for i in range(256)]
_LOG = [0] * 256
_LOG[0] = 128
for _i in range(1, 256):
    _v = pow(45, _i, 257)
    if _v != 256:
        _LOG[_v] = _i


def auth_response(challenge):
    key = bytearray(challenge)
    key[15] ^= 6
    keys = [list(key)]
    reg = list(key) + [0]
    for b in key:
        reg[16] ^= b
    for k in range(1, 17):
        reg = [((r >> 5) | (r << 3)) & 0xFF for r in reg]
        keys.append([(reg[(k + i) % 17] + _BIAS[k - 1][i]) & 0xFF for i in range(16)])

    xor_pos = [bool((1 << i) & _PATTERN) for i in range(16)]
    c = list(_SEQ)
    for rnd in range(8):
        if rnd == 2:
            c = [c[i] ^ _SEQ[i] if xor_pos[i] else (c[i] + _SEQ[i]) & 0xFF for i in range(16)]
        k1, k2 = keys[2 * rnd], keys[2 * rnd + 1]
        c = [c[i] ^ k1[i] if xor_pos[i] else (c[i] + k1[i]) & 0xFF for i in range(16)]
        c = [_EXP[c[i]] if xor_pos[i] else _LOG[c[i]] for i in range(16)]
        c = [(k2[i] + c[i]) & 0xFF if xor_pos[i] else k2[i] ^ c[i] for i in range(16)]
        c = [sum(_COEF[i][j] * c[j] for j in range(16)) & 0xFF for i in range(16)]
    k = keys[16]
    return bytes(k[i] ^ c[i] if xor_pos[i] else (k[i] + c[i]) & 0xFF for i in range(16))


# ---- Client ----

class BudsError(Exception):
    pass


class Client:
    """One authenticated RFCOMM session. Use as a context manager."""

    def __init__(self, mac, channel=DEFAULT_CHANNEL, debug=False):
        self.mac, self.channel, self.debug = mac, channel, debug
        self.sock = None
        self.seq = 0
        self.buf = b""
        self.notifications = []
        self._lock = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def _log(self, *args):
        if self.debug:
            print("[debug]", *args)

    def open(self, attempts=4, lock_timeout=20):
        # The earbuds accept a single client on the channel; serialize our own processes.
        lock_path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "redmi-buds.lock")
        self._lock = open(lock_path, "w")
        deadline = time.time() + lock_timeout
        while True:
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    self._lock.close()
                    self._lock = None
                    raise BudsError("otro proceso de Buds Manager está usando los audífonos")
                time.sleep(0.2)

        last = None
        for attempt in range(attempts):
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            s.settimeout(8)
            try:
                s.connect((self.mac, self.channel))
                self.sock = s
                break
            except OSError as e:
                s.close()
                last = e
                if e.errno in (errno.EHOSTDOWN, errno.EHOSTUNREACH) or attempt == attempts - 1:
                    break
                time.sleep(0.6 * (attempt + 1))
        if self.sock is None:
            self.close()
            raise BudsError(f"no se pudo conectar a {self.mac}: {last}")
        self._authenticate()

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        if self._lock:
            self._lock.close()
            self._lock = None

    def _next_seq(self):
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return s

    def send(self, msg_type, opcode, payload=b"", seq=None):
        if seq is None:
            seq = self._next_seq()
        data = encode(msg_type, opcode, seq, payload)
        self._log(">>", data.hex())
        self.sock.sendall(data)
        return seq

    def _recv_messages(self, timeout):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return []
        if not data:
            raise BudsError("los auriculares cerraron la conexión")
        self.buf += data
        msgs, self.buf = split_frames(self.buf)
        for m in msgs:
            self._log("<<", m)
        return msgs

    def _handle_unsolicited(self, m):
        """Acknowledge status/notify messages pushed by the earbuds."""
        if m.opcode in (REPORT_STATUS, NOTIFY_CONFIG) and _is_request(m.type):
            self.notifications.append(m)
            self.send(RESPONSE, m.opcode, b"", seq=m.seq)
            return True
        return False

    def _authenticate(self):
        self.send(PHONE_REQUEST, AUTH_CHALLENGE, b"\x01" + os.urandom(16))
        deadline = time.time() + 6
        while time.time() < deadline:
            for m in self._recv_messages(0.5):
                if m.opcode == AUTH_CHALLENGE and m.type == RESPONSE:
                    self.send(PHONE_REQUEST, AUTH_CONFIRM, b"\x01\x00")
                elif m.opcode == AUTH_CHALLENGE:
                    self.send(RESPONSE, AUTH_CHALLENGE, b"\x01" + auth_response(m.payload[1:17]), seq=m.seq)
                elif m.opcode == AUTH_CONFIRM and m.type != RESPONSE:
                    self.send(RESPONSE, AUTH_CONFIRM, b"\x01", seq=m.seq)
                    return
                else:
                    self._handle_unsolicited(m)
        raise BudsError("falló la autenticación con los auriculares")

    def request(self, opcode, payload=b"", timeout=3.0):
        """Send a phone request and return the matching response Message."""
        return self.request_many([(opcode, payload)], timeout)[0]

    def request_many(self, items, timeout=4.0):
        pending = {}
        order = []
        for opcode, payload in items:
            seq = self.send(PHONE_REQUEST, opcode, payload)
            pending[(opcode, seq)] = None
            order.append((opcode, seq))
        deadline = time.time() + timeout
        while time.time() < deadline and any(v is None for v in pending.values()):
            for m in self._recv_messages(0.3):
                if self._handle_unsolicited(m):
                    continue
                key = (m.opcode, m.seq)
                if m.type == RESPONSE and key in pending:
                    pending[key] = m
        return [pending[k] for k in order]

    def wait_notifications(self, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            for m in self._recv_messages(0.3):
                self._handle_unsolicited(m)
        return self.notifications


# ---- Channel 17: Xiaomi "basic info" service (no auth, several clients allowed) ----

BASIC_CHANNEL = 17


def read_basic(mac, timeout=2.5):
    """Model id, MAC and battery that the earbuds push on RFCOMM 17 to whoever connects.
    Works even while another device holds the control channel. Returns {id: bytes}."""
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(6)
    out, buf = {}, b""
    try:
        s.connect((mac, BASIC_CHANNEL))
        deadline = time.time() + timeout
        s.settimeout(0.3)
        while time.time() < deadline and 0x03 not in out:
            try:
                data = s.recv(1024)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            while len(buf) >= 4 and buf[0] == 0x03:
                n = struct.unpack(">H", buf[2:4])[0]
                if len(buf) < 4 + n:
                    break
                out[buf[1]] = buf[4:4 + n]
                buf = buf[4 + n:]
            if buf and buf[0] != 0x03:
                buf = b""
    except OSError as e:
        raise BudsError(f"no se pudo leer la información básica: {e}")
    finally:
        s.close()
    return out
