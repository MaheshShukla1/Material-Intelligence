"""BOQ (Bill of Quantities) parser.

A BOQ sheet lists the *planned* quantity of every material for one room /
template — the target the site is working toward. Site Progress needs this as
its baseline: planned_total = boq_qty x num_rooms.

Why this is its own parser and not schema.py: schema.py detects a *stock
register* (dated IN/OUT movement blocks). A BOQ has no dates and no movement —
it is Item No | Description | Unit | Qty. But the same disease is here: every
service sheet spells and *orders* its columns differently. In this one file:

    ELECTRICAL R1 : Item No. | Description | Unit | Qty      (header row 4)
    PHE           : (blank)  | Item Number | Item Desc | Unit | Quantity (row 3)
    HVAC          : Sr. No.  | Description | Qty | Unit      (Qty/Unit SWAPPED)
    FIRE          : Sr. No.  | Description | Unit | Qty      (letter sub-items)

So, like schema.py, we detect the header row and each column by *meaning*, not
by fixed position. A hard-coded parser dies on the second sheet — that is the
whole reason this is synonym-driven.

Output: a clean DataFrame of real line items only —
    item_code, description, unit, qty, section, subcategory
— with section headers ("SECTION 2.00 POINT WIRING") and note/spec rows
dropped, but their section name carried onto the items beneath them.
"""
import re

import pandas as pd

try:
    from . import schema, subcat            # inside the backend package
except ImportError:                         # standalone (tests / scripts)
    import schema, subcat


# --- header synonyms, best-first. Reuses schema.norm for normalisation so the
# same fuzzy/robust matching applies. These are BOQ-specific column meanings.
HDR = {
    "item_code": ["ITEM NO", "ITEM NUMBER", "SR NO", "S NO", "SL NO",
                  "SERIAL NO", "ITEM CODE", "CODE"],
    "description": ["DESCRIPTION OF WORK", "ITEM DESCRIPTION", "DESCRIPTION",
                    "PARTICULARS", "MATERIAL DESCRIPTION", "ITEM NAME",
                    "MATERIAL", "ITEM"],
    "unit": ["UNIT", "UOM", "U O M", "UNITS", "MEASURE"],
    "qty": ["QUANTITY", "QTY", "QNTY", "NOS", "NO OF", "PLANNED QTY",
            "BOQ QTY", "TOTAL QTY"],
}


def _norm(v):
    return schema.norm(v)                   # upper, strip punctuation, collapse ws


def _match(cell, key):
    n = _norm(cell)
    if not n:
        return False
    opts = HDR[key]
    if n in opts:
        return True
    # tight substring so "ITEM NO." / "SR. NO." resolve, but keep it whole-ish
    return any(o == n or f" {o} " in f" {n} " for o in opts)


def _rank(cell, key):
    n = _norm(cell)
    opts = HDR[key]
    for i, o in enumerate(opts):
        if o == n:
            return i
    for i, o in enumerate(opts):
        if f" {o} " in f" {n} ":
            return i
    return len(opts)


def find_header_row(raw, max_scan=15):
    """The header row names a description column AND a unit AND a qty column.
    Among candidates, the first such row wins (BOQ headers are near the top)."""
    for r in range(min(max_scan, len(raw))):
        vals = raw.iloc[r].tolist()
        has_desc = any(_match(v, "description") for v in vals)
        has_unit = any(_match(v, "unit") for v in vals)
        has_qty = any(_match(v, "qty") for v in vals)
        if has_desc and has_unit and has_qty:
            return r
    return None


def _pick_col(hdr, key, exclude=()):
    """Best (lowest-rank) column matching `key`, not already taken."""
    hits = []
    for c in range(len(hdr)):
        if c in exclude:
            continue
        if _match(hdr[c], key):
            hits.append((_rank(hdr[c], key), c))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


# A qty cell is a real planned number; a unit cell is a short word like Nos/Rmt/
# Mtrs/set/no. Used both to place columns and to decide "is this a line item".
_UNIT_WORDS = {"NOS", "NO", "NOS.", "RMT", "RMT.", "MTR", "MTRS", "MTR.",
               "MTRS.", "MTS", "M", "SET", "SETS", "LOT", "LS", "SQM", "SQ M",
               "SQFT", "SQ FT", "KG", "KGS", "LTR", "LTRS", "LITRE", "EACH",
               "EA", "PAIR", "PKT", "ROLL", "POINT", "PTS", "JOB", "R MT",
               "RUNNING MTR", "RM", "CUM", "BOX", "COIL"}


def _looks_unit(v):
    if v is None:
        return False
    n = _norm(v)
    return bool(n) and (n in _UNIT_WORDS or (len(n) <= 5 and not _is_number(v)))


def _is_number(v):
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    s = str(v).strip().replace(",", "")
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _to_qty(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _resolve_unit_qty(hdr, exclude):
    """Unit and Qty columns, robust to HVAC's swapped order.

    First try by header words. If both resolve to sensible distinct columns,
    trust that. If the two collide or one is missing, fall back to the header
    words we did find. Order in the sheet is never assumed."""
    c_unit = _pick_col(hdr, "unit", exclude=exclude)
    c_qty = _pick_col(hdr, "qty", exclude=exclude | ({c_unit} if c_unit is not None else set()))
    return c_unit, c_qty


# --------------------------------------------------------------------------
# ProjectBase export format -- a distinct beast from a MEPF consultant's raw
# estimation sheet, so it gets its own detector + parser rather than being
# forced through the synonym-driven generic path above.
#
# Why not just add "BOQ"/"BOQ Unit"/"Design Quantity" to the HDR synonym
# lists and let the generic path handle it: tried that reasoning first and
# it silently corrupts the sheet -- "BOQ" is also a near-miss for other
# synonyms, "HSN Code" (a tax code, not an item number) gets picked as
# item_code, and -- the actual reason this needed its own path -- the
# generic parser has no concept of a Rate column at all. A raw MEPF sheet
# and a ProjectBase export are different enough documents that detecting
# one first and routing cleanly beats trying to generalise one parser
# across both; a hard-coded parser for two truly different formats is more
# honest than a synonym list stretched to silently cover both.
#
# ProjectBase's own quantity convention: "Design Quantity" is always the
# WHOLE-PROJECT total for that line (confirmed against real exports -- e.g.
# a transformer line reads qty=2 for the entire project, never "2 per
# zone"). So an item parsed from this path never needs BOQ-qty x room-count;
# its planned figure is exactly the qty on the sheet. See
# PROJECTBASE_QTY_IS_TOTAL below -- callers (siteprogress.py's upload route)
# read that flag to auto-seed planned.json overrides, no engineer prompt
# needed for this source specifically.
PROJECTBASE_QTY_IS_TOTAL = True

_PB_SIGNATURE = ("BOQ UNIT", "DESIGN QUANTITY", "RATE", "CLIENT REF NO")
_PB_MIN_SIGNATURE_HITS = 3   # any 3 of the 4 -- tolerates one column reordered/renamed


def _pb_header_row(raw, max_scan=5):
    """The row naming at least _PB_MIN_SIGNATURE_HITS of the ProjectBase
    signature columns. Deliberately strict (near-exact names, not fuzzy) --
    a false positive here would silently misroute a genuine raw BOQ into the
    wrong parser, which is worse than a false negative (that just falls
    through to the generic path, which already worked before this existed)."""
    for r in range(min(max_scan, len(raw))):
        vals = [schema.norm(v) for v in raw.iloc[r].tolist()]
        hits = sum(1 for sig in _PB_SIGNATURE if sig in vals)
        if hits >= _PB_MIN_SIGNATURE_HITS:
            return r
    return None


def is_projectbase(raw):
    return _pb_header_row(raw) is not None


_PB_SKIP_RE = re.compile(r"^\s*(SUB\s*TOTAL|NOTES?|GRAND\s*TOTAL)\b", re.I)


def _parse_projectbase_sheet(raw, service=None):
    """A ProjectBase 'BOQ Details' export: Internal Ref No | Client Ref No |
    BOQ | BOQ Unit | Order Quantity | Design Quantity | Rate | ... (plus a
    separate internal Material Rate / Labour Rate / Estimation Item
    breakdown further right, which this ignores -- Rate already includes
    that breakdown plus the real commercial markup on top, verified against
    a real export: Rate consistently runs ~1.2-1.3x (Material Rate + Labour
    Rate), not equal to it, so it -- not the sum -- is the number that
    matches what actually gets billed).

    Returns the same shape as parse_sheet()'s result, plus a `rates` dict
    {item_code: float} pulled straight from the sheet -- real, already-
    approved commercial rates, not a guess."""
    hdr_row = _pb_header_row(raw)
    if hdr_row is None:
        return None
    hdr = [schema.norm(v) for v in raw.iloc[hdr_row].tolist()]
    c_code = hdr.index("CLIENT REF NO") if "CLIENT REF NO" in hdr else None
    c_desc = hdr.index("BOQ") if "BOQ" in hdr else None
    c_unit = hdr.index("BOQ UNIT") if "BOQ UNIT" in hdr else None
    c_qty = hdr.index("DESIGN QUANTITY") if "DESIGN QUANTITY" in hdr else None
    c_rate = hdr.index("RATE") if "RATE" in hdr else None
    if c_desc is None or c_unit is None or c_qty is None:
        return None    # missing too much of the signature to trust a parse

    rows, section = [], None
    for r in range(hdr_row + 1, len(raw)):
        vals = raw.iloc[r].tolist()
        code = vals[c_code] if c_code is not None and c_code < len(vals) else None
        desc = vals[c_desc] if c_desc < len(vals) else None
        unit = vals[c_unit] if c_unit < len(vals) else None
        qty = vals[c_qty] if c_qty < len(vals) else None
        rate = vals[c_rate] if c_rate is not None and c_rate < len(vals) else None

        # NaN (a real float, not None) sneaks past a plain `is None`/`== ""`
        # check -- str(float('nan')) is the STRING "nan", not empty, so a
        # pre-filter written that way would treat a blank cell as if it had
        # real content. pd.isna() catches None, NaN, and NaT uniformly.
        desc_blank = pd.isna(desc) or str(desc).strip() == ""
        unit_blank = pd.isna(unit) or str(unit).strip() == ""
        qty_blank = pd.isna(qty)

        if desc_blank and unit_blank:
            continue                                    # fully blank row
        if _PB_SKIP_RE.search(str(desc or "")):
            continue                                    # "Sub Total 1.0...", "Notes:"

        # a real line item has both a real unit and a real design quantity;
        # a bare code + description with neither is a section/group header
        if unit_blank or qty_blank:
            section = re.sub(r"\s+", " ", str(desc)).strip() if not desc_blank else section
            continue

        rows.append({
            "item_code": None if pd.isna(code) else str(code).strip(),
            "description": re.sub(r"\s+", " ", str(desc)).strip(),
            "unit": re.sub(r"\s+", " ", str(unit)).strip(),
            "qty": _to_qty(qty),
            "rate": _to_qty(rate),
            "section": section,
        })

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["item_code_raw"] = df["item_code"]
    df["item_code"] = _dedupe_codes(df["item_code"].tolist())
    df["subcategory"] = df["description"].map(subcat.classify)
    if service is not None:
        df.insert(0, "service", service)

    # `rate` stays a real column on `items` (like qty/unit) all the way
    # through parse_workbook()'s merge + re-dedupe step, rather than a
    # separate {code: rate} dict built here -- a dict keyed by this sheet's
    # OWN item_code would silently point at the wrong item the moment two
    # merged sheets collide on a code and get suffixed to "1.1#2": the dict
    # wouldn't know that renaming happened. Keeping it as a column means it
    # travels with its row through concat + dedupe automatically, and the
    # final {code: rate} map only gets built once, from the truly-final
    # codes, at the bottom of parse_workbook().
    return {
        "header_row": int(hdr_row),
        "cols": {"item_code": c_code, "description": c_desc, "unit": c_unit,
                 "qty": c_qty, "rate": c_rate},
        "names": {"item_code": "Client Ref No", "description": "BOQ",
                  "unit": "BOQ Unit", "qty": "Design Quantity", "rate": "Rate"},
        "items": df,
        "source_format": "projectbase",
        "qty_is_total": PROJECTBASE_QTY_IS_TOTAL,
    }


# section / note detection -------------------------------------------------
# A "section" row introduces a group ("SECTION - 2.00 POINT WIRING", or in FIRE
# a bare letter "A  SPRINKLER SYSTEM"). A "note" row is prose with no unit
# (spec text, make lists, "GUEST ROOMS"). Neither is a line item. The signal we
# trust for a *line item* is: it has a unit word in the unit column. Planned qty
# may be blank (not-yet-priced items) — that is fine, qty just becomes None.
_SECTION_RE = re.compile(r"\bSECTION\b|\bSUB\s*HEAD\b|SCHEDULE OF", re.I)
_TOTAL_RE = re.compile(r"\bTOTAL\b|\bSUB\s*TOTAL\b|\bGRAND TOTAL\b|CARRIED (?:OVER|FORWARD)", re.I)


def _is_section(code, desc):
    d = str(desc or "")
    if _SECTION_RE.search(d):
        return True
    # bare integer or single letter code with an ALL-CAPS-ish heading and no dot
    c = str(code or "").strip()
    if c and "." not in c and re.fullmatch(r"[A-Za-z]|[0-9]{1,2}", c):
        letters = re.sub(r"[^A-Za-z]", "", d)
        if letters and letters.upper() == letters and len(d) < 60:
            return True
    return False


def _dedupe_codes(raw_codes):
    """Repeated codes -> '<code>', '<code>#2', '<code>#3', ... in encounter
    order. Shared by parse_sheet (within one sheet) and parse_workbook (across
    merged sheets), so the exact same "never conflate two different items"
    rule applies whether the collision happened on one tab or between two."""
    seen = {}
    out = []
    for c in raw_codes:
        key = "" if c is None else str(c)
        if key in seen:
            seen[key] += 1
            out.append(f"{key}#{seen[key]}")
        else:
            seen[key] = 1
            out.append(key)
    return out


def parse_sheet(raw, service=None):
    """raw: a header-less DataFrame (pd.read_excel(header=None)). Returns a
    dict with the detected mapping and a clean line-item DataFrame, or None if
    the sheet is not a BOQ.

    Tries the ProjectBase format first (a distinct, software-generated
    export shape -- see is_projectbase()). If that signature isn't present,
    falls through unchanged to the generic synonym-driven parser below,
    exactly as before this format existed."""
    if is_projectbase(raw):
        res = _parse_projectbase_sheet(raw, service=service)
        if res is not None:
            return res
        # signature matched but the row-level parse came up empty (e.g. a
        # near-miss header with no real line items below it) -- fall through
        # to the generic path rather than reporting "not a BOQ" outright.

    hdr_row = find_header_row(raw)
    if hdr_row is None:
        return None
    hdr = raw.iloc[hdr_row].tolist()

    c_code = _pick_col(hdr, "item_code")
    c_desc = _pick_col(hdr, "description",
                       exclude={c_code} if c_code is not None else set())
    if c_desc is None:
        return None
    taken = {c for c in (c_code, c_desc) if c is not None}
    c_unit, c_qty = _resolve_unit_qty(hdr, taken)
    if c_unit is None or c_qty is None:
        return None

    rows, section = [], None
    for r in range(hdr_row + 1, len(raw)):
        vals = raw.iloc[r].tolist()
        code = vals[c_code] if c_code is not None and c_code < len(vals) else None
        desc = vals[c_desc] if c_desc < len(vals) else None
        unit = vals[c_unit] if c_unit < len(vals) else None
        qty = vals[c_qty] if c_qty < len(vals) else None

        if (desc is None or str(desc).strip() == "") and \
           (unit is None or str(unit).strip() == ""):
            continue                                    # fully blank

        if _is_section(code, desc):
            section = re.sub(r"\s+", " ", str(desc)).strip()
            continue
        if _TOTAL_RE.search(str(desc or "")) and not _looks_unit(unit):
            continue                                    # TOTAL OF SECTION rows

        # Line item test: a real unit word present. Prose/spec notes have none.
        if not _looks_unit(unit):
            continue

        rows.append({
            "item_code": None if code is None else str(code).strip(),
            "description": re.sub(r"\s+", " ", str(desc)).strip(),
            "unit": re.sub(r"\s+", " ", str(unit)).strip(),
            "qty": _to_qty(qty),
            "section": section,
        })

    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Excel drops a trailing zero, so item 2.10 arrives as "2.1" and collides
    # with the real 2.1 — which would silently merge their rate, mapping and
    # progress. Keep every line distinct: a repeated code gets a "#n" suffix so
    # nothing downstream conflates two different items. The original stays in
    # item_code_raw for display.
    df["item_code_raw"] = df["item_code"]
    df["item_code"] = _dedupe_codes(df["item_code"].tolist())
    df["subcategory"] = df["description"].map(subcat.classify)
    if service is not None:
        df.insert(0, "service", service)

    return {
        "header_row": int(hdr_row),
        "cols": {"item_code": c_code, "description": c_desc,
                 "unit": c_unit, "qty": c_qty},
        "names": {"item_code": None if c_code is None else str(hdr[c_code]).strip(),
                  "description": str(hdr[c_desc]).strip(),
                  "unit": str(hdr[c_unit]).strip(),
                  "qty": str(hdr[c_qty]).strip()},
        "items": df,
    }


# Which workbook sheets are actually BOQ tabs (skip Challans, cable schedules,
# summaries).
_SKIP = re.compile(r"CHALLAN|SCHEDULE|SUMMARY|INDEX|MASTER|MICRODETAIL|MIRCODETAIL",
                   re.I)

# Site-Progress service label per sheet. IMPORTANT: unlike the forecast side
# (schema.clean_service folds HVAC + Fire into one "Fire & HVAC" bucket), Site
# Progress needs Fire and HVAC as *distinct* trades — their activities and rooms
# differ. Folding them here silently dropped the FIRE sheet. So BOQ resolves its
# own distinct labels and never lets one sheet overwrite another.
_SVC_RULES = [
    (re.compile(r"\bFAPA\b|FIRE ALARM|\bFAS\b|\bPA\b", re.I), "FAPA"),
    (re.compile(r"FIRE|FFTG|SPRINKLER|HYDRANT", re.I), "Fire"),
    (re.compile(r"HVAC|CHILLED|DUCT|VENTILAT", re.I), "HVAC"),
    (re.compile(r"PHE|PLUMB|SANITARY|WATER|CPVC|PERT", re.I), "Plumbing"),
    (re.compile(r"ELECTRIC|\bELE\b|\bELV\b", re.I), "Electrical"),
]


def _boq_service(sheet):
    for rx, label in _SVC_RULES:
        if rx.search(sheet):
            return label
    return schema.clean_service(sheet)


def parse_workbook(path):
    """Parse every BOQ sheet in an Excel workbook. Returns
    {service: parse_result} for sheets that parse, plus a skipped list.

    Every parseable sheet is preserved. If two (or more) sheets resolve to the
    same service — e.g. a workbook with separate 'PHE - CPVC' and
    'PHE - Fixtures' tabs, both Plumbing — their items are MERGED into that
    one service entry, so the UI shows exactly one 'Plumbing' tab. Nothing is
    dropped: every row from every contributing sheet survives in the merged
    frame, tagged with a `source_sheet` column so the mixed origin stays
    traceable. Item codes are re-deduplicated ACROSS the merge with the same
    '#n' rule parse_sheet already applies within one sheet — two
    independently-numbered tabs both starting at item code '1' would
    otherwise silently collide once combined, conflating two different
    items' rate/mapping/progress exactly like the within-sheet case
    _dedupe_codes already guards against.

    Each result also carries `rates` ({item_code: float}, built from a real
    `rate` column ONLY when the source was a ProjectBase export -- raw MEPF
    sheets have no such column, so `rates` is simply empty for them, never
    guessed at) and `source_format` ("projectbase" / "raw" / "mixed" when a
    merge combines both). `qty_is_total` is True only when EVERY contributing
    sheet was ProjectBase-sourced -- ProjectBase's own Design Quantity is
    always a whole-project figure, never "per room" (see boq.py's module
    docs). A "mixed" merge leaves qty_is_total unset (None): a raw sheet
    merged alongside a ProjectBase one still needs the engineer's own
    per-room/total call, and silently inheriting the ProjectBase sibling's
    convention would be exactly the kind of guess this app never makes.
    """
    xl = pd.ExcelFile(path)
    by_service, skipped = {}, []          # service -> [parse_sheet result, ...]
    for s in xl.sheet_names:
        if _SKIP.search(s):
            skipped.append({"sheet": s, "why": "not a BOQ tab"})
            continue
        raw = xl.parse(sheet_name=s, header=None, dtype=object)
        svc = _boq_service(s)
        res = parse_sheet(raw, service=svc)
        if res is None:
            skipped.append({"sheet": s, "why": "no BOQ header detected"})
            continue
        res["sheet"] = s
        by_service.setdefault(svc, []).append(res)

    def _finalize(items_df, formats):
        """Build the final {code: rate} map and source_format/qty_is_total
        AFTER any merge + re-dedupe, from whatever `rate` column survived on
        the truly-final item_code values -- never from a pre-merge dict that
        could point at a code that got renamed by dedup."""
        rates = {}
        if "rate" in items_df.columns:
            rates = {c: float(r) for c, r in zip(items_df["item_code"], items_df["rate"])
                     if pd.notna(r)}
        uniq = set(formats)
        fmt = formats[0] if len(uniq) == 1 else "mixed"
        qty_is_total = PROJECTBASE_QTY_IS_TOTAL if fmt == "projectbase" else None
        return rates, fmt, qty_is_total

    out = {}
    for svc, results in by_service.items():
        formats = [r.get("source_format", "raw") for r in results]
        if len(results) == 1:
            r = results[0]
            r["items"] = r["items"].copy()
            r["items"]["service"] = svc
            r["items"]["source_sheet"] = r["sheet"]
            r["sheets"] = [r["sheet"]]
            r["rates"], r["source_format"], r["qty_is_total"] = _finalize(r["items"], formats)
            out[svc] = r
            continue

        # merged case: concat every contributing sheet's items, then
        # re-dedupe item_code globally so two sheets' independent numbering
        # never collides. `rate` (a real column when present) travels along
        # with each row through the concat automatically.
        frames = []
        for r in results:
            df = r["items"].copy()
            df["service"] = svc
            df["source_sheet"] = r["sheet"]
            frames.append(df)
        merged = pd.concat(frames, ignore_index=True)
        merged["item_code"] = _dedupe_codes(merged["item_code_raw"].tolist())
        rates, fmt, qty_is_total = _finalize(merged, formats)
        out[svc] = {
            "header_row": results[0]["header_row"],
            "cols": results[0]["cols"],
            "names": results[0]["names"],
            "items": merged,
            "sheet": " + ".join(r["sheet"] for r in results),
            "sheets": [r["sheet"] for r in results],
            "rates": rates,
            "source_format": fmt,
            "qty_is_total": qty_is_total,
        }
    return out, skipped
