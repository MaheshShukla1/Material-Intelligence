"""Measurements -- WHERE a metre-type quantity was laid, entry by entry (From, To, drum readings).

(Called a "run" inside the code and in the API paths; engineers and the bill call it a "measurement".)

Site Progress stores one number per item: how much is installed. For cable, pipe, tray and earthing that number
is the sum of many entries ("FACP-3 to Corridor Loop A, drum mark 1000 to 858 = 142 m"), and the client signs a
measurement sheet that lists them. An entry is that one line.

An entry belongs to ONE of three places, because a length of cable is not always inside one room:

    zone     one room / zone                     (key = the room id)
    area     a whole floor / level / wing, as one quantity ("Level 2 = 420 Rmt", the area-total of Site Progress)
    project  the whole project, not located      (a feeder, a riser, a main route; the item's overall quantity)

An entry ADDS to the quantity of its place (it is written through the same Site Progress paths as a typed
number, so every %, the DPR and the bill see it); it is never a second source of truth:

    quantity of a place (itemprog)  = what was recorded before entries + every active entry
    sheet lines for the place       = [recorded before From-To detail] + active entries, always adding up to that quantity

The three places never overlap on one item (see scope_table): measuring a zone is refused while the same item has
whole-project entries, a whole level is refused while one of its zones was recorded zone by zone, and so on. That is
what keeps "quantity = entries" true without guessing.

Nothing is erased. An entry is cancelled with a reason and stays on record (like a measurement-book entry that is
struck through and initialled). Pure functions only (no files, no FastAPI) so siteprogress.py / billing.py feed
them and the tests drive them directly.

Store (item_runs.json):  {service: {item_code: {key: [run, ...]}}}   key = room id | "~area:<area>" | "~project"
run = {"id", "from", "to", "start", "end", "qty", "remarks", "at", "cancelled", "reason"}
      start/end are the drum readings (None when the quantity was typed straight in); qty = |start - end| then.
"""
import re
import datetime as _dt
import uuid

from . import itemprog, locqty

AREA = "~area:"           # key of a whole-area entry list: "~area:Level 2"
PROJECT = "~project"      # key of the whole-project entry list

EPS = 5e-5
TOL = 0.01          # a zone quantity is stored as a rounded fraction of its plan; 1 cm of cable is not a missing run
_RUN_UNITS = {"mtr", "mtrs", "mts", "rmt", "rmtr", "rmtrs", "rm", "m", "mt", "metre", "metres", "meter", "meters"}


def is_run_unit(unit):
    """A length unit (Mtr, Mtrs, RMT, Rmtr, RM ...). 'Sq.Mtr' is an area, not a run, so it is not one."""
    return re.sub(r"[^a-z]", "", str(unit or "").lower()) in _RUN_UNITS


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("readings and length must be numbers")
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("readings and length must be numbers")
    return x


def clean_run(payload):
    """Validate one new run. Returns {"from","to","start","end","qty","remarks"}; ValueError with a plain message otherwise.
    With both readings the quantity is |start - end| (what the client's sheet calls Total); a typed quantity alone is also fine."""
    p = payload or {}
    frm, to = str(p.get("from") or "").strip(), str(p.get("to") or "").strip()
    if not frm or not to:
        raise ValueError("say where the measurement starts and ends (From and To)")
    if len(frm) > 80 or len(to) > 80:
        raise ValueError("From and To must be 80 characters or less")
    start, end, qty = _num(p.get("start")), _num(p.get("end")), _num(p.get("qty"))
    if (start is None) != (end is None):
        raise ValueError("give both drum readings (start and end), or neither and type the length")
    if start is not None:
        if start < 0 or end < 0:
            raise ValueError("drum readings cannot be negative")
        q = round(abs(start - end), 4)
    else:
        q = None if qty is None else round(qty, 4)
    if q is None:
        raise ValueError("enter the drum readings or the length")
    if q <= 0:
        raise ValueError("the measurement has no length: start and end are the same, or the length is 0")
    return {"from": frm, "to": to, "start": start, "end": end, "qty": q, "remarks": str(p.get("remarks") or "").strip()[:200]}


def new_run(clean, now=None):
    r = dict(clean)
    r.update({"id": uuid.uuid4().hex[:10], "at": (now or _dt.datetime.now()).isoformat(timespec="seconds"), "cancelled": False, "reason": ""})
    return r


def active(runs):
    return [r for r in (runs or []) if not r.get("cancelled")]


def active_total(runs):
    return round(sum(float(r.get("qty") or 0) for r in active(runs)), 4)


def view(runs, done):
    """What the Zones dialog shows for one zone: its runs, the sum of the active ones, and the part of the recorded
    quantity that has no run behind it (recorded before runs, or by a plain tick / typed number)."""
    tot = active_total(runs)
    other = round(float(done or 0) - tot, 4)
    return {"runs": list(runs or []), "active": tot, "done": round(float(done or 0), 4), "other": 0.0 if abs(other) <= TOL else other}


# ------------------------------------------------------------------ the three places
def area_key(area):
    return AREA + str(area)


def kind_of(key):
    """'project' | 'area' | 'zone' (a room id)."""
    key = str(key)
    return "project" if key == PROJECT else "area" if key.startswith(AREA) else "zone"


def key_quantities(split):
    """{key: quantity} and {key: structure position} of one item, from locqty.split(): what each place holds right now
    (zone-wise rooms, whole-area totals, and the rest that was recorded overall)."""
    q, o = {}, {}
    for rid, v in (split.get("rooms") or {}).items():
        q[str(rid)] = v
    for area, e in (split.get("pools") or {}).items():
        q[area_key(area)] = e["q"]
        o[area_key(area)] = e["o"]
    if (split.get("rest") or 0) > 0:
        q[PROJECT] = split["rest"]
    return q, o


def scope_table(appl, plan, node, pools, runs, loc, name, order, word="area"):
    """Every place an entry of this item can go, with the quantity it holds now and why it is closed when it is.

    appl/plan : the item's rooms and each room's plan (siteprogress._item_plan); node : its progress node; pools : its stored
    area totals; runs : {key: [run,...]}; loc/name/order : room -> area / name / structure position (location_info);
    word : the project's word for an area (floor / level / area).

    Returns a list in screen order -- the whole project, then the whole areas, then the zones:
      {"key","kind","label","group","rooms":[ids],"plan","done","allowed","why","writable", + view(runs, done)}
    `allowed` = a new entry (or a restored one) may be added; `writable` = an existing entry may be cancelled (it only needs the place
    itself to be in a state its quantity can be written back to). Closed places say why in plain words.

    The rules that keep entries and quantity one truth:
      zone    closed while the zone sits inside a whole-area total, or the item has active whole-project entries
      area    closed while one of its zones was recorded zone by zone, or the item has active whole-project entries
      project closed while any zone / area of the item holds its own quantity (a whole-project figure would wipe it)"""
    node = node or {}
    runs = runs or {}
    rooms = [str(r) for r in appl if (plan.get(str(r)) or 0) > 0]
    q = {r: plan[r] * itemprog.frac_for(node, r) for r in rooms}
    valid = locqty.valid_pools(pools, node)
    pooled = {r: p["area"] for p in valid for r in p["rooms"]}
    own = {k for k, v in node.items() if k != "*" and itemprog._f(v) > 0}
    proj_runs = active(runs.get(PROJECT))
    out = []

    def mk(key, kind, label, group, rms, done, allowed, why, writable, wwhy=""):
        v = view(runs.get(key), done)
        out.append(dict(v, key=key, kind=kind, label=label, group=group, rooms=rms, plan=round(sum(plan[r] for r in rms), 4), _raw=float(done),
                        allowed=bool(allowed), why="" if allowed else why, writable=bool(writable), wwhy="" if writable else wwhy))

    rest_q = round(sum(q[r] for r in rooms if r not in node), 4)       # what was recorded overall (the item's '*'), not by a zone / area
    p_ok = not own
    mk(PROJECT, "project", "Whole project (feeder / main route)", "", rooms, rest_q, p_ok,
       "Some zones or levels of this item already have their own quantity. Add the measurement to one of them.", p_ok,
       "Zones of this item were recorded since, so cancelling here would reset them. Change the quantity in Zones.")
    areas = {}
    for r in sorted(rooms, key=lambda r: order.get(r, 10 ** 9)):
        areas.setdefault(loc.get(r, ""), []).append(r)
    for a, rms in areas.items():
        if not a:
            continue
        zonewise = [r for r in rms if r in own and r not in pooled]
        done = round(sum(q[r] for r in rms), 4)
        a_ok = not zonewise
        why = (f"Some zones of {a} were recorded zone by zone. Add the measurement to a zone." if zonewise else "")
        mk(area_key(a), "area", f"{a} · whole {word}", "Whole " + word, rms, done,
           a_ok and not proj_runs, why or "This item is measured for the whole project. Add it there.", a_ok,
           f"Some zones of {a} were recorded zone by zone since, so cancelling here would reset them. Change the quantity in Zones.")
    for r in sorted(rooms, key=lambda r: order.get(r, 10 ** 9)):
        in_pool = pooled.get(r)
        mk(r, "zone", name.get(r, r), loc.get(r, ""), [r], q[r],
           not in_pool and not proj_runs,
           (f"{in_pool} was recorded as one total. Add the measurement to the whole {word}." if in_pool else "This item is measured for the whole project. Add it there."),
           not in_pool, f"{in_pool} was recorded as one total since, so cancelling here would reset it. Change the quantity in Zones.")
    return out


def coverage(table):
    """What a measurement chip says: number of active entries, the quantity they explain (never more than the place holds), and what
    is installed in all. A place holding LESS than its entries (changed by hand since) backs only what it holds."""
    n = sum(len(active(t["runs"])) for t in table)
    backed = round(sum(min(t["active"], t["done"]) for t in table if t["runs"]), 4)
    installed = round(sum(t["_raw"] for t in table if t["kind"] == "zone"), 4)
    return {"n": n, "backed": backed, "done": installed}


# ------------------------------------------------------------------ the sheet
BEFORE = "Recorded without From-To detail"
CHANGED = "Recorded differently from the entries, so shown as one total"
REDUCED = "Quantity of an earlier bill no longer in the records"


def sheet_lines(runs_by_key, now_q, prev_q, prev_mode, info, key_order=None):
    """The From-To lines of ONE item for the client sheet.

    runs_by_key : {key: [run, ...]}      (this item's stored entries; key = room id | "~area:<area>" | "~project")
    now_q       : {key: qty}             (key_quantities() of the item now)
    prev_q      : {key: qty} | None      (the same of the last locked RA that billed the item)
    prev_mode   : "none" | "loc" | "legacy"        (as locqty.lines_for)
    info        : sp.location_info(d)
    key_order   : {key: position} for area keys (a room's position comes from info)

    Per place the lines add up exactly to the place's quantity: [recorded without From-To detail] + active entries, in the order they
    were entered. What the last locked RA already billed there (prev_q[key]) is allocated to the lines in that order, so a line is
    Previous, This bill, or (when an entry straddles the cut) both. A place whose recorded quantity is LESS than its active entries
    (changed by hand since) is shown as one total instead of claiming detail it cannot back.
    Returns [{"key","scope","rid","floor","zone","kind","from","to","start","end","qty","prev","this","remarks"}] in structure order,
    the whole-project lines last; prev/this are None in legacy mode (it was billed before but never located)."""
    name, loc, order = info.get("name") or {}, info.get("loc") or {}, info.get("order") or {}
    word = info.get("word") or "area"
    key_order = key_order or {}
    out = []

    def pos(k):
        return 10 ** 9 + 5 if k == PROJECT else key_order.get(k, 10 ** 9) if k.startswith(AREA) else order.get(k, 10 ** 9)

    def label(k):
        kd = kind_of(k)
        if kd == "project":
            return "", "Whole project (not located)"
        if kd == "area":
            return k[len(AREA):], f"Whole {word} (one total, not zone-wise)"
        return loc.get(k, ""), name.get(k, k)

    keys = [k for k in (runs_by_key or {}) if k in (now_q or {}) and active(runs_by_key[k])]
    for k in sorted(keys, key=pos):
        done = round(float(now_q[k]), 4)
        runs = active(runs_by_key[k])
        rest = round(done - active_total(runs), 4)
        fl, zn = label(k)
        base = {"key": k, "scope": kind_of(k), "rid": k, "floor": fl, "zone": zn, "from": "", "to": "", "start": None, "end": None, "remarks": ""}
        if prev_mode == "loc":
            budget = round(float((prev_q or {}).get(k, 0) or 0), 4)
        elif prev_mode == "none":
            budget = 0.0
        else:
            budget = None
        if rest < -TOL:
            segs = [dict(base, kind="changed", qty=done, remarks=CHANGED)]
        else:
            segs = []
            if rest > TOL:
                segs.append(dict(base, kind="before", qty=rest, remarks=BEFORE))
            for r in runs:
                segs.append(dict(base, kind="run", **{"from": r["from"], "to": r["to"], "start": r.get("start"), "end": r.get("end"),
                                                       "qty": round(float(r["qty"]), 4), "remarks": r.get("remarks") or ""}))
        for sg in segs:
            if budget is None:
                sg["prev"] = sg["this"] = None
            else:
                p = round(min(sg["qty"], budget), 4)
                budget = round(budget - p, 4)
                sg["prev"], sg["this"] = p, round(sg["qty"] - p, 4)
            out.append(sg)
        if budget is not None and budget > EPS:                      # an earlier bill billed more here than the records now hold
            out.append(dict(base, kind="reduced", qty=0.0, prev=budget, this=round(-budget, 4), remarks=REDUCED))
    return out
