"""Where an item's installed quantity sits -- the data behind the bill's
supporting Measurement Sheet.

Site Progress stores, per item, a fraction per room (itemprog.frac_for): a
room's own value, else the item's overall '*' value. That is enough to know
HOW MUCH is installed, but not HOW IT WAS RECORDED, and the bill backup must
not claim more than the site recorded. Three honest cases:

  * zone-wise   -- a room has its own value (the engineer ticked / typed it
                   for that room). Printed as that room's own line.
  * area total  -- the engineer entered ONE total for a whole area ("B3 =
                   420 Rmt"). Site Progress spreads it over that area's rooms
                   (so every % and every existing report keeps working), and
                   remembers here that it was a total. Printed as ONE line
                   for the area, never as invented per-room numbers.
  * overall     -- no room has its own value; the quantity comes from the
                   item's overall '*'. Printed as one "recorded as overall"
                   line, not split by location.

A "pool" (area total) is only trusted while every one of its rooms still
holds exactly the value the pool set. The moment any room changes by any
route, the pool simply stops being valid -- there is no flag to forget to
clear and nothing to keep in sync.

Everything here is a pure function of plain data (no files, no FastAPI), so
siteprogress.py / billing.py feed it and the tests drive it directly.
"""
from . import itemprog

TOL = 1e-5            # a room "still holds the pool's value" within this fraction
EPS = 5e-5            # quantities equal at 4 decimals


def valid_pools(pools, node):
    """The pools of one item whose every room still holds the pool's own value.
    pools: [{"area": str, "rooms": [room_id,...], "frac": float}]; node: the item's
    progress node {"*": f, room_id: f}."""
    out = []
    for p in pools or []:
        rooms = [str(r) for r in (p.get("rooms") or [])]
        if not rooms:
            continue
        f = itemprog._f(p.get("frac"))
        if all(abs(itemprog.frac_for(node, r) - f) <= TOL for r in rooms):
            out.append({"area": str(p.get("area") or ""), "rooms": rooms, "frac": f})
    return out


def split(node, appl, plan, pools, order=None):
    """One item's installed quantity by how it was recorded.

    node  : the item's progress node
    appl  : room ids the item applies to
    plan  : {room_id: planned qty | None} (siteprogress._item_plan)
    pools : stored pools of this item (validated here)
    order : {room_id: position in the structure} -- only used to keep lines in structure order

    Returns {"pools": {area: {"q": qty, "o": order}}, "rooms": {room_id: qty},
             "rest": qty, "sum": qty}. A room whose plan is unknown contributes nothing
    (never a guessed number)."""
    order = order or {}
    appl = [str(r) for r in appl]
    inside = set(appl)
    out_pools, pooled = {}, set()
    for p in valid_pools(pools, node):
        rs = [r for r in p["rooms"] if r in inside and (plan.get(r) or 0) > 0]
        if not rs:
            continue
        q = sum(plan[r] * itemprog.frac_for(node, r) for r in rs)
        e = out_pools.setdefault(p["area"], {"q": 0.0, "o": min(order.get(r, 10 ** 9) for r in rs)})
        e["q"] += q
        e["o"] = min(e["o"], min(order.get(r, 10 ** 9) for r in rs))
        pooled.update(rs)
    rooms, rest = {}, 0.0
    for r in appl:
        if r in pooled:
            continue
        pl = plan.get(r)
        if not pl or pl <= 0:
            continue
        q = pl * itemprog.frac_for(node, r)
        if q <= 0:
            continue
        if r in node:
            rooms[r] = q
        else:
            rest += q
    for e in out_pools.values():
        e["q"] = round(e["q"], 4)
    rooms = {r: round(q, 4) for r, q in rooms.items()}
    rest = round(rest, 4)
    total = round(sum(e["q"] for e in out_pools.values()) + sum(rooms.values()) + rest, 4)
    return {"pools": {a: e for a, e in out_pools.items() if e["q"] > 0}, "rooms": rooms, "rest": rest, "sum": total}


def pool_views(pools, node, plan):
    """What the Zones dialog needs to know: the valid pools with their quantity."""
    out = []
    for p in valid_pools(pools, node):
        rs = [r for r in p["rooms"] if (plan.get(r) or 0) > 0]
        out.append({"area": p["area"], "rooms": p["rooms"],
                    "qty": round(sum(plan[r] * itemprog.frac_for(node, r) for r in rs), 4)})
    return out


# ------------------------------------------------------------ bill lines
_LEGACY = "Location of earlier RAs not recorded"
_CHANGED = "Recorded differently from the last RA, so shown as one total"
_WHOLE = "Total for the whole area, not split zone-wise"
_OVERALL = "Recorded as an overall total, not by location"


def _r4(x):
    return round(float(x or 0), 4)


def _same(a, b):
    return abs(a - b) <= EPS


def lines_for(now, prev, prev_mode, info):
    """The lines one item prints on the Measurement Sheet.

    now, prev  : split() results (prev is ignored unless prev_mode == "loc")
    prev_mode  : "none"   nothing was billed for this item before (previous = 0)
                 "loc"    the last RA that billed it recorded locations (prev is that RA's split)
                 "legacy" it was billed before but the places were never recorded:
                          only TO-DATE is shown per line, previous / this are left blank
    info       : {"name": {rid: str}, "loc": {rid: str}, "order": {rid: int},
                  "word": "level"|"floor"|"area", "collapse": bool, "leaf": "room"}

    Returns [{"label", "prev", "this", "total", "remark"}] in structure order. prev/this are
    None in legacy mode. Quantities are never invented: a line exists only where the site
    recorded something."""
    name, loc, order = info.get("name") or {}, info.get("loc") or {}, info.get("order") or {}
    word = info.get("word") or "area"
    ent = {}          # key -> {"label","now","prev","o","group","kind","rid"}

    def add(key, label, o, group, kind, rid=None):
        return ent.setdefault(key, {"label": label, "now": 0.0, "prev": 0.0, "o": o, "group": group, "kind": kind, "rid": rid})

    for area, e in (now.get("pools") or {}).items():
        add(("p", area), f"{area} — whole {word}", e.get("o", 10 ** 9), area, "pool")["now"] = e["q"]
    for rid, q in (now.get("rooms") or {}).items():
        add(("r", rid), f"{loc.get(rid, '')} — {name.get(rid, rid)}".strip(" —"), order.get(rid, 10 ** 9), loc.get(rid, ""), "room", rid)["now"] = q
    if now.get("rest"):
        add(("x",), "", 10 ** 10, "", "rest")["now"] = now["rest"]
    if prev_mode == "loc" and prev:
        for area, e in (prev.get("pools") or {}).items():
            add(("p", area), f"{area} — whole {word}", e.get("o", 10 ** 9), area, "pool")["prev"] = e["q"]
        for rid, q in (prev.get("rooms") or {}).items():
            add(("r", rid), f"{loc.get(rid, '')} — {name.get(rid, rid)}".strip(" —"), order.get(rid, 10 ** 9), loc.get(rid, ""), "room", rid)["prev"] = q
        if prev.get("rest"):
            add(("x",), "", 10 ** 10, "", "rest")["prev"] = prev["rest"]

    # a place recorded as an area total in one RA and room by room in the other cannot be compared line by line
    collapsed = {}
    if prev_mode == "loc":
        groups = {}
        for k, e in ent.items():
            if e["kind"] in ("pool", "room"):
                groups.setdefault(e["group"], []).append(k)
        for g, ks in groups.items():
            has = lambda kind, field: any(ent[k]["kind"] == kind and ent[k][field] > 0 for k in ks)
            pool_prev, pool_now = has("pool", "prev"), has("pool", "now")
            rooms_prev, rooms_now = has("room", "prev"), has("room", "now")
            if (pool_prev and not pool_now) or (pool_now and rooms_prev and not pool_prev) or (pool_prev and rooms_now and not pool_now):
                collapsed[g] = ks
    lines = []
    for g, ks in collapsed.items():
        p = sum(ent[k]["prev"] for k in ks)
        n = sum(ent[k]["now"] for k in ks)
        lines.append({"label": g, "prev": _r4(p), "this": _r4(n - p), "total": _r4(n), "remark": _CHANGED,
                      "o": min(ent[k]["o"] for k in ks)})
    skip = {k for ks in collapsed.values() for k in ks}
    singles = [(k, e) for k, e in ent.items() if k not in skip and (e["now"] > 0 or e["prev"] > 0)]
    singles.sort(key=lambda t: t[1]["o"])
    run = []

    def flush():
        if not run:
            return
        if info.get("collapse") and len(run) >= 3:
            e0, e1 = run[0][1], run[-1][1]
            lab = f"{e0['group']} — {name.get(e0['rid'], '')}–{name.get(e1['rid'], '')} ({len(run)} {info.get('leaf', 'room')}s)"
            lines.append({"label": lab.strip(" —"), "o": e0["o"], "_run": list(run)})
        else:
            for _, e in run:
                lines.append({"label": e["label"], "o": e["o"], "_one": e})
        run.clear()

    for k, e in singles:
        if e["kind"] == "room" and info.get("collapse"):
            if run and run[-1][1]["kind"] == "room" and run[-1][1]["group"] == e["group"] and \
                    _same(run[-1][1]["now"], e["now"]) and _same(run[-1][1]["prev"], e["prev"]):
                run.append((k, e))
                continue
            flush()
            run.append((k, e))
            continue
        flush()
        lines.append({"label": e["label"], "o": e["o"], "_one": e})
    flush()
    out = []
    has_other = any(l.get("_one") and l["_one"]["kind"] != "rest" for l in lines) or any(l.get("_run") or l.get("remark") == _CHANGED for l in lines)
    for l in sorted(lines, key=lambda x: x["o"]):
        if l.get("remark") == _CHANGED:
            out.append({k: l[k] for k in ("label", "prev", "this", "total", "remark")})
            continue
        if "_run" in l:
            run_ = l["_run"]
            n_ = _r4(sum(e["now"] for _, e in run_))
            p_ = _r4(sum(e["prev"] for _, e in run_))
            rec = {"label": l["label"], "total": n_, "remark": ""}
        else:
            e = l["_one"]
            n_, p_ = _r4(e["now"]), _r4(e["prev"])
            label = e["label"]
            remark = {"pool": _WHOLE, "rest": _OVERALL}.get(e["kind"], "")
            if e["kind"] == "rest":
                label = "Remaining areas (recorded as overall total)" if has_other else "Whole project (recorded as overall total)"
            rec = {"label": label, "total": n_, "remark": remark}
        if prev_mode == "legacy":
            rec["prev"], rec["this"] = None, None
        else:
            rec["prev"], rec["this"] = p_, _r4(n_ - p_)
        out.append(rec)
    return out


LEGACY_REMARK = _LEGACY
