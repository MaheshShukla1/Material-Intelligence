"""Progress: turn ticks into percentages and used quantities.

The tracker records, per service, a grid of floor x activity x room ticks
(✓ done / ~ partial / ✗ pending). This module reads that grid and rolls it up
to the numbers Site Progress shows — activity%, room%, floor%, project% — and,
crucially, converts progress into *used quantity* per BOQ item:

    used(item) = boq_qty_per_room  x  Σ over rooms of that item's fraction

That used-qty is the bridge into Material Intelligence: it is the demand signal
that, together with the register's own consumption rate, answers "will the
stock on hand finish the planned work, and if not, when does it run short".

Nothing here touches the forecast engine. It reads the tracker and the parsed
BOQ, and produces plain frames the API can serve and the UI can render live as
the engineer drags a slider (a slider just overrides one room x activity
fraction; every rollup below recomputes from the same store).
"""
import re

import pandas as pd

try:
    from . import activity as _activity
except ImportError:
    import activity as _activity


# Tick -> completion fraction. Partial counts as half; anything else is 0.
TICK_FRAC = {"✓": 1.0, "√": 1.0, "~": 0.5, "-": 0.5,
             "✗": 0.0, "x": 0.0, "X": 0.0, "○": 0.0, "": 0.0}
_TICKS = set(TICK_FRAC) - {""}


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def _frac(tick):
    return TICK_FRAC.get(_s(tick), 0.0)


# --------------------------------------------------------------------------
# Parse the tick grid into one tidy long frame.
# --------------------------------------------------------------------------
_ROOMDETAIL_RE = re.compile(r"ROOM\s*DETAIL", re.I)


def parse_progress(path):
    """Return a tidy DataFrame: service, floor, activity, room, tick, frac.

    Robust to the repeating-block layout: each block starts with a header row
    ('Activity' in col 1 + room labels across); the floor label sits in col 0
    of the block's first activity row, so it is carried down until the next
    floor appears."""
    xl = pd.ExcelFile(path)
    rows = []
    for sh in xl.sheet_names:
        if not _ROOMDETAIL_RE.search(sh):
            continue
        service = _activity._svc(sh)
        raw = xl.parse(sheet_name=sh, header=None, dtype=object)
        nrows, ncols = raw.shape
        room_cols, floor = {}, None
        for r in range(nrows):
            row = [_s(raw.iat[r, c]) for c in range(ncols)]
            if len(row) > 1 and row[1].upper() == "ACTIVITY":
                # (re)learn which columns are rooms for the blocks that follow
                room_cols = {c: row[c] for c in range(2, ncols)
                             if row[c] and row[c] not in _TICKS
                             and not row[c].startswith("✓")
                             and row[c] not in ("✓", "~", "✗")}
                floor = None
                continue
            c0, act = (row[0] if row else ""), (row[1] if len(row) > 1 else "")
            if c0 and re.search(r"\bFLOOR\b|\bBASEMENT\b|\bWING\b|\bLEVEL\b", c0, re.I) \
                    and c0.upper() not in ("FLOOR", "WING", "LEVEL", "BASEMENT") \
                    and "TICK" not in c0.upper() and "PROJECT" not in c0.upper():
                floor = c0
            if not act or act.upper() == "ACTIVITY":
                continue
            if not any(row[c] in _TICKS for c in room_cols):
                continue                        # not a real activity row
            for c, room in room_cols.items():
                tick = row[c] if c < len(row) else ""
                if tick not in _TICKS:
                    continue
                rows.append({"service": service, "floor": floor or "?",
                             "activity": act, "room": room,
                             "tick": tick, "frac": _frac(tick)})
    return pd.DataFrame(rows, columns=["service", "floor", "activity",
                                       "room", "tick", "frac"])


# --------------------------------------------------------------------------
# Rollups. Each is a simple mean of fractions over the relevant cells.
# --------------------------------------------------------------------------
def _pct(series):
    return round(100.0 * float(series.mean()), 1) if len(series) else 0.0


def rollup(prog, service=None):
    """Nested percentages for one service (or all). Returns a dict:
        { overall, by_activity, by_floor, by_room, by_floor_activity } ."""
    df = prog if service is None else prog[prog.service == service]
    if len(df) == 0:
        return {"overall": 0.0, "by_activity": {}, "by_floor": {},
                "by_room": {}, "by_floor_activity": {}}
    return {
        "overall": _pct(df.frac),
        "by_activity": {a: _pct(g.frac) for a, g in df.groupby("activity")},
        "by_floor": {f: _pct(g.frac) for f, g in df.groupby("floor")},
        "by_room": {f"{f} · {rm}": _pct(g.frac)
                    for (f, rm), g in df.groupby(["floor", "room"])},
        "by_floor_activity": {f"{f} · {a}": _pct(g.frac)
                              for (f, a), g in df.groupby(["floor", "activity"])},
    }


def project_summary(prog):
    """One-line-per-service headline plus a project number, for the dashboard."""
    out = {"by_service": {},
           "rooms": int(prog[["floor", "room"]].drop_duplicates().shape[0])
           if len(prog) else 0}
    for svc, g in prog.groupby("service"):
        out["by_service"][svc] = {
            "overall": _pct(g.frac),
            "activities": int(g.activity.nunique()),
            "done_cells": int((g.frac >= 1.0).sum()),
            "partial_cells": int(((g.frac > 0) & (g.frac < 1)).sum()),
            "pending_cells": int((g.frac <= 0).sum()),
        }
    out["overall"] = _pct(prog.frac) if len(prog) else 0.0
    return out


# --------------------------------------------------------------------------
# The consumption bridge: progress x BOQ -> used / remaining per item.
# --------------------------------------------------------------------------
def _room_activity_frac(prog, service):
    """{(floor,room): {activity: frac}} for one service."""
    d = {}
    sub = prog[prog.service == service]
    for _, r in sub.iterrows():
        d.setdefault((r.floor, r.room), {})[r.activity] = r.frac
    return d


def used_by_item(prog, mapping, boq_items, service):
    """Per BOQ item: planned_total, used, remaining, and progress fraction.

    used(item) = boq_qty x Σ over rooms of the item's fraction in that room.
    An item's fraction in a room is the MAX over the activities it is mapped to
    (so an item shared by two activities is not double-counted). Items mapped to
    no activity fall back to 0 used (progress unknown) but still show planned.
    Returns a DataFrame indexed by item_code."""
    ra = _room_activity_frac(prog, service)
    rooms = list(ra.keys())
    n_rooms = len(rooms)

    # invert mapping: item_code -> set(activities)
    item_acts = {}
    for act in mapping.activities(service):
        for code in mapping.get(service, act):
            item_acts.setdefault(str(code), set()).add(act)

    recs = []
    for _, it in boq_items.iterrows():
        code = _s(it.get("item_code"))
        qty = it.get("qty")
        qty = 0.0 if qty is None or pd.isna(qty) else float(qty)
        planned_total = qty * n_rooms
        acts = item_acts.get(code, set())
        if acts and n_rooms:
            frac_sum = 0.0
            for (fl, rm), amap in ra.items():
                fr = max((amap.get(a, 0.0) for a in acts), default=0.0)
                frac_sum += fr
            used = qty * frac_sum
            progress = frac_sum / n_rooms
        else:
            used, progress = 0.0, 0.0
        recs.append({
            "item_code": code,
            "description": it.get("description"),
            "unit": it.get("unit"),
            "qty_per_room": qty,
            "planned_total": round(planned_total, 3),
            "used": round(used, 3),
            "remaining": round(max(planned_total - used, 0.0), 3),
            "progress_pct": round(100.0 * progress, 1),
            "mapped": bool(acts),
        })
    df = pd.DataFrame(recs)
    return df


# --------------------------------------------------------------------------
# Editable store — what a slider writes to. Wraps the tidy frame; every rollup
# above recomputes from it, so a single set() updates % and ₹ everywhere.
# --------------------------------------------------------------------------
class ProgressStore:
    def __init__(self, df=None):
        cols = ["service", "floor", "activity", "room", "tick", "frac"]
        self.df = df if df is not None else pd.DataFrame(columns=cols)

    @classmethod
    def from_tracker(cls, path):
        return cls(parse_progress(path))

    def set(self, service, floor, room, activity, frac):
        """Override one cell (a slider drag). frac in [0,1]."""
        frac = max(0.0, min(1.0, float(frac)))
        tick = "✓" if frac >= 1 else ("~" if frac > 0 else "✗")
        m = ((self.df.service == service) & (self.df.floor == floor)
             & (self.df.room == room) & (self.df.activity == activity))
        if m.any():
            self.df.loc[m, ["tick", "frac"]] = [tick, frac]
        else:
            self.df.loc[len(self.df)] = [service, floor, activity, room, tick, frac]
        return self

    def to_parquet(self, path):
        self.df.to_parquet(path)

    @classmethod
    def from_parquet(cls, path):
        return cls(pd.read_parquet(path))
