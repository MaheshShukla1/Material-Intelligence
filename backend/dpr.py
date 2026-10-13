"""Daily Progress Report: Layer 1 (auto-capture) + Layer 3 (export).

Layer 1 — record_change() is called from the SAME place a room/item slider
already gets set (progress.ProgressStore.set() / itemprog's per-room write).
It appends one dated fact: {date, service, floor, activity, room}. No new
screen, no typing — the engineer never sees this happen. Same (date, service,
floor, activity, room) logged twice in a day is a no-op (idempotent), so
dragging a slider back and forth doesn't duplicate a row.

This is deliberately NOT a snapshot-and-diff of cumulative % (that would need
re-deriving "what changed" from two full states, and silently misattributes a
correction to "new work"). It logs the actual write, at the moment it happens
-- the same signal the engineer already produces by dragging a slider, just
kept instead of overwritten.

Layer 3 — build_workbook() turns one day's (or a date range's) logged facts
into the exact Excel the site team already hand-builds: one colored section
per service, FLOOR merged down the left when an activity spans a floor,
ACTIVITY as free text, AS PER SCHEDULE / REMARKS left blank with a
Leading/On schedule/Lagging dropdown + auto color -- never invented, because
this system has no schedule baseline to compare against (see pnl.py's own
"never invent a number" rule -- same principle, applied here).

Nothing here touches the forecast engine or pnl.py. It only reads what
progress-setting already writes, and a project's structure tree for floor
ordering.
"""
import json
from datetime import date as _date

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter


# --------------------------------------------------------------------------
# Layer 1 — auto-capture. Pure data, no I/O here (the caller owns the file).
# --------------------------------------------------------------------------
_NO_BASELINE = object()      # "the caller did not say what the quantity was before today's first touch"


def record_change(log, date_str, service, floor, activity, room, item=None, qty=None, unit=None, item_code=None,
                  qty_before=_NO_BASELINE):
    """Append (or UPDATE) one captured fact in `log` (a plain list, e.g.
    loaded from dpr_log.json). `item`/`qty`/`unit` are optional (older log
    entries on disk won't have them -- group_for_export() below tolerates
    that) -- when given, `item` is the BOQ item's own description and `qty`
    is the real per-room quantity this specific action represents (room_qty
    x the fraction just set -- see siteprogress.py's _log_dpr_change() for
    exactly how that's resolved; never a guess, and left None for the
    OVERALL/no-room case where no honest room-scoped quantity exists).
    `item_code` (optional, not part of the uniqueness key -- one description
    always maps to one code) lets a later rollup (rollup_across_floors())
    look the real ₹ rate up by code rather than fuzzy-matching description
    text back to a BOQ row. Older entries simply have it as None.

    Same (date, service, floor, activity, room, item) touched again the same
    day UPDATES that entry's qty in place rather than adding a second row --
    dragging one room's slider from 30% to 70% twice in a day must show 70%
    worth of qty once, not 30%+70% double-counted. Returns the list back (so
    a caller can `log = record_change(log, ...)` or ignore the return).

    `qty` stays what it always was: the CUMULATIVE quantity of that room/floor
    at the moment of the last save that day. `qty_before` (optional) is the
    cumulative quantity BEFORE the day's first save of that line -- written
    once, when the line is first created that day, and never overwritten by
    later saves the same day. `qty - qty_before` is then exactly the work done
    on that day (see qty_today()). An entry without `qty_before` (every entry
    written before this existed, or a caller that did not pass it) has an
    UNKNOWN day quantity -- it is never guessed.
    """
    key = (date_str, service, floor, activity, room, item)
    for e in log:
        if (e["date"], e["service"], e["floor"], e["activity"],
            e.get("room"), e.get("item")) == key:
            e["qty"], e["unit"] = qty, unit
            e["item_code"] = item_code
            return log        # qty_before (if the line has one) is the day's FIRST baseline: kept as it is
    entry = {"date": date_str, "service": service, "floor": floor,
             "activity": activity, "room": room, "item": item, "qty": qty,
             "unit": unit, "item_code": item_code}
    if qty_before is not _NO_BASELINE:
        entry["qty_before"] = qty_before
    log.append(entry)
    return log


def qty_today(entry):
    """The quantity of work done on `entry`'s day: cumulative qty at the last
    save minus the cumulative qty before the first save of that day. None when
    it cannot be known honestly (no baseline recorded -- an entry from before
    this existed -- or either quantity unknown). May be negative: the engineer
    reduced a figure that day (a correction), which is shown as it is."""
    q, b = entry.get("qty"), entry.get("qty_before", None)
    if "qty_before" not in entry or q is None or b is None or q != q or b != b:     # (q != q: a NaN quantity is unknown too)
        return None
    return round(float(q) - float(b), 3)


def entries_for_range(log, start_date, end_date=None):
    """{date_str: [entry, ...]} for every date in [start_date, end_date]
    (inclusive), in the order they were logged. end_date defaults to
    start_date (a single day)."""
    end_date = end_date or start_date
    out = {}
    for e in log:
        if start_date <= e["date"] <= end_date:
            out.setdefault(e["date"], []).append(e)
    return out


def filter_by_area(entries, floors=None, rooms=None):
    """The area half of Issue 2's export scope -- structure-agnostic:
    `floors` is a set of whichever labels this project's structure uses at
    the container level (Floor/Level/Wing names, e.g. {"13th Floor"} or
    {"B3"} -- a set, not one name, since the picker lets an engineer tick
    rooms spanning more than one floor at once). `rooms` is an optional set
    of leaf names (Room/Zone/Corridor, e.g. {"Room 5"}) narrowing further
    within those floors. Works identically for hotel/mall/hospital
    structures since it only ever matches the real floor/room strings
    dpr_log.json already carries -- never invents which floor/room an entry
    belongs to.

    floors=None                          -> no area filter at all (today's
                                            whole-project export, unchanged).
    floors={"13th Floor"}, rooms=None     -> the WHOLE floor: every entry
                                            logged under it, room-specific
                                            or not.
    floors={"13th Floor"}, rooms={"Room 5"} -> Room 5's own room-specific
                                            entries, PLUS that floor's
                                            floor-wide ("overall", no
                                            specific room known) entries --
                                            those weren't captured against
                                            one room, so excluding them
                                            outright would silently hide
                                            real logged work. The caller
                                            (see the area-export mockup) is
                                            responsible for flagging those
                                            in the REMARKS column -- this
                                            function only filters, it does
                                            not annotate.
    A room selection spanning several floors just passes all of those
    floors' names in `floors` -- each floor's own rooms are matched
    independently, exactly as if filtered one floor at a time.
    """
    if not floors:
        return list(entries)
    floor_set = set(floors)
    out = [e for e in entries if e.get("floor") in floor_set]
    if rooms:
        room_set = set(rooms)
        out = [e for e in out if e.get("room") in room_set or e.get("room") is None]
    return out


# --------------------------------------------------------------------------
# Grouping — the exact shape the sheet needs: per service, per floor, in the
# order floors first appear in the log (which is the order the engineer
# actually worked them, a perfectly reasonable default row order).
# --------------------------------------------------------------------------
def group_for_export(day_entries, all_services, leaf_label="ROOM"):
    """day_entries: the list for ONE date, from entries_for_range().
    all_services: every service this project tracks (so a service with zero
    changes still gets its own "NO ACTIVITY" section, matching the site
    team's own sheet, which lists HVAC even when nothing happened there).
    leaf_label: "ROOM" or "ZONE" (see dpr.LEAF_LABEL) -- the word used in
    the generated text ("...IN ROOM NO 5,6" vs "...IN ZONE NO 5,6"), picked
    from the project's structure.kind, never hardcoded.

    Groups by (floor, activity, item) now, not just (floor, activity) --
    two different BOQ items under the same activity (e.g. pipe + saddle
    both under "Wall Piping") must not have their room lists blended into
    one misleading combined list. Older log entries with no `item` (from
    before this field existed) fall back to one row per (floor, activity)
    exactly as before, item/qty columns simply blank.

    Returns {service: [(floor, activity_text, item, qty, unit), ...]}.
    `activity_text` is the activity name plus the rooms touched that day for
    that (floor, activity, item), comma-joined -- built from real logged
    rooms, never guessed. `qty` is the SUM of each captured action's own qty
    for that (floor, activity, item) group -- see record_change()'s own note
    on why repeat touches of the SAME room update in place rather than
    summing (so this sum is real, not double-counted); different rooms
    genuinely do add up.
    """
    by_service = {s: {} for s in all_services}
    order = {s: [] for s in all_services}
    for e in day_entries:
        svc = e["service"]
        if svc not in by_service:
            by_service[svc] = {}
            order[svc] = []
        key = (e["floor"], e["activity"], e.get("item"))
        if key not in by_service[svc]:
            by_service[svc][key] = {"rooms": [], "qty": 0.0, "unit": e.get("unit"), "has_qty": False}
        slot = by_service[svc][key]
        if key not in order[svc]:
            order[svc].append(key)
        if e["room"] and e["room"] not in slot["rooms"]:
            slot["rooms"].append(e["room"])
        if e.get("qty") is not None:
            slot["qty"] += e["qty"]
            slot["has_qty"] = True
            slot["unit"] = e.get("unit") or slot["unit"]

    out = {}
    for svc in all_services:
        rows = []
        for (floor, activity, item) in order.get(svc, []):
            slot = by_service[svc][(floor, activity, item)]
            rooms = slot["rooms"]
            text = (f"{activity.upper()} IN {leaf_label} NO {','.join(rooms)}"
                   if rooms else activity.upper())
            qty = round(slot["qty"], 3) if slot["has_qty"] else None
            rows.append((floor, text, item, qty, slot["unit"]))
        out[svc] = rows
    return out


def rollup_across_floors(entries, all_services, leaf_label="ROOM"):
    """Consolidates ONLY what actually caused the reported "bahut bada" (too
    large) complaint: an item's OVERALL '*' slider (no specific room known --
    see _log_dpr_change's own docstring) fans out to one entry PER applicable
    floor, so touching, say, 6 floors used to write 6 near-identical rows for
    the same activity+item. Those get merged into ONE row, floor names
    comma-joined, qty summed across them (floors are disjoint room sets, so
    summing already-deduplicated per-floor sums never double-counts).

    A SPECIFIC room (a real room/zone name was captured) is NEVER merged
    across floors -- that data was never the redundant part; each floor's
    room list is genuinely different information, and losing it was a real
    regression (caught by a real pre-existing test, not a guess). Those
    entries keep group_for_export()'s own original shape: one row per floor,
    the touched room names appended to the activity text ("...IN ROOM NO
    5,6"), exactly as before. A specific-room fact and an overall-slider fact
    for the same item on the same floor the same day stay as two distinct
    rows -- the room-specific one is never blended into or overwritten by
    the floor-only one.

    Returns {service: [(activity_display, item, item_code, qty, unit,
    floor_label), ...]} -- activity_display already carries any room-list
    suffix for the room-specific case (do not upper()/reformat it again);
    floor_label is the real floor name (single row) or several real floor
    names comma-joined (only ever for the consolidated overall-slider case)
    -- never a synthetic count."""
    by_service = {s: {} for s in all_services}
    order = {s: [] for s in all_services}
    for e in entries:
        svc = e["service"]
        if svc not in by_service:
            by_service[svc] = {}
            order[svc] = []
        has_room = bool(e.get("room"))
        # room-specific: one row per (floor, activity, item), room list kept.
        # overall-slider: one row per (activity, item), floors consolidated.
        key = ("room", e["floor"], e["activity"], e.get("item")) if has_room \
            else ("overall", e["activity"], e.get("item"))
        if key not in by_service[svc]:
            by_service[svc][key] = {"qty": 0.0, "unit": e.get("unit"), "has_qty": False,
                                    "floors": set(), "rooms": [], "item_code": e.get("item_code"),
                                    "activity": e["activity"], "item": e.get("item"),
                                    "floor": e["floor"] if has_room else None}
            order[svc].append(key)
        slot = by_service[svc][key]
        if e.get("qty") is not None:
            slot["qty"] += e["qty"]
            slot["has_qty"] = True
            slot["unit"] = e.get("unit") or slot["unit"]
        if e.get("floor"):
            slot["floors"].add(e["floor"])
        if has_room and e["room"] not in slot["rooms"]:
            slot["rooms"].append(e["room"])
        if e.get("item_code") and not slot["item_code"]:
            slot["item_code"] = e["item_code"]

    out = {}
    for svc in all_services:
        rows = []
        for key in order.get(svc, []):
            slot = by_service[svc][key]
            qty = round(slot["qty"], 3) if slot["has_qty"] else None
            if key[0] == "room":
                floor_label = slot["floor"]
                activity_display = (f"{slot['activity'].upper()} IN {leaf_label} NO {_room_list_label(slot['rooms'])}"
                                    if slot["rooms"] else slot["activity"].upper())
            else:
                floors = sorted(slot["floors"])
                # Real floor names, comma-joined -- NEVER a synthetic "N
                # Floors" count. "6 Floors" sat in the exact column real
                # floor names normally appear in and read as if it WERE one.
                floor_label = ",".join(floors) if floors else "—"
                activity_display = slot["activity"].upper()
            rows.append((activity_display, slot["item"], slot["item_code"], qty, slot["unit"], floor_label))
        out[svc] = rows
    return out


_ROOM_LIST_CAP = 8


def _room_list_label(rooms):
    """The room-list text for one (floor, activity, item) row -- every real
    room name when there are few (the common case: an engineer touched a
    handful of specific rooms, and each one is genuinely useful information
    right there in the cell). Once a bulk update genuinely spans most/all of
    a floor's rooms in one day, listing every single one blows the cell up
    just as badly as the original per-floor duplication did -- a real
    reported case, 20+ room names in one cell -- so past _ROOM_LIST_CAP this
    leads with the real count, then the first few real names, then "+N
    more" rather than either the full list (too large again) or a bare
    count (loses the "which rooms" traceability the room-list exists for in
    the first place)."""
    if len(rooms) <= _ROOM_LIST_CAP:
        return ",".join(rooms)
    shown = rooms[:_ROOM_LIST_CAP]
    more = len(rooms) - _ROOM_LIST_CAP
    return f"{len(rooms)} rooms: {','.join(shown)} +{more} more"


# --------------------------------------------------------------------------
# Layer 3 — the formatter. Pure function: grouped data in, Workbook out.
# --------------------------------------------------------------------------
FONT = "Arial"
# Matches siteprogress.js's own leafLabel(kind) convention (Room vs Zone) --
# same idea, one level up the tree: the label for a room's immediate/joined
# container path, picked from structure.kind, never hardcoded to "FLOOR".
LOCATION_LABEL = {"hotel": "FLOOR", "mall": "LEVEL", "hospital": "WING / FLOOR", "custom": "LOCATION"}
# Matches siteprogress.js's leafLabel(kind): "Zone" only for a mall, "Room"
# for hotel/hospital/custom -- used inside the generated activity text
# ("...IN ROOM NO 5,6" / "...IN ZONE NO 5,6"), not the column header.
LEAF_LABEL = {"hotel": "ROOM", "mall": "ZONE", "hospital": "ROOM", "custom": "ROOM"}
_thin = Side(style="thin", color="000000")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
SECTION_FILL = {  # cycle through these for services beyond the first 4, so a
                  # 5th service never crashes -- just repeats the palette.
    0: "BDD7EE", 1: "FBE0CE", 2: "FFE699", 3: "C6E0B4", 4: "D9D2E9",
}


def _write_day_block(ws, row, date_str, grouped, location_label="FLOOR"):
    ws.cell(row, 1, "DATE:").font = Font(name=FONT, bold=True, size=11, color=HEADER_FILL)
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
    dc = ws.cell(row, 2, date_str)
    dc.font = Font(name=FONT, bold=True, size=11, color=HEADER_FILL)
    for c in range(1, 6):
        ws.cell(row, c).fill = PatternFill("solid", fgColor=HERO_FILL)
        ws.cell(row, c).border = GRID_BORDER
    ws.row_dimensions[row].height = 20
    row += 1

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    t = ws.cell(row, 1, "DAILY WORK UPDATES")
    t.font = Font(name=FONT, bold=True, size=10.5, color="5F5E5A")
    t.alignment = Alignment(horizontal="center")
    for c in range(1, 6):
        ws.cell(row, c).fill = PatternFill("solid", fgColor=HERO_FILL)
        ws.cell(row, c).border = GRID_BORDER
    row += 1

    for i, (service, items) in enumerate(grouped.items()):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        s = ws.cell(row, 1, service.upper())
        s.font = Font(name=FONT, bold=True, size=10.5, color=HEADER_FONT_COLOR)
        s.alignment = Alignment(horizontal="left", indent=1)
        for c in range(1, 6):
            ws.cell(row, c).fill = PatternFill("solid", fgColor=HEADER_FILL)
            ws.cell(row, c).border = GRID_BORDER
        ws.row_dimensions[row].height = 18
        ws.row_dimensions[row].outline_level = 1
        row += 1

        headers = [location_label, "ACTIVITY", "ITEM", "QTY EXECUTED", "REMARKS"]
        _header_row(ws, row, headers)
        ws.row_dimensions[row].outline_level = 1
        row += 1

        if not items:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
            na = ws.cell(row, 1, "NO ACTIVITY")
            na.font = Font(name=FONT, size=10, italic=True, color="9A9890")
            na.alignment = Alignment(horizontal="center")
            for c in range(1, 6):
                ws.cell(row, c).border = GRID_BORDER
            ws.row_dimensions[row].outline_level = 1
            row += 1
            continue

        for row_tuple in items:
            # Accepts BOTH real shapes this file has ever produced: the
            # original group_for_export() 5-tuple (floor, activity_text,
            # item, qty, unit -- activity_text already has "IN ROOM NO..."
            # baked in, one row per floor) still used directly by other real
            # callers/tests, and the newer rollup_across_floors() 6-tuple
            # (activity, item, item_code, qty, unit, floor_label -- one row
            # per activity+item, floor names comma-joined). Detected by
            # length, not by caller, so EITHER pairing keeps working without
            # the caller having to know which shape it's feeding in.
            if len(row_tuple) == 5:
                floor_label, activity, item, qty, unit = row_tuple
                activity_display = activity   # group_for_export() already built this
                # as "ACTIVITY IN ROOM NO Room 2,Room 3" with the activity
                # portion pre-uppercased and the room list left in its real
                # case -- upper()-ing the whole string again would shout the
                # room names too ("Zone 2" -> "ZONE 2"), which was never
                # part of that function's own contract.
            else:
                activity, item, item_code, qty, unit, floor_label = row_tuple
                activity_display = activity   # rollup_across_floors() already
                # pre-formats this too (upper() + any "IN ROOM NO ..." suffix
                # for its own room-specific rows) -- same reasoning as above,
                # do not process it again here.
            fc = ws.cell(row, 1, floor_label)
            fc.font = Font(name=FONT, size=10)
            # Left-aligned + wrapped, not centered -- a single floor name
            # still reads fine either way, but a comma-joined multi-floor
            # list (the whole point of this fix) needs to wrap onto its own
            # lines rather than overflow as one long centered line.
            fc.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ac = ws.cell(row, 2, activity_display)
            ac.font = Font(name=FONT, size=10)
            ic = ws.cell(row, 3, item or "—")
            ic.font = Font(name=FONT, size=10, italic=(not item), color="9A9890" if not item else "000000")
            if qty is not None:
                qc = ws.cell(row, 4, f"{qty:,.2f} {unit}" if unit else round(qty, 2))
                qc.alignment = Alignment(horizontal="right")
                qc.font = Font(name=FONT, size=10)
            else:
                qc = ws.cell(row, 4, "—")
                qc.font = Font(name=FONT, size=10, italic=True, color="9A9890")
            for c in range(1, 6):
                ws.cell(row, c).border = GRID_BORDER
            _zebra(ws, row, 5)
            ws.row_dimensions[row].outline_level = 1
            row += 1
    # collapsible: click the sheet's own [-] outline button (top-left of the
    # row gutter) to fold this whole day's rows away, same real Excel
    # grouping the Activity completion sheet already uses elsewhere in this
    # module -- a multi-week export stops being one giant scroll.
    ws.sheet_properties.outlinePr.summaryBelow = False
    return row


def build_workbook(days, location_label="FLOOR"):
    """days: [(date_str, rolled_up), ...] -- rollup_across_floors()'s output
    for that date, in the order dates should stack. One sheet, one block per
    day, the SAME five columns the site team's own template already uses
    (FLOOR/LEVEL/WING · FLOOR/LOCATION -- whichever this project's own
    structure kind calls it, via location_label; see dpr.LOCATION_LABEL and
    siteprogress.py's _location_label()) -- unchanged headers, on purpose:
    the fix the site engineer/PM actually asked for is fewer rows, not a
    new format to learn. ONE row per (activity, item): FLOOR shows the
    real floor name when only one was touched that day (identical to the
    original per-floor behaviour for the common case), or every real floor
    name comma-joined when the same activity+item spans several -- which is
    exactly the case that used to produce that many near-duplicate rows.
    Never a bare count: a real floor list is still traceable; "N Floors"
    isn't, and reads as a floor name that doesn't exist."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DPR"
    ws.sheet_view.showGridLines = False
    widths = {1: 26, 2: 30, 3: 34, 4: 16, 5: 30}
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    row = 1
    for date_str, grouped in days:
        row = _write_day_block(ws, row, date_str, grouped, location_label=location_label)
        row += 1   # blank row between day blocks
    return wb


# --------------------------------------------------------------------------
# Summary + item-detail sheets -- an MD opens the file and the FIRST tab
# already answers "where do we stand", using the exact same numbers the
# Overall dashboard already shows (pnl.rollup_pnl / project_pnl / itemprog's
# room_buckets). Nothing new is computed here -- this only formats numbers
# those already-tested functions produce. The site team's own existing
# export template (Services/Item/Planned/Used so far/Remaining/Done/In
# progress/Pending) becomes the "Item detail" tab, finally populated with
# real numbers instead of shipping as a blank header.
#
# Room-count totals in Summary are real SUMIF formulas over the Item detail
# sheet (both sheets share that column), not re-passed numbers -- open the
# cell and Excel shows exactly where it came from. ₹ figures stay computed
# values: Item detail's own template (the site team's blank original) never
# had a rate/value column, so there is nothing on that sheet for a ₹ formula
# to reference -- inventing one just to force a formula would be a bigger
# change to their template than asked. A note on the sheet says so plainly,
# per the "document every hardcoded number" rule.
# --------------------------------------------------------------------------
HEADER_FILL = "2C3E50"
HEADER_FONT_COLOR = "FFFFFF"
ZEBRA_FILL = "F2F2F2"
HERO_FILL = "EEF1F4"
_thin_gray = Side(style="thin", color="B0B0B0")
GRID_BORDER = Border(left=_thin_gray, right=_thin_gray, top=_thin_gray, bottom=_thin_gray)


def _header_row(ws, row, headers, col_start=1):
    for i, h in enumerate(headers):
        c = ws.cell(row, col_start + i, h)
        c.font = Font(name=FONT, bold=True, size=10, color=HEADER_FONT_COLOR)
        c.fill = PatternFill("solid", fgColor=HEADER_FILL)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = GRID_BORDER


def _zebra(ws, row, n_cols, col_start=1):
    if row % 2 == 0:
        for c in range(col_start, col_start + n_cols):
            cell = ws.cell(row, c)
            if not cell.fill or cell.fill.fgColor.rgb in (None, "00000000"):
                cell.fill = PatternFill("solid", fgColor=ZEBRA_FILL)


def build_summary_sheet(wb, date_label, project_totals, by_service, generated_at=None):
    """project_totals: pnl.project_pnl()'s dict (done_value, remaining_value,
    full_value, waste_value, pct_value_done, ...).
    by_service: {service: {done_value, remaining_value}} -- ₹ only; room
    counts are pulled from the Item detail sheet via formula, not passed in
    here, so the two sheets can never quietly disagree.
    generated_at: 'YYYY-MM-DD HH:MM' string -- when this file was actually
    produced, so a downloaded report is never mistaken for a live view."""
    ws = wb.create_sheet("Summary", 0)   # first tab -- the thing an MD sees on open
    ws.sheet_view.showGridLines = False
    for c, w in {1: 16, 2: 16, 3: 16, 4: 16, 5: 12, 6: 12, 7: 12}.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    ws.merge_cells("A1:G1")
    h = ws.cell(1, 1, f"Site progress summary — {date_label}")
    h.font = Font(name=FONT, bold=True, size=14, color=HEADER_FILL)
    ws.row_dimensions[1].height = 22
    if generated_at:
        ws.merge_cells("A2:G2")
        g = ws.cell(2, 1, f"Generated {generated_at}")
        g.font = Font(name=FONT, size=9, italic=True, color="9A9890")

    hero = [("Value complete", project_totals.get("pct_value_done", 0) / 100, "0.0%"),
           ("Done", project_totals.get("done_value"), None),
           ("Remaining", project_totals.get("remaining_value"), None),
           ("Material waste", project_totals.get("waste_value"), None)]
    for i, (label, val, fmt) in enumerate(hero):
        c0 = 1 + i * 2
        for r in (3, 4):
            ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c0 + 1)
            cell = ws.cell(r, c0)
            cell.fill = PatternFill("solid", fgColor=HERO_FILL)
            ws.cell(r, c0 + 1).fill = PatternFill("solid", fgColor=HERO_FILL)
            if i < len(hero) - 1:   # right divider so each card reads as its own tile
                ws.cell(r, c0 + 1).border = Border(right=Side(style="thin", color="C9CDD3"))
        lc = ws.cell(3, c0, label)
        lc.font = Font(name=FONT, size=10, color="5F5E5A")
        lc.alignment = Alignment(horizontal="left", indent=1)
        vc = ws.cell(4, c0, val if fmt else round(val or 0))
        vc.font = Font(name=FONT, bold=True, size=16, color=HEADER_FILL)
        vc.number_format = fmt or '"₹"#,##0'
        vc.alignment = Alignment(horizontal="left", indent=1)
    ws.row_dimensions[3].height = 16
    ws.row_dimensions[4].height = 24

    row = 6
    headers = ["Service", "% complete", "Done (₹)", "Remaining (₹)", "Rooms done", "In progress", "Pending"]
    _header_row(ws, row, headers)
    ws.row_dimensions[row].height = 18
    row += 1
    first_data_row = row
    for svc, t in by_service.items():
        done_v = t.get("done_value") or 0
        rem_v = t.get("remaining_value") or 0
        planned_v = done_v + rem_v
        ws.cell(row, 1, svc).border = GRID_BORDER
        pc = ws.cell(row, 2, f"=IFERROR(C{row}/(C{row}+D{row}),0)")
        pc.number_format = "0.0%"
        pc.border = GRID_BORDER
        dv = ws.cell(row, 3, round(done_v))
        dv.number_format = '"₹"#,##0'
        dv.border = GRID_BORDER
        rv = ws.cell(row, 4, round(rem_v))
        rv.number_format = '"₹"#,##0'
        rv.border = GRID_BORDER
        # real formulas -- summed straight off the Item detail sheet's own
        # Done/In progress/Pending columns for this service, so Summary can
        # never quietly drift from the sheet it is summarising.
        for c, col_letter in ((5, "F"), (6, "G"), (7, "H")):
            f = ws.cell(row, c, f"=SUMIF('Item detail'!$A:$A,A{row},'Item detail'!${col_letter}:${col_letter})")
            f.number_format = "#,##0"
            f.border = GRID_BORDER
        for c in range(1, 8):
            ws.cell(row, c).font = Font(name=FONT, size=10)
        _zebra(ws, row, 7)
        row += 1
    ws.conditional_formatting.add(f"B{first_data_row}:B{row - 1}",
        CellIsRule(operator="lessThan", formula=["0.3"], font=Font(name=FONT, size=10, color="A32D2D")))
    ws.conditional_formatting.add(f"B{first_data_row}:B{row - 1}",
        CellIsRule(operator="greaterThanOrEqual", formula=["0.7"], font=Font(name=FONT, size=10, color="3B6D11")))
    ws.freeze_panes = f"A{first_data_row}"

    note = ws.cell(row + 1, 1,
        "₹ figures: computed at export time from each item's BOQ rate x installation % (see the Payment terms "
        "setting). Room counts: live SUMIF formulas off the Item detail sheet, columns F–H.")
    note.font = Font(name=FONT, size=9, italic=True, color="888780")
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 2, end_column=7)
    ws.row_dimensions[row + 1].height = 28
    return ws
def _inr(v):
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 1e7:
        return f"₹{v/1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v/1e5:.2f} L"
    return f"₹{v:,.0f}"


def build_item_detail_sheet(wb, item_rows):
    """item_rows: [{service, item, planned, used, remaining, done,
    in_progress, pending}, ...] -- same 8 columns as the site team's own
    Report_Format.xlsx, done/in_progress/pending are real room counts from
    itemprog.room_buckets (the caller's job to supply -- this only formats),
    never left blank once real progress data exists for that item."""
    ws = wb.create_sheet("Item detail")
    ws.sheet_view.showGridLines = False
    headers = ["Services", "Item", "Planned", "Used so far", "Remaining", "Done", "In progress", "Pending"]
    widths = [14, 36, 10, 12, 10, 8, 10, 8]
    _header_row(ws, 1, headers)
    ws.row_dimensions[1].height = 18
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    numeric_cols = {3, 4, 5, 6, 7, 8}
    for r, item in enumerate(item_rows, start=2):
        vals = [item.get(k) for k in ("service", "item", "planned", "used", "remaining",
                                      "done", "in_progress", "pending")]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(r, c, v)
            cell.font = Font(name=FONT, size=10)
            cell.border = GRID_BORDER
            if c in numeric_cols:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")
        _zebra(ws, r, 8)
    last_row = len(item_rows) + 1
    if last_row >= 2:
        ws.auto_filter.ref = f"A1:H{last_row}"
    ws.freeze_panes = "A2"
    return ws


# --------------------------------------------------------------------------
# Activity completion -- mirrors the site team's own real "Activity
# Completion Summary — Auto from Room Detail" report exactly: per service, a
# dark section header, then one row per activity (Total Rooms / Done /
# Partial / Pending / %Done / %Pending), a bold TOTAL row, and %Done shaded
# green/amber/red by threshold -- same color logic real construction status
# reports use. Floor-level rows sit UNDER each activity, collapsed by
# default via Excel's own outline grouping (click the [+] to expand) so the
# sheet reads clean at a glance but the "13th floor: 106 of 108 done" detail
# is one click away, never deleted.
#
# Source: progress.activity_completion() off the real Room-Detail tick
# grid (progress.parquet) -- a DIFFERENT signal from the item-level BOQ
# sliders that drive the Summary/Item detail/Daily updates sheets. Both are
# real, both are already-tracked data; this sheet is the one that reads off
# the tick grid specifically, because that is what a room-completion
# percentage actually means on this site.
# --------------------------------------------------------------------------
_PCT_GREEN, _PCT_AMBER, _PCT_RED = "C6E0B4", "FFE699", "F4C7C3"


def _pct_fill(pct):
    if pct >= 90:
        return _PCT_GREEN
    if pct >= 70:
        return _PCT_AMBER
    return _PCT_RED


def build_activity_completion_sheet(wb, completion, completion_by_floor=None):
    """completion: progress.activity_completion()'s output (by_floor=False).
    completion_by_floor: the same call with by_floor=True, optional -- when
    given, each activity gets its per-floor rows nested (collapsed) beneath
    it. Both come from the SAME progress.parquet read, so they can never
    disagree with each other."""
    ws = wb.create_sheet("Activity completion")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.outlinePr.summaryBelow = False   # floor rows collapse UNDER their activity row
    widths = {1: 34, 2: 12, 3: 10, 4: 10, 5: 10, 6: 10, 7: 12}
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    row = 1
    for i, (service, activities) in enumerate(completion.items()):
        fill = PatternFill("solid", fgColor=SECTION_FILL[i % len(SECTION_FILL)])
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        s = ws.cell(row, 1, f"{service.upper()} — ACTIVITY COMPLETION (ALL FLOORS COMBINED)")
        s.font = Font(name=FONT, bold=True, size=11)
        s.alignment = Alignment(horizontal="center")
        for c in range(1, 8):
            ws.cell(row, c).fill = fill
            ws.cell(row, c).border = GRID_BORDER
        row += 1

        headers = ["Activity", "Total rooms", "✓ Done", "~ Partial", "✗ Pending", "% Done", "% Pending"]
        _header_row(ws, row, headers)
        row += 1

        tot = {"total": 0, "done": 0, "partial": 0, "pending": 0}
        by_floor_this_svc = (completion_by_floor or {}).get(service, {})
        for act, m in activities.items():
            vals = [act, m["total"], m["done"], m["partial"], m["pending"],
                   m["pct_done"] / 100, m["pct_pending"] / 100]
            for c, v in enumerate(vals, start=1):
                cell = ws.cell(row, c, v)
                cell.font = Font(name=FONT, size=10)
                cell.border = GRID_BORDER
                if c in (2, 3, 4, 5):
                    cell.alignment = Alignment(horizontal="right")
                    cell.number_format = "#,##0"
                if c in (6, 7):
                    cell.number_format = "0.00%"
            ws.cell(row, 6).fill = PatternFill("solid", fgColor=_pct_fill(m["pct_done"]))
            for k in tot:
                tot[k] += m[k]
            activity_row = row
            row += 1

            floors = by_floor_this_svc.get(act)
            if floors:
                for floor, fm in floors.items():
                    frow = [f"    {floor}", fm["total"], fm["done"], fm["partial"], fm["pending"],
                           fm["pct_done"] / 100, fm["pct_pending"] / 100]
                    for c, v in enumerate(frow, start=1):
                        cell = ws.cell(row, c, v)
                        cell.font = Font(name=FONT, size=9, color="6B6A66")
                        cell.border = GRID_BORDER
                        if c in (2, 3, 4, 5):
                            cell.alignment = Alignment(horizontal="right")
                        if c in (6, 7):
                            cell.number_format = "0.00%"
                    ws.row_dimensions[row].outlineLevel = 1
                    ws.row_dimensions[row].hidden = True
                    row += 1

        total_pct_done = round(100.0 * tot["done"] / tot["total"], 2) if tot["total"] else 0.0
        total_pct_pending = round(100.0 * tot["pending"] / tot["total"], 2) if tot["total"] else 0.0
        trow = [f"{service.upper()} TOTAL", tot["total"], tot["done"], tot["partial"], tot["pending"],
               total_pct_done / 100, total_pct_pending / 100]
        for c, v in enumerate(trow, start=1):
            cell = ws.cell(row, c, v)
            cell.font = Font(name=FONT, bold=True, size=10, color=HEADER_FILL)
            cell.border = GRID_BORDER
            if c in (2, 3, 4, 5):
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = "#,##0"
            if c in (6, 7):
                cell.number_format = "0.00%"
        ws.cell(row, 6).fill = PatternFill("solid", fgColor=_pct_fill(total_pct_done))
        row += 2   # blank separator row before the next service
    ws.freeze_panes = "A1"
    return ws


def build_full_export(date_label, project_totals, by_service, item_rows, days, location_label="FLOOR",
                      completion=None, completion_by_floor=None, generated_at=None):
    """The complete DPR: Summary (first, MD-facing) + Activity completion
    (real Room-Detail tick data, when supplied) + Item detail (the site
    team's own existing template, mapped items only) + the day-by-day
    narrative log -- now the CONCISE, rolled-up-across-floors version (see
    build_workbook's own docstring), same column headers as before.

    days: [(date_str, rollup_across_floors_output), ...] -- already rolled
    up by the caller (export_dpr() in siteprogress.py), same real entries
    the old floor-by-floor version used, just aggregated one level higher."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    build_summary_sheet(wb, date_label, project_totals, by_service, generated_at=generated_at)
    if completion:
        build_activity_completion_sheet(wb, completion, completion_by_floor=completion_by_floor)
    build_item_detail_sheet(wb, item_rows)
    narrative = build_workbook(days, location_label=location_label)
    src = narrative.active
    dst = wb.create_sheet("Daily updates")
    import copy
    for row in src.iter_rows():
        for cell in row:
            nc = dst.cell(cell.row, cell.column, cell.value)
            if cell.has_style:
                nc.font = copy.copy(cell.font)
                nc.border = copy.copy(cell.border)
                nc.fill = copy.copy(cell.fill)
                nc.alignment = copy.copy(cell.alignment)
    for col, dim in src.column_dimensions.items():
        dst.column_dimensions[col].width = dim.width
    for mc in src.merged_cells.ranges:
        dst.merge_cells(str(mc))
    dst.sheet_view.showGridLines = False
    return wb


# ==========================================================================
# The CLIENT-facing DPR (industry layout): sheet "DPR" -- progress status, manpower, work done today against the
# client's BOQ, hold-ups, plan -- optionally followed by "Summary" and "BOQ progress" (without or with ₹).
# Pure function: plain data in (assembled by siteprogress.py), Workbook out. Every number is written as a plain value (not a formula) so
# it shows in any viewer; a figure that is not known is written as "—", never as a guess.
# ==========================================================================
import math as _math
from openpyxl.worksheet.pagebreak import Break
from openpyxl.worksheet.properties import PageSetupProperties

_SLATE, _SLATE2, _BAND, _TODAY, _TODAYH, _LINE = "2C3E50", "4B6075", "EEF1F4", "E4F0DC", "C9E0BC", "D0D5DB"
_thin_c = Side(style="thin", color=_LINE)
_BOX = Border(left=_thin_c, right=_thin_c, top=_thin_c, bottom=_thin_c)
_CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
_RIGHT = Alignment(horizontal="right", vertical="center")
DASH = "—"


def _F(**k):
    return Font(name="Calibri", size=k.pop("size", 10), **k)


def _fill(c):
    return PatternFill("solid", fgColor=c)


def _ind(n=1):
    return Alignment(horizontal="left", vertical="center", wrap_text=True, indent=n)


def _short(s, n=96):
    t = " ".join(str(s).split())
    return t if len(t) <= n else t[:n].rstrip() + "…"


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _qty_fmt(unit, v):
    """Whole numbers for counted things (Nos) unless the value really has a fraction; two decimals for lengths and the like."""
    if str(unit).strip().lower() in ("nos", "no", "no.", "pcs", "set", "sets", "each") and _isnum(v) and abs(v - round(v)) < 1e-9:
        return "#,##0"
    return "#,##0.00"


def _w(ws, ref, val=None, font=None, fil=None, al=None, fmt=None, border=True):
    """Write one cell. A value of None is written as "—" (grey): the figure is not known."""
    c = ws[ref]
    unknown = val is None
    c.value = DASH if unknown else val
    c.font = _F(color="888888") if (unknown and font is None) else (font or _F())
    if unknown and font is not None:
        c.font = Font(name=font.name, size=font.size, bold=font.bold, italic=font.italic, color="888888")
    c.alignment = (_RIGHT if unknown and al is None else al) or Alignment(vertical="center")
    if fil:
        c.fill = _fill(fil)
    if fmt and not unknown:
        c.number_format = fmt
    if border:
        c.border = _BOX
    return c


def _m(ws, rng, val=None, font=None, fil=None, al=None, border=True):
    ws.merge_cells(rng)
    _w(ws, rng.split(":")[0], val, font, fil, al, border=border)
    if border:
        for row in ws[rng]:
            for c in row:
                c.border = _BOX


def _print_setup(ws, footer):
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.6
    ws.oddFooter.center.text = footer
    ws.oddFooter.center.size = 8


def location_text(entries, leaf_plural="zones"):
    """Where the work of one BOQ line was done, from its log entries: "B3 · Zone 1, 2, 3" for specific zones/rooms, plain floor names for
    work recorded for the whole floor (overall slider) with "(all zones)" after them."""
    rooms, whole = {}, []
    for e in entries:
        fl = e.get("floor")
        if not fl:
            continue
        if e.get("room"):
            rooms.setdefault(fl, [])
            if e["room"] not in rooms[fl]:
                rooms[fl].append(e["room"])
        elif fl not in whole:
            whole.append(fl)
    parts = [f"{fl} · {_room_list_label(rs)}" for fl, rs in rooms.items() if fl not in whole]
    if whole:
        parts.append(", ".join(sorted(whole)) + f" (all {leaf_plural})")
    return "; ".join(parts) if parts else None


def _pct(v):
    return None if v is None else v


def sheet_dpr(wb, data):
    """data keys used: project, date_label, weekday, dpr_no, period (bool), client, contractor, prepared_by, scope, services, progress,
    overall, manpower, lines, holdups, plan."""
    ws = wb.active
    ws.title = "DPR"
    ws.sheet_view.showGridLines = False
    period = data.get("period")
    for k, v in {"A": 5, "B": 13, "C": 52, "D": 8, "E": 13, "F": 13, "G": 12, "H": 13, "I": 12, "J": 11, "K": 24, "L": 28}.items():
        ws.column_dimensions[k].width = v
    _m(ws, "A1:L1", "DAILY PROGRESS REPORT", _F(size=16, bold=True, color="FFFFFF"), _SLATE,
       Alignment(horizontal="center", vertical="center"))
    ws.row_dimensions[1].height = 30
    _w(ws, "B2", "Project", _F(bold=True), _BAND)
    _w(ws, "C2", data["project"], _F(bold=True, size=11))
    _m(ws, "E2:F2", "Period" if period else "Date", _F(bold=True), _BAND, _CEN)
    _m(ws, "G2:H2", data["date_label"] + (f" ({data['weekday']})" if data.get("weekday") else ""), _F(bold=True), None, _CEN)
    _w(ws, "J2", "DPR No.", _F(bold=True), _BAND, _CEN)
    _w(ws, "K2", data.get("dpr_no"), _F(bold=True), None, Alignment(horizontal="left", vertical="center"))
    _w(ws, "B3", "Client", _F(bold=True), _BAND)
    _w(ws, "C3", data.get("client") or "", _F(), None, _LEFT)
    _m(ws, "E3:F3", "Contractor", _F(bold=True), _BAND, _CEN)
    _m(ws, "G3:H3", data.get("contractor") or "", _F(), None, _CEN)
    _w(ws, "J3", "Prepared by", _F(bold=True), _BAND, _CEN)
    _w(ws, "K3", data.get("prepared_by") or "", _F(), None, Alignment(horizontal="left", vertical="center", wrap_text=True))
    if data.get("scope"):
        _m(ws, "B4:L4", "Scope: " + data["scope"], _F(size=9, italic=True, color="555555"), None, _ind(1), border=False)

    def section(rr, t):
        _m(ws, f"A{rr}:L{rr}", t, _F(size=11, bold=True, color="FFFFFF"), _SLATE, Alignment(vertical="center", indent=1))
        ws.row_dimensions[rr].height = 20

    def head(rr, spec, h=32):
        for col, txt in spec.items():
            if ":" in col:
                a, b = col.split(":")
                _m(ws, f"{a}{rr}:{b}{rr}", txt, _F(bold=True), _BAND, _CEN)
            else:
                _w(ws, f"{col}{rr}", txt, _F(bold=True), _BAND, _CEN)
        ws.row_dimensions[rr].height = h

    svcs = data["services"]
    r = 5
    section(r, "1.  PROGRESS STATUS  (% of contract work complete)")
    r = 6
    head(r, {"B:D": "Service", "E": "Up to start of period" if period else "Up to previous day",
             "F": "This period" if period else "Today", "G": "To date"})
    ws.cell(row=r, column=6).fill = _fill(_TODAYH)
    for i, svc in enumerate(svcs):
        rr = r + 1 + i
        p = data["progress"].get(svc) or {}
        _m(ws, f"B{rr}:D{rr}", svc, _F(bold=True), None, _ind(1))
        _w(ws, f"E{rr}", p.get("prev"), _F(), None, _RIGHT, "0.0%")
        _w(ws, f"F{rr}", p.get("today"), _F(), None, _RIGHT, "0.0%")
        _w(ws, f"G{rr}", p.get("todate"), _F(bold=True), None, _RIGHT, "0.0%")
    rr = r + 1 + len(svcs)
    o = data.get("overall") or {}
    _m(ws, f"B{rr}:D{rr}", "Overall project", _F(bold=True), _BAND, _ind(1))
    _w(ws, f"E{rr}", o.get("prev"), _F(bold=True), _BAND, _RIGHT, "0.0%")
    _w(ws, f"F{rr}", o.get("today"), _F(bold=True), _BAND, _RIGHT, "0.0%")
    _w(ws, f"G{rr}", o.get("todate"), _F(bold=True), _BAND, _RIGHT, "0.0%")

    r = rr + 2
    section(r, "2.  MANPOWER ON SITE " + ("IN THIS PERIOD" if period else "TODAY") + "  (nos)")
    r += 1
    head(r, {"B:D": "Service", "E": "Supervisor", "F": "Skilled", "G": "Helper / unskilled",
             "H": "Total in period" if period else "Total today",
             "I": "Man-days up to start of period" if period else "Man-days up to previous day", "J": "Man-days to date"}, 44)
    mp = data["manpower"]
    sums = {k: [] for k in "EFGHIJ"}
    for i, svc in enumerate(svcs):
        rr = r + 1 + i
        row = mp.get(svc) or {}
        _m(ws, f"B{rr}:D{rr}", svc, _F(bold=True), None, _ind(1))
        for col, k in zip("EFG", ("supervisor", "skilled", "helper")):
            _w(ws, f"{col}{rr}", row.get(k), _F(), None, _RIGHT, "0")
            if _isnum(row.get(k)):
                sums[col].append(row[k])
        tot = row.get("total")
        _w(ws, f"H{rr}", tot, _F(bold=True), None, _RIGHT, "0")
        _w(ws, f"I{rr}", row.get("prev_days"), _F(), None, _RIGHT, "#,##0")
        todate = row.get("to_date")
        _w(ws, f"J{rr}", todate, _F(bold=True), None, _RIGHT, "#,##0")
        for col, v in (("H", tot), ("I", row.get("prev_days")), ("J", todate)):
            if _isnum(v):
                sums[col].append(v)
    rr = r + 1 + len(svcs)
    _m(ws, f"B{rr}:D{rr}", "Total", _F(bold=True), _BAND, _ind(1))
    for col in "EFGHIJ":
        _w(ws, f"{col}{rr}", (sum(sums[col]) if sums[col] else None), _F(bold=True), _BAND, _RIGHT, "#,##0")
    ws.row_breaks.append(Break(id=rr + 1))

    r = rr + 2
    section(r, "3.  WORK DONE " + ("IN THIS PERIOD" if period else "TODAY") +
            ", AGAINST BOQ  (only BOQ lines worked on" + (" in this period" if period else " today") +
            "; quantity = " + ("THIS period's" if period else "THIS day's") + " work)")
    r += 1
    head(r, {"A": "Sr.", "B": "BOQ Ref (client)", "C": "Activity / BOQ item", "D": "Unit", "E": "BOQ qty",
             "F": "Up to start of period" if period else "Up to previous day", "G": "IN PERIOD" if period else "TODAY",
             "H": "Cumulative", "I": "Balance", "J": "% complete", "K": "Location worked" if period else "Location worked today",
             "L": "Remarks"})
    ws.cell(row=r, column=7).fill = _fill(_TODAYH)
    r += 1
    sr = 0
    lines = data["lines"]
    if not lines:
        _m(ws, f"A{r}:L{r}", "No BOQ line was updated in this " + ("period." if period else "day."), _F(italic=True, color="555555"), None, _ind(1))
        r += 1
    last_svc = last_act = last_parent = None
    for ln in lines:
        if ln["service"] != last_svc:
            _m(ws, f"A{r}:L{r}", str(ln["service"]).upper(), _F(bold=True, color="FFFFFF"), _SLATE2, Alignment(vertical="center", indent=1))
            ws.row_dimensions[r].height = 18
            r += 1
            last_svc, last_act, last_parent = ln["service"], None, None
        if ln["activity"] != last_act:
            _m(ws, f"A{r}:L{r}", str(ln["activity"]).upper(), _F(bold=True, color=_SLATE), _BAND, Alignment(vertical="center", indent=1))
            ws.row_dimensions[r].height = 17
            r += 1
            last_act, last_parent = ln["activity"], None
        if ln.get("parent") and ln["parent"] != last_parent:
            _m(ws, f"A{r}:L{r}", _short(ln["parent"], 150), _F(italic=True, color="555555", size=9), None, _ind(2))
            ws.row_dimensions[r].height = 16
            r += 1
        last_parent = ln.get("parent")
        sr += 1
        unit = ln.get("unit") or DASH
        _w(ws, f"A{r}", sr, _F(), None, _CEN)
        _w(ws, f"B{r}", ln.get("ref") or DASH, _F(), None, _CEN)
        _w(ws, f"C{r}", _short(ln["desc"]), _F(), None, _LEFT)
        _w(ws, f"D{r}", unit, _F(), None, _CEN)
        for col, k, bold, fil in (("E", "boq", False, None), ("F", "prev", False, None), ("G", "today", True, _TODAY),
                                  ("H", "cum", False, None), ("I", "balance", False, None)):
            v = ln.get(k)
            _w(ws, f"{col}{r}", v, _F(bold=bold), fil, _RIGHT, _qty_fmt(unit, v))
        _w(ws, f"J{r}", ln.get("pct"), _F(), None, _RIGHT, "0.0%")
        _w(ws, f"K{r}", ln.get("location"), _F(size=9), None, _ind(1))
        _w(ws, f"L{r}", ln.get("remarks"), _F(size=9), None, _ind(1))
        ws.row_dimensions[r].height = max(16, 13 * _math.ceil(len(_short(ln["desc"])) / 58) + 3,
                                          13 * _math.ceil(len(ln.get("location") or "") / 26) + 3)
        r += 1

    r += 1
    section(r, "4.  HOLD-UPS / ISSUES")
    for t in _text_rows(data.get("holdups")):
        r += 1
        _m(ws, f"A{r}:L{r}", t, _F(), None, _ind(1))
        ws.row_dimensions[r].height = 18
    r += 2
    section(r, "5.  PLAN FOR TOMORROW")
    for t in _text_rows(data.get("plan")):
        r += 1
        _m(ws, f"A{r}:L{r}", t, _F(), None, _ind(1))
        ws.row_dimensions[r].height = 18
    r += 2
    _m(ws, f"A{r}:F{r}", "Prepared by (Contractor):", _F(bold=True), None, Alignment(vertical="top", indent=1))
    _m(ws, f"G{r}:L{r}", "Reviewed by (Client / PMC):", _F(bold=True), None, Alignment(vertical="top", indent=1))
    ws.row_dimensions[r].height = 46
    _print_setup(ws, f"{data['project']} - Daily Progress Report - {data['date_label']} - Page &P of &N")
    return ws


def _text_rows(t):
    """The typed free text as table rows: one row per typed line; two empty writing rows when nothing was typed (the DPR is often printed
    and filled in by hand)."""
    rows = [x.strip() for x in str(t or "").splitlines() if x.strip()]
    return rows if rows else ["", ""]


def sheet_boq_progress(wb, data, with_amount):
    ws = wb.create_sheet("BOQ progress")
    ws.sheet_view.showGridLines = False
    ncol = 16 if with_amount else 12
    _m(ws, f"A1:{get_column_letter(ncol)}1", f"BOQ progress to date — {data['project']} — {data['date_label']}",
       _F(size=13, bold=True, color="FFFFFF"), _SLATE, Alignment(vertical="center", indent=1))
    ws.row_dimensions[1].height = 26
    heads = ["Sr.", "Service", "BOQ Ref (client)", "BOQ item", "Unit", "BOQ qty", "Cumulative done (to date)", "Balance", "% complete",
             "Zones done", "In progress", "Pending"]
    widths = [5, 12, 13, 60, 7, 11, 14, 11, 10, 9, 10, 9]
    if with_amount:
        heads += ["Rate (₹)", "BOQ amount (₹)", "Done (₹)", "Balance (₹)"]
        widths += [11, 15, 15, 15]
    for i, (h, w) in enumerate(zip(heads, widths), start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
        _w(ws, f"{get_column_letter(i)}2", h, _F(bold=True), _BAND, _CEN)
    ws.cell(row=2, column=7).fill = _fill(_TODAYH)
    ws.row_dimensions[2].height = 34
    r = 3
    tot = {"N": 0.0, "O": 0.0, "P": 0.0}
    any_amt = {"N": False, "O": False, "P": False}
    for n, it in enumerate(data["boq_rows"], start=1):
        unit = it.get("unit") or DASH
        _w(ws, f"A{r}", n, _F(), None, _CEN)
        _w(ws, f"B{r}", it["service"], _F(), None, _ind(1))
        _w(ws, f"C{r}", it.get("ref") or DASH, _F(), None, _CEN)
        _w(ws, f"D{r}", _short(it["desc"], 110), _F(), None, _LEFT)
        _w(ws, f"E{r}", unit, _F(), None, _CEN)
        planned, cum = it.get("planned"), it.get("cum")
        bal = (planned - cum) if (_isnum(planned) and _isnum(cum)) else None
        pct = (cum / planned) if (_isnum(planned) and _isnum(cum) and planned) else None
        _w(ws, f"F{r}", planned, _F(), None, _RIGHT, _qty_fmt(unit, planned))
        _w(ws, f"G{r}", cum, _F(bold=True), _TODAY, _RIGHT, _qty_fmt(unit, cum))
        _w(ws, f"H{r}", bal, _F(), None, _RIGHT, _qty_fmt(unit, bal))
        _w(ws, f"I{r}", pct, _F(), None, _RIGHT, "0.0%")
        for col, k in zip("JKL", ("zones_done", "zones_inp", "zones_pend")):
            _w(ws, f"{col}{r}", it.get(k), _F(), None, _RIGHT, "0")
        if with_amount:
            rate = it.get("irate")
            if rate is None:
                for col in "MNOP":
                    _w(ws, f"{col}{r}", None)
            else:
                _w(ws, f"M{r}", rate, _F(), None, _RIGHT, "#,##0.00")
                vals = {"N": planned * rate if _isnum(planned) else None,
                        "O": cum * rate if _isnum(cum) else None,
                        "P": bal * rate if bal is not None else None}
                for col, v in vals.items():
                    _w(ws, f"{col}{r}", v, _F(bold=(col == "O")), None, _RIGHT, "#,##0")
                    if v is not None:
                        tot[col] += v
                        any_amt[col] = True
        ws.row_dimensions[r].height = max(16, 13 * _math.ceil(len(_short(it["desc"], 110)) / 66) + 3)
        r += 1
    last = r - 1
    if with_amount:
        _m(ws, f"A{r}:L{r}", "Total", _F(bold=True), _BAND, Alignment(horizontal="right", vertical="center", indent=1))
        _w(ws, f"M{r}", "", _F(), _BAND)
        for col in "NOP":
            _w(ws, f"{col}{r}", (tot[col] if any_amt[col] else None), _F(bold=True), _BAND, _RIGHT, "#,##0")
        r += 1
        _m(ws, f"A{r}:P{r}", "₹ figures: BOQ rate x installation % (Payment terms setting), same basis as the Summary. '—' = item without a BOQ rate.",
           _F(size=9, italic=True, color="555555"), None, _LEFT, border=False)
    ws.freeze_panes = "E3"
    if last >= 3:
        ws.auto_filter.ref = f"A2:{get_column_letter(ncol)}{last}"
    ws.print_title_rows = "1:2"
    _print_setup(ws, f"{data['project']} - BOQ progress to date - {data['date_label']} - Page &P of &N")
    return ws


def sheet_summary(wb, data, with_amount):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False
    period = data.get("period")
    ncol = 8
    for i, w in enumerate([16, 14, 14, 14, 14, 13, 13, 16], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _m(ws, f"A1:{get_column_letter(ncol)}1", f"Site progress summary — {data['project']} — {data['date_label']}",
       _F(size=13, bold=True, color="FFFFFF"), _SLATE, Alignment(vertical="center", indent=1))
    ws.row_dimensions[1].height = 26
    _m(ws, f"A2:{get_column_letter(ncol)}2", f"Generated {data.get('generated_at', '')}".strip(), _F(size=9, italic=True, color="888888"),
       None, Alignment(vertical="center"), border=False)
    svcs = data["services"]
    o = data.get("overall") or {}
    val = data.get("values") or {}
    brows = data["boq_rows"]
    zsum = lambda svc, k: (sum(b.get(k) or 0 for b in brows if b["service"] == svc))
    if with_amount:
        tiles = [("Value complete", o.get("todate"), "0.0%"), ("Done (₹)", val.get("done"), '"₹"#,##0'),
                 ("Remaining (₹)", val.get("remaining"), '"₹"#,##0'), ("Material waste (₹)", None, '"₹"#,##0')]
    else:
        mp_total = data.get("manpower_total")
        tiles = [("Progress complete", o.get("todate"), "0.0%"), ("This period" if period else "Today", o.get("today"), "+0.0%"),
                 ("Manpower in period" if period else "Manpower today", mp_total, "0"),
                 ("BOQ lines worked", len(data["lines"]), "0")]
    for i, (lab, v, fmt) in enumerate(tiles):
        c0 = 1 + i * 2
        _m(ws, f"{get_column_letter(c0)}4:{get_column_letter(c0 + 1)}4", lab, _F(size=9, bold=True, color="555555"), _BAND, _CEN)
        _m(ws, f"{get_column_letter(c0)}5:{get_column_letter(c0 + 1)}5", v, _F(size=15, bold=True, color=_SLATE), None, _CEN)
        if v is not None:
            ws[f"{get_column_letter(c0)}5"].number_format = fmt
    ws.row_dimensions[5].height = 30
    if with_amount:
        heads = ["Service", "% complete", "Done (₹)", "Remaining (₹)", "Total (₹)", "Zones done", "In progress", "Pending"]
    else:
        heads = ["Service", "Up to start of period" if period else "Up to previous day", "This period" if period else "Today",
                 "To date (% complete)", "Zones done", "In progress", "Pending", "Manpower in period" if period else "Manpower today"]
    for i, h in enumerate(heads, start=1):
        _w(ws, f"{get_column_letter(i)}7", h, _F(bold=True, color="FFFFFF"), _SLATE, _CEN)
    ws.row_dimensions[7].height = 32
    r0 = 8
    for k, svc in enumerate(svcs):
        r = r0 + k
        p = data["progress"].get(svc) or {}
        v = (val.get("by_service") or {}).get(svc) or {}
        _w(ws, f"A{r}", svc, _F(bold=True), None, _ind(1))
        if with_amount:
            _w(ws, f"B{r}", p.get("todate"), _F(), None, _RIGHT, "0.0%")
            for col, key in zip("CDE", ("done", "remaining", "total")):
                _w(ws, f"{col}{r}", v.get(key), _F(), None, _RIGHT, '"₹"#,##0')
            for col, key in zip("FGH", ("zones_done", "zones_inp", "zones_pend")):
                _w(ws, f"{col}{r}", zsum(svc, key), _F(), None, _RIGHT, "#,##0")
        else:
            _w(ws, f"B{r}", p.get("prev"), _F(), None, _RIGHT, "0.0%")
            _w(ws, f"C{r}", p.get("today"), _F(), None, _RIGHT, "0.0%")
            _w(ws, f"D{r}", p.get("todate"), _F(bold=True), None, _RIGHT, "0.0%")
            for col, key in zip("EFG", ("zones_done", "zones_inp", "zones_pend")):
                _w(ws, f"{col}{r}", zsum(svc, key), _F(), None, _RIGHT, "#,##0")
            _w(ws, f"H{r}", (data["manpower"].get(svc) or {}).get("total"), _F(), None, _RIGHT, "0")
    r = r0 + len(svcs)
    first, last = r0, r - 1
    _w(ws, f"A{r}", "Overall project", _F(bold=True), _BAND, _ind(1))
    colsum = lambda col: (sum(ws[f"{col}{x}"].value for x in range(first, last + 1) if _isnum(ws[f"{col}{x}"].value)))
    if with_amount:
        _w(ws, f"B{r}", o.get("todate"), _F(bold=True), _BAND, _RIGHT, "0.0%")
        for col, key in zip("CDE", ("done", "remaining", "total")):
            _w(ws, f"{col}{r}", val.get(key), _F(bold=True), _BAND, _RIGHT, '"₹"#,##0')
        for col in "FGH":
            _w(ws, f"{col}{r}", colsum(col), _F(bold=True), _BAND, _RIGHT, "#,##0")
        _m(ws, f"A{r + 2}:{get_column_letter(ncol)}{r + 2}",
           "₹ figures: computed from each item's BOQ rate x installation % (Payment terms setting). Zone counts: from the 'BOQ progress' sheet.",
           _F(size=9, italic=True, color="555555"), None, _LEFT, border=False)
    else:
        _w(ws, f"B{r}", o.get("prev"), _F(bold=True), _BAND, _RIGHT, "0.0%")
        _w(ws, f"C{r}", o.get("today"), _F(bold=True), _BAND, _RIGHT, "0.0%")
        _w(ws, f"D{r}", o.get("todate"), _F(bold=True), _BAND, _RIGHT, "0.0%")
        for col in "EFG":
            _w(ws, f"{col}{r}", colsum(col), _F(bold=True), _BAND, _RIGHT, "#,##0")
        _w(ws, f"H{r}", data.get("manpower_total"), _F(bold=True), _BAND, _RIGHT, "0")
        _m(ws, f"A{r + 2}:{get_column_letter(ncol)}{r + 2}", "Zone counts: from the 'BOQ progress' sheet.",
           _F(size=9, italic=True, color="555555"), None, _LEFT, border=False)
    _print_setup(ws, f"{data['project']} - Summary - {data['date_label']} - Page &P of &N")
    return ws


def build_client_dpr(data, mode=None):
    """mode: None = the DPR sheet only (the client copy) | "without" = + Summary and BOQ progress, percentages only | "with" = the same
    with ₹ amounts (internal). Sheet order: DPR, Summary, BOQ progress."""
    wb = openpyxl.Workbook()
    sheet_dpr(wb, data)
    if mode:
        with_amount = (mode == "with")
        sheet_summary(wb, data, with_amount)
        sheet_boq_progress(wb, data, with_amount)
    return wb
