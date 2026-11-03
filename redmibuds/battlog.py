"""Battery history: one CSV row whenever a level or the charging state changes.

~/.local/state/redmi-buds/battery.csv  — time,left,right,case,left_charging,right_charging,case_charging
(empty cell = unknown). Used for the chart in Buds Manager, the time-left estimate (tray tooltip, app) and the
long-term discharge rate (to notice battery wear over the months).
"""
import csv
import os
import time

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "redmi-buds")
PATH = os.path.join(STATE_DIR, "battery.csv")
FIELDS = ["time", "left", "right", "case", "left_charging", "right_charging", "case_charging"]
KEEP = 400 * 24 * 3600          # a bit over a year, enough to compare months
MIN_SPAN = 20 * 60              # a discharge estimate needs at least this much history…
MIN_DROP = 5                    # …and this many points of drop


def _row(batt):
    row = {"time": int(time.time())}
    for side in ("left", "right", "case"):
        b = (batt or {}).get(side)
        row[side] = b["level"] if b else ""
        row[f"{side}_charging"] = int(bool(b and b.get("charging"))) if b else ""
    return row


class BatteryLog:
    def __init__(self, path=PATH):
        self.path = path
        self.last = None

    def record(self, batt):
        """Appends a row if anything changed since the last one. Returns True if written."""
        if not batt or not (batt.get("left") or batt.get("right")):
            return False
        row = _row(batt)
        key = {k: v for k, v in row.items() if k != "time"}
        if self.last is None:
            rows = load(self.path, since=0, limit_tail=1)
            if rows:
                self.last = {k: rows[-1].get(k) for k in key}
        if key == self.last:
            return False
        self.last = key
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        new = not os.path.exists(self.path)
        with open(self.path, "a", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        return True


def _num(v):
    return int(v) if v not in ("", None) else None


def load(path=PATH, since=None, limit_tail=None):
    """Rows (dicts with ints / None) newer than `since` (default: last 48 h)."""
    if since is None:
        since = time.time() - 48 * 3600
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    if limit_tail:
        rows = rows[-limit_tail:]
    out = []
    for r in rows:
        try:
            t = int(r["time"])
        except (KeyError, ValueError):
            continue
        if t >= since:
            out.append({"time": t, **{k: _num(r.get(k)) for k in FIELDS[1:]}})
    return out


def _segments(rows, side):
    """Discharge runs of one earbud: consecutive rows while not charging."""
    seg = []
    for r in rows:
        lvl, ch = r.get(side), r.get(f"{side}_charging")
        if lvl is None or ch:
            if len(seg) > 1:
                yield seg
            seg = []
            continue
        if seg and lvl > seg[-1][1]:       # went up without a charging flag: new run
            if len(seg) > 1:
                yield seg
            seg = []
        seg.append((r["time"], lvl))
    if len(seg) > 1:
        yield seg


def _rate(seg):
    """% per hour over a discharge run, or None if it is too short to tell."""
    (t0, l0), (t1, l1) = seg[0], seg[-1]
    if t1 - t0 < MIN_SPAN or l0 - l1 < MIN_DROP:
        return None
    return (l0 - l1) / ((t1 - t0) / 3600)


def estimate(rows=None, now=None):
    """{"hours": float, "rate": %/h} for the earbud that will run out first, or None.
    Uses the current discharge run (falls back to the average rate of recent runs)."""
    rows = rows if rows is not None else load(since=time.time() - 14 * 24 * 3600)
    now = now or time.time()
    best = None
    for side in ("left", "right"):
        segs = list(_segments(rows, side))
        if not segs:
            continue
        cur = segs[-1]
        last_row = rows[-1] if rows else {}
        if last_row.get(f"{side}_charging") or now - cur[-1][0] > 6 * 3600:
            continue                       # charging now, or no recent discharge
        rate = _rate(cur)
        if rate is None:
            past = [r for r in (_rate(s) for s in segs[:-1]) if r]
            rate = sum(past[-5:]) / len(past[-5:]) if past else None
        if not rate:
            continue
        hours = cur[-1][1] / rate
        if best is None or hours < best["hours"]:
            best = {"hours": hours, "rate": rate, "side": side}
    return best


def monthly_rates(rows=None):
    """[(\"2026-11\", %/h), …] average discharge rate per month (both earbuds), oldest first."""
    rows = rows if rows is not None else load(since=0)
    by_month = {}
    for side in ("left", "right"):
        for seg in _segments(rows, side):
            r = _rate(seg)
            if r:
                by_month.setdefault(time.strftime("%Y-%m", time.localtime(seg[0][0])), []).append(r)
    return [(m, sum(v) / len(v)) for m, v in sorted(by_month.items())]


def fmt_hours(h):
    m = int(round(h * 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def prune(path=PATH):
    rows = load(path, since=time.time() - KEEP)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in FIELDS})
    os.replace(tmp, path)
