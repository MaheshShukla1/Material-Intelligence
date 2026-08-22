"""Site Progress API — the third tab, wired to the six new modules.

This is a self-contained FastAPI router. api.py includes it with two lines and
is otherwise untouched; not one forecast route changes. Everything here is
read/write on a NEW per-project store:

    data/projects/<slug>/
        tracker.xlsx      (last uploaded progress tracker, for re-parse)
        boq.xlsx          (last uploaded BOQ)
        structure.json    (editable floor/room tree)
        activities.json   ({service: [activity, ...]})
        mapping.json      (editable {service: {activity: [item_code]}})
        rates.json        ({service: {item_code: ₹/unit}})
        settings.json     ({"default_install_pct": 0-100} — optional project-
                           level payment-term default; absent => feature unused,
                           every rate counts as installation value, unchanged
                           from before this existed)
        install_pct.json  ({service: {item_code: 0-100}} — optional per-item
                           payment-term overrides, take precedence over the
                           project default)
        boq.parquet       (parsed line items, all services)
        progress.parquet  (tidy tick grid, editable by the slider)

The forecast side is only ever READ: linkage looks up the project's latest
forecast run (forecast.parquet) to attach stock/rate/order-by to a BOQ item.
The engine, its routes and its files are never written here.
"""
import json
import io
import shutil
import datetime as dt
import functools
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Response

from . import structure, boq, activity, progress, pnl, linkage, schema, subcat, realtime, itemprog, dpr

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "data" / "projects"
RUNS = ROOT / "data" / "runs"
PROJECTS.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/api/siteprogress", tags=["site-progress"])


# ----------------------------------------------------------------- helpers
def _slugify(name):
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return s or "untitled"


def _dir(slug):
    d = PROJECTS / _slugify(slug)
    return d


def _need(slug):
    d = _dir(slug)
    if not d.exists():
        raise HTTPException(404, "no Site Progress data for this project yet — "
                                 "upload a tracker and a BOQ first")
    return d


def _scrub(obj):
    """Make a value JSON-safe: NaN/NaT -> None, Timestamp -> YYYY-MM-DD, recurse
    into dicts/lists/tuples. The forecast frame carries NaN and Timestamps that
    stdlib json rejects (the forecast routes use their own jsonable() for this)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return None if pd.isna(obj) else obj.strftime("%Y-%m-%d")
    try:
        if obj is None:
            return None
        if isinstance(obj, float) and (obj != obj):     # NaN
            return None
        if pd.isna(obj):                                # NaT and pandas NA
            return None
    except (TypeError, ValueError):
        pass
    return obj


def _read_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def _save_upload(file: UploadFile, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)


def _load_progress(d):
    p = d / "progress.parquet"
    if not p.exists():
        return progress.ProgressStore(pd.DataFrame(
            columns=["service", "floor", "activity", "room", "tick", "frac"]))
    return progress.ProgressStore(pd.read_parquet(p))


def _quick_items_df(d, service=None):
    """Items added straight from the stock register via '+ Add item' (no BOQ
    line ever existed for them). Same columns a real parsed BOQ sheet
    produces, so every downstream consumer — progress, pnl, itemprog,
    linkage — treats a quick item exactly like a BOQ item with no separate
    code path. Planned qty always starts at 0; the engineer sets it via the
    same ✎ (planned override) every other item uses — nothing here invents
    a quantity."""
    store = _read_json(d / "quick_items.json", {}) or {}
    svcs = [service] if service else list(store.keys())
    recs = []
    for svc in svcs:
        for it in (store.get(svc) or {}).get("items", []):
            recs.append({
                "service": svc, "item_code": it["item_code"],
                "description": it["description"], "unit": it["unit"],
                "qty": 0.0, "section": "Quick-added (from stock)",
                "item_code_raw": it["item_code"],
                "subcategory": subcat.classify(it["description"]),
            })
    return pd.DataFrame(recs, columns=["service", "item_code", "description",
                                       "unit", "qty", "section",
                                       "item_code_raw", "subcategory"])


def _load_boq(d, service=None):
    p = d / "boq.parquet"
    df = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    qi = _quick_items_df(d, service)
    df = pd.concat([df, qi], ignore_index=True, sort=False)
    if service and len(df) and "service" in df.columns:
        return df[df.service == service]
    return df


def _load_mapping(d):
    return activity.Mapping.from_dict(_read_json(d / "mapping.json", {}) or {})


def _rates(d, service):
    return (_read_json(d / "rates.json", {}) or {}).get(service, {})


def _settings(d):
    """Project-level settings — currently just `default_install_pct` (the
    payment-term default: what share of a combined supply+install rate
    counts as "installation" for done/remaining ₹, when an item has no
    override of its own). A separate small file rather than folding into an
    existing store, matching this codebase's own convention (item_rooms.json,
    labour_only.json, etc. are each their own file) and so it never risks
    colliding with rates.json's existing {service: {item_code: rate}} shape."""
    return _read_json(d / "settings.json", {}) or {}


def _install_pct(d, service):
    """Per-item installation-percent OVERRIDES for one service — kept in its
    own file (install_pct.json), deliberately separate from rates.json, so
    an existing rates.json on disk (a flat {item_code: rate} number) never
    needs a migration to hold a second field. Missing => no override, i.e.
    fall back to the project default (see pnl._install_pct_for)."""
    return (_read_json(d / "install_pct.json", {}) or {}).get(service, {})


# --------------------------------------------------------------------------
# DPR (Daily Progress Report) -- Layer 1: auto-capture, Layer 3: export.
# dpr_log.json is a plain append-only list of {date, service, floor,
# activity, room} facts, written from the same routes that already write
# item_progress.json -- the engineer never sees this happen, it is the same
# slider drag they already do today, just kept instead of overwritten.
# --------------------------------------------------------------------------
def _dpr_log(d):
    """Read dpr_log.json, silently dropping legacy entries this project's
    log may still carry from BEFORE the overall-slider fix (an earlier
    version of _log_dpr_change wrote a literal floor="OVERALL" placeholder;
    current code never writes that string -- any entry with it is always
    stale, pre-fix data, safe to drop on every read without ever touching
    the file, so old noise disappears on its own as real data accumulates)."""
    log = _read_json(d / "dpr_log.json", []) or []
    return [e for e in log if e.get("floor") != "OVERALL"]


def _save_dpr_log(d, log):
    (d / "dpr_log.json").write_text(json.dumps(log, ensure_ascii=False))


def _room_location_map(d):
    """{room_id: {"name":..., "location": "13TH" / "Wing A · Floor 1" / "B1"}}
    -- location is the room's own container path (Structure.rooms()'s own
    `path`, minus the project name at path[0]), joined. Generic across
    hotel/mall/hospital/custom -- see dpr.py's own note on why this is a
    joined path, not a flat "floor" field (a hospital has one more container
    level -- Wing > Floor -- than hotel/mall do)."""
    st = _read_json(d / "structure.json", None)
    if not st:
        return {}
    s = structure.Structure.from_dict(st)
    return {r["id"]: {"name": r["name"], "location": " · ".join(r["path"][1:])}
           for r in s.rooms()}


def _location_label(d):
    st = _read_json(d / "structure.json", {}) or {}
    return dpr.LOCATION_LABEL.get(st.get("kind"), "LOCATION")


def _leaf_label(d):
    st = _read_json(d / "structure.json", {}) or {}
    return dpr.LEAF_LABEL.get(st.get("kind"), "ROOM")


def _qty_for_room(d, service, item_code, room_id, frac):
    """The real quantity one specific captured action represents: the room's
    own quantity (room_qty_groups override if the item uses them -- reuses
    itemprog's own group-resolution helper rather than reimplementing it --
    else the item's plain per-room qty from the BOQ) x the fraction just
    set. NOT a true delta from the room's PREVIOUS fraction (no history is
    kept -- see dpr.py's module docstring on why this logs the fact of an
    action, not a diff of two states), so two saves the same day update in
    place (record_change()'s own job) rather than summing. Returns
    (qty, unit) -- (None, None) when no real quantity is knowable, never a
    guessed number."""
    groups = _item_room_qty(d).get(service, {}).get(str(item_code))
    room_qty = None
    if groups:
        rq, _ = itemprog._room_qty_from_groups(groups, {str(room_id)})
        if rq and str(room_id) in rq:
            room_qty = rq[str(room_id)]
    if room_qty is None:
        boqdf = _load_boq(d, service)
        row = boqdf[boqdf.item_code.astype(str) == str(item_code)]
        if row.empty:
            return None, None
        v = row.iloc[0].get("qty")
        room_qty = float(v) if v is not None else None
        unit = row.iloc[0].get("unit")
    else:
        boqdf = _load_boq(d, service)
        row = boqdf[boqdf.item_code.astype(str) == str(item_code)]
        unit = row.iloc[0].get("unit") if not row.empty else None
    if room_qty is None:
        return None, None
    return round(room_qty * max(0.0, min(1.0, float(frac))), 4), unit


def _applicable_rooms(d, service, item_code):
    """The real set of rooms one item applies to -- mirrors
    itemprog.compute()'s own applicability resolution exactly (room_qty_groups
    layered as exceptions on top of the base item_rooms.json applicability,
    else every room) so this can never quietly disagree with what the item's
    own progress/quantity math already uses. Needed for the overall '*'
    slider case below: it applies uniformly across every one of THESE rooms,
    so every FLOOR they belong to is real, known scope for this item -- not
    invented, just not narrowed to one specific room within that floor."""
    all_room_ids = _all_room_ids(d)
    room_set = set(all_room_ids)
    code = str(item_code)
    groups = _item_room_qty(d).get(service, {}).get(code)
    room_qty, group_appl = itemprog._room_qty_from_groups(groups, room_set)
    rooms_svc = _item_rooms(d).get(service, {})
    if room_qty is not None:
        base_appl = rooms_svc.get(code) or all_room_ids
        base_appl = [r for r in base_appl if r in room_set] or all_room_ids
        return list(dict.fromkeys(base_appl + group_appl))
    appl = rooms_svc.get(code) or all_room_ids
    return [r for r in appl if r in room_set] or all_room_ids


def _log_dpr_change(d, service, room_id, item_code, frac=None):
    """Layer 1's actual hook. Works generically across hotel/mall/hospital/
    custom -- never assumes what a "room" or "floor" is called, only reads
    Structure.rooms()'s own path.

    Two cases, both logged honestly, never inventing a location that isn't
    known:
      - A SPECIFIC room is known (per-room slider, or /mark-rooms-done) ->
        logged against that room's real location (floor/level/wing+floor),
        with the item's own description and (when `frac` is given) a real
        qty via _qty_for_room() above.
      - room_id is None (the overall "*" slider -- itemprog.set_progress's
        own docs explain why this ONE number can't be attributed to any one
        room) -> logged against EVERY REAL floor the item is actually
        applicable on (via _applicable_rooms() above, mapped to their real
        floors) -- not a generic "OVERALL" bucket. This is still never a
        guess: the fraction genuinely does apply uniformly across all of an
        item's applicable rooms, so every floor those rooms belong to is
        real, known scope, not invented. No specific room name is claimed
        within that floor (qty also stays blank -- see below), since that
        part genuinely isn't known.

    Only for an item actually mapped to an activity (an unmapped item has no
    home in the narrative log, same reasoning pnl.rollup_pnl already applies
    to unmapped items' money). A silent no-op otherwise -- callers never
    need to check first."""
    m = _load_mapping(d)
    acts = [a for a in m.activities(service) if str(item_code) in m.get(service, a)]
    if not acts:
        return
    boqdf = _load_boq(d, service)
    row = boqdf[boqdf.item_code.astype(str) == str(item_code)]
    item_desc = str(row.iloc[0].get("description")) if not row.empty else None
    log = _dpr_log(d)
    today = dt.date.today().isoformat()
    if room_id:
        info = _room_location_map(d).get(str(room_id))
        if not info or not info["location"]:
            return
        qty, unit = (None, None)
        if frac is not None:
            qty, unit = _qty_for_room(d, service, item_code, room_id, frac)
        for act in acts:
            dpr.record_change(log, today, service, info["location"], act, info["name"],
                              item=item_desc, qty=qty, unit=unit)
    else:
        rooms_map = _room_location_map(d)
        appl = _applicable_rooms(d, service, item_code)
        by_floor = {}
        for r in appl:
            info = rooms_map.get(r)
            if info and info["location"]:
                by_floor.setdefault(info["location"], []).append(r)
        if not by_floor:
            return   # no structure/rooms yet -- nothing real to attach this to
        for act in acts:
            for floor in sorted(by_floor):
                # honest floor-level qty: sum of THIS floor's own applicable
                # rooms' real quantity (same per-room resolution as the
                # specific-room path, room_qty_groups aware) x the fraction
                # just set. Still never claims which room within the floor
                # did the work -- only that this floor's total applicable
                # quantity moved by this much.
                qty_total, unit, has_qty = 0.0, None, False
                if frac is not None:
                    for r in by_floor[floor]:
                        q, u = _qty_for_room(d, service, item_code, r, frac)
                        if q is not None:
                            qty_total += q
                            unit = u or unit
                            has_qty = True
                dpr.record_change(log, today, service, floor, act, None, item=item_desc,
                                  qty=(round(qty_total, 3) if has_qty else None), unit=unit)
    _save_dpr_log(d, log)


def _latest_run_for(slug):
    """Newest forecast run whose meta.project_slug == slug (read-only)."""
    slug = _slugify(slug)
    best, best_created = None, ""
    if not RUNS.exists():
        return None
    for rd in RUNS.iterdir():
        m = rd / "meta.json"
        if not m.exists():
            continue
        try:
            meta = json.loads(m.read_text())
        except Exception:
            continue
        if meta.get("project_slug") == slug and meta.get("created", "") >= best_created:
            best, best_created = rd, meta.get("created", "")
    return best


@functools.lru_cache(maxsize=64)
def _read_forecast_parquet_cached(path_str, mtime_ns):
    return pd.read_parquet(path_str)


def _read_forecast_parquet(run_dir):
    """Cached read of one run's forecast.parquet. Safe to cache unconditionally
    because runs are immutable once written (README: "Runs are immutable ...
    Nothing is overwritten") -- the (path, mtime) cache key means this can
    never serve stale data even if that assumption were ever violated: a
    rewritten file gets a new mtime, which is a new cache key, forcing a
    fresh read rather than returning stale bytes.

    Before this, loading one Site Progress service tab meant re-reading this
    exact same file from disk 2-3 separate times within a single click (once
    each in _forecast_pool, _actual_consumed, and — after the Bug 2 fix —
    _full_run_rows) -- the main structural cause behind the reported
    service-switch slowness.

    UPDATE (broader performance pass): the more expensive step riding on top
    of this cached DataFrame -- linkage.match() token matching, and the
    iterrows()/_scrub() dict-building in _forecast_pool/_full_run_rows -- is
    now ALSO cached, in _cached_match()/_forecast_pool_cached()/
    _full_run_rows_cached() below. Real profiling on a synthetic Hyatt-scale
    project (108 rooms, 385 materials, 540 BOQ items) showed linkage.match()
    alone was ~25% of a single /overall call and over half of a single
    /links/{service} call. Same (path, mtime) keying as this function -- see
    those functions' own docstrings for the exact invalidation story (why it
    needs no manual cache-bust in any write route)."""
    p = Path(run_dir) / "forecast.parquet"
    return _read_forecast_parquet_cached(str(p), p.stat().st_mtime_ns)


# --------------------------------------------------------------- compute
def _all_room_ids(d):
    st = _read_json(d / "structure.json", None)
    if not st:
        return []
    return [r["id"] for r in structure.Structure.from_dict(st).rooms()]


def _item_prog(d):
    return _read_json(d / "item_progress.json", {}) or {}


def _item_rooms(d):
    return _read_json(d / "item_rooms.json", {}) or {}


def _item_room_qty(d):
    """{service: {item_code: [{"rooms":[...], "qty":X}, ...]}} -- see
    itemprog.py's own module docstring. Opt-in, per-item: most items have no
    entry here and fall straight through to the uniform qty_per_room x
    room-count path, unchanged."""
    return _read_json(d / "item_room_qty.json", {}) or {}


def _activity_prog(d):
    """{service: {activity: {"*":frac, room_id:frac}}} -- same shape as
    item_progress.json, but keyed by ACTIVITY NAME instead of item_code, for
    activities with no BOQ material at all (Zari work, core-cutting,
    chasing, testing -- see activity.is_labour_keyword()). Deliberately a
    separate file from item_progress.json rather than folding activity
    names into the same store: an item_code and an activity name could
    theoretically collide, and keeping the two fully separate means an
    activity's labour-only % can never be misread as some item's progress
    or vice versa."""
    return _read_json(d / "activity_progress.json", {}) or {}


def _labour_only(d):
    """{service: [activity_name, ...]} -- which activities are currently
    toggled to labour-only (%-only, no BOQ items) tracking. Only the
    TOGGLE lives here; the recorded % itself lives in _activity_prog() and
    is never deleted when an activity is toggled back to item-tracked, the
    same way an item's recorded progress survives its activity mapping
    being removed (see pnl.rollup_pnl's own docstring on this)."""
    return _read_json(d / "labour_only.json", {}) or {}


def _service_view_core(d, service, items, used, prog, rooms_cfg, room_qty_groups,
                       all_rooms, room=None):
    """The body of _service_view() from the point `used` (itemprog.compute's
    output) is already known — split out so /overall can share ONE items +
    used pair across both the normal per-service view AND its waste calc,
    instead of loading the BOQ and running itemprog.compute() twice for the
    exact same service on every single /overall call (see _waste_for below
    and overall()'s own loop). Real profiling on a synthetic Hyatt-scale
    project (108 rooms, 385 materials, 540 items) showed this duplication
    was roughly a quarter of /overall's total time by itself. _service_view()
    below is unchanged for every OTHER caller — this split changes no
    behaviour, only who pays for loading/computing `items`/`used`."""
    m = _load_mapping(d)
    acts = (_read_json(d / "activities.json", {}) or {}).get(service, [])
    rates = _rates(d, service)
    install_pct = _install_pct(d, service)
    default_install_pct = _settings(d).get("default_install_pct")
    ip = pnl.compute_item_pnl(used, rates=rates, install_pct=install_pct,
                              default_install_pct=default_install_pct)
    rp = pnl.rollup_pnl(ip, m, service)
    planned_over = _planned(d, service)   # for the "manually set" flag below

    # room bucket counts for the drawer's "Rooms — this item" panel (Mockup 2).
    # Always computed against the FULL room set regardless of `room` -- this
    # is whole-item info ("how many of this item's rooms are done"), not
    # scoped to whichever single room the engineer happens to be drilled into.
    room_buckets = {str(it.item_code): itemprog.room_buckets(it.item_code, prog, rooms_cfg, all_rooms, room_qty_groups=room_qty_groups)
                    for it in items.itertuples()} if all_rooms else {}

    sub = {str(r.item_code): r.subcategory for r in items.itertuples()}
    quick_codes = {it["item_code"] for it in
                  (_read_json(d / "quick_items.json", {}) or {}).get(service, {}).get("items", [])}
    inv = {}
    for a in m.activities(service):
        for c in m.get(service, a):
            inv.setdefault(str(c), []).append(a)
    # raw per-item overrides only (not the resolved/effective install_pct
    # already on `it`) -- the "Set rates" modal needs to tell "this item has
    # its OWN override" apart from "inheriting the project default" so it can
    # leave the input blank (default shown as placeholder) instead of
    # silently turning every item into an explicit override the moment the
    # engineer saves without touching that column.
    own_install_pct = {str(k): v for k, v in install_pct.items()} if install_pct else {}

    def fv(x):
        try:
            return None if x is None or (isinstance(x, float) and x != x) else float(x)
        except Exception:
            return None

    rows = []
    for _, it in ip.iterrows():
        c = str(it.item_code)
        rb = room_buckets.get(c) or {"done": 0, "in_progress": 0, "not_started": 0, "total": 0}
        rows.append({
            "code": c, "desc": it.description, "unit": it.unit,
            "sub": sub.get(c, "Other"), "acts": inv.get(c, []),
            "qty": fv(it.qty_per_room), "planned": fv(it.planned_total),
            "used": fv(it.used), "remaining": fv(it.remaining),
            "pct": fv(it.progress_pct), "mapped": bool(inv.get(c)),
            "rooms": int(it.rooms), "in_room": bool(it.in_room),
            "rate": fv(it.rate), "quick": c in quick_codes,
            "done_val": fv(it.done_value), "rem_val": fv(it.remaining_value),
            "full_val": fv(it.full_value), "install_pct": fv(it.install_pct),
            "install_rate": fv(it.install_rate),
            "install_pct_own": fv(own_install_pct.get(c)),
            "room_done": rb["done"], "room_progress": rb["in_progress"],
            "room_pending": rb["not_started"], "room_total": rb["total"],
            "planned_override": c in planned_over,
            "has_room_groups": bool(it.get("has_room_groups", False)),
        })

    # activity % and overall % are the MEAN of member items' % (item-driven)
    # for a normal activity -- but a LABOUR-ONLY activity (no BOQ material at
    # all, e.g. Zari work, core-cutting, chasing, testing -- see
    # activity.is_labour_keyword()) has no items to average, so its % comes
    # straight from its own directly-recorded value instead. Never both at
    # once for the same activity: while `labour` is on for it, its item-based
    # `codes` are ignored here even if some happen to still be mapped (the
    # frontend hides the "add items" controls while labour-only is on, and
    # only offers the toggle in the first place when there are none).
    pctmap = {str(r.item_code): (r.progress_pct if r.progress_pct == r.progress_pct else 0.0)
              for r in ip.itertuples()}
    labour = set(_labour_only(d).get(service) or [])
    aprog = _activity_prog(d).get(service, {})
    act_pct, labour_pct = {}, {}
    for a in acts:
        if a in labour:
            node = aprog.get(a, {})
            val = node.get(room) if room and room in node else node.get("*")
            pct = round(float(val) * 100, 1) if val is not None else 0.0
            act_pct[a] = pct
            labour_pct[a] = pct
        else:
            codes = [str(x) for x in m.get(service, a)]
            vals = [pctmap.get(c, 0.0) for c in codes if c in pctmap]
            act_pct[a] = round(sum(vals) / len(vals), 1) if vals else None
    tracked = [pctmap[c] for c in pctmap if inv.get(c)] + list(labour_pct.values())
    overall = round(sum(tracked) / len(tracked), 1) if tracked else 0.0
    labour_suggested = {a: activity.is_labour_keyword(a) for a in acts}

    return {
        "service": service, "room": room,
        "activities": acts,
        "mapping": {a: [str(x) for x in m.get(service, a)] for a in acts},
        "act_pct": act_pct,
        "overall_pct": overall,
        "items": rows,
        "pnl_by_activity": rp["by_activity"],
        "pnl_totals": rp["totals"],
        "pnl_unmapped_value": rp["unmapped_value"],
        "item_rooms": rooms_cfg,
        "item_progress": prog,
        "item_room_qty": room_qty_groups,
        "unmapped": m.unmapped(service, items.item_code.dropna().astype(str).tolist()),
        "labour_only": {a: (a in labour) for a in acts},
        "labour_pct": labour_pct,
        "labour_suggested": labour_suggested,
    }


def _service_view(d, service, room=None):
    """Everything the UI needs for one service. Per-item, per-room progress:
    each BOQ item has its own completion, so sliders never couple. `room` scopes
    the numbers to a single room (the drill-down).

    Loads + computes `items`/`used` fresh, then hands off to
    _service_view_core() -- this is every caller's entry point EXCEPT
    overall(), which pre-loads/computes these once and calls
    _service_view_core() directly to avoid doing it twice (see that
    function's own docstring)."""
    items = _load_boq(d, service)
    if items.empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    all_rooms = _all_room_ids(d)
    prog = _item_prog(d).get(service, {})
    rooms_cfg = _item_rooms(d).get(service, {})
    room_qty_groups = _item_room_qty(d).get(service, {})
    used = itemprog.compute(items, prog, rooms_cfg, all_rooms,
                            _planned(d, service), room=room, room_qty_groups=room_qty_groups)
    return _service_view_core(d, service, items, used, prog, rooms_cfg,
                              room_qty_groups, all_rooms, room=room)


# ----------------------------------------------------------------- routes
@router.get("/{slug}")
def get_state(slug: str):
    """Top-level state: structure tree, services present, per-service progress %
    and the currently servable services."""
    d = _need(slug)
    struct = _read_json(d / "structure.json", None)
    activities = _read_json(d / "activities.json", {}) or {}
    boqdf = _load_boq(d)
    services = sorted(boqdf.service.unique().tolist()) if not boqdf.empty else []
    prog = _load_progress(d).df
    summary = progress.project_summary(prog) if len(prog) else {"by_service": {}, "overall": 0, "rooms": 0}
    nrooms = structure.Structure.from_dict(struct).count_rooms() if struct else 0
    return {
        "slug": _slugify(slug),
        "structure": struct,
        "rooms": nrooms,
        "services": services,
        "activities": activities,
        "progress_summary": summary,
        "has_boq": not boqdf.empty,
        "settings": _settings(d),
    }


def _waste_for(d, slug, service, items=None, used=None):
    """Wasted / saved ₹ for one service (supplied vs consumed), best-effort:
    needs a linked forecast run. Returns {wasted, saved, caveat, recorded_pct}.

    `caveat`/`recorded_pct` are pnl.waste_summary()'s OWN quantity-weighted
    "how much of this service's planned work is actually recorded as done"
    figure -- passed through as-is, never recomputed here. /overall used to
    build its own separate low-progress check from an unrelated item-count
    average across every service (most of which have nothing to do with the
    waste figure at all, being unrated), which could -- and did, on real data
    -- disagree with the honest per-service number sitting right there in
    pnl.py, showing a contradictory "94% value complete" ring next to
    "Only 0.0% recorded" waste caveat on the same page. Propagating the real
    number pnl.py already computed removes that whole class of mismatch.

    `items`/`used` are optional pre-computed inputs -- overall() is the only
    caller, and it already loads `items` and runs itemprog.compute() once
    for its own service view; passing those straight in here means this no
    longer repeats that exact same load + compute a second time (profiling
    showed that repeat as roughly a quarter of one /overall call). Any other
    caller that doesn't pass them keeps the old, fully self-contained
    behaviour -- loads and computes fresh, unchanged."""
    try:
        if items is None:
            items = _load_boq(d, service)
        if items.empty:
            return {"wasted": 0.0, "saved": 0.0, "caveat": None, "recorded_pct": None}
        if used is None:
            used = itemprog.compute(items, _item_prog(d).get(service, {}),
                                    _item_rooms(d).get(service, {}), _all_room_ids(d),
                                    _planned(d, service), room_qty_groups=_item_room_qty(d).get(service, {}))
        actual = _actual_consumed(slug, service, items)
        # install_pct/default passed through for consistency with every other
        # compute_item_pnl() call -- waste_value itself stays on the full
        # rate regardless (see pnl.py), this only keeps this frame's
        # planned/done/remaining columns from silently disagreeing with the
        # ones the rest of the page already shows, if anything here ever
        # starts reading them.
        ip = pnl.compute_item_pnl(used, rates=_rates(d, service), actual_consumed=actual,
                                  install_pct=_install_pct(d, service),
                                  default_install_pct=_settings(d).get("default_install_pct"))
        w = pnl.waste_summary(ip)
        if w.get("available"):
            return {"wasted": w.get("wasted_value") or 0.0, "saved": w.get("saved_value") or 0.0,
                    "caveat": w.get("caveat"), "recorded_pct": w.get("progress_pct")}
    except Exception:
        pass
    return {"wasted": 0.0, "saved": 0.0, "caveat": None, "recorded_pct": None}


@router.get("/{slug}/overall")
def overall(slug: str):
    """Whole-site rollup across every service — the top-level "how far is the
    project" number the PM wants: total % complete, ₹ work done / remaining, and
    material waste, plus a per-service breakdown. Generic across hotel/mall/
    hospital because it just sums whatever services the BOQ has."""
    d = _need(slug)
    boqdf = _load_boq(d)
    if boqdf.empty:
        raise HTTPException(404, "no BOQ uploaded yet")
    services = sorted(boqdf.service.unique().tolist())
    per, all_pcts = {}, []
    tot = {"done": 0.0, "planned": 0.0, "remaining": 0.0, "waste": 0.0, "saved": 0.0, "full": 0.0}
    m = _load_mapping(d)
    all_rooms = _all_room_ids(d)
    # Loaded ONCE for the whole route. Each of these already holds every
    # service's own data in one file -- re-reading + re-json.loads()-ing the
    # WHOLE file per service inside the loop below (the old code's
    # `_item_prog(d).get(svc, {})` etc., called both for rooms_data AND a
    # second time inside the old _waste_for()) meant e.g. item_progress.json
    # alone was parsed 3x per service on a 6-service project. Reading each
    # store exactly once here and indexing by service in the loop is the
    # same data, same result, without the repeat I/O + JSON parsing.
    all_item_prog = _item_prog(d)
    all_item_rooms = _item_rooms(d)
    all_item_room_qty = _item_room_qty(d)
    rooms_data = []   # for itemprog.project_room_status -- one tuple per service
    waste_caveats = []   # honest, service-scoped caveats straight from pnl.waste_summary
    for svc in services:
        # reuse the already-loaded boqdf instead of hitting disk again via
        # _load_boq(d, svc) -- boqdf was already read once at the top of this
        # route; filtering it in memory is the same result for zero extra I/O.
        items_svc = boqdf[boqdf.service == svc]
        if items_svc.empty:
            continue
        prog_svc = all_item_prog.get(svc, {})
        rooms_cfg_svc = all_item_rooms.get(svc, {})
        room_qty_groups_svc = all_item_room_qty.get(svc, {})
        # Computed ONCE per service, then shared below by both the normal
        # service view (_service_view_core) and its waste calc (_waste_for)
        # -- this exact (items, used) pair used to be loaded and run through
        # itemprog.compute() a SECOND time inside the old _waste_for() for
        # every service on every /overall call. Real profiling on a
        # synthetic Hyatt-scale project (108 rooms, 385 materials, 540
        # items) showed that repeat as roughly a quarter of this route's
        # total time by itself.
        used_svc = itemprog.compute(items_svc, prog_svc, rooms_cfg_svc, all_rooms,
                                    _planned(d, svc), room_qty_groups=room_qty_groups_svc)
        view = _service_view_core(d, svc, items_svc, used_svc, prog_svc,
                                  rooms_cfg_svc, room_qty_groups_svc, all_rooms)
        t = view["pnl_totals"]
        done = t.get("done_value") or 0.0
        planned = t.get("planned_value") or 0.0
        rem = t.get("remaining_value") or 0.0
        full = t.get("full_value") or 0.0
        w = _waste_for(d, slug, svc, items=items_svc, used=used_svc)
        tot["done"] += done; tot["planned"] += planned; tot["remaining"] += rem
        tot["full"] += full
        tot["waste"] += w["wasted"]; tot["saved"] += w["saved"]
        if w.get("caveat"):
            waste_caveats.append(f"{svc}: {w['caveat']}")
        for it in view["items"]:
            if it.get("qty") and it.get("mapped"):
                all_pcts.append(it.get("pct") or 0.0)
        all_pcts.extend(view["labour_pct"].values())
        per[svc] = {"pct": view["overall_pct"], "done_value": round(done, 2),
                    "remaining_value": round(rem, 2), "planned_value": round(planned, 2),
                    "full_value": round(full, 2),
                    "items": len(view["items"]), "waste_value": round(w["wasted"], 2),
                    "waste_recorded_pct": w.get("recorded_pct")}
        mapped_codes = {code for a in m.activities(svc) for code in m.get(svc, a)}
        # 5-tuple form (room_qty_groups last) -- project_room_status() uses a
        # grouped item's real group-union rooms instead of item_rooms.json.
        rooms_data.append((items_svc, prog_svc, rooms_cfg_svc, mapped_codes, room_qty_groups_svc))
    overall_pct = round(sum(all_pcts) / len(all_pcts), 1) if all_pcts else 0.0
    rooms_summary = (itemprog.project_room_status(rooms_data, all_rooms)
                      if all_rooms else {"done": 0, "in_progress": 0,
                                          "not_started": 0, "total": 0})
    pct_value = round(100.0 * tot["done"] / tot["planned"], 1) if tot["planned"] > 0 else 0.0
    # Each service's own waste caveat already comes from pnl.waste_summary()'s
    # quantity-weighted % of THAT service's planned work -- the same basis
    # already shown as that service's "% complete" in the by-service list, so
    # it can never contradict what's sitting right next to it on this same
    # page. (The old version of this check used a raw item-count average
    # across every service, unrelated to which service the waste even came
    # from -- on real data that showed a "94% value complete" ring next to
    # "Only 0.0% recorded" waste text, because waste here only ever comes
    # from rated services, and the stale check averaged in every unrated
    # service's 0% alongside them.)
    waste_caveat = " | ".join(waste_caveats) if waste_caveats else None
    return {
        "overall_pct": overall_pct, "pct_value_done": pct_value,
        "done_value": round(tot["done"], 2), "planned_value": round(tot["planned"], 2),
        "remaining_value": round(tot["remaining"], 2), "full_value": round(tot["full"], 2),
        "waste_value": round(tot["waste"], 2), "saved_value": round(tot["saved"], 2),
        "waste_caveat": waste_caveat,
        "services": services, "by_service": per,
        "rooms_summary": rooms_summary,
    }


@router.get("/{slug}/service/{service}")
def get_service(slug: str, service: str, room: str = None):
    return _service_view(_need(slug), service, room=room)


@router.post("/{slug}/progress/item")
def set_item_progress(slug: str, payload: dict):
    """Independent per-item progress. Body:
    {"service","item_code","frac"(0..1), "room"?}. room omitted => all rooms."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    store = _item_prog(d)
    itemprog.set_progress(store, svc, code, payload.get("frac", 0), room=payload.get("room"))
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    _log_dpr_change(d, svc, payload.get("room"), code, frac=payload.get("frac", 0))
    return _service_view(d, svc, room=payload.get("room"))


@router.post("/{slug}/progress/activity")
def set_activity_progress(slug: str, payload: dict):
    """Labour-only activity progress -- no BOQ item involved at all (Zari
    work, core-cutting, chasing, testing...). Body: {"service","activity",
    "frac"(0..1),"room"?}. Only meaningful once that activity has been
    toggled on via /activity-labour; setting a value before that is
    harmless (it's just recorded, unused until the toggle is on) rather than
    rejected, matching this codebase's general "never invent, but never
    block a value being recorded either" stance elsewhere.

    itemprog.set_progress() is completely generic on its `code` argument
    (just a dict key -- no item/BOQ lookups inside it), so it's reused as-is
    here keyed by activity name instead of item_code, rather than
    duplicating its clamp-to-[0,1] and room-override-reset logic."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    if not svc or not act:
        raise HTTPException(400, "service and activity are required")
    store = _activity_prog(d)
    itemprog.set_progress(store, svc, act, payload.get("frac", 0), room=payload.get("room"))
    (d / "activity_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    return _service_view(d, svc, room=payload.get("room"))


@router.post("/{slug}/activity-labour")
def set_activity_labour(slug: str, payload: dict, room: str = None):
    """Toggle labour-only (%-only, no BOQ material) tracking for one
    activity. Body: {"service","activity","on":bool}.

    Turning ON is refused if the activity currently has 1+ BOQ items
    mapped to it -- an activity is either item-tracked or labour-tracked,
    never both at once (see _service_view_core's own note on this), and a
    stale UI offering the toggle for an activity that already has real
    item data would otherwise silently start ignoring that data's
    percentage rather than erroring. The frontend only shows the toggle's
    entry point when there are zero mapped items in the first place, so
    this is a safety net, not the primary guard.

    Turning OFF never deletes the recorded % in activity_progress.json --
    it just stops counting it, exactly like an item's recorded progress
    surviving its activity mapping being removed (pnl.rollup_pnl's own
    docstring covers the same "recorded work is never silently erased"
    principle for items)."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    if not svc or not act:
        raise HTTPException(400, "service and activity are required")
    store = _labour_only(d)
    cur = set(store.get(svc, []))
    if payload.get("on"):
        m = _load_mapping(d)
        mapped = m.get(svc, act)
        if mapped:
            raise HTTPException(400, f"'{act}' already has {len(mapped)} BOQ item(s) mapped "
                                    "-- unmap them first to track this activity by % instead")
        cur.add(act)
    else:
        cur.discard(act)
    store[svc] = sorted(cur)
    (d / "labour_only.json").write_text(json.dumps(store, ensure_ascii=False))
    return _service_view(d, svc, room=room)


@router.post("/{slug}/item-rooms")
def set_item_rooms(slug: str, payload: dict, room: str = None):
    """Which rooms a BOQ item is installed in. Body:
    {"service","item_code","rooms":[room_id,...]}. Empty => all rooms."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    store = _item_rooms(d)
    itemprog.set_rooms(store, svc, code, payload.get("rooms") or [])
    (d / "item_rooms.json").write_text(json.dumps(store, ensure_ascii=False))
    return _service_view(d, svc, room=room)


@router.post("/{slug}/item-room-qty")
def set_item_room_qty(slug: str, payload: dict, room: str = None):
    """The real per-room-quantity workflow (see itemprog.py's module
    docstring and set_room_qty_group()): the SAME "Rooms" chip used for
    plain applicability, but with a real quantity attached to whichever
    rooms are ticked. Body: {"service","item_code","rooms":[room_id,...],
    "qty": float|null}.

    Calling this more than once for the same item with DIFFERENT room
    selections builds up multiple groups (e.g. 100 standard rooms at one
    qty, 9 corner rooms at another) -- compute() then sums every group's
    own qty x room count for the item's true planned total, instead of a
    single uniform "qty x every applicable room" that can't represent real
    per-room variance at all. `qty: null` (or an empty `rooms` list) removes
    those rooms from every group without adding a new one -- how you shrink
    or delete a group.

    This is a genuinely different mechanism from /item-rooms (plain yes/no
    applicability) -- an item using groups here has its applicability
    entirely defined by the union of its groups, not item_rooms.json, the
    moment it has even one group. The two stores are not meant to both
    apply to the same item at once; the frontend's room-qty modal is what
    decides which one an item actually uses."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    store = _item_room_qty(d)
    itemprog.set_room_qty_group(store, svc, code, payload.get("rooms") or [], payload.get("qty"))
    (d / "item_room_qty.json").write_text(json.dumps(store, ensure_ascii=False))
    return _service_view(d, svc, room=room)


@router.post("/{slug}/mark-rooms-done")
def mark_rooms_done(slug: str, payload: dict, room: str = None):
    """Mark a set of rooms 100% done (or undo that -- see `done` below) for
    one item, in a single call -- the companion action to /item-room-qty in
    the exact same Rooms modal: tick rooms (the same tick-list either way),
    then either save a quantity for them (existing) or mark them done
    (this). Body: {"service","item_code","rooms":[room_id,...],
    "done": bool (default True)}.

    This exists because the ONLY other way to mark a room done was the
    item's own single overall %/quantity slider in the "All rooms" view --
    which sets the '*' fraction and, by itemprog.set_progress()'s own
    documented behaviour, WIPES every existing per-room override to keep
    the bar uniform. An engineer who had already marked individual rooms
    done, then touched that overall slider by mistake, would see their
    real per-room progress silently flatten to one number -- a real
    reported accident this route is built to prevent, not just a
    convenience. Each room here gets its OWN per-room override
    (frac=1.0 or 0.0) via itemprog.set_progress(..., room=room_id) --
    called once per room, never the room=None ("*") global path -- so this
    can never wipe any other room's state, marked-done or still in
    progress, the way the overall slider can. Ticking an already-matching
    room (already done when done=True, already not-started when
    done=False) is a harmless no-op; an unticked room is simply left
    untouched.

    `done=False` is the direct undo for a room ticked/marked done by
    mistake -- a second reported real accident (tick the wrong room while
    ticking a batch, or mark done then realise the work wasn't actually
    finished). It sets the SAME ticked rooms back to 0% (not started), via
    the identical per-room mechanism, so it carries the exact same "never
    wipes another room" guarantee as marking done does -- undo is not a
    special case, it's the same action with the opposite target value."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    room_ids = [str(r) for r in (payload.get("rooms") or [])]
    done = payload.get("done", True)
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    if not room_ids:
        raise HTTPException(400, "tick at least one room")
    store = _item_prog(d)
    frac = 1.0 if done else 0.0
    for rid in room_ids:
        itemprog.set_progress(store, svc, code, frac, room=rid)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    if done:   # an undo is not "work done today" -- never logged as one
        for rid in room_ids:
            _log_dpr_change(d, svc, rid, code, frac=1.0)
    return _service_view(d, svc, room=room)


@router.post("/{slug}/init-from-tracker")
async def init_from_tracker(slug: str, file: UploadFile = File(...),
                            project_name: str = Form("")):
    """Zero-setup: upload the progress tracker → build structure, activities and
    the progress grid. Safe to re-run; it refreshes these three, and does NOT
    touch mapping/rates the engineer has already set."""
    if not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(400, "upload an Excel tracker (.xlsx)")
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "tracker.xlsx"
    _save_upload(file, dest)
    try:
        s = structure.from_tracker(str(dest), name=project_name or slug)
        acts = activity.parse_activities(str(dest))
        prog = progress.parse_progress(str(dest))
    except Exception as e:
        raise HTTPException(422, f"could not read this tracker: {e}")
    (d / "structure.json").write_text(s.to_json())
    (d / "activities.json").write_text(json.dumps(acts, ensure_ascii=False))
    prog.to_parquet(d / "progress.parquet")
    return {"slug": _slugify(slug), "floors": len(s.containers("floor")),
            "rooms": s.count_rooms(),
            "services": sorted(acts.keys()),
            "activities": {k: len(v) for k, v in acts.items()},
            "progress_rows": int(len(prog))}


@router.post("/{slug}/boq")
async def upload_boq(slug: str, file: UploadFile = File(...)):
    """Upload the BOQ workbook → parse every service sheet, store line items,
    and seed a suggested activity→item mapping for any service not already
    mapped (never overwrites an existing mapping the engineer edited).

    A ProjectBase-sourced service (see boq.py) gets its real rates AND its
    whole-project planned quantities seeded automatically here too -- zero
    wizard steps, since ProjectBase's own Design Quantity convention is
    already known (always whole-project, never per-room). A raw/MEPF sheet
    gets neither: it has no rate column to read, and whether its qty means
    "per room" or "already the total" is genuinely ambiguous from the data
    alone -- that's the `needs_qty_mode` list in the response, which the
    frontend wizard turns into one toggle per flagged service (see
    /{slug}/qty-mode below). A "mixed" service (some sheets ProjectBase,
    some raw, merged under one service name) also needs the toggle, since
    inheriting the ProjectBase sibling's convention for the raw rows would
    be exactly the kind of guess this app doesn't make."""
    if not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(400, "upload an Excel BOQ (.xlsx)")
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "boq.xlsx"
    _save_upload(file, dest)
    try:
        parsed, skipped = boq.parse_workbook(str(dest))
    except Exception as e:
        raise HTTPException(422, f"could not read this BOQ: {e}")
    if not parsed:
        raise HTTPException(422, "no BOQ sheets detected in this workbook")

    # seed rates.json / planned.json from whatever ProjectBase-sourced data
    # came through, before the (ephemeral, pipeline-only) `rate` column gets
    # dropped -- rates.json/planned.json are the canonical, durable homes
    # for this, boq.parquet never carries a rate column.
    all_rates = _read_json(d / "rates.json", {}) or {}
    all_planned = _read_json(d / "planned.json", {}) or {}
    needs_qty_mode, auto_rated = [], {}
    for svc, r in parsed.items():
        svc_rates = all_rates.setdefault(svc, {})
        new_rates = 0
        for code, rate in (r.get("rates") or {}).items():
            if code not in svc_rates:          # never clobber a manual rate edit
                svc_rates[code] = rate
                new_rates += 1
        if new_rates:
            auto_rated[svc] = new_rates

        if r.get("qty_is_total") is True:
            svc_planned = all_planned.setdefault(svc, {})
            for it in r["items"].itertuples():
                code = str(it.item_code)
                qty = getattr(it, "qty", None)
                if code not in svc_planned and qty is not None and qty == qty:  # qty==qty excludes NaN
                    svc_planned[code] = float(qty)
        elif r.get("source_format") != "raw" or r.get("qty_is_total") is None:
            # "mixed", or any future non-raw format that hasn't resolved a
            # convention -- ambiguous, needs the engineer's call
            needs_qty_mode.append(svc)

    (d / "rates.json").write_text(json.dumps(all_rates, ensure_ascii=False))
    (d / "planned.json").write_text(json.dumps(all_planned, ensure_ascii=False))

    frames = []
    for r in parsed.values():
        items = r["items"]
        if "rate" in items.columns:
            items = items.drop(columns=["rate"])
        frames.append(items)
    allitems = pd.concat(frames, ignore_index=True)
    allitems.to_parquet(d / "boq.parquet")

    # seed suggested mapping, but only for activities that are still
    # genuinely empty. IMPORTANT: this checks per-ACTIVITY item codes, not
    # "does this service exist in mapping.json" -- a freshly-created activity
    # always starts with zero codes, and the old check (`m.data.get(svc)`)
    # treated the service KEY merely existing as "already configured", which
    # silently blocked auto-seeding forever the moment even one empty
    # activity was created for that service. Real, non-empty activities are
    # never touched here -- only ones with zero item codes get a suggestion.
    m = _load_mapping(d)
    acts = _read_json(d / "activities.json", {}) or {}
    seeded = {}
    for svc, r in parsed.items():
        svc_acts = acts.get(svc, [])
        if not svc_acts:
            continue
        empty_acts = [a for a in svc_acts if not m.get(svc, a)]
        if not empty_acts:
            continue                   # every activity here already has real items
        sug = activity.suggest(empty_acts, r["items"], svc)
        for a, codes in sug.items():
            if codes:
                m.set(svc, a, codes)
        seeded_here = {a: len(c) for a, c in sug.items() if c}
        if seeded_here:
            seeded[svc] = seeded_here
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))

    return {"slug": _slugify(slug),
            "services": {s: int(len(r["items"])) for s, r in parsed.items()},
            "skipped": [s["sheet"] for s in skipped],
            "seeded_mapping": seeded,
            "auto_rated": auto_rated,
            "needs_qty_mode": needs_qty_mode}


@router.post("/{slug}/qty-mode")
def set_qty_mode(slug: str, payload: dict):
    """The wizard step for a RAW (non-ProjectBase) BOQ service: does this
    sheet's qty column mean "one typical unit, multiply by every applicable
    room/zone" (the existing default -- nothing to do here), or "already
    the whole-project total" (auto-seed planned.json overrides from the
    BOQ's own qty, same mechanism a ProjectBase source gets automatically)?

    Body: {"service": str, "mode": "total"|"per_room"}. "per_room" is a
    no-op (that's already the default behaviour) -- this only ever writes
    when mode=="total". Never overwrites a planned qty the engineer already
    set some other way (via /planned or a prior call here)."""
    d = _need(slug)
    svc, mode = payload.get("service"), payload.get("mode")
    if not svc or mode not in ("total", "per_room"):
        raise HTTPException(400, "service and mode ('total' or 'per_room') are required")
    if mode == "per_room":
        return {"service": svc, "mode": mode, "seeded": 0}
    items = _load_boq(d, svc)
    if items.empty:
        raise HTTPException(404, f"no BOQ items for service {svc}")
    allp = _read_json(d / "planned.json", {}) or {}
    svc_planned = allp.setdefault(svc, {})
    seeded = 0
    for it in items.itertuples():
        code = str(it.item_code)
        qty = getattr(it, "qty", None)
        if code not in svc_planned and qty is not None and qty == qty:
            svc_planned[code] = float(qty)
            seeded += 1
    (d / "planned.json").write_text(json.dumps(allp, ensure_ascii=False))
    return {"service": svc, "mode": mode, "seeded": seeded}


@router.post("/{slug}/activities")
def edit_activities(slug: str, payload: dict, room: str = None):
    """Let the engineer manage the activity list for a service themselves
    (create / rename / delete), not only the ones read from the tracker.
    Body: {"service", "action": "add|rename|delete", "name", "new_name"}.
    Returns the refreshed service view."""
    d = _need(slug)
    svc = payload.get("service")
    action = payload.get("action")
    name = payload.get("name")
    if not svc or not action:
        raise HTTPException(400, "service and action are required")
    acts_all = _read_json(d / "activities.json", {}) or {}
    lst = acts_all.get(svc, [])
    if action == "add":
        if not name:
            raise HTTPException(400, "name required")
        if name not in lst:
            lst.append(name)
    elif action == "rename":
        new = payload.get("new_name")
        if not name or not new:
            raise HTTPException(400, "name and new_name required")
        lst = [new if a == name else a for a in lst]
        # carry the mapping across the rename so items aren't lost
        m = _load_mapping(d)
        if name in m.data.get(svc, {}):
            m.data[svc][new] = m.data[svc].pop(name)
            (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))
    elif action == "delete":
        lst = [a for a in lst if a != name]
        m = _load_mapping(d)
        if name in m.data.get(svc, {}):
            m.data[svc].pop(name)
            (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))
    else:
        raise HTTPException(400, f"unknown action '{action}'")
    acts_all[svc] = lst
    (d / "activities.json").write_text(json.dumps(acts_all, ensure_ascii=False))
    return _service_view(d, svc, room=room)


# ---- planned-quantity overrides (per BOQ item, editable) --------------
def _planned(d, service):
    return (_read_json(d / "planned.json", {}) or {}).get(service, {})


def _apply_planned(used, overrides):
    """Replace auto planned_total (boq_qty x rooms) with an engineer override
    where given, and recompute remaining / progress% from it."""
    if not overrides or used is None or len(used) == 0:
        return used
    for i in used.index:
        code = str(used.at[i, "item_code"])
        if code in overrides:
            try:
                p = float(overrides[code])
            except (TypeError, ValueError):
                continue
            u = float(used.at[i, "used"] or 0)
            used.at[i, "planned_total"] = round(p, 3)
            used.at[i, "remaining"] = round(max(p - u, 0.0), 3)
            used.at[i, "progress_pct"] = round(100.0 * u / p, 1) if p > 0 else 0.0
    return used


@router.post("/{slug}/planned")
def save_planned(slug: str, payload: dict, room: str = None):
    """Set the WHOLE-PROJECT planned quantity for a BOQ item (overrides the
    auto boq_qty x applicable-rooms). Body: {"service","item_code","planned"}
    -- "planned": null clears the override, falling back to auto again.

    There is no per-room planned override in this data model -- an item's
    planned figure is always project-wide, exactly like its ₹ rate. This
    route used to silently accept a `room` query param and just ignore it
    for the actual save (only using it to shape the returned view) --
    meaning editing "planned" while drilled into a single room quietly
    overwrote the WHOLE item's total with whatever number that one room's
    view happened to be showing, while the room view's own label read
    "planned HERE" -- implying (wrongly) that it was scoped to that room.
    Once set, that stale override then permanently wins over the auto
    qty x rooms calculation, so even correctly fixing an item's room-
    applicability (the 🏠 chip) afterwards had no visible effect on Overall
    -- a real, previously-undiagnosed bug traced end to end from a report
    that the room-applicability chip "wasn't working". Fixed at the root:
    this route now refuses to save while `room` is set at all, and the
    per-service page never offers an editable planned control inside a
    room drill-down in the first place (see rowHTML in siteprogress.js)."""
    if room:
        raise HTTPException(400, "planned quantity is whole-project only — "
                             "edit it from the service view, not a single room")
    d = _need(slug)
    svc, code = payload.get("service"), payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    allp = _read_json(d / "planned.json", {}) or {}
    p = payload.get("planned")
    if p is None:
        allp.setdefault(svc, {}).pop(str(code), None)
    else:
        allp.setdefault(svc, {})[str(code)] = p
    (d / "planned.json").write_text(json.dumps(allp, ensure_ascii=False))
    return _service_view(d, svc, room=None)


@router.post("/{slug}/activity")
def edit_activity(slug: str, payload: dict, room: str = None):
    """Engineer-driven activity management (create / rename / delete).
    Body: {"service","op":"create|rename|delete","name", "new_name"?}.
    Keeps activities.json, mapping.json and the progress grid consistent."""
    d = _need(slug)
    svc = payload.get("service")
    op = payload.get("op")
    name = payload.get("name")
    if not svc or not op or not name:
        raise HTTPException(400, "service, op and name are required")
    acts_all = _read_json(d / "activities.json", {}) or {}
    acts = acts_all.get(svc, [])
    m = _load_mapping(d)

    if op == "create":
        if name not in acts:
            acts.append(name)
        m.set(svc, name, m.get(svc, name))          # ensure a (possibly empty) mapping slot
    elif op == "rename":
        new = payload.get("new_name")
        if not new:
            raise HTTPException(400, "new_name required for rename")
        acts = [new if a == name else a for a in acts]
        md = m.data.get(svc, {})
        if name in md:
            md[new] = md.pop(name)
        _rename_progress_activity(d, svc, name, new)
    elif op == "delete":
        acts = [a for a in acts if a != name]
        m.data.get(svc, {}).pop(name, None)
        _delete_progress_activity(d, svc, name)
    else:
        raise HTTPException(400, f"unknown op '{op}'")

    acts_all[svc] = acts
    (d / "activities.json").write_text(json.dumps(acts_all, ensure_ascii=False))
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))
    return _service_view(d, svc, room=room)


def _rename_progress_activity(d, svc, old, new):
    p = d / "progress.parquet"
    if not p.exists():
        return
    df = pd.read_parquet(p)
    mask = (df.service == svc) & (df.activity == old)
    if mask.any():
        df.loc[mask, "activity"] = new
        df.to_parquet(p)


def _delete_progress_activity(d, svc, name):
    p = d / "progress.parquet"
    if not p.exists():
        return
    df = pd.read_parquet(p)
    df = df[~((df.service == svc) & (df.activity == name))]
    df.to_parquet(p)


@router.post("/{slug}/mapping")
def save_mapping(slug: str, payload: dict, room: str = None):
    """Configure-once: set which BOQ items belong to one activity.
    Body: {"service": "...", "activity": "...", "codes": ["2.16", ...]}
    Returns the refreshed service view so the UI updates live."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    codes = payload.get("codes", [])
    if not svc or act is None:
        raise HTTPException(400, "service and activity are required")
    m = _load_mapping(d)
    m.set(svc, act, [str(c) for c in codes])
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))
    return _service_view(d, svc, room=room)


@router.post("/{slug}/progress")
def set_progress(slug: str, payload: dict):
    """Slider drag / tick edit. Body:
    {"service","floor","room","activity","frac"} (frac 0..1).
    Persists and returns the refreshed service view (live % + ₹)."""
    d = _need(slug)
    svc = payload.get("service")
    for k in ("floor", "room", "activity"):
        if payload.get(k) is None:
            raise HTTPException(400, f"{k} is required")
    store = _load_progress(d)
    store.set(svc, payload["floor"], payload["room"], payload["activity"],
              float(payload.get("frac", 0)))
    store.to_parquet(d / "progress.parquet")
    return _service_view(d, svc, room=payload["room"])


@router.post("/{slug}/progress/bulk")
def set_progress_bulk(slug: str, payload: dict, room: str = None):
    """Set one activity's completion across EVERY room in one call — what the
    activity/item slider uses, so a drag is a single request not one-per-room.
    Body: {"service","activity","frac"}."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    if not svc or act is None:
        raise HTTPException(400, "service and activity are required")
    frac = max(0.0, min(1.0, float(payload.get("frac", 0))))
    tick = "\u2713" if frac >= 1 else ("~" if frac > 0 else "\u2717")
    store = _load_progress(d)
    df = store.df
    mask = (df.service == svc) & (df.activity == act)
    if mask.any():
        df.loc[mask, "frac"] = frac
        df.loc[mask, "tick"] = tick
    else:
        # activity had no rows (not in the tracker grid) — seed one per room
        struct = _read_json(d / "structure.json", None)
        if struct:
            for rm in structure.Structure.from_dict(struct).rooms():
                floor = rm["path"][-1] if rm["path"] else "?"
                df.loc[len(df)] = [svc, floor, act, rm["name"], tick, frac]
    store.to_parquet(d / "progress.parquet")
    return _service_view(d, svc, room=room)


@router.post("/{slug}/rates")
def save_rates(slug: str, payload: dict, room: str = None):
    """Set rates and, optionally, per-item payment-term overrides in one
    save — same "Set rates" panel, same click, both stores updated together.

    Body: {"service", "rates": {"2.16": 95, ...},
           "install_pct": {"2.16": 20, ...}}   (install_pct is optional; a
    project with no payment-term feature in use never sends it, and nothing
    below changes for that project.)

    Merges into rates.json and (if given) install_pct.json — an item with no
    key in `install_pct` here keeps whatever it already had (or falls back to
    the project default at read time, never guessed here). Returns the
    refreshed service view."""
    d = _need(slug)
    svc = payload.get("service")
    new = payload.get("rates", {})
    new_install_pct = payload.get("install_pct")
    if not svc:
        raise HTTPException(400, "service is required")
    allr = _read_json(d / "rates.json", {}) or {}
    cur = allr.get(svc, {})
    for k, v in new.items():
        cur[str(k)] = v
    allr[svc] = cur
    (d / "rates.json").write_text(json.dumps(allr, ensure_ascii=False))

    if new_install_pct:
        allip = _read_json(d / "install_pct.json", {}) or {}
        curip = allip.get(svc, {})
        for k, v in new_install_pct.items():
            if v is None:
                curip.pop(str(k), None)     # explicit clear -> back to project default
            else:
                curip[str(k)] = v
        allip[svc] = curip
        (d / "install_pct.json").write_text(json.dumps(allip, ensure_ascii=False))

    return _service_view(d, svc, room=room)


@router.post("/{slug}/settings")
def save_settings(slug: str, payload: dict):
    """Project-level settings. Currently one field: {"default_install_pct":
    15} (or null to clear it). Not scoped to a service or a room, and not
    part of the BOQ-upload wizard — this is meant to be revisited any time
    from the same "Set rates" panel, the same way a rate itself can be
    changed at any time. Merges (only keys present in the payload are
    touched), so future settings fields can be added here without this route
    needing to change."""
    d = _need(slug)
    cur = _settings(d)
    if "default_install_pct" in payload:
        v = payload["default_install_pct"]
        if v is None:
            cur.pop("default_install_pct", None)
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, "default_install_pct must be a number or null")
            if not (0.0 <= v <= 100.0):
                raise HTTPException(400, "default_install_pct must be between 0 and 100")
            cur["default_install_pct"] = v
    (d / "settings.json").write_text(json.dumps(cur, ensure_ascii=False))
    return cur


@router.get("/{slug}/dpr/today")
def dpr_today(slug: str):
    """What the projBar() indicator shows: how many distinct (service,
    location, activity) rows today's captured changes will produce in the
    export -- the exact same grouping key group_for_export() itself uses,
    so this count can never disagree with what the export actually shows."""
    d = _need(slug)
    today = dt.date.today().isoformat()
    todays = [e for e in _dpr_log(d) if e["date"] == today]
    keys = {(e["service"], e["floor"], e["activity"]) for e in todays}
    return {"date": today, "count": len(keys)}


@router.get("/{slug}/export-dpr")
def export_dpr(slug: str, start: str, end: str = None):
    """The full DPR -- Summary + Item detail + Daily updates -- for one date
    (end omitted) or a range (inclusive). Reuses the exact same pnl.py /
    itemprog.py / dpr.py functions every other route here already calls;
    nothing new is computed, only assembled and formatted."""
    d = _need(slug)
    end = end or start
    struct = _read_json(d / "structure.json", None)
    if not struct:
        raise HTTPException(400, "no structure yet")
    location_label = _location_label(d)
    leaf_label = _leaf_label(d)

    boqdf = _load_boq(d)
    services = sorted(boqdf.service.unique().tolist()) if not boqdf.empty else []
    m = _load_mapping(d)
    all_rooms = _all_room_ids(d)

    rollups, item_rows = {}, []
    for svc in services:
        items = boqdf[boqdf.service == svc]
        if items.empty:
            continue
        prog_svc = _item_prog(d).get(svc, {})
        rooms_cfg = _item_rooms(d).get(svc, {})
        room_qty_groups = _item_room_qty(d).get(svc, {})
        planned_over = _planned(d, svc)
        used = itemprog.compute(items, prog_svc, rooms_cfg, all_rooms, planned_over,
                                room_qty_groups=room_qty_groups)
        ip = pnl.compute_item_pnl(used, rates=_rates(d, svc), install_pct=_install_pct(d, svc),
                                  default_install_pct=_settings(d).get("default_install_pct"))
        rp = pnl.rollup_pnl(ip, m, svc)
        rollups[svc] = rp
        mapped_codes = {str(c) for a in m.activities(svc) for c in m.get(svc, a)}
        for it in ip.itertuples():
            code = str(it.item_code)
            if code not in mapped_codes:
                continue   # only items actually inside an activity -- not raw/unmapped BOQ noise
            rb = itemprog.room_buckets(code, prog_svc, rooms_cfg, all_rooms, room_qty_groups=room_qty_groups)
            item_rows.append({
                "service": svc, "item": it.description,
                "planned": round(it.planned_total, 2) if it.planned_total is not None else None,
                "used": round(it.used, 2) if it.used is not None else None,
                "remaining": round(it.remaining, 2) if it.remaining is not None else None,
                "done": rb["done"], "in_progress": rb["in_progress"], "pending": rb["not_started"]})

    proj = pnl.project_pnl(rollups) if rollups else {
        "done_value": 0.0, "remaining_value": 0.0, "full_value": 0.0,
        "waste_value": 0.0, "pct_value_done": 0.0}
    by_service = {svc: {"done_value": rp["totals"]["done_value"],
                        "remaining_value": rp["totals"]["remaining_value"]}
                 for svc, rp in rollups.items()}

    try:
        start_d, end_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start/end must be YYYY-MM-DD")
    if end_d < start_d:
        raise HTTPException(400, "end date is before start date")
    by_date = dpr.entries_for_range(_dpr_log(d), start, end)
    date_list, cur = [], start_d
    while cur <= end_d:
        date_list.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    days = [(ds, dpr.group_for_export(by_date.get(ds, []), services, leaf_label=leaf_label)) for ds in date_list]

    date_label = start if start == end else f"{start} to {end}"
    prog = _load_progress(d).df
    completion = progress.activity_completion(prog) if len(prog) else None
    completion_by_floor = progress.activity_completion(prog, by_floor=True) if len(prog) else None
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    wb = dpr.build_full_export(date_label, proj, by_service, item_rows, days,
                               location_label=location_label, completion=completion,
                               completion_by_floor=completion_by_floor, generated_at=generated_at)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"DPR_{_slugify(slug)}_{start}" + (f"_to_{end}" if end != start else "") + ".xlsx"
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/{slug}/structure/reset")
def reset_structure(slug: str):
    """Wipe the structure so the setup wizard's template picker (hotel / mall /
    hospital / custom / from-tracker) can run again — e.g. the engineer picked
    the wrong shape, or wants to rebuild after big changes on site.

    BOQ, activities, mapping, rates and links survive — none of those depend
    on the room structure at all (an activity's item list, a ₹/unit rate, a
    BOQ-to-stock link are the same facts regardless of how many rooms exist).

    ALL progress is cleared, though — both per-room overrides AND each item's
    overall "*" value. Earlier this kept "*", on the theory that it was real
    recorded work independent of any one room id. That was wrong: "*" is
    still computed against the OLD room count (planned = qty-per-room ×
    however many rooms existed), so a rebuild that changes the room count or
    shape invalidates it exactly like a per-room override — keeping it just
    let a stale % done silently carry over onto a structure it was never
    measured against. This clears every service in one pass, since
    item_progress.json is a single project-wide file, not split per service."""
    d = _need(slug)
    (d / "structure.json").unlink(missing_ok=True)
    (d / "item_rooms.json").unlink(missing_ok=True)
    (d / "item_progress.json").unlink(missing_ok=True)
    (d / "item_room_qty.json").unlink(missing_ok=True)   # groups reference old room ids too
    return {"reset": True}


@router.post("/{slug}/structure")
def save_structure(slug: str, payload: dict):
    """Replace the structure tree (after the engineer edits floors/rooms).

    Deleting a room here used to leave it as a ghost everywhere else: its id
    stayed in item_room_qty.json's groups, item_rooms.json's applicability
    lists, and item_progress.json's per-room overrides, forever. compute()
    itself was always safe (it filters every room reference against the
    CURRENT room set) -- but nothing that just DISPLAYS the raw stored data
    (the Rooms modal's "current groups" summary, the 🏠 chip's room count)
    filtered the same way, so a deleted room's stale entry kept silently
    padding those counts -- "109 of 108 rooms" after deleting one room from
    a 109-room hotel is exactly that: one group still listing the room that
    no longer exists. Pruned here, once, at the source of the change,
    across every service and every item -- the honest fix, rather than
    making every future reader remember to filter defensively too."""
    d = _need(slug)
    tree = payload.get("structure")
    if not tree:
        raise HTTPException(400, "structure is required")
    # validate it round-trips
    s = structure.Structure.from_dict(tree)
    new_room_ids = {r["id"] for r in s.rooms()}
    (d / "structure.json").write_text(s.to_json())
    _prune_deleted_rooms(d, new_room_ids)
    return {"rooms": s.count_rooms(), "floors": len(s.containers("floor"))}


def _prune_deleted_rooms(d, valid_room_ids):
    """Remove every reference to a room id that no longer exists, from all
    three per-room stores, across every service and item. A no-op (and
    cheap) when nothing was actually deleted -- every id it would try to
    remove is already valid and matches nothing."""
    changed = False

    groups_store = _item_room_qty(d)
    for svc, items in groups_store.items():
        for code, groups in list(items.items()):
            kept = []
            for g in groups:
                remaining = [r for r in g.get("rooms", []) if r in valid_room_ids]
                if remaining:
                    kept.append({"rooms": remaining, "qty": g.get("qty")})
                    if len(remaining) != len(g.get("rooms", [])):
                        changed = True
                else:
                    changed = True
            if kept:
                items[code] = kept
            else:
                items.pop(code, None)
                changed = True
    if changed:
        (d / "item_room_qty.json").write_text(json.dumps(groups_store, ensure_ascii=False))

    changed = False
    rooms_store = _item_rooms(d)
    for svc, items in rooms_store.items():
        for code, ids in list(items.items()):
            kept = [r for r in ids if r in valid_room_ids]
            if kept != ids:
                changed = True
                if kept:
                    items[code] = kept
                else:
                    items.pop(code, None)
    if changed:
        (d / "item_rooms.json").write_text(json.dumps(rooms_store, ensure_ascii=False))

    changed = False
    prog_store = _item_prog(d)
    for svc, items in prog_store.items():
        for code, node in items.items():
            for r in [k for k in node.keys() if k != "*" and k not in valid_room_ids]:
                node.pop(r, None)
                changed = True
    if changed:
        (d / "item_progress.json").write_text(json.dumps(prog_store, ensure_ascii=False))


@router.post("/{slug}/structure/template")
def structure_from_template(slug: str, payload: dict):
    """Build the structure from a named template so the app is not hotel-only.

    Body: {"kind":"hotel|mall|hospital|custom", "name":..., plus the template's
    own params, e.g. floors/rooms_per_floor, levels/zones_per_level,
    wings/floors/rooms_per_floor}. The tree is generic, so the same UI renders
    a mall (levels→zones) or hospital (wings→floors→rooms) with no code change.
    """
    kind = (payload.get("kind") or "custom").lower()
    if kind not in structure.TEMPLATES:
        raise HTTPException(400, f"unknown template '{kind}' — "
                                 f"one of {list(structure.TEMPLATES)}")
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    fn = structure.TEMPLATES[kind]
    # pass through only the kwargs this template accepts
    import inspect
    allowed = set(inspect.signature(fn).parameters)
    kwargs = {k: v for k, v in payload.items() if k in allowed and k != "kind"}
    try:
        s = fn(**kwargs)
    except Exception as e:
        raise HTTPException(422, f"could not build structure: {e}")
    (d / "structure.json").write_text(s.to_json())
    # "Rebuild" replaces the whole tree with fresh room ids -- almost every
    # old reference is now stale at once (the same problem save_structure()
    # fixes for a single deleted room, just all-at-once here).
    _prune_deleted_rooms(d, {r["id"] for r in s.rooms()})
    return {"kind": kind, "rooms": s.count_rooms(),
            "nodes": len(s.containers("floor")) + len(s.containers("level"))
            + len(s.containers("wing"))}


@router.get("/{slug}/pnl/{service}")
def service_pnl(slug: str, service: str, room: str = None):
    """₹ rollup for one service. planned/done/remaining are ROOM-SCOPED when
    `room` is given — itemprog already stores progress per room, so a room's
    own done/remaining ₹ is a real number, not a guess (this is what makes
    the room drill-down honest: #3/#8).

    Waste is always WHOLE-SERVICE, even inside a room drill-down, on purpose:
    waste needs the actual quantity consumed from the stock register
    (`_actual_consumed`), and that register has no room column at all — it
    cannot know which room a given OUT movement was for. A "this room's
    waste" number would therefore be an allocated guess, not a fact, and
    Mahesh was explicit that waste must never show an invented number (see
    the Site Progress room-drilldown session notes, item #1). So waste is
    computed from the service's OWN whole-project `used` regardless of which
    room the engineer is currently looking at, and the room parameter never
    touches it."""
    d = _need(slug)
    items = _load_boq(d, service)
    m = _load_mapping(d)
    prog = _item_prog(d).get(service, {})
    rooms_cfg = _item_rooms(d).get(service, {})
    all_rooms = _all_room_ids(d)
    planned_over = _planned(d, service)
    rates = _rates(d, service)
    install_pct = _install_pct(d, service)
    default_install_pct = _settings(d).get("default_install_pct")
    room_qty_groups = _item_room_qty(d).get(service, {})

    used = itemprog.compute(items, prog, rooms_cfg, all_rooms, planned_over, room=room, room_qty_groups=room_qty_groups)
    ip = pnl.compute_item_pnl(used, rates=rates, install_pct=install_pct,
                              default_install_pct=default_install_pct)
    rp = pnl.rollup_pnl(ip, m, service)
    proj = pnl.project_pnl({service: rp})

    used_whole = used if room is None else itemprog.compute(
        items, prog, rooms_cfg, all_rooms, planned_over, room=None, room_qty_groups=room_qty_groups)
    actual = _actual_consumed(slug, service, items)
    ip_whole = pnl.compute_item_pnl(used_whole, rates=rates, actual_consumed=actual,
                                    install_pct=install_pct, default_install_pct=default_install_pct)
    waste = pnl.waste_summary(ip_whole)

    return {"service": service, "room": room, "project": proj,
            "by_activity": rp["by_activity"], "waste": waste,
            "rated_items": rp["totals"].get("rated"),
            "total_items": rp["totals"].get("items"),
            "unmapped_value": rp["unmapped_value"]}


@router.get("/{slug}/forecast-link/{service}")
def forecast_link(slug: str, service: str):
    """Attach the project's latest forecast run to each BOQ item (READ-ONLY).
    Returns per item: best stock match, confidence, and — when matched — the
    forecast row (status/days_left/rate/balance/order_by)."""
    d = _need(slug)
    items = _load_boq(d, service)
    if items.empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    run = _latest_run_for(slug)
    if run is None:
        return {"linked": False,
                "reason": "no forecast run for this project yet — upload the "
                          "stock register on the Forecast tab first",
                "links": {}}
    fdf = _read_forecast_parquet(run)
    # forecast folds Fire+HVAC into "Fire & HVAC"; match against that pool.
    # No fallback to the unfiltered pool when this service's own rows are
    # empty -- same rule as _forecast_pool()/_actual_consumed(): a suggested
    # "confident" link here is something the engineer may just accept, so
    # searching outside this service's materials risks confidently
    # suggesting the wrong one rather than honestly suggesting nothing.
    pool = fdf
    if "service" in fdf.columns:
        pool = fdf[fdf.service == _forecast_service(service)]
    link = _cached_match(slug, service)
    fore = linkage.attach_forecast(link, pool)
    out = {}
    for code, info in link.items():
        out[code] = {"best": info["best"], "score": info["score"],
                     "confident": info["confident"],
                     "candidates": info["candidates"][:3],
                     "forecast": _scrub(fore.get(code))}
    return {"linked": True, "run": run.name,
            "confident": sum(1 for v in link.values() if v["confident"]),
            "links": out}


# ------------------------------------------------------------------ links
# The "item master": a persistent, engineer-confirmed map from a BOQ item to
# the stock register material(s) that supply it. Done ONCE per site; after that
# every progress update flows through it into a realistic forecast. Many-to-one
# is supported (a "wire in conduit" BOQ line links to BOTH the wire and the
# conduit SKU), which is exactly what the fuzzy auto-match could not express.
def _load_links(d):
    return _read_json(d / "links.json", {}) or {}


def _norm_link_entries(entries):
    """A link entry is stored as {"material": str, "factor": float|None}.
    Older links.json files (from before the conversion-factor feature)
    stored a bare material-name string instead -- read those transparently
    as {"material": s, "factor": None} rather than requiring a migration.
    Never invents a factor for an old entry; None still means "not set",
    same as it always has."""
    out = []
    for e in entries or []:
        if isinstance(e, str):
            out.append({"material": e, "factor": None})
        elif isinstance(e, dict) and e.get("material"):
            f = e.get("factor")
            try:
                f = float(f) if f is not None else None
            except (TypeError, ValueError):
                f = None
            out.append({"material": str(e["material"]), "factor": f})
    return out


@functools.lru_cache(maxsize=32)
def _full_run_rows_cached(run_dir_str, mtime_ns):
    fdf = _read_forecast_parquet_cached(str(Path(run_dir_str) / "forecast.parquet"), mtime_ns)
    return {linkage._norm(r["material"]): _scrub(r.to_dict()) for _, r in fdf.iterrows()}


def _full_run_rows(slug):
    """Every material in the latest run's forecast, regardless of service --
    used ONLY to resolve a material name that is already a CONFIRMED link
    (links.json), never for a picker's candidate list. A saved link is an
    exact reference the engineer already made (e.g. via quick-item's own
    all_services=True fallback, see add_quick_item()) -- not a guess -- so
    widening the lookup here for it doesn't reintroduce the "wrong service
    candidate" risk _forecast_pool()'s docstring warns about; it only fixes
    a real gap where add_quick_item() could create a cross-service link that
    nothing downstream (the drawer's realistic forecast, the Link-stock
    modal) could actually resolve, showing a genuine link as "not linked".

    Cached across requests now (mtime-keyed, same story as
    _read_forecast_parquet_cached above) — not just per-request as before.
    Building this dict is an iterrows() + _scrub() pass over the WHOLE run
    (every material, every service), which profiling showed as a real cost
    every time a cross-service link needed resolving; a run never changes
    once written (README: "Runs are immutable"), so re-deriving this exact
    same dict on every such request was pure waste. A genuinely new run
    (different path or mtime) is a new cache key automatically."""
    latest = _latest_run_for(slug)
    if latest is None:
        return {}
    try:
        fp = latest / "forecast.parquet"
        mtime_ns = fp.stat().st_mtime_ns
    except Exception:
        return {}
    return _full_run_rows_cached(str(latest), mtime_ns)


@functools.lru_cache(maxsize=256)
def _forecast_pool_cached(run_dir_str, mtime_ns, forecast_label):
    # reads via the already-cached parquet DataFrame -- deciding
    # has_service_col HERE (not in the caller) means _forecast_pool() below
    # never needs its own separate call into _read_forecast_parquet_cached
    # just to inspect columns; there is exactly one path to that cache now.
    fdf = _read_forecast_parquet_cached(str(Path(run_dir_str) / "forecast.parquet"), mtime_ns)
    if "service" in fdf.columns:
        pool = fdf[fdf.service == forecast_label]
        filtered = True
    else:
        pool = fdf                      # no service column at all -> nothing to filter by
        filtered = False
    rows = {linkage._norm(r["material"]): _scrub(r.to_dict())
            for _, r in pool.iterrows()}
    names = pool["material"].astype(str).tolist()
    return rows, names, filtered


def _forecast_pool(slug, service):
    """The latest run's forecast rows for THIS service (READ-ONLY), plus the
    list of stock material names. Returns (rows_by_normkey, names, run_name,
    filtered) where `filtered` tells the caller whether the service filter
    actually matched anything.

    IMPORTANT: never falls back to the whole unfiltered pool when the exact
    service-label filter comes up empty. That fallback used to silently show
    every OTHER service's materials (Plumbing, HVAC, Fire, Safety...) inside
    e.g. Electrical's "+ Add item" picker the moment the forecast run's own
    `service` label didn't exactly match `_forecast_service(service)` — wrong
    materials appearing beats no materials appearing, and this codebase's own
    rule elsewhere (linkage.py, boq.py) is "no match beats a wrong match".
    An empty, honestly-reported result is a real signal that the engine's
    service label for this run doesn't line up with Site Progress's label,
    which needs fixing at the source (or reported) — not papered over here.

    The rows/names-building step (iterrows() + _scrub() over every material
    in the run) is now cached (mtime, service label)-keyed via
    _forecast_pool_cached -- this used to re-run on EVERY call (every
    service-switch, every Link-stock open, every quick-item picker open),
    profiled at a real cost proportional to the run's material count. A new
    forecast run (new path/mtime) is a new cache key automatically; nothing
    here needs a manual bust on any Site Progress write route, since none of
    them can change what a forecast run already contains.
    """
    run = _latest_run_for(slug)
    if run is None:
        return {}, [], None, True
    try:
        fp = run / "forecast.parquet"
        mtime_ns = fp.stat().st_mtime_ns
    except Exception:
        return {}, [], None, True
    label = _forecast_service(service)
    try:
        rows, names, filtered = _forecast_pool_cached(str(run), mtime_ns, label)
    except Exception:
        return {}, [], None, True
    return rows, names, run.name, filtered


@functools.lru_cache(maxsize=256)
def _linkage_match_cached(slug_norm, service, boq_mtime_ns, quick_items_mtime_ns, run_key):
    d = PROJECTS / slug_norm
    items = _load_boq(d, service)
    if items.empty:
        return {}
    _rows, names, _run_name, _filtered = _forecast_pool(slug_norm, service)
    if not names:
        return {}
    return linkage.match(items, names)


def _cached_match(slug, service):
    """Cached linkage.match(items, stock_names) for one service — the single
    biggest uncached cost profiling found (real synthetic Hyatt-scale run:
    ~25% of one /overall call, over half of one /links/{service} call). Used
    by get_links(), forecast_link() and _actual_consumed() — three separate
    call sites that were each re-running this exact O(items x materials)
    token-matching pass from scratch, sometimes more than once per single
    page load (e.g. /overall calls _actual_consumed once per service).

    linkage.match()'s result is a pure function of two things: the service's
    BOQ items (boq.parquet + quick_items.json — a quick-added item is a real
    input to matching too, see add_quick_item()) and the run's stock material
    names (forecast.parquet, filtered by service). The cache key is exactly
    those three files' mtimes, the same (path, mtime) trick already proven
    safe by _read_forecast_parquet_cached above — so this needs, and gets,
    NO manual cache-bust in any write route: a BOQ re-upload, a new quick
    item, or a new forecast run each naturally mint a new mtime and therefore
    a new cache key, forcing a fresh match rather than ever serving a stale
    one. Nothing else that Site Progress writes (progress %, mapping, rates,
    planned overrides, links.json itself) can change what BOQ items or stock
    materials exist, so nothing else needs to invalidate this."""
    slug_norm = _slugify(slug)
    d = PROJECTS / slug_norm
    boq_p = d / "boq.parquet"
    qi_p = d / "quick_items.json"
    boq_mtime = boq_p.stat().st_mtime_ns if boq_p.exists() else 0
    qi_mtime = qi_p.stat().st_mtime_ns if qi_p.exists() else 0
    run = _latest_run_for(slug_norm)
    if run is None:
        run_key = (None, 0)
    else:
        fp = run / "forecast.parquet"
        run_key = (str(fp), fp.stat().st_mtime_ns if fp.exists() else 0)
    return _linkage_match_cached(slug_norm, service, boq_mtime, qi_mtime, run_key)


@router.get("/{slug}/quick-items/{service}/candidates")
def quick_item_candidates(slug: str, service: str, all_services: bool = False):
    """Stock materials this service's latest forecast run knows about — the
    '+ Add item' picker's source list. Lets the engineer add a plannable line
    straight from what is really in the register, with no BOQ upload needed
    for it at all. Already quick-added materials are flagged (not hidden) so
    re-picking one is a harmless no-op rather than a surprise duplicate.

    `all_services=true` is an explicit, engineer-initiated widen — e.g. a
    real site register that keeps ALL its PVC piping under one "Electrical"
    tab even though some of it is genuinely used for HVAC fresh-air ducting.
    This is NEVER automatic: the earlier "silently search everything when
    the exact filter is empty" behaviour was a real bug (fixed), because a
    confident-looking wrong match is worse than an honest empty result. This
    is different — the engineer explicitly asked to widen the search, so
    every cross-service result carries its own real `service` label, never
    disguised as belonging to the service being viewed."""
    d = _need(slug)
    run = _latest_run_for(slug)
    if run is None:
        return {"available": False,
                "reason": "no forecast run for this project yet — upload the "
                          "stock register on the Forecast tab first",
                "materials": []}

    if all_services:
        try:
            fdf = _read_forecast_parquet(run)
        except Exception:
            return {"available": False,
                    "reason": "couldn't read the latest forecast run",
                    "materials": []}
        has_svc_col = "service" in fdf.columns
        rows = {linkage._norm(r["material"]): _scrub(r.to_dict())
                for _, r in fdf.iterrows()}
        names_with_service = [
            (str(r["material"]), (str(r["service"]) if has_svc_col and pd.notna(r.get("service")) else None))
            for _, r in fdf.iterrows()]
        run_name = run.name
    else:
        rows, names, run_name, filtered = _forecast_pool(slug, service)
        if not names and filtered:
            # the forecast run exists but nothing in it is tagged as this
            # service -- report that plainly rather than silently showing
            # every other service's materials (the old behaviour). This is a
            # real signal the forecast run's own service label doesn't line
            # up with Site Progress's "{service}" label for this run.
            return {"available": True, "run": run_name, "materials": [],
                    "reason": f"the forecast run has no materials tagged "
                              f"'{_forecast_service(service)}' — the register's own "
                              f"service labels may not match; ask to check the "
                              f"Forecast tab's classification for this service, "
                              f"or search other services if this material is "
                              f"genuinely stocked under a different one"}
        names_with_service = [(n, None) for n in names]   # same service, no tag needed

    store = _read_json(d / "quick_items.json", {}) or {}
    already = {it["material"] for it in (store.get(service) or {}).get("items", [])}
    seen, materials = set(), []
    for n, svc_label in names_with_service:
        if n in seen:
            continue
        seen.add(n)
        row = rows.get(linkage._norm(n)) or {}
        cross = bool(svc_label) and svc_label != _forecast_service(service)
        materials.append({"name": n, "unit": row.get("unit"), "already": n in already,
                          "other_service": svc_label if cross else None})
    materials.sort(key=lambda m: (m["already"], bool(m["other_service"]), m["name"]))
    return {"available": True, "run": run_name, "materials": materials}


@router.post("/{slug}/quick-item")
def add_quick_item(slug: str, payload: dict, room: str = None):
    """Add a plannable line straight from a stock/register material, for the
    common case where the site engineer knows the material name and wants to
    plan it under an activity without it first existing as a BOQ line. The
    material comes from THIS service's own latest forecast run, so there is
    no fuzzy-match step — the stock link is created immediately and exactly.
    Body: {"service","activity","material"}. Planned qty starts at 0; the
    engineer sets it afterwards via the same ✎ every other item uses."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    material = (payload.get("material") or "").strip()
    if not svc or not act or not material:
        raise HTTPException(400, "service, activity and material are required")
    known_acts = (_read_json(d / "activities.json", {}) or {}).get(svc, [])
    if act not in known_acts:
        raise HTTPException(400, f"activity '{act}' does not exist yet — create it first")

    rows, names, run, _filtered = _forecast_pool(slug, svc)
    if run is None:
        raise HTTPException(400, "no forecast run for this project yet — upload "
                                 "the stock register on the Forecast tab first")
    row = rows.get(linkage._norm(material))
    if row is None:
        # not in this service's own scoped pool -- the engineer may have
        # picked it via the picker's explicit "search other services"
        # option (e.g. PVC piping the register keeps under Electrical but
        # is genuinely used for an HVAC activity). Check the FULL run as a
        # fallback, only ever adding a real row that actually exists there
        # -- never inventing one. (Same lookup _full_run_rows() gives the
        # drawer/link-modal, so a quick item linked this way is resolvable
        # everywhere it's shown, not just here at creation time.)
        row = _full_run_rows(slug).get(linkage._norm(material))
    if row is None:
        raise HTTPException(404, f"'{material}' is not in the stock register")
    unit = row.get("unit") or "Nos"

    store = _read_json(d / "quick_items.json", {}) or {}
    svc_store = store.setdefault(svc, {"_seq": 0, "items": []})
    m = _load_mapping(d)
    # Reuse an existing quick-item line only when it's already mapped to
    # THIS activity -- that's a harmless re-click (re-opening the picker and
    # picking the same material again for the same activity). Picking "the
    # same" material for a DIFFERENT activity now mints its own independent
    # item_code instead of reusing the first activity's. Reusing it used to
    # make the second activity's planned quantity silently mirror the
    # first's, because planned.json is keyed by item_code alone (see
    # save_planned()'s docstring: planned is deliberately project-wide PER
    # ITEM -- that stays true here, this fix just stops treating two
    # different activities' picks of the same material as one item).
    existing = next((it for it in svc_store["items"]
                     if it["material"] == material
                     and it["item_code"] in m.get(svc, act)), None)
    if existing:
        code = existing["item_code"]           # re-picking for the SAME activity reuses its line
    else:
        svc_store["_seq"] += 1
        code = f"QI{svc_store['_seq']}"
        svc_store["items"].append({"item_code": code, "description": material,
                                   "unit": unit, "material": material})
    (d / "quick_items.json").write_text(json.dumps(store, ensure_ascii=False))

    if code not in m.get(svc, act):             # don't let a re-pick toggle it back OUT
        m.toggle(svc, act, code)
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))

    links = _load_links(d)
    links.setdefault(svc, {})[code] = [material]   # exact — we picked it from the register itself
    (d / "links.json").write_text(json.dumps(links, ensure_ascii=False))

    return _service_view(d, svc, room=room)


@router.get("/{slug}/links/{service}")
def get_links(slug: str, service: str):
    """Current confirmed links for this service + a fuzzy suggestion for every
    BOQ item (so the engineer starts from mostly-filled checkboxes) + the full
    stock name list to pick from. This is the once-per-site setup screen.

    Each linked entry now carries its own conversion `factor` (BOQ-unit ->
    material-unit, None when not set) and the material's own `unit` -- so the
    frontend can tell at a glance when a BOQ item's unit (e.g. "Nos") doesn't
    match a linked material's unit (e.g. "Rmt") and prompt for a factor right
    there, instead of that mismatch silently defaulting to nothing until
    someone notices the forecast doesn't add up."""
    d = _need(slug)
    items = _load_boq(d, service)
    if items.empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    links = _load_links(d).get(service, {})
    rows, names, run, _filtered = _forecast_pool(slug, service)
    sugg = _cached_match(slug, service) if names else {}
    full_rows = None   # built at most once, only if a link needs it
    out_items = []
    for _, it in items.iterrows():
        code = str(it.item_code)
        s = sugg.get(code, {})
        linked = []
        for entry in _norm_link_entries(links.get(code, [])):
            # a saved link may point at a material this service's own
            # scoped pool doesn't carry (see add_quick_item()'s
            # all_services fallback) — resolve those from the full run
            # instead of showing a real link as blank/unmatched.
            row = rows.get(linkage._norm(entry["material"]))
            if row is None:
                if full_rows is None:
                    full_rows = _full_run_rows(slug)
                row = full_rows.get(linkage._norm(entry["material"]))
            linked.append({"material": entry["material"], "factor": entry["factor"],
                           "unit": (row or {}).get("unit"),
                           "units_match": bool(row) and str(row.get("unit") or "").strip().upper() == str(it.unit or "").strip().upper()})
        out_items.append({
            "code": code, "desc": it.description, "unit": it.unit,
            "sub": it.subcategory,
            "linked": linked,
            "suggestion": {"best": s.get("best"), "confident": s.get("confident", False),
                           "candidates": [c[0] for c in s.get("candidates", [])]},
        })
    return {"service": service, "has_run": run is not None, "run": run,
            "stock_names": names, "material_units": {n: (rows.get(linkage._norm(n)) or {}).get("unit") for n in names},
            "items": out_items}


@router.post("/{slug}/links")
def save_link(slug: str, payload: dict):
    """Fix the link for one BOQ item. Body:
    {"service","item_code","materials":[...]} where each entry is either a
    bare material-name string (factor unset) or {"material","factor"} --
    accepting both means the frontend never has to know which shape an
    older link was originally saved in. Stored in links.json, always
    normalized to the object shape — the canonical item master."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    mats = payload.get("materials", [])
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    normalized = _norm_link_entries(mats)
    all_links = _load_links(d)
    all_links.setdefault(svc, {})[str(code)] = normalized
    (d / "links.json").write_text(json.dumps(all_links, ensure_ascii=False))
    return {"service": svc, "item_code": str(code), "materials": normalized}


@router.get("/{slug}/realistic/{service}")
def realistic(slug: str, service: str):
    """The realistic forecast: for each BOQ item, combine remaining planned work
    (progress) with the linked stock's on-hand + burn rate (engine, read-only)
    to say whether stock will finish the work, and the order quantity if not.
    Items with no link fall back to verdict NOT_LINKED — their stock keeps being
    forecast the ordinary rate-only way on the Forecast tab, untouched."""
    d = _need(slug)
    items = _load_boq(d, service)
    if items.empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    prog_svc = _item_prog(d).get(service, {})
    rooms_cfg = _item_rooms(d).get(service, {})
    all_rooms = _all_room_ids(d)
    room_qty_groups = _item_room_qty(d).get(service, {})
    used = itemprog.compute(items, prog_svc, rooms_cfg, all_rooms,
                            _planned(d, service), room_qty_groups=room_qty_groups)
    links = _load_links(d).get(service, {})
    rows, names, run, _filtered = _forecast_pool(slug, service)

    used_by_code = {str(r.item_code): r for r in used.itertuples()}
    descs = {str(r.item_code): r.description for r in items.itertuples()}
    full_rows = None   # built at most once, only if a link needs it
    out, n_short = [], 0
    for code, raw_mats in links.items():
        u = used_by_code.get(code)
        if u is None:
            continue
        item = {"item_code": code, "unit": u.unit,
                "planned_total": u.planned_total, "used": u.used,
                "remaining": u.remaining, "progress_pct": u.progress_pct}
        # each linked entry carries its own conversion factor (None if not
        # set) -- merged onto the real forecast row here so combine_item()
        # can decide per-material whether a safe 1.0 applies (units match)
        # or whether it genuinely doesn't know the conversion yet.
        stock_rows = []
        for entry in _norm_link_entries(raw_mats):
            # same cross-service gap as get_links() above: a confirmed link
            # (e.g. from add_quick_item()'s all_services fallback) can point
            # at a material outside this service's own scoped pool. Without
            # this fallback the item renders as verdict NOT_LINKED even
            # though a real link exists — that was the actual reported bug.
            row = rows.get(linkage._norm(entry["material"]))
            if row is None:
                if full_rows is None:
                    full_rows = _full_run_rows(slug)
                row = full_rows.get(linkage._norm(entry["material"]))
            if row:
                stock_rows.append({**row, "factor": entry["factor"]})
        # room_buckets() feeds sentence()'s wording only (see realtime.py's
        # combine_item docstring) -- `remaining`/`order_qty` don't change.
        rb = itemprog.room_buckets(code, prog_svc, rooms_cfg, all_rooms, room_qty_groups=room_qty_groups) if all_rooms else None
        res = realtime.combine_item(item, stock_rows, rooms=rb)
        res["desc"] = descs.get(code)
        res["message"] = realtime.sentence(res)
        if res["verdict"] == "SHORTAGE":
            n_short += 1
        out.append(res)
    return {"service": service, "has_run": run is not None, "run": run,
            "linked_items": len(links), "shortages": n_short,
            "items": out}


# --- helpers for the forecast-linked bits -------------------------------
def _forecast_service(sp_service):
    """Map a Site-Progress service label to the forecast register's label.
    (Forecast folds Fire+HVAC into 'Fire & HVAC'.)"""
    return {"Fire": "Fire & HVAC", "HVAC": "Fire & HVAC",
            "FAPA": "Fire & HVAC"}.get(sp_service, sp_service)


def _actual_consumed(slug, service, items):
    """Real OUT quantity per BOQ item from the linked forecast run, via linkage.
    Returns {item_code: qty} for confidently-linked items, or None if no run /
    no 'consumed' column. Read-only on engine output.

    IMPORTANT: when this service's own forecast rows are empty (e.g. after
    correctly folding HVAC/Fire/FAPA to the forecast's combined "Fire & HVAC"
    label, that label still isn't present in this run's data), this must NOT
    fall back to matching against the WHOLE unfiltered forecast pool -- that
    is the exact same "no match beats a wrong match" rule _forecast_pool()
    already follows, and searching every other service's materials risks a
    confident-looking match against the wrong one, not just a wider net.

    `items` is kept as a parameter for call-site stability, but the actual
    match now goes through _cached_match(slug, service), which loads its own
    (identical) items internally so it can be safely cached across requests
    -- see its docstring. Was profiled as the single biggest uncached cost
    in this function (linkage.match() over the full BOQ x stock-pool)."""
    run = _latest_run_for(slug)
    if run is None:
        return None
    try:
        fdf = _read_forecast_parquet(run)
    except Exception:
        return None
    qty_col = next((c for c in ("total_consumed", "consumed", "total_out",
                                "qty_out", "issued") if c in fdf.columns), None)
    if qty_col is None:
        return None
    pool = fdf
    if "service" in fdf.columns:
        pool = fdf[fdf.service == _forecast_service(service)]
    if pool.empty:
        return None
    link = _cached_match(slug, service)
    lut = {linkage._norm(r.material): float(getattr(r, qty_col))
           for r in pool.itertuples() if pd.notna(getattr(r, qty_col))}
    out = {}
    for code, info in link.items():
        if info["confident"] and info["best"]:
            v = lut.get(linkage._norm(info["best"]))
            if v is not None:
                out[code] = v
    return out or None
