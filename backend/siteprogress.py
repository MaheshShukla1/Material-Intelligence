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
import os
import hmac
import time
import shutil
import datetime as dt
import functools
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, Response, Query

from . import structure, boq, activity, progress, pnl, linkage, schema, subcat, realtime, itemprog, dpr, shortage_history, clientref, locqty, runlog, measurexl

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


_PLAN_AS_NOW = object()     # "use the planned figure as it is stored now" (the normal case)


def _qty_for_room(d, service, item_code, room_id, frac, planned=_PLAN_AS_NOW):
    """The real quantity one specific captured action represents: the room's
    own quantity (room_qty_groups override if the item uses them -- reuses
    itemprog's own group-resolution helper rather than reimplementing it --
    else the item's effective per-room qty) x the fraction just set. NOT a
    true delta from the room's PREVIOUS fraction (no history is kept -- see
    dpr.py's module docstring on why this logs the fact of an action, not a
    diff of two states), so two saves the same day update in place
    (record_change()'s own job) rather than summing. Returns (qty, unit) --
    (None, None) when no real quantity is knowable, never a guessed number.

    "Effective per-room qty" (no groups case) MUST account for a whole-
    project planned-quantity override (save_planned(), the (pencil) edit),
    not just the raw BOQ qty column -- confirmed against real production
    data: every quick-added item (add_quick_item() starts qty at 0, per its
    own docstring) still logged qty=0.0 forever, even on items whose real
    total HAD been set afterward via that same pencil, because that edit
    writes planned_total to planned.json, never back into a per-room qty.
    Divides the override by the SAME applicable-room count
    _applicable_rooms() resolves (mirrors itemprog.compute()'s own
    denominator exactly, per that function's own docstring), so this never
    quietly disagrees with how planned_total itself was derived.

    `planned` (optional) = the whole-project planned figure to use INSTEAD of
    the stored one (None = no override). Only the DPR's "before" quantity uses
    it, when the very same save raised the planned figure above the BOQ."""
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
        unit = row.iloc[0].get("unit")
        override = _planned(d, service).get(str(item_code)) if planned is _PLAN_AS_NOW else planned
        if override is not None:
            try:
                planned_total = float(override)
                appl_rooms = _applicable_rooms(d, service, item_code)
                n_rooms = len(appl_rooms)
                if n_rooms:
                    room_qty = planned_total / n_rooms
                    shares = itemprog.whole_split(unit, planned_total, appl_rooms)       # counted unit: whole shares, same as itemprog.compute()
                    if shares and str(room_id) in shares:
                        room_qty = shares[str(room_id)]
            except (TypeError, ValueError):
                pass
        if room_qty is None:
            v = row.iloc[0].get("qty")
            room_qty = float(v) if v is not None else None
    else:
        boqdf = _load_boq(d, service)
        row = boqdf[boqdf.item_code.astype(str) == str(item_code)]
        unit = row.iloc[0].get("unit") if not row.empty else None
    if room_qty is None:
        return None, None
    cap = 1.0
    if _is_free(d, service, item_code):                 # plan removed: a zone may hold more than an equal share (up to the whole item)
        cap = float(max(1, len(_applicable_rooms(d, service, item_code))))
    return round(room_qty * max(0.0, min(cap, float(frac))), 4), unit


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


def _node_before(store, service, item_code):
    """A copy of one item's progress node ({"*": f, room: f, ...}) as it is
    BEFORE a write -- taken by the progress routes ahead of itemprog.set_progress()
    so _log_dpr_change() can tell how much the write ADDED (the DPR's "today"
    quantity). {} when the item had no progress yet (= 0% everywhere)."""
    return dict(((store.get(service) or {}).get(str(item_code))) or {})


def _log_dpr_change(d, service, room_id, item_code, frac=None, before=None, before_planned=_PLAN_AS_NOW, only_existing=False):
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

    `before` = the item's progress node as it was BEFORE this write (see
    _node_before()). When given, each line also records qty_before -- what the
    same room/floor quantity was at the start, resolved with the same
    per-room fraction itemprog.compute() uses (itemprog.frac_for) -- so the
    DPR can show only the day's own work (qty - qty_before) instead of the
    cumulative quantity. dpr.record_change() keeps the FIRST baseline of the
    day, so several saves in one day still add up to the day's real work.
    Not given = no baseline (the day quantity is then unknown, never guessed).
    `before_planned` = the whole-project planned figure as it was before the
    write, for the one route that changes it together with the progress
    ("quantity above the BOQ" and its undo); otherwise the stored one.
    `only_existing` = an undo (a room set back to not-started): it is not
    "work done today" so it never creates a line, but if the SAME room/item
    already has a line today (ticked by mistake, then undone) that line is
    brought back to what is true now, instead of keeping the quantity of the
    wrong tick.

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
        extra = {}
        if frac is not None:
            qty, unit = _qty_for_room(d, service, item_code, room_id, frac)
            if before is not None:
                q0, _u0 = _qty_for_room(d, service, item_code, room_id,
                                        itemprog.frac_for(before, str(room_id)), planned=before_planned)
                extra["qty_before"] = q0
        for act in acts:
            if only_existing and not any((e["date"], e["service"], e["floor"], e["activity"], e.get("room"), e.get("item")) ==
                                         (today, service, info["location"], act, info["name"], item_desc) for e in log):
                continue
            dpr.record_change(log, today, service, info["location"], act, info["name"],
                              item=item_desc, qty=qty, unit=unit, item_code=str(item_code), **extra)
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
                before_total, before_ok = 0.0, True
                if frac is not None:
                    for r in by_floor[floor]:
                        q, u = _qty_for_room(d, service, item_code, r, frac)
                        if q is not None:
                            qty_total += q
                            unit = u or unit
                            has_qty = True
                        if before is not None:
                            q0, _u0 = _qty_for_room(d, service, item_code, r, itemprog.frac_for(before, str(r)),
                                                    planned=before_planned)
                            if q0 is None:
                                before_ok = False
                            else:
                                before_total += q0
                extra = {}
                if frac is not None and before is not None:
                    extra["qty_before"] = round(before_total, 3) if (has_qty and before_ok) else None
                dpr.record_change(log, today, service, floor, act, None, item=item_desc,
                                  qty=(round(qty_total, 3) if has_qty else None), unit=unit,
                                  item_code=str(item_code), **extra)
    _save_dpr_log(d, log)


def _stock_slug(slug):
    """Which stock this project reads. Its own name, as always, unless the person picked another stock for it
    (stock.json in the project folder, written by set_stock). Nothing picked = exactly the old behaviour."""
    own = _slugify(slug)
    try:
        v = (_read_json(PROJECTS / own / "stock.json", {}) or {}).get("stock")
    except Exception:
        v = None
    return _slugify(v) if v else own


def _latest_run_for(slug):
    """Newest forecast run of the stock this project reads (read-only): the stock the person picked for it,
    else the runs whose meta.project_slug is the project's own name."""
    slug = _stock_slug(slug)
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


def _item_free(d):
    """{service: {item_code: true}} -- items whose per-zone PLAN was removed (item_free.json). Opt-in, per item: an item not listed
    here behaves exactly as it always did (every zone is capped at its own share of the plan; a tick = that share)."""
    return _read_json(d / "item_free.json", {}) or {}


def _is_free(d, service, item_code):
    return bool((_item_free(d).get(service) or {}).get(str(item_code)))


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

    # Work recorded above the BOQ quantity (see set_item_qty): the planned figure as it was BEFORE the raise, per item. A record
    # only counts while the item's planned override is still the value the raise set (a later manual edit takes over).
    over_rec = (_read_json(d / "over_boq.json", {}) or {}).get(service, {})

    def _boq_planned(c, planned_now):
        r = over_rec.get(c)
        return r["boq"] if r and c in planned_over and abs(float(planned_over[c]) - float(r["raised"])) < 1e-6 else planned_now

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
            "boq_planned": fv(_boq_planned(c, it.planned_total)),
            "has_room_groups": bool(it.get("has_room_groups", False)),
        })

    # items that have measurement entries (From / To, see runlog.py): what their chip says. Only those items cost anything here.
    runs_svc = item_runs_all(d, service)
    metered = [r for r in rows if any(runlog.active(v) for v in (runs_svc.get(r["code"]) or {}).values())]
    if metered:
        mctx, minfo = location_context(d, service), location_info(d)
        for r in metered:
            r["meas"] = item_meas_summary(d, service, r["code"], mctx, minfo)

    # activity % and overall % are the MEAN of member items' % (item-driven)
    # for a normal activity -- but a LABOUR-ONLY activity (no BOQ material at
    # all, e.g. Zari work, core-cutting, chasing, testing -- see
    # activity.is_labour_keyword()) has no items to average, so its % comes
    # from its own recorded per-room fractions instead. Never both at once
    # for the same activity: while `labour` is on for it, its item-based
    # `codes` are ignored here even if some happen to still be mapped (the
    # frontend hides the "add items" controls while labour-only is on, and
    # only offers the toggle in the first place when there are none).
    #
    # Room-tick-driven, not a single flat number: a labour activity's overall
    # % is now the MEAN of itemprog.frac_for() over every room in the
    # project -- the exact same room-averaging an item's own % already uses
    # (compute()'s uniform path) -- reusing itemprog.frac_for()/room_buckets()
    # completely unchanged, since they're generic on the dict key (activity
    # name here instead of item_code). An activity that only ever had the
    # old overall slider touched (no per-room ticks yet) still reads
    # correctly: frac_for() falls back to "*" for every room with no
    # per-room override, so the mean collapses to that one number -- a
    # strict backward-compatible generalisation, not a behaviour change for
    # existing data. A room-scoped view (`room` set) still shows just that
    # one room's own fraction, same as before.
    pctmap = {str(r.item_code): (r.progress_pct if r.progress_pct == r.progress_pct else 0.0)
              for r in ip.itertuples()}
    labour = set(_labour_only(d).get(service) or [])
    aprog = _activity_prog(d).get(service, {})
    act_pct, labour_pct, labour_buckets = {}, {}, {}
    for a in acts:
        if a in labour:
            node = aprog.get(a, {})
            if room:
                pct = round(itemprog.frac_for(node, room) * 100, 1)
            elif all_rooms:
                pct = round(100.0 * sum(itemprog.frac_for(node, r) for r in all_rooms)
                           / len(all_rooms), 1)
            else:
                pct = round(float(node.get("*", 0.0)) * 100, 1)
            act_pct[a] = pct
            labour_pct[a] = pct
            labour_buckets[a] = (itemprog.room_buckets(a, aprog, {}, all_rooms)
                                 if all_rooms else
                                 {"done": 0, "in_progress": 0, "not_started": 0, "total": 0})
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
        "activity_progress": aprog,
        "labour_buckets": labour_buckets,
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
@router.get("/projects-without-stock")
def projects_without_stock():
    """Projects that have Site Progress data but no stock run under their own name (e.g. the stock was deleted, or
    never uploaded). /api/projects is built from stock runs only, so these are missing from it; the Site Progress and
    Billing project lists add them so the work stays reachable. Name = the one the project was set up with."""
    have = set(_stock_sources())
    out = []
    for d in sorted(PROJECTS.iterdir()) if PROJECTS.exists() else []:
        if not d.is_dir() or d.name in have:
            continue
        st = _read_json(d / "structure.json", None)
        if not isinstance(st, dict) and not (d / "boq.parquet").exists():
            continue
        name = (st or {}).get("name") if isinstance(st, dict) else None
        out.append({"slug": d.name, "project": name or d.name, "runs": 0, "latest_run": None, "latest": None})
    return out


@router.get("/{slug}")
def get_state(slug: str):
    """Top-level state: structure tree, services present, per-service progress %
    and the currently servable services."""
    d = _need(slug)
    struct = _read_json(d / "structure.json", None)
    activities = _read_json(d / "activities.json", {}) or {}
    boqdf = _load_boq(d)
    # Upload/sheet order, not alphabetical -- plain sorted() is case-
    # sensitive, so an all-caps service like "ELV" sorts before a
    # Title-case one like "Electrical" in plain ASCII order, landing
    # it first in the pill row for no reason the engineer would expect.
    # .unique() already preserves first-seen order on its own, which
    # naturally matches the order sheets appeared in the uploaded
    # workbook -- what the engineer actually expects.
    services = boqdf.service.unique().tolist() if not boqdf.empty else []
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
    services = boqdf.service.unique().tolist()   # upload order, see /{slug} above
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
    before = _node_before(store, svc, code)
    itemprog.set_progress(store, svc, code, payload.get("frac", 0), room=payload.get("room"))
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    _log_dpr_change(d, svc, payload.get("room"), code, frac=payload.get("frac", 0), before=before)
    return _service_view(d, svc, room=payload.get("room"))


@router.post("/{slug}/progress/item-qty")
def set_item_qty(slug: str, payload: dict):
    """Record the real installed quantity of one item for the whole project. Body: {"service","item_code","qty"}.

    Up to the item's planned quantity this is exactly the usual progress (frac = qty / planned). ABOVE it (for items without zone-wise quantities) the number is kept,
    not cut back: the item's planned quantity is raised to the typed quantity (the same project-wide override the "planned"
    pencil sets) and the item sits at 100%. The planned figure from before the first raise is remembered (over_boq.json) so the
    screen can mark the excess and "Back to BOQ qty" can restore it exactly. The BOQ itself never changes, Billing bills up to
    the BOQ quantity only, and every progress figure stays inside 0..100%."""
    d = _need(slug)
    svc, code = payload.get("service"), payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    try:
        qty = max(0.0, float(payload.get("qty")))
    except (TypeError, ValueError):
        raise HTTPException(400, "qty must be a number")
    code = str(code)
    items = _load_boq(d, svc)
    prog = _item_prog(d).get(svc, {})
    allp = _read_json(d / "planned.json", {}) or {}
    cur = itemprog.compute(items, prog, _item_rooms(d).get(svc, {}), _all_room_ids(d), allp.get(svc, {}),
                           room=None, room_qty_groups=_item_room_qty(d).get(svc, {}))
    row = cur[cur["item_code"].astype(str) == code]
    if row.empty:
        raise HTTPException(404, "that item is not in this service")
    planned = float(row.iloc[0]["planned_total"] or 0.0)
    plan_before = allp.get(svc, {}).get(code)       # the stored override before this save (None = none)
    if planned <= 0:
        frac = 0.0
    elif qty <= planned + 1e-9 or (_item_room_qty(d).get(svc, {}) or {}).get(code):
        # within the plan -- or an item whose quantity is set zone by zone (those groups ARE its plan; a project-wide override is
        # ignored for it by itemprog), so it stays capped exactly as before
        frac = min(1.0, qty / planned)
    else:
        over = _read_json(d / "over_boq.json", {}) or {}
        rec = over.setdefault(svc, {}).get(code)
        if not rec or abs(float(allp.get(svc, {}).get(code, -1)) - float(rec["raised"])) > 1e-6:
            rec = {"boq": planned, "prior": allp.get(svc, {}).get(code)}      # first raise (or the old record went stale)
        rec["raised"] = round(qty, 4)
        over[svc][code] = rec
        (d / "over_boq.json").write_text(json.dumps(over, ensure_ascii=False))
        allp.setdefault(svc, {})[code] = round(qty, 4)
        (d / "planned.json").write_text(json.dumps(allp, ensure_ascii=False))
        frac = 1.0
    store = _item_prog(d)
    before = _node_before(store, svc, code)
    itemprog.set_progress(store, svc, code, frac, room=None)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    _log_dpr_change(d, svc, None, code, frac=frac, before=before, before_planned=plan_before)
    return _service_view(d, svc, room=None)


@router.post("/{slug}/progress/item-qty/undo")
def undo_item_over(slug: str, payload: dict):
    """"Back to BOQ qty": put the item's planned figure back exactly as it was before it was raised above the BOQ, and sit the
    item at 100% of that. Body: {"service","item_code"}. Nothing to undo => 404."""
    d = _need(slug)
    svc, code = payload.get("service"), str(payload.get("item_code"))
    over = _read_json(d / "over_boq.json", {}) or {}
    rec = (over.get(svc) or {}).get(code)
    if not rec:
        raise HTTPException(404, "this item has no quantity above the BOQ to undo")
    allp = _read_json(d / "planned.json", {}) or {}
    plan_before = allp.get(svc, {}).get(code)
    if rec.get("prior") is None:
        allp.setdefault(svc, {}).pop(code, None)
    else:
        allp.setdefault(svc, {})[code] = rec["prior"]
    (d / "planned.json").write_text(json.dumps(allp, ensure_ascii=False))
    over[svc].pop(code, None)
    (d / "over_boq.json").write_text(json.dumps(over, ensure_ascii=False))
    store = _item_prog(d)
    before = _node_before(store, svc, code)
    itemprog.set_progress(store, svc, code, 1.0, room=None)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    _log_dpr_change(d, svc, None, code, frac=1.0, before=before, before_planned=plan_before)
    return _service_view(d, svc, room=None)


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
    if payload.get("qty") is not None and _is_free(d, svc, code):
        raise HTTPException(400, "The plan is removed for this item, so a planned quantity can't be set for zones. Restore the plan first.")
    store = _item_room_qty(d)
    itemprog.set_room_qty_group(store, svc, code, payload.get("rooms") or [], payload.get("qty"))
    (d / "item_room_qty.json").write_text(json.dumps(store, ensure_ascii=False))
    return _service_view(d, svc, room=room)


def _q_txt(v):
    """A quantity for a message: up to 2 decimals, no trailing zeros."""
    return f"{round(float(v), 2):,.2f}".rstrip("0").rstrip(".")


def _free_total_check(store, svc, code, appl, plan, unit, new):
    """Plan removed (item_free.json): a zone may hold more than an equal share, so the item's own planned total is the one limit.
    `new` = {room_id: fraction} about to be written. Refuses (nothing written) when everything recorded would pass that total."""
    node = dict(((store.get(svc) or {}).get(str(code)) or {}))
    for r, f in new.items():
        node[str(r)] = f
    limit = sum(v for v in plan.values() if v)
    total = sum((plan.get(r) or 0.0) * itemprog.frac_for(node, r) for r in appl)
    if total > limit * (1 + 1e-9) + 1e-6:
        u = f" {unit}" if unit else ""
        raise HTTPException(400, f"These quantities add up to {_q_txt(total)}{u} for this item, but its planned total is {_q_txt(limit)}{u}. "
                                 "Lower a figure, or raise the item's planned quantity first (the pencil next to it).")


@router.post("/{slug}/item-plan-off")
def set_item_plan_off(slug: str, payload: dict, room: str = None):
    """Remove (or restore) the per-zone PLAN of ONE item. Body: {"service","item_code","off": true|false} (off defaults to true).

    Removed = the item has no per-zone plan: a zone / whole area takes the quantity that is typed for it exactly, even above an
    equal share of the item's total, and a plain tick has no quantity to count (the dialog asks for the number). The item's own
    planned TOTAL stays -- it is what progress % and the stock forecast are measured against -- and is the one limit.

    Nothing recorded changes: progress is stored per zone as a fraction of its share, removing the plan only lifts the "at most 100 %
    of the share" cap for this item. Restoring refuses while any zone holds more than its share (that quantity would be cut).
    An item whose plan is quantity groups (a quantity typed per zone) keeps the existing 'Remove' on each group; it has to go first."""
    d = _need(slug)
    svc, code = payload.get("service"), payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    code = str(code)
    boqdf = _load_boq(d, svc)
    if boqdf[boqdf.item_code.astype(str) == code].empty:
        raise HTTPException(404, "that item is not in this service")
    off = payload.get("off", True)
    store = _item_free(d)
    if off:
        if (_item_room_qty(d).get(svc) or {}).get(code):
            raise HTTPException(400, "This item's plan is a quantity set per zone (a group). Remove those groups first, then remove the plan.")
        store.setdefault(svc, {})[code] = True
    else:
        node = (_item_prog(d).get(svc) or {}).get(code) or {}
        over = [r for r, v in node.items() if r != "*" and itemprog._f(v) > 1 + 1e-9]
        if over:
            names = _room_location_map(d)
            nm = ", ".join((names.get(r) or {}).get("name", r) for r in over[:3]) + (f" +{len(over) - 3} more" if len(over) > 3 else "")
            raise HTTPException(400, f"{nm} hold more than an equal share of this item, so the plan can't be restored without cutting that quantity. Lower it first.")
        (store.get(svc) or {}).pop(code, None)
        if not store.get(svc):
            store.pop(svc, None)
    (d / "item_free.json").write_text(json.dumps(store, ensure_ascii=False))
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
    if done and _is_free(d, svc, code):
        raise HTTPException(400, "The plan is removed for this item, so a tick has no quantity. Type the quantity for each zone instead.")
    store = _item_prog(d)
    frac = 1.0 if done else 0.0
    before = _node_before(store, svc, code)
    for rid in room_ids:
        itemprog.set_progress(store, svc, code, frac, room=rid)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    for rid in room_ids:   # an undo is not "work done today" -- it never adds a line, it only corrects one of today's own
        _log_dpr_change(d, svc, rid, code, frac=frac, before=before, only_existing=not done)
    return _service_view(d, svc, room=room)


def _item_plan(d, service, item_code, _ctx=None):
    """(rooms, plan, unit) for ONE item: the rooms it applies to, and each
    room's own planned quantity -- resolved with exactly itemprog.compute()'s
    rules, so the per-room numbers the Rooms/Zones dialog shows always add up
    to the item row's own planned/used (test_room_done_qty.py asserts this).

      - item has quantity groups: a room in a group uses that group's qty,
        any other applicable room uses the item's own BOQ qty (compute's
        `_rq`). Applicability is the explicit item_rooms list unioned with the
        group rooms, or the group rooms alone when no list is set.
      - no groups: the whole-project planned override (else qty x rooms)
        shared equally across the applicable rooms.

    A room whose quantity is not a positive number is None, never a guess.
    ([], {}, None) when the item is not in this service's BOQ.

    Deliberately NOT built on _applicable_rooms()/_qty_for_room(): those feed
    the DPR and still treat "no item_rooms list" as every room even for an
    item with quantity groups, which compute() no longer does."""
    code = str(item_code)
    # `_ctx` lets a caller that plans MANY items of one service (the bill's measurement sheet) read the
    # stores once instead of once per item; the figures are exactly the same.
    c = _ctx or {}
    boqdf = c["boq"] if "boq" in c else _load_boq(d, service)
    row = boqdf[boqdf.item_code.astype(str) == code]
    if row.empty:
        return [], {}, None
    unit = row.iloc[0].get("unit")
    qty = itemprog._f(row.iloc[0].get("qty"))
    all_ids = c["all_ids"] if "all_ids" in c else _all_room_ids(d)
    room_set = c["room_set"] if "room_set" in c else set(all_ids)
    room_qty, group_appl = itemprog._room_qty_from_groups((c["groups"] if "groups" in c else _item_room_qty(d).get(service, {})).get(code), room_set)
    explicit = (c["rooms"] if "rooms" in c else _item_rooms(d).get(service, {})).get(code)
    if room_qty is not None:
        base = ([r for r in explicit if r in room_set] or all_ids) if explicit else list(group_appl)
        appl = list(dict.fromkeys(base + group_appl))
        plan = {r: room_qty.get(r, qty) for r in appl}
    else:
        appl = explicit or all_ids
        appl = [r for r in appl if r in room_set] or all_ids
        over = (c["planned"] if "planned" in c else _planned(d, service)).get(code)
        per = (itemprog._f(over) / len(appl) if appl else None) if over is not None else qty
        plan = {r: per for r in appl}
        shares = itemprog.whole_split(unit, over, appl) if over is not None else None     # counted unit: whole shares (7 Nos / 4 zones = 2,2,2,1)
        if shares:
            plan = dict(shares)
    plan = {str(r): (v if v is not None and v > 0 else None) for r, v in plan.items()}
    return [str(r) for r in appl], plan, unit


@router.get("/{slug}/item-room-plan")
def item_room_plan(slug: str, service: str, item_code: str):
    """What the Rooms/Zones dialog needs to let an engineer record a REAL
    quantity per room instead of a plain tick: for every room this item
    applies to, that room's own planned quantity and how much of it is
    recorded done. Rooms the item does not apply to are not listed -- their
    planned quantity would be invented. `planned` is null when it cannot be
    known (never a guess); the UI then offers the plain tick only.

    service/item_code are query parameters, not path segments: real item
    codes can contain "/" (e.g. QI8/13.2.1), which would break a path route."""
    d = _need(slug)
    appl, plan, unit = _item_plan(d, service, item_code)
    node = _item_prog(d).get(service, {}).get(str(item_code))
    rooms = {}
    for rid in appl:
        p = plan.get(rid)
        rooms[rid] = {"planned": (round(p, 4) if p is not None else None),
                      "frac": itemprog.frac_for(node, rid)}
    # areas whose quantity was entered as ONE total (see locqty.py); only the ones still true
    pools = locqty.pool_views(_item_pools(d).get(service, {}).get(str(item_code)), node or {}, plan)
    out = {"unit": unit, "rooms": rooms, "pools": pools, "free": _is_free(d, service, item_code)}
    if out["free"]:                                    # plan removed: the item's total is the only limit; tell the dialog how much is left
        out["total"] = round(sum(v for v in plan.values() if v), 4)
        out["used"] = round(sum((plan.get(r) or 0.0) * itemprog.frac_for(node or {}, r) for r in appl), 4)
    return out


# ------------------------------------------------------------------ area totals
# A "whole area" quantity ("B3 = 420 Rmt") is recorded the same way every other room quantity is -- as a
# fraction per room, spread over that area's rooms, so every percentage and report keeps working -- and
# item_pools.json remembers that it was ONE total. See locqty.py for why that note can never go stale.
def _item_pools(d):
    return _read_json(d / "item_pools.json", {}) or {}


def item_location_qty(d, service, code, _ctx=None):
    """locqty.split() of one item: how its installed quantity was recorded (area totals / per room / overall)."""
    appl, plan, _u = _item_plan(d, service, code, _ctx)
    node = _item_prog(d).get(service, {}).get(str(code)) or {}
    pools = _item_pools(d).get(service, {}).get(str(code))
    order = (_ctx or {}).get("order")
    if order is None:
        order = {r: i for i, r in enumerate(_all_room_ids(d))}
    return locqty.split(node, appl, plan, pools, order)


def location_context(d, service):
    """The stores item_location_qty() needs, read once for a whole service."""
    ids = _all_room_ids(d)
    return {"boq": _load_boq(d, service), "all_ids": ids, "room_set": set(ids),
            "groups": _item_room_qty(d).get(service, {}), "rooms": _item_rooms(d).get(service, {}),
            "planned": _planned(d, service), "order": {r: i for i, r in enumerate(ids)}}


def location_info(d):
    """What lines_for() needs to name places: room names, their area, the project's word for an area."""
    st = _read_json(d / "structure.json", {}) or {}
    m = _room_location_map(d)
    return {"name": {r: v["name"] for r, v in m.items()}, "loc": {r: v["location"] for r, v in m.items()},
            "order": {r: i for i, r in enumerate(_all_room_ids(d))},
            "word": {"hotel": "floor", "mall": "level"}.get(st.get("kind"), "area"),
            "leaf": _leaf_label(d).lower(), "collapse": _leaf_label(d) == "ROOM"}


@router.post("/{slug}/progress/area-total")
def set_area_total(slug: str, payload: dict, room: str = None):
    """Record ONE quantity for a whole area of an item ("B3 = 420 Rmt") when it was not measured zone by zone.
    Body: {"service","item_code","rooms":[room_id,...],"qty":number} -- the rooms are the area's rooms the item
    applies to. The quantity is spread over them as a fraction of each room's plan (so a room at/above its plan
    counts as done and every existing reader keeps working), and the area is remembered as a total.
    All validated before anything is written. qty 0 clears the area."""
    d = _need(slug)
    svc, code = payload.get("service"), payload.get("item_code")
    ids = [str(r) for r in (payload.get("rooms") or [])]
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    if not ids:
        raise HTTPException(400, "pick the area's rooms")
    try:
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        raise HTTPException(400, "quantity must be a number")
    if qty != qty or qty < 0:
        raise HTTPException(400, "quantity must be 0 or more")
    appl, plan, _unit = _item_plan(d, svc, code)
    if itemprog.is_count_unit(_unit):
        qty = float(int(qty + 0.5))                 # whole things: 5.5 fans is not a quantity
    bad = [r for r in ids if r not in set(appl)]
    if bad:
        raise HTTPException(400, f"room {bad[0]} is not one of this item's rooms")
    unknown = [r for r in ids if not plan.get(r)]
    if unknown:
        raise HTTPException(400, "the planned quantity of some of these rooms is not set, so a total cannot be spread "
                                 "over them -- set the plan first")
    where = {(_room_location_map(d).get(r) or {}).get("location") for r in ids}
    if len(where) != 1 or None in where:
        raise HTTPException(400, "an area total belongs to ONE area; pick the rooms of a single floor / level / wing")
    area = next(iter(where)) or ""
    total_plan = sum(plan[r] for r in ids)
    free = _is_free(d, svc, code)
    if free:                                         # plan removed: the typed quantity counts as typed, above this area's equal share too
        frac = round(qty / total_plan, 10)
    else:
        frac = 1.0 if qty >= total_plan * (1 - 1e-9) else round(qty / total_plan, 10)    # fine enough that 1500 comes back as 1500
    store = _item_prog(d)
    if free:
        _free_total_check(store, svc, code, appl, plan, _unit, {r: frac for r in ids})
    before = _node_before(store, svc, code)
    cap = float(len(appl)) if free else 1.0
    for rid in ids:
        itemprog.set_progress(store, svc, code, frac, room=rid, cap=cap)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    pools = _item_pools(d)
    mine = [p for p in pools.get(svc, {}).get(str(code), []) if not (set(p.get("rooms") or []) & set(ids)) and p.get("area") != area]
    if frac > 0:
        mine.append({"area": area, "rooms": ids, "frac": frac})
    pools.setdefault(svc, {})[str(code)] = mine
    (d / "item_pools.json").write_text(json.dumps(pools, ensure_ascii=False))
    for rid in ids:
        if frac > 0:
            _log_dpr_change(d, svc, rid, code, frac=frac, before=before)
    return _service_view(d, svc, room=room)


@router.post("/{slug}/progress/rooms")
def set_rooms_progress(slug: str, payload: dict, room: str = None):
    """Record a REAL installed quantity for several rooms of one item in one
    call -- the partial-progress counterpart of /mark-rooms-done (which can
    only say 0% or 100%). Body: {"service","item_code","entries":[{"room":
    room_id,"qty":number}, ...]}.

    Reported case: a Thoth jet fan planned at 9 Nos in Zone 5 but only 5
    actually fitted -- ticking the zone could only ever say 9. Each room
    gets its OWN per-room override via itemprog.set_progress(..., room=rid)
    (never the room=None '*' path), so no other room's recorded state can be
    wiped, exactly like /mark-rooms-done. The stored value stays a fraction
    of that room's planned qty (frac = qty / room_planned, clamped to [0,1]),
    so every existing reader of item_progress.json keeps working unchanged;
    a quantity at/above the room's plan is stored as exactly 1.0 so the room
    counts as done.

    All entries are validated before ANYTHING is written (one bad room =
    nothing saved). A room whose planned quantity is unknown is refused, not
    guessed. Logged to the DPR like any other room update (frac > 0 only --
    setting a room back to 0 is not "work done today", same as undo)."""
    d = _need(slug)
    svc = payload.get("service")
    code = payload.get("item_code")
    entries = payload.get("entries") or []
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    if not entries:
        raise HTTPException(400, "at least one room entry is required")
    appl_list, plan, _unit = _item_plan(d, svc, code)
    appl = set(appl_list)
    free = _is_free(d, svc, code)
    todo, exprs = [], {}
    for e in entries:
        e = e or {}
        rid = str(e.get("room"))
        if rid not in appl:
            raise HTTPException(400, f"room {rid} is not one of this item's rooms")
        try:
            qty = float(e.get("qty"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"quantity for room {rid} must be a number")
        if qty != qty or qty < 0:
            raise HTTPException(400, f"quantity for room {rid} must be 0 or more")
        if itemprog.is_count_unit(_unit):
            qty = float(int(qty + 0.5))             # whole things: 5.5 fans is not a quantity
        exprs[rid] = _clean_expr(e.get("expr"), qty)
        planned = plan.get(rid)
        if planned is None or planned <= 0:
            raise HTTPException(400, f"planned quantity for room {rid} is not set, so a partial quantity "
                                     "cannot be recorded -- set the plan first, or just tick it")
        if free:                                     # plan removed: exactly what was typed, even above an equal share
            frac = round(qty / planned, 10)
        else:
            frac = 1.0 if qty >= planned * (1 - 1e-9) else round(qty / planned, 6)
        todo.append((rid, frac))
    store = _item_prog(d)
    if free:
        _free_total_check(store, svc, code, appl_list, plan, _unit, dict(todo))
    before = _node_before(store, svc, code)
    cap = float(len(appl_list)) if free else 1.0
    for rid, frac in todo:
        itemprog.set_progress(store, svc, code, frac, room=rid, cap=cap)
    (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))
    _remember_exprs(d, svc, code, exprs)
    for rid, frac in todo:
        if frac > 0:
            _log_dpr_change(d, svc, rid, code, frac=frac, before=before)
    return _service_view(d, svc, room=room)


# ------------------------------------------------------------------ the sum behind a typed quantity (=112+155)
# A quantity surveyor types =112+155 in a box and the sheet keeps the pieces. The grid does the same: the quantity is stored exactly as before
# (a fraction of the zone's plan); the sum is only a NOTE next to it in item_exprs.json, remembered with the value it adds up to. The grid shows
# the note only while that value is still what the box holds, so a number changed anywhere else (Zones dialog, an area total, a measurement
# entry) can never leave a sum on screen that no longer adds up -- the same way item_pools.json is kept honest (locqty.valid_pools).
def _item_exprs(d):
    return _read_json(d / "item_exprs.json", {}) or {}


def _clean_expr(raw, qty):
    """The sum as it will be remembered ("=112+155"), or None when there is nothing to remember: not a sum of numbers, a plain number, or
    a sum that does not add up to the quantity being saved."""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().replace("\u00D7", "*").replace("x", "*").replace("X", "*").replace(" ", "")
    s = s if s.startswith("=") else "=" + s
    if not any(ch in s[1:] for ch in "+*("):
        return None
    r = measurexl.parse_qty(s)
    if r[0] != "ok" or abs(r[1] - float(qty)) > 1e-4 or len(s) > 200:
        return None
    return s


def _remember_exprs(d, svc, code, exprs):
    """exprs: {room_id: "=a+b" or None} for the rooms just saved. A room saved without a sum forgets its old one."""
    if not exprs:
        return
    store = _item_exprs(d)
    node = store.setdefault(svc, {}).setdefault(str(code), {})
    for rid, e in exprs.items():
        if e:
            r = measurexl.parse_qty(e)
            node[rid] = {"e": e, "v": round(r[1], 4)}
        else:
            node.pop(rid, None)
    if not node:
        store[svc].pop(str(code), None)
    (d / "item_exprs.json").write_text(json.dumps(store, ensure_ascii=False))


# ------------------------------------------------------------------ measurements (From / To / drum readings)
# For a length item (Mtr, RMT ...) the installed quantity is the sum of runs of cable / pipe / tray; the client signs a sheet that lists
# them. An entry ("measurement") belongs to a zone, to a whole floor / level (an area total) or to the whole project (the item's overall
# quantity). It is written through the very same Site Progress paths as a typed number (zone: /progress/rooms, area: /progress/area-total,
# project: /progress/item-qty), so every %, the DPR and the bill see it. runlog.py holds the rules (which places are open, and why a
# closed one is closed); item_runs.json the entries. Cancelling an entry keeps it on record. See runlog.py.
def _item_runs(d):
    return _read_json(d / "item_runs.json", {}) or {}


def item_runs_all(d, service):
    """{item_code: {key: [run, ...]}} of one service, read once (billing walks every line of a bill)."""
    return _item_runs(d).get(service, {}) or {}


def item_runs_of(d, service, code):
    """{key: [run, ...]} of one item (what the bill's From-To sheet is built from)."""
    return (_item_runs(d).get(service, {}) or {}).get(str(code)) or {}


def _recorded(d, svc, code, rid, plan):
    node = _item_prog(d).get(svc, {}).get(str(code))
    return float(plan.get(rid) or 0.0) * itemprog.frac_for(node, rid)


def _meas_table(d, svc, code, _ctx=None, _info=None):
    """runlog.scope_table() of one item, fed from the stores. None when the item is not in this service."""
    appl, plan, unit = _item_plan(d, svc, code, _ctx)
    if not appl:
        return None
    info = _info or location_info(d)
    node = _item_prog(d).get(svc, {}).get(str(code)) or {}
    pools = _item_pools(d).get(svc, {}).get(str(code))
    runs = item_runs_of(d, svc, code)
    return {"unit": unit, "appl": appl, "plan": plan, "node": node, "info": info,
            "table": runlog.scope_table(appl, plan, node, pools, runs, info["loc"], info["name"], info["order"], info["word"])}


def item_meas_summary(d, svc, code, _ctx=None, _info=None):
    """{"n","backed","done"} of an item that has measurement entries (what its chip in Site Progress and Billing says); None otherwise."""
    if not any(runlog.active(v) for v in item_runs_of(d, svc, code).values()):
        return None
    m = _meas_table(d, svc, code, _ctx, _info)
    return runlog.coverage(m["table"]) if m else None


def _scope_of(table, payload):
    """The place an entry goes: payload {"scope": "zone"|"area"|"project", "room"| "area"}; no "scope" means a zone (the first version)."""
    kind = str(payload.get("scope") or "zone")
    if kind == "project":
        key = runlog.PROJECT
    elif kind == "area":
        key = runlog.area_key(str(payload.get("area") or ""))
    elif kind == "zone":
        key = str(payload.get("room"))
    else:
        raise HTTPException(400, "scope must be zone, area or project")
    t = next((x for x in table if x["key"] == key), None)
    if t is None:
        raise HTTPException(400, {"zone": f"room {payload.get('room')} is not one of this item's rooms with a plan",
                                  "area": f"{payload.get('area')!r} is not an area of this item", "project": "this item has no rooms"}[kind])
    return t


def _fits(d, svc, code, t, cur, new_done):
    """A place's plan is a limit (the typed-quantity path would quietly cut a bigger number back to the plan, which would leave an entry the
    place does not hold). With the plan removed the item's own total is the limit, checked by Site Progress itself."""
    if t["kind"] != "project" and _is_free(d, svc, code):
        return                                         # zone / level: Site Progress checks the item's total itself. The project path would RAISE the plan instead, so it is checked here
    if new_done > float(t["plan"]) + 1e-6:
        what = {"zone": "this zone", "area": "this " + "level / floor", "project": "this item"}[t["kind"]]
        raise HTTPException(400, f"{what} is planned at {round(float(t['plan']), 4):g}; {round(cur, 4):g} is recorded, so {round(new_done - cur, 4):g} more "
                                 "does not fit. Raise the plan first.")


def _write_quantity(slug, d, svc, code, t, new_done, room):
    """Set the place's recorded quantity to new_done through the Site Progress path that already owns that kind of number."""
    if t["kind"] == "zone":
        return set_rooms_progress(slug, {"service": svc, "item_code": code, "entries": [{"room": t["key"], "qty": new_done}]}, room=room)
    if t["kind"] == "area":
        return set_area_total(slug, {"service": svc, "item_code": code, "rooms": t["rooms"], "qty": new_done}, room=room)
    plan = float(t["plan"])
    return set_item_qty(slug, {"service": svc, "item_code": code, "qty": min(new_done, plan) if new_done <= plan + 1e-6 else new_done})


def _public(t):
    return {k: v for k, v in t.items() if k != "rooms" and not k.startswith("_")}


@router.get("/{slug}/progress/runs")
def get_runs(slug: str, service: str, item_code: str):
    """The measurement entries of one item and the places they can go:
    {"unit","runs_ok","rooms":{room_id:{"runs","active","done","other"}} (zones that have entries),
     "scopes":[{"key","kind":"project"|"area"|"zone","label","group","plan","done","allowed","why","writable","runs","active","other"}],
     "summary":{"n","backed","done"}}.  `other` is the recorded quantity no active entry explains (recorded before, or by a tick / typed number)."""
    d = _need(slug)
    m = _meas_table(d, service, item_code)
    if not m:
        raise HTTPException(404, "that item is not in this service")
    tb = m["table"]
    return {"unit": m["unit"], "runs_ok": runlog.is_run_unit(m["unit"]),
            "rooms": {t["key"]: {k: t[k] for k in ("runs", "active", "done", "other")} for t in tb if t["kind"] == "zone" and t["runs"]},
            "scopes": [_public(t) for t in tb], "summary": runlog.coverage(tb)}


# ------------------------------------------------------------------ measurement grid (read-only view)
# One BOQ line per row, one zone per column. Every number here is read from the stores Site Progress already owns (itemprog fractions x
# plan, item_runs.json); nothing is stored, nothing is computed a second way, so the grid can never disagree with Site Progress or the bill.
def _qty_is_total(boqdf, planned):
    """True when the BOQ `qty` column of this service is the WHOLE-PROJECT quantity (what the BOQ itself says), not a per-room figure.

    The upload does not keep that choice, but it leaves a trace: a service whose qty is a total gets a planned figure written for every
    line (ProjectBase upload, or the 'total' answer of /qty-mode). A per-room service only has planned figures for the few lines an
    engineer edited by hand. So 'at least 90% of the lines that have a quantity also have a planned figure' means total. Fewer than 5
    such lines is too little to say, and that counts as 'not known'."""
    codes = {str(r.item_code) for r in boqdf.itertuples() if itemprog._f(getattr(r, "qty", None)) > 0}
    if len(codes) < 5:
        return False
    return sum(1 for c in codes if c in planned) >= 0.9 * len(codes)


def _entry_rows(table, limit=8):
    """The latest active measurement entries of one item, newest first, each with the place it belongs to."""
    out = []
    for t in table:
        for r in runlog.active(t["runs"]):
            out.append({"place": (t["group"] + " \u25B8 " + t["label"]) if t["kind"] == "zone" and t["group"] else t["label"], "from": r.get("from"), "to": r.get("to"), "qty": r.get("qty"),
                        "at": r.get("at"), "remarks": r.get("remarks") or ""})
    # `at` has a one-second resolution, so two entries made in the same second tie: the one added later (later in its list) comes first
    out = [e for _, e in sorted(enumerate(out), key=lambda p: (p[1]["at"] or "", p[0]), reverse=True)]
    return out[:limit]


def _zone_locks(table, pools, node, runs_of_item, word):
    """{room_id: {"kind","why"}} of the zones a number cannot be TYPED into, and why. Three honest reasons, each a rule the Site Progress
    data already has: the zone has measurement entries (its number is the sum of them: add / cancel them in the Measurement sheet), the
    zone sits inside a whole-area total (typing one zone would leave that total stale), or the item is measured for the whole project."""
    pooled = {r: p["area"] for p in locqty.valid_pools(pools, node) for r in p["rooms"]}
    proj = runlog.active(runs_of_item.get(runlog.PROJECT))
    out = {}
    for t in table:
        if t["kind"] != "zone":
            continue
        n = len(runlog.active(t["runs"]))
        if n:
            out[t["key"]] = {"kind": "entries", "why": f"{n} measurement entr{'y' if n == 1 else 'ies'} make up this number. Add or cancel them in the item's Measurement sheet."}
        elif t["key"] in pooled:
            out[t["key"]] = {"kind": "area", "why": f"{pooled[t['key']]} was recorded as one total, so its zones are not typed one by one. Change it in Zones."}
        elif proj:
            out[t["key"]] = {"kind": "project", "why": "This item is measured for the whole project. Change it in the item's Measurement sheet."}
    return out


def _grid_data(d, service, code=None):
    """The payload of measure_grid(), for a project folder (shared by the grid, its Excel export and its Excel import)."""
    boqdf = _load_boq(d, service)
    if boqdf.empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    ctx, info = location_context(d, service), location_info(d)
    prog = _item_prog(d).get(service, {})
    pools = _item_pools(d).get(service, {})
    runs = item_runs_all(d, service)
    basis = _qty_is_total(boqdf, ctx["planned"])
    free = _item_free(d).get(service, {}) or {}
    only = None if code is None else str(code)
    rows, cells, locks, exprs, seen = [], {}, {}, {}, set()
    saved_exprs = _item_exprs(d).get(service, {}) or {}
    for r in boqdf.itertuples():
        code = str(r.item_code)
        if code in seen or (only is not None and code != only):
            continue
        seen.add(code)
        appl, plan, unit = _item_plan(d, service, code, ctx)
        table = runlog.scope_table(appl, plan, prog.get(code) or {}, pools.get(code), runs.get(code) or {},
                                   info["loc"], info["name"], info["order"], info["word"])
        zones = [t for t in table if t["kind"] == "zone"]
        done = round(sum(t["_raw"] for t in zones), 4)
        for t in zones:
            cells[f"{code}|{t['key']}"] = round(t["_raw"], 4)
        for rid, lk in _zone_locks(table, pools.get(code), prog.get(code) or {}, runs.get(code) or {}, info["word"]).items():
            locks[f"{code}|{rid}"] = lk
        for rid, n in (saved_exprs.get(code) or {}).items():
            k = f"{code}|{rid}"
            # only while it still adds up to what the box holds. A zone's quantity is stored as a fraction of its plan rounded to 6 decimals, so the
            # number read back can differ from the typed one by up to ~5e-7 x plan (143.7 reads back as 143.7001 in a 243.8 plan): that is not a change
            tol = 2e-4 + 1e-6 * float(plan.get(rid) or 0)
            if k in cells and k not in locks and abs(cells[k] - n["v"]) < tol:
                exprs[k] = n["e"]
        plan_total = round(sum(v for v in plan.values() if v), 4)
        bq = itemprog._f(getattr(r, "qty", None))
        boq_qty = round(bq, 4) if basis and bq > 0 else None
        cov = runlog.coverage(table)
        desc = getattr(r, "description", None)
        rows.append({
            "code": code, "desc": "" if desc is None or desc != desc else str(desc), "unit": unit if unit == unit else None,
            "boq_qty": boq_qty, "plan": plan_total, "done": done,
            "pending": None if boq_qty is None else round(max(boq_qty - done, 0.0), 4),
            "pct": None if boq_qty is None else round(100.0 * done / boq_qty, 1),
            "plan_above_boq": boq_qty is not None and plan_total > boq_qty + 1e-6,
            "done_above_boq": boq_qty is not None and done > boq_qty + 1e-6,
            "runs_ok": runlog.is_run_unit(unit),
            "count": itemprog.is_count_unit(unit), "free": bool(free.get(code)),
            "entries": {"n": cov["n"], "backed": cov["backed"], "other": round(max(done - cov["backed"], 0.0), 4),
                        "latest": _entry_rows(table)},
        })
    if only is not None and not rows:
        raise HTTPException(404, "that item is not in this service")
    return {"service": service, "basis": "total" if basis else "unknown", "rows": rows, "cells": cells, "locks": locks, "exprs": exprs}


@router.get("/{slug}/done-where/{service}")
def done_where(slug: str, service: str):
    """WHERE each item of a service has been executed, for the one-line "Done where" under an item on Site Progress. Read-only.

    The figures are locqty.split() of the item -- the same split the bill's backup sheet (Annexure C) prints -- so the row, the grid
    and the bill cannot disagree. Per item, only when something is recorded:
      {"unit", "sum", "rest", "areas": [{"area", "n", "q", "whole", "cells", "done"}]}
      area  : the level / floor / wing-floor the zones sit in, in structure order ("" = a zone without an area)
      n     : zones of that area the item applies to (a zone whose plan is unknown is not counted: never a guessed share)
      q     : quantity recorded in that area; whole : it was typed as ONE total for the area, so no per-zone figure is given or invented
      cells : per applicable zone the fraction of its share that is done (0..1+, for the little strip); empty when `whole`
      done  : [{"id","name","q"}] only the zones holding a quantity
      rest  : recorded as an overall total, not by location
    Items with nothing recorded are left out."""
    d = _need(slug)
    ctx = location_context(d, service)
    if ctx["boq"].empty:
        raise HTTPException(404, f"no BOQ items for service '{service}'")
    info = location_info(d)
    prog, pools = _item_prog(d).get(service, {}), _item_pools(d).get(service, {})
    items, seen = {}, set()
    for r in ctx["boq"].itertuples():
        code = str(r.item_code)
        if code in seen:
            continue
        seen.add(code)
        appl, plan, unit = _item_plan(d, service, code, ctx)
        node = prog.get(code) or {}
        sp = locqty.split(node, appl, plan, pools.get(code), ctx["order"])
        if sp["sum"] <= 0:
            continue
        shown = {}
        for rid in appl:
            share = plan.get(rid)
            if not share or share <= 0:
                continue
            a = info["loc"].get(rid, "")
            e = shown.setdefault(a, {"area": a, "n": 0, "q": 0.0, "whole": False, "cells": [], "done": [], "_o": ctx["order"].get(rid, 10 ** 9)})
            e["_o"] = min(e["_o"], ctx["order"].get(rid, 10 ** 9))
            e["n"] += 1
            q = sp["rooms"].get(rid, 0.0)
            e["cells"].append(round(q / share, 2))
            if q > 0:
                e["q"] += q
                e["done"].append({"id": rid, "name": info["name"].get(rid, rid), "q": q})
        for a, pe in sp["pools"].items():                    # a whole-area total: one number for the area, never split into zones
            e = shown.setdefault(a, {"area": a, "n": 0, "q": 0.0, "whole": True, "cells": [], "done": [], "_o": pe["o"]})
            e["whole"] = True
            e["q"] += pe["q"]
            e["_o"] = min(e["_o"], pe["o"])
        areas = []
        for e in sorted(shown.values(), key=lambda x: x["_o"]):
            e.pop("_o")
            e["q"] = round(e["q"], 4)
            e["done"] = [dict(z, q=round(z["q"], 4)) for z in e["done"]]
            if e["whole"]:
                e["cells"] = []
            if e["q"] > 0:
                areas.append(e)
        items[code] = {"unit": unit if unit == unit else None, "sum": sp["sum"], "rest": sp["rest"], "areas": areas}
    return {"service": service, "word": info["word"], "leaf": info["leaf"], "items": items}


@router.get("/{slug}/measure-grid/{service}")
def measure_grid(slug: str, service: str, code: str = None):
    """Read-only data of the Measurement grid of one service:
    {"service","basis":"total"|"unknown",
     "rows":[{"code","desc","unit","boq_qty","plan","done","pending","pct","plan_above_boq","done_above_boq","runs_ok",
              "entries":{"n","backed","other","latest":[{"place","from","to","qty","at","remarks"}]}}],
     "cells":{"<item_code>|<room_id>": quantity done in that zone},
     "locks":{"<item_code>|<room_id>": {"kind","why"}}  (zones a number cannot be typed into, see _zone_locks),
     "exprs":{"<item_code>|<room_id>": "=112+155"}  (the sum behind a typed quantity, only while it still adds up to the box; see _item_exprs)}
    Every row also says `count` (a counted unit: whole numbers only) and `free` (the item's per-zone plan was removed). `?code=` returns that
    one item only (rows, cells, locks), which is how the grid refreshes a line after a save without re-reading the whole service.

    boq_qty is the BOQ's own quantity and is only given when the service's qty column is a whole-project total (see _qty_is_total);
    otherwise it is null and the grid shows a dash, never a guess. plan is the planned quantity Site Progress works with (the pencil
    edit, else the BOQ). pending = boq_qty - done (never below 0); null without a boq_qty. A zone the item does not apply to has no
    cell. `entries.backed` is the part of the installed quantity that measurement entries explain; `other` is the rest (ticks or typed
    quantities), so the grid can say how much of a number was measured."""
    d = _need(slug)
    return _grid_data(d, service, code)


def _grid_columns(d):
    """The places of the grid in sheet order: [{"id","loc","name"}] (loc = the room's level / floor, as the DPR names it)."""
    info = location_info(d)
    return [{"id": r, "loc": info["loc"].get(r, ""), "name": info["name"].get(r, r)} for r in _all_room_ids(d)]


def _run_lines(d, service, grid, cols):
    """The From-To lines of the length items for the export's vertical Measurement sheet, in BOQ order then place order: the active entries of each place,
    and (so the sheet ties to the Measurement sheet) one 'Recorded without From-To detail' line when the place holds more than its entries.
    Whole-level / whole-project entries are listed under their own name. Cancelled entries are not quantity, so they are not listed."""
    runs_all = item_runs_all(d, service)
    pos = {c["id"]: i for i, c in enumerate(cols)}
    meta = {c["id"]: c for c in cols}
    out = []
    for row in grid["rows"]:
        code = row["code"]
        per = runs_all.get(code) or {}
        keys = [k for k in per if runlog.active(per[k])]
        for k in sorted(keys, key=lambda k: (runlog.kind_of(k) != "zone", pos.get(k, 10 ** 9), str(k))):
            kind = runlog.kind_of(k)
            if kind == "zone":
                c = meta.get(k) or {"loc": "", "name": k}
                level, place = c["loc"], c["name"]
            elif kind == "area":
                level, place = k[len(runlog.AREA):], "Whole level / floor (one total)"
            else:
                level, place = "", "Whole project (not located)"
            act = runlog.active(per[k])
            rest = None
            if kind == "zone" and f"{code}|{k}" in grid["cells"]:
                rest = round(grid["cells"][f"{code}|{k}"] - runlog.active_total(act), 4)
            if rest is not None and rest > runlog.TOL:
                out.append({"code": code, "level": level, "place": place, "from": "Recorded without From-To detail", "to": "", "start": None, "end": None, "qty": rest, "remarks": "", "kind": "before"})
            for r in act:
                out.append({"code": code, "level": level, "place": place, "from": r.get("from"), "to": r.get("to"), "start": r.get("start"), "end": r.get("end"),
                            "qty": r.get("qty"), "remarks": r.get("remarks") or "", "kind": "run"})
    return out


@router.get("/{slug}/measure-grid/{service}/export")
def measure_grid_export(slug: str, service: str):
    """The grid as an Excel file (layout and rules: measurexl.py). Quantities are what Site Progress holds right now."""
    d = _need(slug)
    grid = _grid_data(d, service)
    st = _read_json(d / "structure.json", {}) or {}
    cols = _grid_columns(d)
    try:
        data = measurexl.build(st.get("name") or slug, service, slug, cols, grid, runs=_run_lines(d, service, grid, cols))
    except ValueError as e:
        raise HTTPException(400, str(e))
    fname = f"Measurement_{_slugify(slug)}_{_slugify(service)}_{dt.date.today().isoformat()}.xlsx"
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/{slug}/measure-grid/{service}/import-preview")
async def measure_grid_import_preview(slug: str, service: str, file: UploadFile = File(...)):
    """Read an Excel file made by the export and say, box by box, what importing it WOULD do. Nothing is saved here: the grid applies the
    changes itself through the normal save (POST /progress/rooms), so a locked box, a plan limit or a whole-number rule is judged exactly as
    when typing. Returns {"counts","changes":[{code,desc,unit,room,place,cur,new,over_plan}],"skipped":[{code,place,kind,why}],
    "unknown_items":[code],"unknown_places":[id]}.  kinds: locked, not_in_place, bad. A blank box means "no change"."""
    d = _need(slug)
    raw = await file.read()
    try:
        parsed = measurexl.read(raw)
    except measurexl.XlError as e:
        raise HTTPException(400, str(e))
    if parsed["service"] and parsed["service"] != service:
        raise HTTPException(400, f"This file was made for the service '{parsed['service']}', but this grid is '{service}'. Open that service's tab and import it there.")
    if parsed["slug"] and parsed["slug"] != slug:
        raise HTTPException(400, "This file was exported from a different project.")
    grid = _grid_data(d, service)
    rows = {r["code"]: r for r in grid["rows"]}
    cols = {c["id"]: c for c in _grid_columns(d)}
    ctx = location_context(d, service)
    changes, skipped, unknown_items, unknown_cols, same = [], [], [], set(), 0
    place = lambda pid: (cols[pid]["loc"] + " \u25B8 " + cols[pid]["name"]) if cols[pid]["loc"] and cols[pid]["loc"] != cols[pid]["name"] else cols[pid]["name"]
    for fr in parsed["rows"]:
        code = fr["code"]
        row = rows.get(code)
        if row is None:
            unknown_items.append(code)
            continue
        _appl, plan, _unit = _item_plan(d, service, code, ctx)
        for pid, rawv in fr["cells"].items():
            if pid not in cols:
                unknown_cols.add(pid)
                continue
            res = measurexl.parse_qty(rawv, row["count"])
            if res[0] == "blank":
                continue
            key = f"{code}|{pid}"
            if res[0] == "bad":
                skipped.append({"code": code, "place": place(pid), "kind": "bad", "why": res[1]})
                continue
            if key not in grid["cells"]:
                skipped.append({"code": code, "place": place(pid), "kind": "not_in_place", "why": "This item is not in this place."})
                continue
            if key in grid["locks"]:
                if abs(grid["cells"][key] - res[1]) < 1e-9:
                    same += 1
                else:
                    skipped.append({"code": code, "place": place(pid), "kind": "locked", "why": grid["locks"][key]["why"]})
                continue
            if abs(grid["cells"][key] - res[1]) < 1e-9:
                same += 1
                continue
            pl = plan.get(pid)
            changes.append({"code": code, "desc": row["desc"], "unit": row["unit"], "room": pid, "place": place(pid), "cur": grid["cells"][key], "new": res[1],
                            "expr": _clean_expr(fr["formulas"].get(pid), res[1]),
                            "over_plan": bool(not row["free"] and pl is not None and res[1] > pl + 1e-6)})
    kinds = lambda k: sum(1 for x in skipped if x["kind"] == k)
    return {"service": service,
            "counts": {"change": len(changes), "same": same, "over_plan": sum(1 for c in changes if c["over_plan"]), "locked": kinds("locked"),
                       "bad": kinds("bad"), "not_in_place": kinds("not_in_place"), "unknown_items": len(unknown_items), "unknown_places": len(unknown_cols)},
            "changes": changes, "skipped": skipped, "unknown_items": unknown_items, "unknown_places": sorted(unknown_cols)}


@router.post("/{slug}/progress/runs")
def add_run(slug: str, payload: dict, room: str = None):
    """Add one measurement entry. Body: {"service","item_code","scope"?: "zone" (default) | "area" | "project", "room" (zone) | "area" (the area's
    name, as GET lists it), "from","to", and either "start"+"end" (drum readings; the length is |start - end|) or "qty", "remarks"?}.
    The length is ADDED to what that place already has recorded, through the same checks as a typed quantity (a plan is a limit unless it
    was removed), and only into a place the item allows (see runlog.scope_table). Nothing is saved if any of that fails.
    Returns {"svc": service view, "zone": that place's entries, "summary": {...}}."""
    d = _need(slug)
    svc, code = payload.get("service"), payload.get("item_code")
    if not svc or code is None:
        raise HTTPException(400, "service and item_code are required")
    m = _meas_table(d, svc, code)
    if not m:
        raise HTTPException(404, "that item is not in this service")
    if not runlog.is_run_unit(m["unit"]):
        raise HTTPException(400, f"measurements are for length items (Mtr, RMT); this item is in {m['unit'] or 'no unit'}")
    t = _scope_of(m["table"], payload)
    if not t["allowed"]:
        raise HTTPException(400, t["why"])
    try:
        clean = runlog.clean_run(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cur = t["_raw"]
    new_done = round(cur + clean["qty"], 3)
    _fits(d, svc, code, t, cur, new_done)
    view = _write_quantity(slug, d, svc, code, t, new_done, room)
    runs = _item_runs(d)
    lst = runs.setdefault(svc, {}).setdefault(str(code), {}).setdefault(t["key"], [])
    lst.append(runlog.new_run(clean))
    (d / "item_runs.json").write_text(json.dumps(runs, ensure_ascii=False))
    return {"svc": view, "zone": runlog.view(lst, new_done), "summary": runlog.coverage(_meas_table(d, svc, code)["table"])}


@router.post("/{slug}/progress/runs/cancel")
def cancel_run(slug: str, payload: dict, room: str = None):
    """Cancel (or restore) one measurement entry. Body: {"service","item_code","scope"?,"room"|"area","run_id","cancelled": true|false (default
    true), "reason"?}. A cancelled entry stays on record, struck through; the place's quantity goes down (restore: up) by its length, through the
    same checks as any quantity -- a restore that would not fit the plan, or into a place that has since been closed, is refused and nothing changes."""
    d = _need(slug)
    svc, code, run_id = payload.get("service"), payload.get("item_code"), payload.get("run_id")
    if not svc or code is None or not run_id:
        raise HTTPException(400, "service, item_code and run_id are required")
    cancel = payload.get("cancelled", True) is not False
    m = _meas_table(d, svc, code)
    if not m:
        raise HTTPException(404, "that item is not in this service")
    t = _scope_of(m["table"], payload)
    runs = _item_runs(d)
    lst = ((runs.get(svc) or {}).get(str(code)) or {}).get(t["key"])
    run = next((r for r in lst or [] if r.get("id") == run_id), None)
    if run is None:
        raise HTTPException(404, "that measurement is not on record")
    if bool(run.get("cancelled")) == cancel:
        raise HTTPException(409, "that measurement is already " + ("cancelled" if cancel else "active"))
    if cancel and not t["writable"]:
        raise HTTPException(400, t["wwhy"])
    if not cancel and not t["allowed"]:
        raise HTTPException(400, t["why"])
    cur = t["_raw"]
    new_done = round(max(0.0, cur - float(run["qty"])) if cancel else cur + float(run["qty"]), 3)
    if not cancel:
        _fits(d, svc, code, t, cur, new_done)
    view = _write_quantity(slug, d, svc, code, t, new_done, room)
    run["cancelled"] = cancel
    run["reason"] = str(payload.get("reason") or "").strip()[:200] if cancel else ""
    (d / "item_runs.json").write_text(json.dumps(runs, ensure_ascii=False))
    return {"svc": view, "zone": runlog.view(lst, new_done), "summary": runlog.coverage(_meas_table(d, svc, code)["table"])}


@router.post("/{slug}/mark-activity-rooms-done")
def mark_activity_rooms_done(slug: str, payload: dict, room: str = None):
    """Labour-only companion to /mark-rooms-done above -- same exact
    mechanism, but for a labour-only ACTIVITY (Zari work, core-cutting,
    chasing, testing...) instead of a BOQ item. Body: {"service","activity",
    "rooms":[room_id,...], "done": bool (default True)}.

    Replaces the old "drag the overall % slider" way of recording labour
    progress. That set the '*' fraction directly and, by
    itemprog.set_progress()'s own documented behaviour, WIPED every
    existing per-room override to keep the bar uniform -- the exact same
    silent-flatten risk /mark-rooms-done was built to prevent for items,
    just never fixed for labour-only activities until now. Each room here
    gets its OWN per-room override (frac=1.0 or 0.0) via
    itemprog.set_progress(..., room=room_id) -- called once per room,
    never the room=None ("*") global path -- so marking a batch done can
    never wipe any other room's recorded state, the same guarantee
    /mark-rooms-done already gives items.

    `done=False` is the direct undo, same as /mark-rooms-done -- the same
    action with the opposite target value, not a special case."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    room_ids = [str(r) for r in (payload.get("rooms") or [])]
    done = payload.get("done", True)
    if not svc or not act:
        raise HTTPException(400, "service and activity are required")
    if not room_ids:
        raise HTTPException(400, "tick at least one room")
    store = _activity_prog(d)
    frac = 1.0 if done else 0.0
    for rid in room_ids:
        itemprog.set_progress(store, svc, act, frac, room=rid)
    (d / "activity_progress.json").write_text(json.dumps(store, ensure_ascii=False))
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
            "services": list(acts.keys()),   # upload order, see /{slug} above
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
        # a rate column the engineer chose for a sheet earlier is remembered (boq_columns.json) and used again
        parsed, skipped = boq.parse_workbook(str(dest), overrides=_read_json(d / "boq_columns.json", {}) or {})
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
        # a split-rate sheet (Supply Rate + Installation Rate) also tells each line's installation share; same never-clobber rule
        if r.get("install_pcts"):
            all_ip = _read_json(d / "install_pct.json", {}) or {}
            svc_ip = all_ip.setdefault(svc, {})
            for code, pct in r["install_pcts"].items():
                if code not in svc_ip:
                    svc_ip[code] = pct
            (d / "install_pct.json").write_text(json.dumps(all_ip, ensure_ascii=False))

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
        if "install_share" in items.columns:
            items = items.drop(columns=["install_share"])
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
            "needs_qty_mode": needs_qty_mode,
            "read": {svc: r["report"] for svc, r in parsed.items()}}


@router.post("/{slug}/boq/column")
def choose_boq_column(slug: str, payload: dict):
    """The engineer's answer to "which column is the rate?" for one sheet of the saved BOQ.
    Body: {"sheet": str, "role": "rate", "header": the column's header text}.
    The saved boq.xlsx is read again with that column; the choice is remembered for the sheet (boq_columns.json). Only rates / installation
    shares that the PREVIOUS reading put there are replaced -- one the engineer typed by hand is never touched."""
    d = _need(slug)
    sheet, role, header = payload.get("sheet"), payload.get("role"), payload.get("header")
    if role != "rate" or not sheet or not header:
        raise HTTPException(400, "sheet, role ('rate') and header are required")
    dest = d / "boq.xlsx"
    if not dest.exists():
        raise HTTPException(404, "no BOQ has been uploaded for this project")
    old = _read_json(d / "boq_columns.json", {}) or {}
    new = {**old, sheet: {**(old.get(sheet) or {}), "rate": header}}
    try:
        before, _ = boq.parse_workbook(str(dest), overrides=old)
        after, _ = boq.parse_workbook(str(dest), overrides=new)
    except Exception as e:
        raise HTTPException(422, f"could not read this BOQ: {e}")
    rep = next((x for r in after.values() for x in r["report"]["sheets"] if x["sheet"] == sheet), None)
    if rep is None:
        raise HTTPException(404, f"sheet {sheet!r} is not in this BOQ")
    got = [c["header"] for c in rep["columns"] if c["role"] == "Rate"]
    if rep["choices"] or not got or not (boq._htext(got[0]) == boq._htext(header) or boq._canon(got[0]) == boq._canon(header)):
        raise HTTPException(400, f"{header!r} is not a rate column of sheet {sheet!r}")
    all_rates = _read_json(d / "rates.json", {}) or {}
    all_ip = _read_json(d / "install_pct.json", {}) or {}
    for svc, r in after.items():
        prev = before.get(svc) or {}
        for store, key in ((all_rates.setdefault(svc, {}), "rates"), (all_ip.setdefault(svc, {}), "install_pcts")):
            for code, val in (r.get(key) or {}).items():
                if code not in store or store[code] == (prev.get(key) or {}).get(code):    # empty, or still what the old reading wrote
                    store[code] = val
    (d / "rates.json").write_text(json.dumps(all_rates, ensure_ascii=False))
    if any(all_ip.values()):
        (d / "install_pct.json").write_text(json.dumps(all_ip, ensure_ascii=False))
    (d / "boq_columns.json").write_text(json.dumps(new, ensure_ascii=False))
    return {"ok": True, "read": {svc: r["report"] for svc, r in after.items()}}


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
    over = _read_json(d / "over_boq.json", {}) or {}
    if (over.get(svc) or {}).pop(str(code), None) is not None:        # a manual planned edit takes over from "above the BOQ"
        (d / "over_boq.json").write_text(json.dumps(over, ensure_ascii=False))
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
        # with_progress: ALSO wipe the recorded work of the items that belong to this activity alone (item_progress.json).
        # Without it (the default, and what delete always did) the items keep their recorded work -- only the grouping goes.
        wipe = _activity_only_codes(d, svc, name) if payload.get("with_progress") else []
        acts = [a for a in acts if a != name]
        m.data.get(svc, {}).pop(name, None)
        _delete_progress_activity(d, svc, name)
        if wipe:
            _clear_item_progress(d, svc, wipe)
    else:
        raise HTTPException(400, f"unknown op '{op}'")

    acts_all[svc] = acts
    (d / "activities.json").write_text(json.dumps(acts_all, ensure_ascii=False))
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))
    return _service_view(d, svc, room=room)


def _activity_only_codes(d, svc, name):
    """BOQ items mapped to this activity and to NO other activity of the service. An item shared with another activity
    is never wiped by deleting one of them: the other activity still owns its work."""
    md = _load_mapping(d).data.get(svc, {})
    mine = [str(c) for c in md.get(name, [])]
    others = {str(c) for a, cs in md.items() if a != name for c in cs}
    return [c for c in dict.fromkeys(mine) if c not in others]


def _clear_item_progress(d, svc, codes):
    """Erase the recorded progress (overall and per-room) of these items. This is the part that cannot be undone."""
    store = _item_prog(d)
    node = store.get(svc) or {}
    hit = [c for c in codes if c in node]
    for c in hit:
        node.pop(c, None)
    if hit:
        (d / "item_progress.json").write_text(json.dumps(store, ensure_ascii=False))


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


# ---------------------------------------------------------------- DPR: manpower, hold-ups, plan, parties
# What the site engineer types once a day for the client-facing DPR, next to what the progress sliders already capture:
#   dpr_days.json : {"2026-10-13": {"manpower": {service: {"supervisor": n, "skilled": n, "helper": n}}, "holdups": "...", "plan": "..."}}
#   dpr_meta.json : {"client": "...", "contractor": "...", "prepared_by": "..."}     (project level, typed once)
# Nothing is ever filled in for the engineer: a service with no manpower row, or a day with no text, is simply "not entered" and the
# DPR shows "—" for it.
_MANPOWER_ROLES = ("supervisor", "skilled", "helper")
_DPR_TEXT_MAX = 2000
_DPR_META_KEYS = ("client", "contractor", "prepared_by")


def _dpr_days(d):
    v = _read_json(d / "dpr_days.json", {})
    return v if isinstance(v, dict) else {}


def _dpr_meta(d):
    v = _read_json(d / "dpr_meta.json", {})
    return v if isinstance(v, dict) else {}


def _iso_date(v, what="date"):
    try:
        return dt.date.fromisoformat(str(v)).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be YYYY-MM-DD")


def _count(v, what):
    """A head-count box: blank = nothing typed (None), else a whole number 0..9999."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be a whole number")
    if f != f or f < 0 or f > 9999 or f != int(f):
        raise HTTPException(400, f"{what} must be a whole number between 0 and 9999")
    return int(f)


def _day_view(d, date):
    days, meta = _dpr_days(d), _dpr_meta(d)
    day = days.get(date) or {}
    boqdf = _load_boq(d)
    return {"date": date,
            "weekday": dt.date.fromisoformat(date).strftime("%A"),
            "services": boqdf.service.unique().tolist() if not boqdf.empty else [],
            "manpower": day.get("manpower") or {},
            "holdups": day.get("holdups") or "",
            "plan": day.get("plan") or "",
            **{k: meta.get(k) or "" for k in _DPR_META_KEYS}}


@router.get("/{slug}/dpr/day")
def dpr_day(slug: str, date: str = None):
    """The manpower / hold-ups / plan entered for one day (today when `date` is omitted), plus the project's client / contractor / prepared-by."""
    d = _need(slug)
    return _day_view(d, _iso_date(date or dt.date.today().isoformat()))


@router.post("/{slug}/dpr/day")
def save_dpr_day(slug: str, payload: dict):
    """Save what was typed for one day. Body (every key optional -- only the keys present are touched):
        {"date": "YYYY-MM-DD" (default today),
         "manpower": {service: {"supervisor": n, "skilled": n, "helper": n} | null, ...},
         "holdups": "text", "plan": "text",
         "client": "...", "contractor": "...", "prepared_by": "..."}      <- these three are project level, not per day
    A service row counts as entered as soon as one of its three boxes has a number (the blank boxes of that row are then 0); a row with
    all three blank, or null, removes that service's entry for the day. Nothing is carried over from another day."""
    d = _need(slug)
    date = _iso_date(payload.get("date") or dt.date.today().isoformat())
    days = _dpr_days(d)
    day = days.get(date) or {}
    if "manpower" in payload:
        mp_in = payload.get("manpower") or {}
        if not isinstance(mp_in, dict):
            raise HTTPException(400, "manpower must be an object keyed by service")
        known = set(_load_boq(d).service.unique().tolist()) if not _load_boq(d).empty else set()
        mp = dict(day.get("manpower") or {})
        for svc, row in mp_in.items():
            if svc not in known:
                raise HTTPException(400, f"unknown service: {svc}")
            vals = {r: _count((row or {}).get(r), f"{svc} {r}") for r in _MANPOWER_ROLES}
            if all(v is None for v in vals.values()):
                mp.pop(svc, None)
            else:
                mp[svc] = {r: (v or 0) for r, v in vals.items()}
        day["manpower"] = mp
    for k in ("holdups", "plan"):
        if k in payload:
            t = str(payload.get(k) or "").strip()
            if len(t) > _DPR_TEXT_MAX:
                raise HTTPException(400, f"{k} is too long (max {_DPR_TEXT_MAX} characters)")
            day[k] = t
    if any(k in payload for k in _DPR_META_KEYS):
        meta = _dpr_meta(d)
        for k in _DPR_META_KEYS:
            if k in payload:
                t = " ".join(str(payload.get(k) or "").split())
                if len(t) > 200:
                    raise HTTPException(400, f"{k} is too long (max 200 characters)")
                meta[k] = t
        (d / "dpr_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    if day.get("manpower") or day.get("holdups") or day.get("plan"):
        days[date] = day
    else:
        days.pop(date, None)
    (d / "dpr_days.json").write_text(json.dumps(days, ensure_ascii=False))
    return _day_view(d, date)


def _nn(v):
    """A pandas/numpy number as a plain float, None for a missing one."""
    try:
        if v is None or v != v:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _entries_qty(entries):
    """Work quantity of a set of DPR log entries (their qty_today): 0 for none, None as soon as one of them cannot be known."""
    tot = 0.0
    for e in entries:
        q = dpr.qty_today(e)
        if q is None:
            return None
        tot += q
    return round(tot, 3)


def _client_dpr_data(d, slug, start, end, service, rooms_set, floor_names, room_names):
    """Everything the client-facing DPR shows, as plain data for dpr.build_client_dpr(). Same engines as every other number in the app
    (itemprog.compute, pnl.compute_item_pnl), nothing re-derived.

    "Today" = the work of the report day(s) only: the sum of each log line's qty_today (what the day ADDED, see dpr.qty_today). "Cumulative"
    = the item's real quantity done as of the end date (what is done now, minus whatever was logged after that date). "Up to previous day" =
    cumulative minus today. A figure that cannot be known (a log line from before the day-quantity was recorded, a quantity the BOQ does not
    give) stays None and is written as "—"."""
    struct = _read_json(d / "structure.json", None)
    boqdf = _load_boq(d)
    all_services = boqdf.service.unique().tolist() if not boqdf.empty else []
    services = [service] if service else all_services
    m = _load_mapping(d)
    all_rooms = _all_room_ids(d)
    refs = clientref.refs_for(d, boqdf)
    leaf_plural = _leaf_label(d).lower() + "s"
    full_log = _dpr_log(d)
    log = dpr.filter_by_area(full_log, floors=floor_names, rooms=room_names) if floor_names else full_log
    period = start != end

    def by_item(entries):
        out = {}
        for e in entries:
            if e.get("item_code") is None:
                continue
            out.setdefault((e["service"], str(e["item_code"])), []).append(e)
        return out

    def one_activity(entries):               # an item mapped to several activities is logged once per activity: count it once
        return [e for e in entries if e["activity"] == entries[0]["activity"]] if entries else []

    in_range = by_item([e for e in log if start <= e["date"] <= end])
    after = by_item([e for e in log if e["date"] > end])

    boq_rows, lines = [], []
    progress, values_by_svc = {}, {}
    pos = 0
    for si, svc in enumerate(services):
        items = boqdf[boqdf.service == svc]
        total_v = done_v = today_v = 0.0
        done_known = today_known = True
        if not items.empty:
            prog_svc = _item_prog(d).get(svc, {})
            rooms_cfg = _item_rooms(d).get(svc, {})
            rqg = _item_room_qty(d).get(svc, {})
            used = itemprog.compute(items, prog_svc, rooms_cfg, all_rooms, _planned(d, svc), room_qty_groups=rqg, rooms=rooms_set)
            ip = pnl.compute_item_pnl(used, rates=_rates(d, svc), install_pct=_install_pct(d, svc),
                                      default_install_pct=_settings(d).get("default_install_pct"))
            mapped = {str(c) for a in m.activities(svc) for c in m.get(svc, a)}
            acts = m.activities(svc)
            for it in ip.itertuples():
                code = str(it.item_code)
                if code not in mapped:
                    continue
                pos += 1
                planned, used_now, irate = _nn(it.planned_total), _nn(it.used), _nn(it.install_rate)
                rb = itemprog.room_buckets(code, prog_svc, rooms_cfg, all_rooms, room_qty_groups=rqg, rooms=rooms_set)
                got = in_range.get((svc, code), [])
                today = _entries_qty(one_activity(got))
                later = _entries_qty(one_activity(after.get((svc, code), [])))
                cum = None if (used_now is None or later is None) else round(used_now - later, 3)
                prev = None if (cum is None or today is None) else round(cum - today, 3)
                ref = refs.get((svc, code)) or {}
                desc = ref.get("text") or " ".join(str(it.description).split())
                unit = getattr(it, "unit", None)
                unit = None if unit is None or (isinstance(unit, float) and unit != unit) else str(unit)
                q = dpr.present_qty(unit, prev, today, cum)         # what is PRINTED: whole for counted units, never a negative "today"
                boq_rows.append({"service": svc, "ref": ref.get("ref"), "desc": desc, "unit": unit, "planned": planned, "cum": cum,
                                 "irate": irate, "zones_done": rb["done"], "zones_inp": rb["in_progress"], "zones_pend": rb["not_started"]})
                if irate is not None and planned is not None:
                    total_v += planned * irate
                    if cum is None:
                        done_known = False
                    else:
                        done_v += cum * irate
                    if q["today"] is None and got:
                        today_known = False
                    elif q["today"]:
                        today_v += q["today"] * irate
                if got and (today is None or abs(today) > 1e-9):
                    mine = one_activity(got)
                    ai = acts.index(mine[0]["activity"]) if mine[0]["activity"] in acts else len(acts)
                    lines.append({"_k": (si, ai, pos), "service": svc, "activity": mine[0]["activity"],
                                  "parent": " ".join(ref["parent"].split()) if ref.get("parent") else None,
                                  "ref": ref.get("ref"), "desc": desc, "unit": unit, "boq": planned, "prev": q["prev"], "today": q["today"], "cum": q["cum"],
                                  "balance": None if (planned is None or q["cum"] is None) else round(planned - q["cum"], 3),
                                  "pct": None if (planned in (None, 0) or cum is None) else cum / planned,
                                  "location": dpr.location_text(mine, leaf_plural), "remarks": q["remark"]})
        todate = (done_v / total_v) if (total_v > 0 and done_known) else None
        tdy = (today_v / total_v) if (total_v > 0 and today_known) else None
        progress[svc] = {"todate": todate, "today": tdy,
                         "prev": None if (todate is None or tdy is None) else todate - tdy}
        values_by_svc[svc] = {"total": round(total_v, 2) if total_v > 0 else None,
                              "done": round(done_v, 2) if (total_v > 0 and done_known) else None,
                              "remaining": round(total_v - done_v, 2) if (total_v > 0 and done_known) else None,
                              "_t": total_v, "_d": done_v, "_y": today_v, "_dk": done_known, "_yk": today_known}
    lines.sort(key=lambda x: x.pop("_k"))

    rated = [v for v in values_by_svc.values() if v["_t"] > 0]
    tot_all = sum(v["_t"] for v in rated)
    overall = {"todate": None, "today": None, "prev": None}
    values = {"done": None, "remaining": None, "total": None, "by_service": {k: {a: b for a, b in v.items() if not a.startswith("_")}
                                                                            for k, v in values_by_svc.items()}}
    if tot_all > 0:
        if all(v["_dk"] for v in rated):
            overall["todate"] = sum(v["_d"] for v in rated) / tot_all
            values.update(done=round(sum(v["_d"] for v in rated), 2), remaining=round(tot_all - sum(v["_d"] for v in rated), 2),
                          total=round(tot_all, 2))
        if all(v["_yk"] for v in rated):
            overall["today"] = sum(v["_y"] for v in rated) / tot_all
        if overall["todate"] is not None and overall["today"] is not None:
            overall["prev"] = overall["todate"] - overall["today"]

    # manpower typed by the engineer: this day's (or the period's) head-count per service, and the man-days of the days before
    days = _dpr_days(d)
    manpower, mp_total = {}, []
    for svc in services:
        today_rows = [(ds, (days.get(ds) or {}).get("manpower", {}).get(svc)) for ds in days if start <= ds <= end]
        today_rows = [r for _ds, r in today_rows if r]
        prev_days = sum(sum(int(r.get(k) or 0) for k in dpr_roles) for ds in days if ds < start
                        for r in [((days.get(ds) or {}).get("manpower") or {}).get(svc)] if r)
        row = {"prev_days": prev_days if any(ds < start and ((days.get(ds) or {}).get("manpower") or {}).get(svc) for ds in days) else None}
        if today_rows:
            for k in dpr_roles:
                row[k] = sum(int(r.get(k) or 0) for r in today_rows)
            row["total"] = sum(row[k] for k in dpr_roles)
            row["to_date"] = (row["prev_days"] or 0) + row["total"]
            mp_total.append(row["total"])
        manpower[svc] = row

    # DPR number = how many different days have a DPR up to this one (a day with progress logged or manpower typed, plus this day itself)
    seen = {e["date"] for e in full_log if e["date"] <= end} | {ds for ds in days if ds <= end} | {end}
    meta = _dpr_meta(d)
    def fmt_day(ds):
        return dt.date.fromisoformat(ds).strftime("%d-%b-%Y")
    texts = lambda key: "\n".join(
        (f"{fmt_day(ds)}: {t}" if period else t) for ds in sorted(days) if start <= ds <= end for t in [(days[ds].get(key) or "").strip()] if t)
    scope = []
    if service:
        scope.append(service)
    if floor_names:
        scope.append(", ".join(sorted(floor_names)) + (" \u2192 " + ", ".join(sorted(room_names)) if room_names else ""))
    return {"project": (struct or {}).get("name") or slug, "date_label": fmt_day(start) if not period else f"{fmt_day(start)} to {fmt_day(end)}",
            "weekday": None if period else dt.date.fromisoformat(start).strftime("%A"), "period": period,
            "dpr_no": None if period else len(seen), "client": meta.get("client"), "contractor": meta.get("contractor"),
            "prepared_by": meta.get("prepared_by"), "scope": " \u00b7 ".join(scope), "services": [s_ for s_ in services if s_ in progress],
            "progress": progress, "overall": overall, "values": values, "manpower": manpower,
            "manpower_total": sum(mp_total) if mp_total else None, "lines": lines, "boq_rows": boq_rows,
            "holdups": texts("holdups"), "plan": texts("plan"), "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}


dpr_roles = ("supervisor", "skilled", "helper")

_SUMMARY_SUFFIX = {"none": "", "without": "_summary-without-amount", "with": "_summary-with-amount"}


@router.get("/{slug}/export-dpr")
def export_dpr(slug: str, start: str, end: str = None, service: str = None,
               rooms: list[str] = Query(None), summary: str = None):
    """The full DPR -- Summary + Item detail + Daily updates -- for one date
    (end omitted) or a range (inclusive). Reuses the exact same pnl.py /
    itemprog.py / dpr.py functions every other route here already calls;
    nothing new is computed, only assembled and formatted.

    service : optional -- restrict every sheet to just this one service.
    rooms   : optional -- structure NODE ids (the picker's tree, same ids
              item_rooms.json/item_progress.json already key by), scoping
              ALL THREE sheets together to just this area: Summary and Item
              detail via itemprog.compute()/room_buckets()'s own `rooms`
              param (summed over just these rooms, real numbers, no new
              engine), and Daily updates via dpr.filter_by_area() (which
              works in floor/room NAME space -- translated via the
              already-existing _room_location_map(), the SAME lookup
              _log_dpr_change() itself uses to write dpr_log.json in the
              first place, so the two can never disagree -- including the
              hospital case, where "floor" is really a joined "Wing ·
              Floor" location string, not a single level). Neither param
              changes a single number for a caller that never passes them
              -- today's whole-project export, byte-for-byte."""
    d = _need(slug)
    end = end or start
    struct = _read_json(d / "structure.json", None)
    if not struct:
        raise HTTPException(400, "no structure yet")
    location_label = _location_label(d)
    leaf_label = _leaf_label(d)

    boqdf = _load_boq(d)
    all_services = boqdf.service.unique().tolist() if not boqdf.empty else []   # upload order, see /{slug} above
    services = [service] if service else all_services
    if service and service not in all_services:
        raise HTTPException(400, f"unknown service: {service}")
    m = _load_mapping(d)
    all_rooms = _all_room_ids(d)

    # Translate the picker's room ids into the name-space dpr_log.json
    # itself logs in, via the SAME lookup _log_dpr_change() uses to write
    # that log -- never a second, possibly-diverging translation. rooms_set
    # stays in ID-space for itemprog (compute()/room_buckets() already key
    # by structure ids); floor_names/room_names are name-space, for
    # dpr.filter_by_area() only.
    rooms_set = set(rooms) if rooms else None
    floor_names = room_names = None
    if rooms_set:
        lookup = _room_location_map(d)
        room_names = {lookup[r]["name"] for r in rooms_set if r in lookup and lookup[r]["name"]}
        floor_names = {lookup[r]["location"] for r in rooms_set if r in lookup and lookup[r]["location"]}

    if summary is not None:
        # The client-facing DPR (sheet "DPR", optionally + Summary and BOQ progress). `summary` = none | without | with. Without this
        # parameter the export below is exactly what it always was.
        if summary not in _SUMMARY_SUFFIX:
            raise HTTPException(400, "summary must be none, without or with")
        try:
            sd, ed = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        except ValueError:
            raise HTTPException(400, "start/end must be YYYY-MM-DD")
        if ed < sd:
            raise HTTPException(400, "end date is before start date")
        data = _client_dpr_data(d, slug, start, end, service, rooms_set, floor_names, room_names)
        wb = dpr.build_client_dpr(data, None if summary == "none" else summary)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        bits = [_slugify(slug), start] + ([f"to_{end}"] if end != start else []) + \
            ([_slugify(service)] if service else []) + (["area"] if rooms_set else [])
        fname = "DPR_" + "_".join(bits) + _SUMMARY_SUFFIX[summary] + ".xlsx"
        return Response(content=buf.read(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

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
                                room_qty_groups=room_qty_groups, rooms=rooms_set)
        ip = pnl.compute_item_pnl(used, rates=_rates(d, svc), install_pct=_install_pct(d, svc),
                                  default_install_pct=_settings(d).get("default_install_pct"))
        rp = pnl.rollup_pnl(ip, m, svc)
        rollups[svc] = rp
        mapped_codes = {str(c) for a in m.activities(svc) for c in m.get(svc, a)}
        for it in ip.itertuples():
            code = str(it.item_code)
            if code not in mapped_codes:
                continue   # only items actually inside an activity -- not raw/unmapped BOQ noise
            rb = itemprog.room_buckets(code, prog_svc, rooms_cfg, all_rooms,
                                       room_qty_groups=room_qty_groups, rooms=rooms_set)
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
    log = _dpr_log(d)
    if floor_names:
        log = dpr.filter_by_area(log, floors=floor_names, rooms=room_names)
    by_date = dpr.entries_for_range(log, start, end)
    date_list, cur = [], start_d
    while cur <= end_d:
        date_list.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    # Rolled up ACROSS floors, per day -- the real fix for "bahut bada"
    # (the same activity+item touching several floors used to produce that
    # many near-duplicate lines in this exact sheet). Same real entries as
    # before, aggregated one level higher; nothing about what actually
    # happened is lost, see dpr.rollup_across_floors's own docstring.
    days = [(ds, dpr.rollup_across_floors(by_date.get(ds, []), services, leaf_label=leaf_label)) for ds in date_list]

    date_label = start if start == end else f"{start} to {end}"
    if service:
        date_label += f" \u2014 {service}"
    if floor_names:
        # A room selection that covers every one of its floor's real rooms
        # (an engineer ticked the floor itself, or happened to tick all its
        # rooms individually -- indistinguishable, and doesn't need to be
        # distinguished) reads as that whole floor, not "N of M rooms".
        floor_room_ids = {rid for rid, info in _room_location_map(d).items() if info["location"] in floor_names}
        whole_floors = (rooms_set is not None and floor_room_ids <= rooms_set)
        area_label = ", ".join(sorted(floor_names)) if whole_floors else \
            ", ".join(sorted(floor_names)) + " \u2192 " + ", ".join(sorted(room_names))
        date_label += f" \u2014 {area_label}"
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
    scope_bits = [_slugify(slug), start] + ([f"to_{end}"] if end != start else []) + \
        ([_slugify(service)] if service else []) + (["area"] if rooms_set else [])
    fname = "DPR_" + "_".join(scope_bits) + ".xlsx"
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------------------ admin lock
# Rebuilding the structure wipes ALL recorded progress of the project. The Rebuild button is hidden for everybody (the page only shows it
# after the owner's secret hold / Ctrl+Shift+A). OPTIONAL extra lock: if the environment variable MI_ADMIN_PASSWORD is set on the server
# (start.bat / the host's settings), Rebuild also needs that password: the browser sends it in the X-Mi-Admin header (URL-encoded) and the
# server compares it in constant time. NOT set => no password is asked (hiding the button is then the only protection).
ADMIN_ENV = "MI_ADMIN_PASSWORD"


def _require_admin(supplied):
    from urllib.parse import unquote
    pw = os.environ.get(ADMIN_ENV, "")
    if not pw:
        return                                           # no password configured: nothing to check
    if not supplied or not hmac.compare_digest(unquote(supplied).encode("utf-8"), pw.encode("utf-8")):
        time.sleep(0.4)                                  # slows down guessing
        raise HTTPException(403, "Wrong admin password.")


@router.post("/{slug}/admin/check")
def admin_check(slug: str, x_mi_admin: str | None = Header(default=None)):
    """Does this password open the admin lock? `protected` says whether the server has a password at all (the UI then skips the dialog)."""
    _need(slug)
    _require_admin(x_mi_admin)
    return {"ok": True, "protected": bool(os.environ.get(ADMIN_ENV, ""))}


@router.post("/{slug}/structure/reset")
def reset_structure(slug: str, x_mi_admin: str | None = Header(default=None)):
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
    _require_admin(x_mi_admin)                           # wipes every recorded progress of the project
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
def structure_from_template(slug: str, payload: dict, x_mi_admin: str | None = Header(default=None)):
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
    if (d / "structure.json").exists():                  # replacing a structure that is already there = the same damage as a rebuild
        _require_admin(x_mi_admin)
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
    all_names = [n for n, _s in names_with_service]
    for n, svc_label in names_with_service:
        if n in seen:
            continue
        seen.add(n)
        row = rows.get(linkage._norm(n)) or {}
        cross = bool(svc_label) and svc_label != _forecast_service(service)
        # Real register rows that are very likely the SAME material as `n`,
        # differing only in a batch/coil/roll size (see
        # linkage.batch_siblings' own docstring) -- lets the picker offer
        # "also add these N batches?" right where the engineer already is,
        # instead of a separate trip to the link editor's own search box to
        # hunt for each one by hand. Purely a SUGGESTION the engineer still
        # has to tick -- nothing here merges anything on its own.
        materials.append({"name": n, "unit": row.get("unit"), "already": n in already,
                          "other_service": svc_label if cross else None,
                          "batch_siblings": linkage.batch_siblings(n, all_names)})
    materials.sort(key=lambda m: (m["already"], bool(m["other_service"]), m["name"]))
    return {"available": True, "run": run_name, "materials": materials}


@router.post("/{slug}/quick-item")
def add_quick_item(slug: str, payload: dict, room: str = None):
    """Add a plannable line straight from a stock/register material, for the
    common case where the site engineer knows the material name and wants to
    plan it under an activity without it first existing as a BOQ line. The
    material comes from THIS service's own latest forecast run, so there is
    no fuzzy-match step — the stock link is created immediately and exactly.
    Body: {"service","activity","material"} for the common single-material
    case, OR {"service","activity","materials":[...]} to combine several
    register rows into ONE plannable item and ONE link from the start --
    the real, reported case being the SAME physical material split into
    several register lines purely because it arrived in different coil
    sizes ("2.5 SQMM WIRE BLACK (90 MTR/COIL)" and "...(270 MTR/COIL)" are
    the same wire, not two materials). This never auto-detects or merges
    that on its own -- see realtime.combine_item()'s own "never invent"
    stance -- the engineer explicitly ticks which register rows are really
    the same material, in the picker, before this runs; nothing here
    guesses that on its own. Planned qty starts at 0; the engineer sets it
    afterwards via the same ✎ every other item uses, ONCE, not once per
    coil batch."""
    d = _need(slug)
    svc = payload.get("service")
    act = payload.get("activity")
    materials = payload.get("materials")
    if not materials:
        single = (payload.get("material") or "").strip()
        materials = [single] if single else []
    materials = [m.strip() for m in materials if m and m.strip()]
    if not svc or not act or not materials:
        raise HTTPException(400, "service, activity and at least one material are required")
    known_acts = (_read_json(d / "activities.json", {}) or {}).get(svc, [])
    if act not in known_acts:
        raise HTTPException(400, f"activity '{act}' does not exist yet — create it first")

    rows, names, run, _filtered = _forecast_pool(slug, svc)
    if run is None:
        raise HTTPException(400, "no forecast run for this project yet — upload "
                                 "the stock register on the Forecast tab first")
    full_rows = None
    resolved = []   # [(material, row), ...] -- every pick verified against a real register row
    for material in materials:
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
            if full_rows is None:
                full_rows = _full_run_rows(slug)
            row = full_rows.get(linkage._norm(material))
        if row is None:
            raise HTTPException(404, f"'{material}' is not in the stock register")
        resolved.append((material, row))
    # unit comes from the FIRST material -- when combining several coil-batch
    # rows of the same wire, they always share one real unit (MTR); a
    # genuine unit mismatch across ticked rows is a sign they are NOT the
    # same material after all, so this is flagged rather than silently
    # picking one arbitrarily.
    unit = resolved[0][1].get("unit") or "Nos"
    units_seen = {(r.get("unit") or "").strip().upper() for _m, r in resolved}
    if len(units_seen) > 1:
        raise HTTPException(400, "the selected materials don't share the same unit — "
                                 "they're probably not the same material; combine only "
                                 "rows that genuinely are")
    description = resolved[0][0]   # the first pick's own real name, never invented

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
                     if it["material"] == description
                     and it["item_code"] in m.get(svc, act)), None)
    if existing:
        code = existing["item_code"]           # re-picking for the SAME activity reuses its line
    else:
        svc_store["_seq"] += 1
        code = f"QI{svc_store['_seq']}"
        svc_store["items"].append({"item_code": code, "description": description,
                                   "unit": unit, "material": description})
    (d / "quick_items.json").write_text(json.dumps(store, ensure_ascii=False))

    if code not in m.get(svc, act):             # don't let a re-pick toggle it back OUT
        m.toggle(svc, act, code)
    (d / "mapping.json").write_text(json.dumps(m.to_dict(), ensure_ascii=False))

    links = _load_links(d)
    # exact -- every material here was picked straight from the register
    # itself (or explicitly ticked by the engineer as "this is the same
    # material"), never a fuzzy guess.
    links.setdefault(svc, {})[code] = [m for m, _r in resolved]
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


def _stock_sources():
    """Every stock that exists, newest first: {stock_slug: {...of its latest run}}. A stock is what the Forecast tab made
    from one upload or one Google Sheet link; its name is the project name it was given there."""
    by = {}
    if not RUNS.exists():
        return by
    for rd in RUNS.iterdir():
        m = rd / "meta.json"
        if not m.exists():
            continue
        try:
            meta = json.loads(m.read_text())
        except Exception:
            continue
        k = meta.get("project_slug")
        if not k:
            continue
        e = by.setdefault(k, {"stock": k, "name": meta.get("project") or k, "runs": 0, "created": "", "run": None, "meta": {}})
        e["runs"] += 1
        if meta.get("created", "") >= e["created"]:
            e.update(created=meta.get("created", ""), run=rd, meta=meta, name=meta.get("project") or k)
    return by


def _stock_option(d, e):
    """One pick-list row: what the stock is, how fresh, and how many of THIS project's links it already carries."""
    meta, run = e["meta"], e["run"]
    out = {"stock": e["stock"], "name": e["name"], "runs": e["runs"], "created": e["created"], "run_id": meta.get("run_id") or run.name,
           "kind": "Google Sheet" if meta.get("source_link") else "Upload",
           "filename": None if meta.get("source_link") else meta.get("filename"),
           "source_link": meta.get("source_link") or None,
           "materials": None, "links": {"saved": 0, "found": 0}}
    try:
        fp = run / "forecast.parquet"
        rows = _full_run_rows_cached(str(run), fp.stat().st_mtime_ns)
        out["materials"] = len(rows)
        saved = found = 0
        for _svc, per in _load_links(d).items():
            for _code, entries in per.items():
                for en in _norm_link_entries(entries):
                    saved += 1
                    found += 1 if linkage._norm(en["material"]) in rows else 0
        out["links"] = {"saved": saved, "found": found}
    except Exception:
        pass
    return out


def _stock_view(slug):
    d = PROJECTS / _slugify(slug)
    own, eff = _slugify(slug), _stock_slug(slug)
    src = _stock_sources()
    opts = [_stock_option(d, e) for e in sorted(src.values(), key=lambda x: x["created"], reverse=True)]
    cur = next((o for o in opts if o["stock"] == eff), None)
    return {"project": own, "stock": eff, "chosen": eff != own, "current": cur, "options": opts}


@router.get("/{slug}/stock")
def get_stock(slug: str):
    """Which stock this project reads, and every stock it could read. Read-only."""
    return _stock_view(slug)


@router.post("/{slug}/stock")
def set_stock(slug: str, payload: dict):
    """Body {stock: "<stock slug>"} (or null = back to the stock named like the project). Only which stock the project
    READS changes: Site Progress, the BOQ, saved links and every bill stay exactly as they are. A saved link whose
    material is not in the chosen stock simply shows as not found; nothing is re-pointed to a similar name."""
    d = _need(slug)
    want = payload.get("stock")
    p = d / "stock.json"
    if not want or _slugify(want) == _slugify(slug):
        p.unlink(missing_ok=True)
        return _stock_view(slug)
    want = _slugify(want)
    if want not in _stock_sources():
        raise HTTPException(404, "that stock does not exist (upload a stock register or sync a Google Sheet on the Forecast tab first)")
    p.write_text(json.dumps({"stock": want, "at": dt.datetime.now().isoformat(timespec="seconds")}))
    return _stock_view(slug)


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
    forecast the ordinary rate-only way on the Forecast tab, untouched.

    Also snapshots every linked item's verdict for THIS run into
    shortage_log.json (shortage_history.snapshot_run(), idempotent per run —
    safe to call on every hit) — see shortage_history.py's own module
    docstring for why this can only track forward from here, never
    reconstruct old runs after the fact."""
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
    rate_map = _rates(d, service)

    used_by_code = {str(r.item_code): r for r in used.itertuples()}
    descs = {str(r.item_code): r.description for r in items.itertuples()}
    full_rows = None   # built at most once, only if a link needs it
    shortage_log = _read_json(d / "shortage_log.json", []) or []
    today = dt.date.today().isoformat()
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
        if run is not None:
            on_hand, shortfall = _aggregate_stock_snapshot(res)
            rate = rate_map.get(str(code))
            try:
                rate = float(rate) if rate is not None else None
            except (TypeError, ValueError):
                rate = None
            shortage_log = shortage_history.snapshot_run(
                shortage_log, today, run, service, code, res["verdict"],
                remaining_need=item["remaining"], on_hand=on_hand,
                shortfall=shortfall, rate=rate)
        out.append(res)
    if run is not None:
        (d / "shortage_log.json").write_text(json.dumps(shortage_log, ensure_ascii=False))
    return {"service": service, "has_run": run is not None, "run": run,
            "linked_items": len(links), "shortages": n_short,
            "items": out}


def _aggregate_stock_snapshot(res):
    """(on_hand, shortfall) for shortage_history.snapshot_run() from one
    combine_item() result -- summed across every linked stock material (the
    common case is exactly one). None for either when nothing is actually
    known yet (an UNKNOWN/UNKNOWN_FACTOR/NOT_LINKED item's links carry no
    real on_hand/shortfall figures) -- never a zero standing in for
    "unknown", matching this module's own "never invent" rule elsewhere."""
    on_hand_total, shortfall_total, known = 0.0, 0.0, False
    for link in res.get("links", []):
        if link.get("on_hand") is not None:
            on_hand_total += link["on_hand"]
            known = True
        if link.get("shortfall") is not None:
            shortfall_total += link["shortfall"]
    return (on_hand_total if known else None), (shortfall_total if known else None)


@router.get("/{slug}/shortage-summary")
def shortage_summary(slug: str, service: str = None, year_month: str = None):
    """The ticker's own data: {"flagged","prevented","materialized",
    "no_longer_needed","ongoing","value_protected"} for one month (default
    the latest month tracked). Reads shortage_log.json directly -- nothing
    here recomputes a verdict, only rolls up what realistic() already
    snapshotted."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    return shortage_history.month_summary(log, service=service, year_month=year_month)


@router.get("/{slug}/shortage-summary-lifetime")
def shortage_summary_lifetime(slug: str, service: str = None):
    """The PERSISTENT trust signal -- cumulative across the whole project,
    with a catch_rate percent, so it never disappears just because this
    particular month had nothing new flagged (see
    shortage_history.lifetime_summary's own docstring on why that matters)."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    return shortage_history.lifetime_summary(log, service=service)


@router.get("/{slug}/shortage-episodes")
def shortage_episodes(slug: str, service: str = None, year_month: str = None):
    """The ticker's DRILL-DOWN: every individual episode behind its
    aggregate counts, across every service when `service` isn't given (the
    Overall view's own scope) -- so "1 shortage flagged this month" is a
    real click target, not a dead-end number. Item descriptions are attached
    best-effort per service (from the same BOQ each service's own item list
    already reads) so the popover can show more than a bare item code."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    episodes = shortage_history.month_episodes(log, service=service, year_month=year_month)
    return {"episodes": _attach_descs(d, episodes)}


@router.get("/{slug}/shortage-episodes-all")
def shortage_episodes_all(slug: str, service: str = None):
    """Same drill-down shape as shortage-episodes, but for the LIFETIME
    catch-rate ticker -- every episode ever, not scoped to one month."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    episodes = shortage_history.all_episodes(log, service=service)
    return {"episodes": _attach_descs(d, episodes)}


def _attach_descs(d, episodes):
    """Best-effort item descriptions for a list of shortage episodes, from
    each episode's own service's BOQ. A genuinely blank BOQ description cell
    reads as NaN through pandas, which json.dumps refuses to serialise
    ("Out of range float values are not JSON compliant") -- normalised to
    None here, the honest JSON-safe way to say "no description on file"."""
    descs = {}
    for svc in {e["service"] for e in episodes}:
        try:
            items = _load_boq(d, svc)
            for r in items.itertuples():
                desc = r.description
                descs[str(r.item_code)] = None if pd.isna(desc) else desc
        except Exception:
            pass
    for e in episodes:
        e["desc"] = descs.get(e["item_code"])
    return episodes


@router.get("/{slug}/shortage-timeline/{service}/{item_code}")
def shortage_timeline(slug: str, service: str, item_code: str):
    """The small popover's data for ONE item -- never rendered in the main
    drawer (see shortage_history.item_timeline's own docstring). Empty list
    when the item has no shortage history at all, which is the common case
    and exactly why the UI trigger for this should only appear when this
    list is non-empty, not as a permanent fixture on every item row."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    return {"item_code": item_code, "service": service,
           "timeline": shortage_history.item_timeline(log, service, item_code)}


@router.get("/{slug}/shortage-items/{service}")
def shortage_items(slug: str, service: str):
    """{item_code: {"episodes","ongoing"}} for every item in this service
    with real shortage history -- the ONE bulk call the item list needs to
    decide which rows get a history trigger at all, instead of one request
    per item. Most items are simply absent from the response."""
    d = _need(slug)
    log = _read_json(d / "shortage_log.json", []) or []
    return shortage_history.items_with_history(log, service)


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
