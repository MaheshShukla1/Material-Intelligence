"""Per-item, per-room progress — the source of truth for Site Progress.

The old model stored one fraction per (activity, room), so every BOQ item in an
activity shared it — dragging one item's slider moved the others and the per-item
% was wrong. This module gives every BOQ ITEM its own progress, per ROOM, so:

  * each item's slider is independent, and
  * you can see how much of each item is done in a specific room.

Store  (item_progress.json):
    {service: {item_code: {"*": overall_frac, room_id: override_frac, ...}}}
    "*"  = value applied to every applicable room; per-room keys override it.

Applicability (item_rooms.json):
    {service: {item_code: [room_id, ...]}}   which rooms the item is installed in.
    Missing => all rooms (the common case). This is the hotel/mall/hospital
    difference: most items are in every room; a few only in some — the engineer
    ticks the rooms and everything below just works.

    used(item)    = boq_qty_per_room  x  sum over applicable rooms of frac(room)
    planned(item) = boq_qty_per_room  x  number of applicable rooms
    item %        = mean frac over applicable rooms

Per-room quantity groups (item_room_qty.json) — an OPT-IN layer on top of the
above, for the real case a uniform "qty x room count" can't represent: not
every room actually needs the same amount of an item. A corner room's
conduit run is longer than a standard room's; a mall's zones vary in area
directly. The engineer's own workflow for this: walk one room, read the real
qty off it, tick every room where that number applies (the same "Rooms" chip
applicability already used for yes/no), save — that becomes one GROUP. Walk
a different room with a different real number, tick its rooms, save again —
a second group. No new data-entry habit, just the existing chip used once
per distinct real quantity instead of once for a single uniform number.

    {service: {item_code: [{"rooms": [room_id, ...], "qty": float}, ...]}}

A room belongs to at most one group (first group wins on overlap, so groups
should partition rooms, not duplicate them). When an item has no groups
defined here (the default, common case — most items ARE uniform), every
function below falls through unchanged to the qty_per_room x room-count path
exactly as before this existed:

    used(item)    = sum over every group of (that group's qty x sum of its
                    rooms' own frac)
    planned(item) = sum over every group of (qty x room count in that group)
    item %        = used / planned x 100  (quantity-weighted, not a bare
                    room-count average — correct once rooms can legitimately
                    carry different quantities; identical to the old
                    room-count average in the common case where every
                    group's qty happens to be the same)
"""
import pandas as pd


def _f(v, d=0.0):
    try:
        if v is None:
            return d
        x = float(v)
        return d if x != x else x
    except (TypeError, ValueError):
        return d


def frac_for(item_node, room_id):
    """Effective fraction for one room: a per-room override, else the '*' value."""
    if not item_node:
        return 0.0
    if room_id in item_node:
        return _f(item_node[room_id])
    return _f(item_node.get("*", 0.0))


def _room_qty_from_groups(groups, room_set):
    """Flatten one item's room_room_qty.json groups into {room_id: qty} plus
    the ordered list of rooms they cover (their union). Returns (None, None)
    when there are no real groups, so callers can cleanly fall through to
    the uniform qty_per_room path -- this never invents a group."""
    if not groups:
        return None, None
    room_qty, appl = {}, []
    for g in groups:
        q = _f(g.get("qty"))
        for r in (g.get("rooms") or []):
            if r in room_set and r not in room_qty:   # first group wins on overlap
                room_qty[r] = q
                appl.append(r)
    if not room_qty:
        return None, None
    return room_qty, appl


def compute(items_df, prog_svc, rooms_svc, all_room_ids, planned_over,
            room=None, room_qty_groups=None):
    """Return a used/planned/% DataFrame, one row per BOQ item.

    prog_svc        : item_progress for this service {code: {"*":f, room:f}}
    rooms_svc       : item_rooms for this service {code: [room_id,...]}
    all_room_ids    : every room id in the structure
    planned_over    : {code: planned_qty} engineer overrides
    room            : if given, compute for that single room only (drill-down).
    room_qty_groups : optional {code: [{"rooms":[...], "qty":X}, ...]} --
                      see this module's own docstring. Omitted or empty for
                      an item -> behaves exactly as before this existed.

    Precedence when both are set for the same item: room_qty_groups wins,
    always, over a flat planned_over -- deliberately the opposite of "the
    manual override wins", because a QUICK item's planned_over isn't a
    deliberate correction at all, it's just how a quick-added item's
    quantity is stored by default (see set_room_qty_group's docstring /
    siteprogress.py's quick-item flow). If the flat number won, using the
    Rooms chip to build a real per-room breakdown for a quick item -- the
    exact workflow it exists for -- would silently do nothing, forever,
    because every quick item always has a planned_over set.

    Rooms NOT covered by any group still count, at the item's own
    qty_per_room -- groups are read as named EXCEPTIONS on top of the
    item's normal applicability, not a full replacement that requires
    covering every room before they're safe to use at all.

    Performance note: was iterrows() + `r in all_room_ids` (a list, so an
    O(n) scan) per applicable room per item -- O(items x rooms²) worst case.
    On a 100+ item, 100+ room project that's the real reason a service view
    (and therefore /overall, which loads every service) felt slow. Switched
    to itertuples() (no per-row Series allocation) and a room SET built once
    up front (O(1) membership instead of O(n)) -- same filtering result,
    same output shape, just without paying rooms² per item."""
    prog_svc = prog_svc or {}
    rooms_svc = rooms_svc or {}
    planned_over = planned_over or {}
    room_qty_groups = room_qty_groups or {}
    room_set = set(all_room_ids)
    recs = []
    for it in items_df.itertuples():
        code = str(it.item_code)
        node = prog_svc.get(code, {})
        qty = _f(getattr(it, "qty", None))
        over = planned_over.get(code)
        room_qty, group_appl = _room_qty_from_groups(room_qty_groups.get(code), room_set)

        if room_qty is not None:
            # Groups are EXCEPTIONS layered on top of the item's normal
            # applicability -- matching this app's own established mental
            # model for the Rooms chip ("all rooms typical by default;
            # untick/override the exceptions"). A room covered by a group
            # uses that group's real qty. A room that's still applicable
            # (by the ordinary item_rooms.json rule) but NOT yet covered by
            # any group falls back to the item's own qty_per_room -- it is
            # never silently excluded just because the engineer hasn't
            # gotten to it yet. (Earlier version of this: a room outside
            # every group dropped out of applicability entirely the moment
            # ANY group existed for the item -- so grouping only 60 of 109
            # rooms silently shrank the item down to 60 rooms' worth of
            # total instead of leaving the other 49 on their normal
            # per-room qty. Real per-room groups should describe known
            # exceptions, not require covering every single room before
            # they're safe to use at all.)
            base_appl = rooms_svc.get(code) or all_room_ids
            base_appl = [r for r in base_appl if r in room_set] or all_room_ids
            appl = list(dict.fromkeys(base_appl + group_appl))   # union, order-stable
            def _rq(r):
                return room_qty.get(r, qty)
            if room is not None:
                in_room = room in appl
                q = _rq(room) if in_room else 0.0
                fr = frac_for(node, room) if in_room else 0.0
                planned = q if in_room else 0.0
                used = q * fr
            else:
                planned = sum(_rq(r) for r in appl)
                used = sum(_rq(r) * frac_for(node, r) for r in appl)
            pct = (100.0 * used / planned) if planned else 0.0
        else:
            # the original, uniform path: unchanged in every respect when
            # room_qty_groups has nothing for this item.
            appl = rooms_svc.get(code) or all_room_ids
            appl = [r for r in appl if r in room_set] or all_room_ids
            if room is not None:
                in_room = room in appl
                fr = frac_for(node, room) if in_room else 0.0
                n = 1 if in_room else 0
                frac_sum = fr
            else:
                n = len(appl)
                frac_sum = sum(frac_for(node, r) for r in appl)
            planned = _f(over) if over is not None else qty * (1 if room is not None else n)
            # used must scale off `planned`, not the raw per-room `qty` -- they
            # are the same number when nothing is overridden (planned = qty*n),
            # so this changes nothing for a normal BOQ item. But a quick-added
            # item (picked straight from stock, see quick_items in siteprogress.py)
            # has no per-room qty at all -- qty is always 0, its real quantity
            # lives entirely in `planned` -- so `qty * frac_sum` was always 0
            # regardless of progress, silently zeroing its done/remaining ₹ even
            # at 100% complete. Scaling off `planned` fixes that and also makes
            # a manually-overridden regular BOQ item's used ₹ track the override
            # instead of quietly ignoring it.
            used = planned * (frac_sum / n) if n else 0.0
            pct = (100.0 * frac_sum / n) if n else 0.0

        recs.append({
            "item_code": code, "description": getattr(it, "description", ""),
            "unit": getattr(it, "unit", ""), "qty_per_room": qty,
            "rooms": (len(appl) if room is None else (1 if room in appl else 0)),
            "planned_total": round(planned, 3), "used": round(used, 3),
            "remaining": round(max(planned - used, 0.0), 3),
            "progress_pct": round(pct, 1),
            "in_room": (room is None or room in appl),
            "has_room_groups": room_qty is not None,
        })
    return pd.DataFrame(recs)


# --------------------------------------------------------------------------
# Room-count buckets -- the "Rooms" panels in Mockup 2 (item drawer) and
# Mockup 1 (Overall hero) both need rooms grouped into done / in_progress /
# not_started buckets instead of averaged into one %. Both read straight off
# the existing item_progress.json / item_rooms.json stores -- no new data
# model, same applicability + frac_for() resolution compute() already uses,
# just grouped differently.
_DONE_TOL = 1e-6


def room_buckets(item_code, prog_svc, rooms_svc, all_room_ids, room_qty_groups=None):
    """One item's applicable rooms, bucketed by that room's own completion
    fraction: >=1.0 (within tolerance) -> done, 0<frac<1 -> in_progress,
    ==0 -> not_started. Mirrors compute()'s applicability resolution
    (item_room_qty.json groups when the item has them, else item_rooms.json
    falling back to every room) exactly, so this can never disagree with the
    used/planned/% numbers already shown for the item -- it is the same
    progress store, just grouped into buckets instead of summed into one
    total.

    Returns {"done", "in_progress", "not_started", "total"}.

    Performance note: filters `appl` against a SET built from all_room_ids,
    not the list itself -- `r in a_list` is an O(n) scan, so filtering up to
    n applicable rooms against it was O(n²) per item. Multiplied across every
    item in a service (this runs once per item in _service_view), that scan
    dominated real load time on a 100+ room project. Same result, just once
    per item instead of once per (item x room) pair."""
    prog_svc = prog_svc or {}
    rooms_svc = rooms_svc or {}
    code = str(item_code)
    room_set = set(all_room_ids)
    _room_qty, group_appl = _room_qty_from_groups((room_qty_groups or {}).get(code), room_set)
    base_appl = rooms_svc.get(code) or all_room_ids
    base_appl = [r for r in base_appl if r in room_set] or all_room_ids
    # groups are exceptions on top of the item's normal applicability, same
    # rule as compute() -- a room outside every group still counts if it's
    # applicable the ordinary way, it just has no group-specific qty.
    appl = list(dict.fromkeys(base_appl + group_appl)) if group_appl is not None else base_appl
    node = prog_svc.get(code, {})
    done = in_progress = not_started = 0
    for r in appl:
        fr = frac_for(node, r)
        if fr >= 1.0 - _DONE_TOL:
            done += 1
        elif fr > _DONE_TOL:
            in_progress += 1
        else:
            not_started += 1
    return {"done": done, "in_progress": in_progress,
            "not_started": not_started, "total": len(appl)}


def project_room_status(services_data, all_room_ids):
    """Whole-project room rollup -- "Rooms - whole site" on the Overall hero.
    A ROOM (not an item) is done/in_progress/not_started, looking across every
    *mapped* item in every service that applies to it (unmapped items are
    excluded from this headline for the same reason pnl.rollup_pnl excludes
    them from ₹ totals -- an item with no activity home should not silently
    move the number the PM is looking at).

    services_data: [(items_df, prog_svc, rooms_svc, mapped_codes), ...] or
    [(items_df, prog_svc, rooms_svc, mapped_codes, room_qty_groups), ...] --
    the 5-tuple form (room_qty_groups last) lets a grouped item's real
    applicable rooms (the union of its groups) count correctly instead of
    falling back to item_rooms.json, which a grouped item may not even have
    an entry in. Either tuple length is accepted so existing callers that
    don't yet pass groups keep working unchanged.

    A room counts as:
      done         -- every mapped item that applies to it is at frac==1.0
      in_progress  -- at least one applies AND at least one has frac>0, but
                      not every applicable item is done
      not_started  -- either nothing mapped applies to it yet, or everything
                      that applies is still at frac==0 -- both read the same
                      to a PM ("nothing recorded here"), so both count here
                      rather than inventing a fourth bucket for a case that
                      looks identical on site.

    Performance note: this was originally written room-first (for each room,
    re-scan every item in every service with DataFrame.iterrows()) -- correct,
    but iterrows() re-creates a Series per row and paid that cost once PER
    ROOM instead of once per item, so a 109-room x 5-service project reran a
    fresh iterrows() pass over every service's items 109 times over -- the
    real reason /overall felt slow to load. Rewritten item-first (one
    itertuples() pass per service, tallying into every room that item
    touches) -- same result, same O(services x items x that item's own room
    count) in the worst case, just without paying DataFrame-iteration
    overhead 109x more often than necessary.
    """
    seen = {r: False for r in all_room_ids}
    all_full = {r: True for r in all_room_ids}
    any_progress = {r: False for r in all_room_ids}
    room_set = set(all_room_ids)
    for entry in services_data:
        if len(entry) == 5:
            items_df, prog_svc, rooms_svc, mapped_codes, room_qty_groups = entry
        else:
            items_df, prog_svc, rooms_svc, mapped_codes = entry
            room_qty_groups = None
        prog_svc = prog_svc or {}
        rooms_svc = rooms_svc or {}
        room_qty_groups = room_qty_groups or {}
        if items_df is None or len(items_df) == 0 or not mapped_codes:
            continue
        for it in items_df.itertuples():
            code = str(it.item_code)
            if code not in mapped_codes:
                continue
            _rq, group_appl = _room_qty_from_groups(room_qty_groups.get(code), room_set)
            base_appl = rooms_svc.get(code) or all_room_ids
            appl = list(dict.fromkeys(list(base_appl) + group_appl)) if group_appl is not None else base_appl
            node = prog_svc.get(code, {})
            for r in appl:
                if r not in room_set:
                    continue
                seen[r] = True
                fr = frac_for(node, r)
                if fr < 1.0 - _DONE_TOL:
                    all_full[r] = False
                if fr > _DONE_TOL:
                    any_progress[r] = True
    done = in_progress = not_started = 0
    for r in all_room_ids:
        if seen[r] and all_full[r]:
            done += 1
        elif seen[r] and any_progress[r]:
            in_progress += 1
        else:
            not_started += 1
    return {"done": done, "in_progress": in_progress,
            "not_started": not_started, "total": len(all_room_ids)}


def set_progress(store, service, code, frac, room=None):
    """Mutate the item_progress dict. room=None sets the overall '*' (all rooms);
    a room id sets just that room. frac clamped to [0,1]."""
    frac = max(0.0, min(1.0, _f(frac)))
    node = store.setdefault(service, {}).setdefault(str(code), {})
    if room:
        node[str(room)] = frac
    else:
        node["*"] = frac
        # a fresh overall wipes stale per-room overrides so the bar is uniform
        for k in list(node.keys()):
            if k != "*":
                node.pop(k, None)
    return store


def set_rooms(rooms_store, service, code, room_ids):
    """Set which rooms an item applies to (applicability). Empty/None => all."""
    svc = rooms_store.setdefault(service, {})
    if room_ids:
        svc[str(code)] = [str(r) for r in room_ids]
    else:
        svc.pop(str(code), None)
    return rooms_store


def set_room_qty_group(groups_store, service, code, room_ids, qty):
    """The 'real per-room quantity' workflow: an engineer walks one room,
    reads the real qty off it, ticks every room where that number applies
    (the same chip already used for plain yes/no applicability), saves --
    that becomes one group: {"rooms": [...], "qty": X}. Calling this again
    with a DIFFERENT set of rooms and a DIFFERENT qty adds a second group
    (e.g. 100 standard rooms at 52 MTR, 9 corner rooms at 60 MTR) -- the
    item's total becomes the sum across every group once compute() sees
    them, no new data-entry habit beyond using the same chip more than once.

    Any room in the NEW group is first removed from every OTHER existing
    group for this item -- a room can only belong to one group at a time,
    so re-ticking a room into a new group is how you MOVE it, not duplicate
    it into two groups at once (which would double-count its quantity).
    Saving with `qty=None` or empty `room_ids` removes rooms from every
    group without adding a new one -- the way to shrink or delete a group."""
    svc = groups_store.setdefault(service, {})
    groups = svc.setdefault(str(code), [])
    new_rooms = {str(r) for r in (room_ids or [])}
    # detach these rooms from every existing group first (a room moving
    # groups, or being removed entirely if qty is None)
    kept = []
    for g in groups:
        remaining = [r for r in g.get("rooms", []) if r not in new_rooms]
        if remaining:
            kept.append({"rooms": remaining, "qty": g.get("qty")})
    if new_rooms and qty is not None:
        kept.append({"rooms": sorted(new_rooms), "qty": _f(qty)})
    if kept:
        svc[str(code)] = kept
    else:
        svc.pop(str(code), None)
    return groups_store
