"""Billing (RA bills) -- the fourth tab. A self-contained FastAPI router, like
siteprogress.py: api.py includes it with two lines, and NOTHING in
siteprogress.py / dpr.py / pnl.py / realtime.py changes. Billing only READS
what those modules already compute and writes ONE new file per project:

    data/projects/<slug>/billing.json
        setup  : {service: {"mode": "separate"|"together",
                            "supply_pct": 0-100|null, "install_pct": 0-100|null,
                            "line_pct": {item_code: {"supply": x, "install": y}}}}
        groups : {service: [{"sr","name","sheet","from","to","wo"}]}   (annexure rows)
        meta   : {"client","contractor","wo_no","work": {service: text}}
        ras    : [ {id, service, series, no, status, cutoff, created, locked_at,
                    overrides: {item_code: {stage: {"qty","reason"}}},
                    snap: {...locked snapshot...}} ]
        packages: {service: [ {name, ranges: [{sheet,from,to}]} | {name, rest: true} ]}
                 (Work Orders of one service, e.g. Electrical "Excl. point wiring" / "Only point
                  wiring". Each Work Order is its OWN unit "Service › Name": setup, groups,
                  challans, RA numbers and drafts are all keyed by the unit, so the two
                  Work Orders each have an RA 1. A service with no packages is its own unit.)
        challans: {service: [ {id, no, date, ra, note, qty: {item_code: qty}} ]}
                 (supply delivered on site; `ra` = the RA number it is billed in,
                  null = held back, not in any RA)

MODEL
  * An RA belongs to ONE service and ONE series: "supply", "install" or
    "together" (one RA carries both). The series is chosen on EACH new RA --
    a service's "mode" in Billing setup is only the default offered first, it
    never forbids a bill type. A service with no payment term set cannot
    bill at all -- no % is ever guessed.
  * Numbers follow the stages: a new RA gets 1 + the highest number already
    used on any stage it carries (a Together RA: the higher of its two
    stages), so two RAs never carry the same stage under the same number, and
    Supply RA 2 + Installation RA 2 is simply "cycle 2" billed as two invoices.
    Only one draft may be open per stage.
  * Cumulative method: this bill = (qty up to date) - (qty in earlier LOCKED
    RAs of the same stage). Over-billing shows as a NEGATIVE line.
  * Amount = rate x qty_this x pct / 100, per stage.
  * Installation qty up to date = Site Progress "used" (itemprog.compute with
    the exact inputs _service_view uses), capped at the BOQ qty.
  * Supply qty up to date has two sources, chosen per service in Billing setup
    (`supply_source`):
      - "challan": the delivery challans entered for the service. A challan
        carries the RA number it is billed in (default: the open RA). Qty up
        to date = challans tagged with an RA number <= this RA's number.
        The stock register is only a cross-check here.
      - "stock" (default when nothing is chosen): stock "received"
        (= total_consumed + on-hand, the Forecast tab's Total rcvd) / link
        factor, ONLY for an item linked to exactly one material, that material
        linked to no other item, with a known factor. Anything else is MANUAL:
        the engineer types it; it is never guessed. "As of the latest forecast run".
  * A locked RA stores a full snapshot (rate, %, qty, amount, description) so
    later rate / progress / link edits never change a submitted bill.

Deploy together: billing.py + boq.py (the parser now also reads Internal Ref
No and the sheet row) + api.py (one include). siteprogress.py is only read.
"""
import io
import os
import re
import time
import json
import functools
import contextvars
import math
import datetime as dt
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Response, UploadFile, File, Form, Query

from . import siteprogress as sp, itemprog, linkage, boq, projectbase as pbx

router = APIRouter(prefix="/api/billing", tags=["billing"])

STAGES = {"supply": ["supply"], "install": ["install"], "together": ["supply", "install"]}
STAGE_NAME = {"supply": "Supply", "install": "Installation"}
SERIES_NAME = {"supply": "Supply", "install": "Installation", "together": "Supply + Installation"}
QUICK_SECTION = "Quick-added (from stock)"
SEP = " \u203a "            # "Electrical › Only point wiring": the key of one Work Order inside a service


def _base(unit):
    """The BOQ service a billing unit belongs to ("Electrical › Only point wiring" -> "Electrical")."""
    return unit.split(SEP, 1)[0]


def _uname(unit):
    """The Work Order name of a unit ("" for a plain service)."""
    return unit.split(SEP, 1)[1] if SEP in unit else ""


def _ukey(base, name):
    return f"{base}{SEP}{name}"


def _unit_text(unit, sep=" - "):
    """A unit as plain text for file / sheet names."""
    return unit.replace(SEP, sep)


def _unit_paren(unit):
    """"Electrical (Only point wiring)" -- a unit as it reads inside a sentence."""
    return f"{_base(unit)} ({_uname(unit)})" if SEP in unit else unit


# ------------------------------------------------------------------ store
def _path(d):
    return d / "billing.json"


def _load(d):
    s = sp._read_json(_path(d), {}) or {}
    s.setdefault("setup", {})
    s.setdefault("groups", {})
    s.setdefault("meta", {})
    s.setdefault("ras", [])
    s.setdefault("seq", 0)
    s.setdefault("challans", {})
    s.setdefault("wire", {})
    s.setdefault("stock_hold", {})
    s.setdefault("stock_skip", {})
    s.setdefault("wire_map", {})
    s.setdefault("packages", {})
    s.setdefault("pb", {})
    return s


_PK_CACHE = {}


def _save(d, store):
    tmp = _path(d).with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False))
    tmp.replace(_path(d))
    _PK_CACHE.pop(str(_path(d)), None)


def _pkdefs(d):
    """{service: [Work Order definitions]} straight from billing.json, kept per (file, mtime, size) so the many
    _lines()/_services() calls of one request do not re-read the (large) store."""
    p = _path(d)
    try:
        st = p.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _PK_CACHE.get(str(p))
    if hit and hit[0] == stamp:
        return hit[1]
    pk = (sp._read_json(p, {}) or {}).get("packages") or {}
    _PK_CACHE[str(p)] = (stamp, pk)
    return pk


def _pkg_of(plist, ln):
    """Name of the Work Order a BOQ line belongs to: the one whose group rows hold its sheet row, else the
    remainder Work Order. Nothing is guessed from the text."""
    rest = None
    for p in plist:
        if p.get("rest"):
            rest = p["name"]
            continue
        for r in p.get("ranges") or []:
            if ln.get("src_row") and r["from"] <= ln["src_row"] <= r["to"] and (not r.get("sheet") or r["sheet"] == ln.get("sheet")):
                return p["name"]
    return rest


def _num(x):
    try:
        if x is None:
            return None
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _pct(x, label):
    if x is None or x == "":
        return None
    v = _num(x)
    if v is None or v <= 0 or v > 100:
        raise HTTPException(400, f"{label} must be above 0 and at most 100")
    return v


def _ra(store, ra_id):
    for r in store["ras"]:
        if r["id"] == ra_id:
            return r
    raise HTTPException(404, "RA not found")


def _stage_pct(setup, code, stage):
    """The % a line is billed at for one stage: its own line override if the
    engineer set one, else the service's. None => not set => not billable."""
    lp = ((setup or {}).get("line_pct") or {}).get(str(code)) or {}
    v = _num(lp.get(stage))
    if v is not None:
        return v
    return _num((setup or {}).get(stage + "_pct"))


# ------------------------------------------------------------ BOQ lines
def _ensure_refs(d, items):
    """Internal Ref No / sheet row come from boq.py's parser. A project whose
    boq.parquet predates that has neither column: re-read the SAVED boq.xlsx
    (the very file that parquet came from) and attach them by (sheet,
    item_code). Nothing is guessed -- a code the saved workbook doesn't
    confirm keeps blank refs."""
    if "src_row" in items.columns and "internal_ref" in items.columns:
        return items
    x = d / "boq.xlsx"
    items = items.copy()
    items["internal_ref"] = None
    items["src_row"] = None
    if not x.exists():
        return items
    try:
        parsed, _ = boq.parse_workbook(str(x))
    except Exception:
        return items
    lut = {}
    for svc, r in parsed.items():
        df = r["items"]
        for t in df.itertuples():
            lut[(svc, str(t.item_code))] = (getattr(t, "internal_ref", None), getattr(t, "src_row", None),
                                            getattr(t, "source_sheet", None))
    refs, rows, sheets = [], [], []
    for t in items.itertuples():
        v = lut.get((t.service, str(t.item_code)))
        refs.append(v[0] if v else None)
        rows.append(v[1] if v else None)
        sheets.append(v[2] if v else None)
    items["internal_ref"], items["src_row"] = refs, rows
    if "source_sheet" not in items.columns:
        items["source_sheet"] = sheets
    return items


# What Billing computes from the project's data files only (BOQ lines, installation quantities) is remembered until one of those
# files changes, so opening Billing does not re-read and re-compute the same BOQ for every service, series and RA. The stamp is the
# name / modified-time / size of every file in the project folder except billing.json (billing's own state, rewritten on every
# billing action; Work Order definitions live there and are applied AFTER the remembered part). A file written in the last second is
# not trusted yet ("racily clean": two writes inside one timestamp tick could look identical), so right after an edit it simply
# recomputes. Same numbers as before: only the repeated work is skipped.
_DATA_CACHE = {}
_RACY_NS = 1_000_000_000


def _dsig(d):
    try:
        files = [(e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(d) if e.is_file() and e.name != "billing.json"]
    except OSError:
        return None
    if not files or max(f[1] for f in files) > time.time_ns() - _RACY_NS:
        return None
    return (str(d), tuple(sorted(files)))


def _remember(kind, d, service, make):
    sig = _dsig(d)
    if sig is None:
        return make()
    key = (kind, service, sig)
    if key not in _DATA_CACHE:
        value = make()
        if len(_DATA_CACHE) > 48:
            for k in [k for k in _DATA_CACHE if k[2] != sig]:
                _DATA_CACHE.pop(k, None)
            if len(_DATA_CACHE) > 48:
                _DATA_CACHE.clear()
        _DATA_CACHE[key] = value
    return _DATA_CACHE[key]


def _lines(d, service):
    """Billable BOQ lines of one billing unit (a service, or one Work Order of it)."""
    base = _base(service)
    lines = _base_lines(d, base)
    if service == base:
        return lines
    plist = _pkdefs(d).get(base)
    if not plist:
        return []
    name = _uname(service)
    return [l for l in lines if _pkg_of(plist, l) == name]


def _base_lines(d, service):
    """Billable BOQ lines of one service (a copy: callers may change their dicts)."""
    return [dict(l) for l in _remember("lines", d, service, lambda: _base_lines_raw(d, service))]


def _base_lines_raw(d, service):
    """Billable BOQ lines of one service, in sheet order:
    qty > 0 and a real rate. Rate-only ("RO") lines have no qty and are not
    billable; quick-added stock items are not in the contract."""
    items = sp._load_boq(d, service)
    if items.empty:
        return []
    items = items[items.get("section", "") != QUICK_SECTION] if "section" in items.columns else items
    items = _ensure_refs(d, items)
    rates = sp._rates(d, service)
    out = []
    for t in items.itertuples():
        qty, rate = _num(getattr(t, "qty", None)), _num(rates.get(str(t.item_code)))
        if qty is None or qty <= 0 or rate is None:
            continue
        sr = getattr(t, "src_row", None)
        out.append({
            "item_code": str(t.item_code), "desc": str(t.description), "unit": str(t.unit or ""),
            "boq_qty": qty, "rate": rate,
            "internal_ref": None if sr is None or pd.isna(sr) else getattr(t, "internal_ref", None),
            "src_row": None if sr is None or pd.isna(sr) else int(sr),
            "sheet": getattr(t, "source_sheet", None) if isinstance(getattr(t, "source_sheet", None), str) else None,
            "_ref_ok": sr is not None and not pd.isna(sr),
        })
    return out


# ------------------------------------------------------------- quantities
# "What if": set only while the activity-delete preview recomputes the open drafts. A ContextVar, so a request running in
# parallel never sees it, and nothing is written anywhere.
_WHATIF_ZERO = contextvars.ContextVar("bl_whatif_zero", default=None)


def _install_qty(d, service):
    service = _base(service)                # Site Progress knows the service, not the Work Order
    out = dict(_remember("inst", d, service, lambda: _install_qty_raw(d, service)))
    wh = _WHATIF_ZERO.get()
    if wh:                                              # activity-delete preview only: pretend these items have no work
        for code in wh.get(service, ()):
            if code in out:
                out[code] = 0.0
    return out


def _install_qty_raw(d, service):
    items = sp._load_boq(d, service)
    if items.empty:
        return {}
    all_rooms = sp._all_room_ids(d)
    prog = sp._item_prog(d).get(service, {})
    used = itemprog.compute(items, prog, sp._item_rooms(d).get(service, {}), all_rooms,
                            sp._planned(d, service), room_qty_groups=sp._item_room_qty(d).get(service, {}))
    return {str(r.item_code): float(r.used or 0) for r in used.itertuples()}


def _material_use_counts(d):
    """How many BOQ items (across ALL services) link to each stock material.
    A material shared by two items can't be split between them safely."""
    cnt = {}
    for _svc, per in sp._load_links(d).items():
        for _code, entries in per.items():
            for e in sp._norm_link_entries(entries):
                k = linkage._norm(e["material"])
                cnt[k] = cnt.get(k, 0) + 1
    return cnt


def _stock_supply_qty(slug, d, service, lines):
    """{item_code: (qty|None, source, note)} from the stock register -- see module docstring."""
    out = {}
    service = _base(service)                # stock links / forecast belong to the service
    links = sp._load_links(d).get(service, {})
    rows, _names, run, _f = sp._forecast_pool(slug, service)
    shared = _material_use_counts(d)
    full = None
    for ln in lines:
        code = ln["item_code"]
        entries = sp._norm_link_entries(links.get(code, []))
        if not entries:
            out[code] = (None, "manual", "not linked to the stock register")
            continue
        if len(entries) != 1:
            out[code] = (None, "manual", "linked to more than one material")
            continue
        e = entries[0]
        key = linkage._norm(e["material"])
        if shared.get(key, 0) > 1:
            out[code] = (None, "manual", "this material is also linked to another item")
            continue
        row = rows.get(key)
        if row is None:
            if full is None:
                full = sp._full_run_rows(slug)
            row = full.get(key)
        if not row:
            out[code] = (None, "manual", "material not found in the latest forecast run")
            continue
        stock, consumed = _num(row.get("stock")), _num(row.get("total_consumed"))
        if stock is None or consumed is None:
            out[code] = (None, "manual", "stock or consumed figure is missing")
            continue
        factor = e["factor"]
        if factor is None:
            same = (str(row.get("unit") or "").strip().upper() == ln["unit"].strip().upper()) and ln["unit"].strip()
            factor = 1.0 if same else None
        if not factor:
            out[code] = (None, "manual", "units differ and no conversion factor is set")
            continue
        out[code] = (round((consumed + stock) / factor, 4), "stock",
                     f"{e['material']}: {consumed:g} use + {stock:g} stock = {consumed + stock:g} received"
                     + (f" ÷ {factor:g}" if factor != 1 else ""))
    return out, run


# ---------------------------------------------------------------- challans
def _supply_series(store, service):
    """The RA series that carries the SUPPLY stage of a service."""
    return "together" if (store["setup"].get(service) or {}).get("mode") == "together" else "supply"


def _last_locked_no(store, service):
    """Highest number of a LOCKED RA that carries the supply stage (a Supply RA or a Together RA)."""
    return max([r["no"] for r in store["ras"]
                if r["service"] == service and "supply" in STAGES[r["series"]] and r["status"] == "locked"] or [0])


def _open_no(store, service):
    """The RA number new challans go into by default (the open / next supply RA)."""
    return _last_locked_no(store, service) + 1


def _source(store, service):
    return (store["setup"].get(service) or {}).get("supply_source") or "stock"


def _challans(store, service):
    real = store["challans"].get(service, [])
    w = (store.get("wire") or {}).get(service)
    return real + _wire_virtual(store, service) if w and w.get("alloc") else real


def _challan_frozen(store, service, c):
    return c.get("ra") is not None and c["ra"] <= _last_locked_no(store, service)


def _challan_qty(store, service, upto):
    """({code: qty}, {code: [(challan no, qty)]}) of the challans billed in RA <= upto."""
    tot, used = {}, {}
    for c in _challans(store, service):
        if c.get("ra") is None or c["ra"] > upto:
            continue
        for code, q in c["qty"].items():
            tot[code] = tot.get(code, 0.0) + q
            used.setdefault(code, []).append((c["no"], q))
    return tot, used


def _supply_qty(slug, d, store, service, lines, ra_no):
    """(out, run, stock): out = {item_code: (qty|None, source, note)}.
    `stock` = {item_code: stock-register qty|None}, filled only when the challans
    are the source (it is the cross-check, never the bill)."""
    stock_out, run = _stock_supply_qty(slug, d, service, lines)
    if _source(store, service) != "challan":
        return stock_out, run, {}
    tot, used = _challan_qty(store, service, ra_no)
    out, stk = {}, {}
    for ln in lines:
        code = ln["item_code"]
        parts = used.get(code, [])
        if parts:
            note = "Challan " + ", ".join(f"{n} ({q:g})" for n, q in parts[:6]) + (f" +{len(parts) - 6} more" if len(parts) > 6 else "")
        else:
            note = "No challan entered for this line"
        out[code] = (tot.get(code, 0.0), "challan", note)
        stk[code] = stock_out.get(code, (None,))[0]
    return out, run, stk


# ---------------------------------------------------- wire metres -> points
# Point wiring is BOUGHT as wire (metres) but BILLED as points. The standard way (and the way the site's own Excel
# does it): every circuit has its own phase, neutral and earth wire laid together, so the length of circuit laid is
# the length of the PHASE wire only; neutral and earth run beside it and are never counted again. Points of a
# BOQ line = metres given to that line / the "average point length" the BOQ itself states for it (never guessed:
# a line whose BOQ text states no length cannot take wire until a person types the length). A line can never be
# claimed above its BOQ points. The engineer decides which metres go to which line. The points feed the RA exactly
# like a delivery challan (one "Wire RA-nn" challan per RA), so the rest of the bill needs no special case.
# Which colours are the phase wires depends on the wiring colour code of the site: legacy (R-Y-B phases, black
# neutral) or the current IEC / IS code (brown-black-grey phases, blue neutral). One choice for the project.
WIRE_SCHEMES = {
    "ryb": {"label": "Red · Yellow · Blue phases (legacy)", "note": "Black neutral, Green earth",
            "colours": [("RED", "Red"), ("YELLOW", "Yellow"), ("BLUE", "Blue"), ("BLACK", "Black"), ("GREEN", "Green")],
            "phases": ("RED", "YELLOW", "BLUE")},
    "iec": {"label": "Brown · Black · Grey phases (IEC)", "note": "Blue neutral, Green-yellow earth",
            "colours": [("BROWN", "Brown"), ("BLACK", "Black"), ("GREY", "Grey"), ("BLUE", "Blue"), ("GREEN_YELLOW", "Green-yellow")],
            "phases": ("BROWN", "BLACK", "GREY")},
}
_PTLEN = re.compile(r"(?:average|avg\.?)\s+(?:point\s+)?(?:wiring\s+)?length\b([^0-9]{0,40}?)(\d+(?:\.\d+)?)\s*(?:r\.?\s*)?"
                    r"(?:m(?:tr|t)?|metres?|meters?)(?![a-z])", re.I)


def _point_len(desc):
    """The metres-per-point a BOQ line states in its own text ("... with average point length upto & including
    20 Rmtr") or None. {per, quote, upto}."""
    m = _PTLEN.search(str(desc or ""))
    if not m:
        return None
    v = float(m.group(2))
    if v <= 0:
        return None
    return {"per": v, "quote": re.sub(r"\s+", " ", m.group(0)).strip(), "upto": bool(re.search(r"up\s*to", m.group(1), re.I))}


def _wscheme(store):
    return WIRE_SCHEMES.get((store.get("meta") or {}).get("wire_scheme") or "ryb") or WIRE_SCHEMES["ryb"]


def _wunit(store, unit):
    return (store.get("wire") or {}).get(unit) or {"challans": [], "alloc": []}


def _wsize(x):
    v = _num(x)
    if v is None or v <= 0 or v > 1000:
        raise HTTPException(400, "Wire size must be a number of sq mm, like 1.5 or 2.5.")
    return f"{v:g}"


def _w_counts(w, c):
    """Does this wire challan count towards the wire received? Every challan did, until a unit chooses where its
    wire deliveries come from (stock register or typed challans). From then on, a challan in an RA BEFORE the
    switch always counts (it is part of a bill already made); a later one counts only if it comes from the chosen
    source, so the same delivery is never counted from both."""
    if c.get("ra") is None:
        return False
    sw = w.get("switch_ra")
    if sw is None or c["ra"] < sw:
        return True
    return ((c.get("src") or {}).get("kind") == "stock") == (w.get("source") == "stock")


def _w_counted(store, w, upto=None):
    """{size: metres of PHASE wire received} in the wire challans billed in RA <= upto (all, when None)."""
    ph = set(_wscheme(store)["phases"])
    out = {}
    for c in w.get("challans", []):
        if not _w_counts(w, c) or (upto is not None and c["ra"] > upto):
            continue
        for size, cols in c["qty"].items():
            out[size] = out.get(size, 0.0) + sum(m for col, m in cols.items() if col in ph)
    return out


def _w_given(w, upto=None):
    out = {}
    for a in w.get("alloc", []):
        if upto is None or a["ra"] <= upto:
            out[a["size"]] = out.get(a["size"], 0.0) + a["metres"]
    return out


def _w_line_metres(w, upto=None):
    out, per = {}, {}
    for a in w.get("alloc", []):
        if upto is None or a["ra"] <= upto:
            out[a["item_code"]] = out.get(a["item_code"], 0.0) + a["metres"]
        per[a["item_code"]] = a["per"]
    return out, per


def _w_pts(w, upto=None):
    """{item_code: whole points} to date at RA <= upto. Whole points only: 4,020.6 is 4,020 until the rest is laid."""
    m, per = _w_line_metres(w, upto)
    return {c: int(math.floor(v / per[c] + 1e-9)) for c, v in m.items() if per.get(c)}


def _w_check(store, w):
    """None when the wire is consistent, else why not: in no RA may more metres be given to point lines than the
    wire received up to that RA."""
    ras = sorted({c["ra"] for c in w.get("challans", []) if _w_counts(w, c)} | {a["ra"] for a in w.get("alloc", [])})
    for n in ras:
        got, gave = _w_counted(store, w, n), _w_given(w, n)
        for size, m in gave.items():
            if m > got.get(size, 0.0) + 1e-6:
                return (f"{m:g} m of {size} sq mm wire is given to point lines but only {got.get(size, 0.0):g} m of phase wire has "
                        f"been received up to {raL_(n)}. Reduce the metres given, or add the wire challan.")
    return None


def raL_(n):
    return f"RA-{n:02d}"


def _wire_virtual(store, unit):
    """The points of the wire, as one challan per RA ('Wire RA-01'), in the same shape as a typed challan."""
    w = (store.get("wire") or {}).get(unit)
    if not w or not w.get("alloc"):
        return []
    out, prev = [], {}
    for n in sorted({a["ra"] for a in w["alloc"]}):
        cur = _w_pts(w, n)
        qty = {c: float(cur[c] - prev.get(c, 0)) for c in cur if cur[c] - prev.get(c, 0) > 0}
        prev = cur
        if not qty:
            continue
        dates = [c["date"] for c in w["challans"] if _w_counts(w, c) and c["ra"] <= n]
        out.append({"id": f"w{n}", "no": f"Wire {raL_(n)}", "date": max(dates) if dates else dt.date.today().isoformat(), "ra": n,
                    "note": "Points from wire metres", "qty": qty, "virtual": True})
    return out


def _wired_codes(store, unit):
    return {a["item_code"] for a in _wunit(store, unit).get("alloc", [])}


# ------------------------------------------------------------------ groups
_SUBTOTAL = re.compile(r"^\s*(sub\s*-?\s*total|total|cost\s+of)\b", re.I)


_SHEET_CACHE = {}


def _sheet_rows(d, sheets):
    """Rows of the named BOQ sheets. Opening boq.xlsx is by far the slowest step of a bill (the same
    workbook was re-read for every RA / preview on every screen refresh), so the rows are kept per
    (file, modified-time, size, sheet): a re-uploaded BOQ has a new mtime/size and is read afresh."""
    x = d / "boq.xlsx"
    out = {}
    if not x.exists():
        return out
    st = x.stat()
    stamp = (str(x), st.st_mtime_ns, st.st_size)
    missing = [s for s in sheets if (stamp, s) not in _SHEET_CACHE]
    if missing:
        import openpyxl
        wb = openpyxl.load_workbook(str(x), read_only=True, data_only=True)
        try:
            for s in missing:
                _SHEET_CACHE[(stamp, s)] = list(wb[s].iter_rows(values_only=True)) if s in wb.sheetnames else None
        finally:
            wb.close()
        if len(_SHEET_CACHE) > 64:                       # keep it small; stale file versions drop out first
            for k in [k for k in _SHEET_CACHE if k[0] != stamp]:
                _SHEET_CACHE.pop(k, None)
    for s in sheets:
        rows = _SHEET_CACHE.get((stamp, s))
        if rows is not None:
            out[s] = rows
    return out


def suggest_groups(d, lines):
    """Annexure rows taken from the sheet's OWN structure, never invented: a
    block ends at a sub-total row ("Cost of ...", "TOTAL ...", "SUB TOTAL
    ..."); its name is the block's first heading row. The tail after the last
    sub-total is its own block. A service with no sub-total rows is one block.
    Only blocks that contain a billable line are kept."""
    by_sheet = {}
    for ln in lines:
        if ln["sheet"] and ln["src_row"]:
            by_sheet.setdefault(ln["sheet"], []).append(ln["src_row"])
    rowsets = _sheet_rows(d, list(by_sheet))
    groups = []
    for sheet, billable in by_sheet.items():
        rows = rowsets.get(sheet)
        if not rows:
            continue
        billable = sorted(billable)
        ends = []
        for i, r in enumerate(rows[1:], start=2):
            unit = r[3] if len(r) > 3 else None
            text = r[2] if len(r) > 2 else None
            if unit in (None, "") and isinstance(text, str) and _SUBTOTAL.match(text):
                ends.append(i)
        start = 2
        blocks = []
        for e in ends:
            blocks.append((start, e))
            start = e + 1
        blocks.append((start, max(billable[-1], start)))
        for a, b in blocks:
            if not any(a <= r <= b for r in billable):
                continue
            name, sr = "", ""
            for i in range(a, min(b, len(rows)) + 1):
                r = rows[i - 1]
                text = r[2] if len(r) > 2 else None
                if (r[3] if len(r) > 3 else None) in (None, "") and isinstance(text, str) and text.strip() \
                        and not _SUBTOTAL.match(text) and r[0] != "Overhead" \
                        and not re.match(r"\s*notes?\b", text, re.I):
                    name = re.sub(r"\s+", " ", text.split("\n")[0]).strip()[:90]
                    ref = r[1] if len(r) > 1 else None
                    sr = str(ref).strip() if ref is not None and len(str(ref).strip()) <= 4 else ""
                    break
            groups.append({"sr": sr, "name": name or "Items", "sheet": sheet, "from": a, "to": b, "wo": None})
    return groups


def _groups_for(d, store, service, lines):
    saved = store["groups"].get(service)
    if saved:
        return saved, True
    return suggest_groups(d, lines), False


def _group_of(groups, ln):
    for i, g in enumerate(groups):
        if ln["src_row"] and g.get("from") <= ln["src_row"] <= g.get("to") and (not g.get("sheet") or g["sheet"] == ln["sheet"]):
            return i
    return None


# ----------------------------------------------------------------- compute
def _prev(store, service, stage, exclude_id):
    """{item_code: (qty, amount)} already billed for one stage in the LOCKED
    RAs of this service (from their snapshots -- never recomputed)."""
    prev = {}
    for r in store["ras"]:
        if r["service"] != service or r["status"] != "locked" or r["id"] == exclude_id:
            continue
        if stage not in STAGES[r["series"]]:
            continue
        for row in r["snap"]["lines"]:
            c = row.get(stage)
            if c and c.get("this") is not None:
                q, a = prev.get(row["item_code"], (0.0, 0.0))
                prev[row["item_code"]] = (q + c["this"], a + (c.get("amount") or 0.0))
    return prev


def _accepted(ra, code, stage):
    """True when the person accepted, in THIS draft, billing above the BOQ qty for this line and stage."""
    return bool((((ra.get("accept_excess") or {}).get(code)) or {}).get(stage))


def _fmt_ra(ra):
    return f"{ra['service']} · {SERIES_NAME[ra['series']]} RA {ra['no']}"


def _ra_summary(ra, this_total=None):
    return {"id": ra["id"], "service": ra["service"], "base": _base(ra["service"]), "package": _uname(ra["service"]),
            "series": ra["series"], "no": ra["no"], "label": _fmt_ra(ra), "status": ra["status"], "cutoff": ra.get("cutoff"),
            "created": ra.get("created"), "locked_at": ra.get("locked_at"),
            "this_total": this_total, "stages": STAGES[ra["series"]],
            "inv_no": ra.get("inv_no") or "", "inv_date": ra.get("inv_date") or ""}


# ------------------------------------------------- Tax Invoice cover: details
# Company / client / tax details live in meta (typed once in Billing setup ->
# Invoice details); the invoice no. and date belong to each RA. Whatever is not
# filled in stays EMPTY on the sheet -- the app reports it, it never guesses it.
_META_TEXT = ("client", "contractor", "wo_no", "client_address", "place_of_supply", "client_gstin",
              "client_state", "client_state_code", "project_name", "supplier_gstin", "supplier_state",
              "supplier_state_code", "supplier_pan", "supplier_category")
_UPPER = ("client_gstin", "supplier_gstin", "supplier_pan")
# label shown to the user for each detail the Tax Invoice sheet needs
_NEEDED = [("contractor", "Your company name"), ("supplier_gstin", "Your GSTIN"), ("supplier_state", "Your state"),
           ("supplier_state_code", "Your state code"), ("supplier_pan", "Your PAN"),
           ("client", "Client company"), ("client_address", "Client address"), ("place_of_supply", "Place of supply"),
           ("client_gstin", "Client GSTIN"), ("client_state", "Client state"), ("client_state_code", "Client state code"),
           ("project_name", "Project name"), ("wo_no", "W/O number")]


# Your own company's details are the same on every project, so what was typed on one project is offered
# (never silently applied) on the next: kept in ONE small file next to the projects folder. Only company
# identity is shared -- client, W/O, project name and GST % belong to a project and are never carried over.
_PROFILE_KEYS = ("contractor", "supplier_gstin", "supplier_state", "supplier_state_code", "supplier_pan", "supplier_category")


def _profile_path():
    return sp.PROJECTS.parent / "billing_profile.json"


def _profile_load():
    p = sp._read_json(_profile_path(), {}) or {}
    return {"fields": {k: v for k, v in (p.get("fields") or {}).items() if k in _PROFILE_KEYS and str(v or "").strip()},
            "from": p.get("from")}


def _profile_remember(slug, meta):
    """Keep the non-blank company details of the project just saved (a blank never erases what is kept)."""
    mine = {k: meta.get(k) for k in _PROFILE_KEYS if str(meta.get(k) or "").strip()}
    if not mine:
        return
    cur = _profile_load()
    cur["fields"].update(mine)
    _profile_path().write_text(json.dumps({"fields": cur["fields"], "from": sp._slugify(slug)}, indent=1), encoding="utf-8")


def _profile_offer(slug, store):
    """The kept company details, when this project still lacks some of them and has not said 'none'; else None."""
    m = store["meta"]
    if m.get("profile_skip"):
        return None
    prof = _profile_load()
    if not prof["fields"]:
        return None
    if all(str(m.get(k) or "").strip() or k not in prof["fields"] for k in _PROFILE_KEYS):
        return None
    return {"fields": prof["fields"], "from": prof["from"]}


def _wo_no(meta, unit):
    """The W/O number printed for one unit: its own Work Order's number if typed, else the project's."""
    return str(((meta.get("wo_of") or {}).get(unit)) or "").strip() or meta.get("wo_no")


def _meta_missing(store):
    """The project-level details the Tax Invoice sheet still lacks (labels, in form order)."""
    m = store["meta"]
    out = [lab for k, lab in _NEEDED if not str(m.get(k) or "").strip()]
    if m.get("sgst") is None or m.get("cgst") is None:
        out.append("GST % (SGST / CGST)")
    return out


def _cover_missing(store, ra):
    """What the Tax Invoice sheet of this RA will show empty (labels, in form order)."""
    m = store["meta"]
    out = _meta_missing(store)
    if str(((m.get("wo_of") or {}).get(ra["service"])) or "").strip() and "W/O number" in out:
        out.remove("W/O number")                 # this Work Order has its own number
    if not str(((m.get("sac") or {}).get(ra["service"])) or "").strip():
        out.append(f"SAC code of {ra['service']}")
    if not str(ra.get("inv_no") or "").strip():
        out.append("Invoice no.")
    if not ra.get("inv_date"):
        out.append("Invoice date")
    return out


def _bill(slug, d, store, ra):
    """The full LIVE bill for a draft RA (a locked RA returns its snapshot)."""
    service = ra["service"]
    stages = STAGES[ra["series"]]
    setup = store["setup"].get(service) or {}
    lines = _lines(d, service)
    groups, confirmed = _groups_for(d, store, service, lines)
    inst = _install_qty(d, service) if "install" in stages else {}
    sup, run, stk = ({}, None, {})
    if "supply" in stages:
        ra_no = ra["no"] if ra.get("no") else _open_no(store, service)
        sup, run, stk = _supply_qty(slug, d, store, service, lines, ra_no)
    prevs = {s: _prev(store, service, s, ra["id"]) for s in stages}

    rows, warnings = [], []
    unfilled = {s: 0 for s in stages}
    zero = lambda: {"wo": 0.0, "prev": 0.0, "this": 0.0}
    gt = [{s: zero() for s in stages} for _ in groups]
    ot = {s: zero() for s in stages}
    tot = {s: zero() for s in stages}
    wo_g, wo_other = [0.0] * len(groups), 0.0
    removed = set(ra.get("removed") or ())              # lines the person took out of THIS draft (see remove_line)
    gone = []
    for ln in lines:
        code = ln["item_code"]
        gi = _group_of(groups, ln)
        if gi is None:
            wo_other += ln["rate"] * ln["boq_qty"]
        else:
            wo_g[gi] += ln["rate"] * ln["boq_qty"]
        if code in removed:                             # not billed in this draft: no quantity, no amount, not counted as "unfilled"
            gone.append({k: ln[k] for k in ("item_code", "desc", "unit")})
            continue
        row = {k: ln[k] for k in ("item_code", "desc", "unit", "boq_qty", "rate", "internal_ref", "src_row", "sheet")}
        row["group"] = gi
        active = False
        for stage in stages:
            pct = _stage_pct(setup, code, stage)
            pq, pa = prevs[stage].get(code, (0.0, 0.0))
            ov = ((ra.get("overrides") or {}).get(code) or {}).get(stage)
            # The cap is the BOQ qty. Extra that an EARLIER RA already billed stays billed (so the next RA never goes negative),
            # and a line the person ACCEPTED in this draft has no cap: nothing goes above the BOQ without that explicit step.
            acc = _accepted(ra, code, stage)
            cap = float("inf") if acc else max(ln["boq_qty"], pq)
            have = None
            if stage == "install":
                have = inst.get(code, 0.0)
                utd, source, note = min(have, cap), "progress", "Work done, from Site Progress"
                if have > cap + 1e-9:
                    warnings.append({"item_code": code, "stage": stage, "over": ov is None, "have": have, "cap": cap, "source": "progress", "msg":
                        f"{ln['desc'][:70]}: Site Progress shows {have:g} {ln['unit']} done, {have - ln['boq_qty']:g} {ln['unit']} above the BOQ qty of {ln['boq_qty']:g}. Billed only up to the BOQ qty."})
            else:
                q, source, note = sup.get(code, (None, "manual", ""))
                have = q
                utd = None if q is None else min(q, cap)
                if source == "challan" and q is not None and q > cap + 1e-9:
                    warnings.append({"item_code": code, "stage": stage, "over": ov is None, "have": q, "cap": cap, "source": "challan", "msg":
                        f"{ln['desc'][:70]}: challans total {q:g} {ln['unit']} but the BOQ qty is {ln['boq_qty']:g}. Billed only up to the BOQ qty."})
            if ov is not None:
                this, reason = ov["qty"], ov.get("reason", "")
                if source == "manual":
                    source = "manual-entered"
            else:
                this = None if utd is None else round(utd - pq, 4)
                reason = ""
            amount = None if (this is None or pct is None) else ln["rate"] * this * pct / 100.0
            c = {"pct": pct, "prev": round(pq, 4), "prev_amount": pa, "utd": utd, "this": this, "amount": amount,
                 "source": source, "overridden": ov is not None, "reason": reason, "note": note}
            if this is not None and pq + this > max(ln["boq_qty"], pq) + 1e-9:       # qty above the BOQ that THIS bill carries
                xq = round(pq + this - max(ln["boq_qty"], pq), 4)
                c["extra"] = xq
                c["extra_amount"] = None if pct is None else ln["rate"] * xq * pct / 100.0
            if acc:
                c["accepted"] = True
            if stage == "supply" and code in stk:
                c["stock_qty"] = stk[code]
            row[stage] = c
            tgt = gt[gi][stage] if gi is not None else ot[stage]
            tgt["prev"] += pa
            tot[stage]["prev"] += pa
            if amount is not None:
                tgt["this"] += amount
                tot[stage]["this"] += amount
            if this is None:
                if source == "manual":
                    unfilled[stage] += 1
            elif this != 0 or ov is not None:
                active = True
            if this is not None and this < 0 and ov is None:
                warnings.append({"item_code": code, "stage": stage, "msg":
                    f"{ln['desc'][:70]}: {pq:g} {ln['unit']} was billed earlier but only {utd:g} shows now ({this:g})"})
        if active:
            rows.append(row)
    gout = []
    for i, g in enumerate(groups):
        gout.append({**g, "wo_calc": wo_g[i], "wo_amount": g["wo"] if g.get("wo") is not None else wo_g[i],
                     "stages": {s: {"prev": gt[i][s]["prev"], "this": gt[i][s]["this"],
                                    "total": gt[i][s]["prev"] + gt[i][s]["this"]} for s in stages}})
    other = None
    if wo_other or any(ot[s]["this"] or ot[s]["prev"] for s in stages):
        other = {"wo_amount": wo_other, "stages": {s: {"prev": ot[s]["prev"], "this": ot[s]["this"],
                                                       "total": ot[s]["prev"] + ot[s]["this"]} for s in stages}}
    this_total = sum(tot[s]["this"] for s in stages)
    # quantity above the BOQ: what is NOT billed yet (waiting for the person's accept) and what IS billed as extra
    excess, extra_lines = [], []
    for w_ in warnings:
        if not w_.get("over"):
            continue
        ln_ = next(x for x in lines if x["item_code"] == w_["item_code"])
        pct_ = _stage_pct(setup, w_["item_code"], w_["stage"])
        ex = round(w_["have"] - w_["cap"], 4)
        excess.append({"item_code": w_["item_code"], "stage": w_["stage"], "desc": ln_["desc"], "unit": ln_["unit"],
                       "boq_qty": ln_["boq_qty"], "have": w_["have"], "extra": ex, "source": w_["source"], "rate": ln_["rate"],
                       "pct": pct_, "amount": None if pct_ is None else ln_["rate"] * ex * pct_ / 100.0})
    for r_ in rows:
        for s_ in stages:
            c_ = r_.get(s_) or {}
            if c_.get("extra"):
                extra_lines.append({"item_code": r_["item_code"], "stage": s_, "desc": r_["desc"], "unit": r_["unit"],
                                    "boq_qty": r_["boq_qty"], "extra": c_["extra"], "amount": c_.get("extra_amount"),
                                    "accepted": bool(c_.get("accepted"))})
    extra_total = sum(x["amount"] or 0.0 for x in extra_lines)
    return {"ra": _ra_summary(ra, this_total), "excess": excess, "extra_lines": extra_lines, "extra_total": extra_total,
            "pct": {s: _num(setup.get(s + "_pct")) for s in stages},
            "lines": rows, "groups": gout, "groups_confirmed": confirmed, "other": other,
            "totals": {s: {**tot[s], "total": tot[s]["prev"] + tot[s]["this"]} for s in stages},
            "this_total": this_total, "unfilled": unfilled, "warnings": warnings, "run": run,
            "removed": gone,
            "contract_value": sum(ln["rate"] * ln["boq_qty"] for ln in lines)}


def _stage_this(bill):
    """{stage: this-bill amount} -- lets the lists say 'Installation Rs 0' without opening every RA."""
    return {st: t.get("this") or 0.0 for st, t in (bill.get("totals") or {}).items()}


def _get_bill(slug, d, store, ra):
    if ra["status"] == "locked":
        b = dict(ra["snap"])
        b["ra"] = _ra_summary(ra, b["this_total"])
        return b
    return _bill(slug, d, store, ra)


# ------------------------------------------------------------------ setup
def _need_setup(store, service, series):
    s = store["setup"].get(service)
    if not s or not s.get("mode"):
        raise HTTPException(409, f"{service} has no billing setup yet. Set its payment term in Billing setup first.")
    for st in STAGES[series]:
        if _num(s.get(st + "_pct")) is None:
            raise HTTPException(409, f"{service}: {STAGE_NAME[st]} % is not set. Set it in Billing setup; no % is guessed.")


def _bases(d):
    """The BOQ services (as Site Progress knows them)."""
    return list(_remember("bases", d, None, lambda: _bases_raw(d)))


def _bases_raw(d):
    items = sp._load_boq(d)
    if items.empty or "service" not in items.columns:
        return []
    return list(dict.fromkeys(items.service.tolist()))


def _services(d):
    """The billing units, in BOQ order: a service, or -- when it is split into Work Orders -- one unit per Work Order."""
    pk = _pkdefs(d)
    out = []
    for b in _bases(d):
        plist = pk.get(b)
        out += [_ukey(b, p["name"]) for p in plist] if plist else [b]
    return out


def _mode_series(mode):
    """The bill types a service's default mode puts first (the home screen's 'ready' figure)."""
    return ["together"] if mode == "together" else ["supply", "install"]


def _next_no(store, service, series):
    """Number the next RA of this series would get: 1 + the highest number on any stage it carries."""
    stages = set(STAGES[series])
    return 1 + max([r["no"] for r in store["ras"]
                    if r["service"] == service and stages & set(STAGES[r["series"]])] or [0])


def _open_draft(store, service, series, exclude_id=None):
    """A draft of this service that carries one of the same stages (only one draft per stage), or None."""
    stages = set(STAGES[series])
    return next((r for r in store["ras"] if r["service"] == service and r["status"] == "draft"
                 and r["id"] != exclude_id and stages & set(STAGES[r["series"]])), None)


@router.get("/{slug}/setup")
def get_setup(slug: str):
    d = sp._need(slug)
    store = _load(d)
    out = []
    for svc in _services(d):
        lines = _lines(d, svc)
        s = store["setup"].get(svc) or {}
        groups, confirmed = _groups_for(d, store, svc, lines)
        out.append({"service": svc, "base": _base(svc), "package": _uname(svc),
                    "wo_no": ((store["meta"].get("wo_of") or {}).get(svc) or ""),
                    "mode": s.get("mode"), "supply_pct": _num(s.get("supply_pct")),
                    "install_pct": _num(s.get("install_pct")), "line_pct": s.get("line_pct") or {},
                    "supply_source": s.get("supply_source") or "stock",
                    "auto_draft": s.get("auto_draft", True) is not False,
                    "challans": len(_challans(store, svc)),
                    "lines": len(lines), "contract_value": sum(l["rate"] * l["boq_qty"] for l in lines),
                    "has_ras": any(r["service"] == svc for r in store["ras"]),
                    "groups": groups, "groups_confirmed": confirmed,
                    "refs_ok": bool(lines) and all(l["_ref_ok"] for l in lines)})
    return {"services": out, "meta": store["meta"], "invoice_missing": _meta_missing(store),
            "profile": _profile_offer(slug, store), "bases": _bases_view(d, store)}


@router.post("/{slug}/setup")
def save_setup(slug: str, payload: dict):
    """Body: {service, mode: "separate"|"together" (the default bill type offered on a new RA; it can be
    changed any time and never blocks a bill type), supply_pct?, install_pct?,
    supply_source?: "challan"|"stock", line_pct?: {code: {supply?, install?}}}.
    A % left out/blank stays NOT SET."""
    d = sp._need(slug)
    store = _load(d)
    svc = payload.get("service")
    if svc not in _services(d):
        raise HTTPException(400, "this service is not in this project")
    mode = payload.get("mode")
    if mode not in ("separate", "together"):
        raise HTTPException(400, "mode must be 'separate' or 'together'")
    cur = store["setup"].get(svc) or {}
    sup, ins = _pct(payload.get("supply_pct"), "Supply %"), _pct(payload.get("install_pct"), "Installation %")
    if sup is not None and ins is not None and sup + ins > 100.0001:
        raise HTTPException(400, "Supply % + Installation % cannot exceed 100")
    lp = {}
    for code, v in (payload.get("line_pct") or {}).items():
        e = {}
        for st in ("supply", "install"):
            x = _pct((v or {}).get(st), f"Line {code} {STAGE_NAME[st]} %")
            if x is not None:
                e[st] = x
        if e:
            lp[str(code)] = e
    source = payload.get("supply_source") or cur.get("supply_source") or "stock"
    if source not in ("challan", "stock"):
        raise HTTPException(400, "supply_source must be 'challan' or 'stock'")
    if source != (cur.get("supply_source") or "stock") and _last_locked_no(store, svc) > 0:
        raise HTTPException(409, f"{svc} already has a locked Supply RA, so where its supply quantity comes from cannot be changed.")
    auto = payload["auto_draft"] if "auto_draft" in payload else cur.get("auto_draft", True)
    if not isinstance(auto, bool):
        raise HTTPException(400, "auto_draft must be true or false")
    store["setup"][svc] = {"mode": mode, "supply_pct": sup, "install_pct": ins, "line_pct": lp,
                           "supply_source": source, "auto_draft": auto}
    _save(d, store)
    return {"ok": True, "service": svc}


@router.post("/{slug}/meta")
def save_meta(slug: str, payload: dict):
    """Annexure header text. Anything left blank stays blank."""
    d = sp._need(slug)
    store = _load(d)
    m = store["meta"]
    for k in _META_TEXT:
        if k in payload:
            v = str(payload[k] or "").strip()
            m[k] = v.upper() if k in _UPPER else v
    # saving every company field blank = "this project starts without the kept company details"
    if all(k in payload for k in _PROFILE_KEYS):
        if all(not str(m.get(k) or "").strip() for k in _PROFILE_KEYS):
            m["profile_skip"] = True
        else:
            m.pop("profile_skip", None)
    elif any(str(m.get(k) or "").strip() for k in _PROFILE_KEYS):
        m.pop("profile_skip", None)
    for k in ("sgst", "cgst"):          # GST %: blank = not set (never a default rate)
        if k in payload:
            v = payload[k]
            if v is None or str(v).strip() == "":
                m[k] = None
            else:
                x = _num(v)
                if x is None or x < 0 or x > 50:
                    raise HTTPException(400, f"{k.upper()} % must be a number from 0 to 50")
                m[k] = x
    for k in ("work", "sac", "inv_desc", "wo_of"):   # per service / Work Order: Work text, SAC code, invoice line text, W/O no.
        if isinstance(payload.get(k), dict):
            m.setdefault(k, {}).update({s: str(v or "").strip() for s, v in payload[k].items()})
    _save(d, store)
    if any(k in payload for k in _PROFILE_KEYS):
        _profile_remember(slug, m)
    return {"ok": True, "meta": m}


@router.post("/{slug}/ra/{ra_id}/invoice")
def save_ra_invoice(slug: str, ra_id: str, payload: dict):
    """The invoice no. and date of one RA (allowed on a locked RA too: the invoice is
    raised after the bill is locked). Blank clears."""
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if "no" in payload:
        ra["inv_no"] = str(payload["no"] or "").strip()
    if "date" in payload:
        raw = payload["date"]
        if raw in (None, ""):
            ra["inv_date"] = ""
        else:
            iso = _iso_date(raw)
            if not iso:
                raise HTTPException(400, "The invoice date is not a valid date.")
            ra["inv_date"] = iso
    _save(d, store)
    return {"ok": True, "inv_no": ra.get("inv_no") or "", "inv_date": ra.get("inv_date") or "",
            "missing": _cover_missing(store, ra)}


@router.post("/{slug}/groups/{service}")
def save_groups(slug: str, service: str, payload: dict):
    """Annexure rows for a service. Body {groups: [{sr, name, sheet, from, to,
    wo?}]}; {groups: []} goes back to the sheet-derived suggestion."""
    d = sp._need(slug)
    store = _load(d)
    out = []
    for g in payload.get("groups") or []:
        try:
            a, b = int(g["from"]), int(g["to"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "every group needs a 'from' and a 'to' row")
        if a < 1 or b < a:
            raise HTTPException(400, "the group rows are invalid")
        wo = _num(g.get("wo"))
        out.append({"sr": str(g.get("sr") or "").strip(), "name": str(g.get("name") or "").strip() or "Items",
                    "sheet": g.get("sheet") or None, "from": a, "to": b, "wo": wo})
    if out:
        store["groups"][service] = out
    else:
        store["groups"].pop(service, None)
    _save(d, store)
    return {"ok": True, "groups": out}


# ------------------------------------------------------- Work Orders (packages)
# A service can carry several Work Orders (Electrical: "Excl. point wiring" and "Only point wiring").
# Each Work Order is its own billing unit "Service › Name" with its own RA numbers, payment term, annexure
# groups, challans and invoice, so both of them can have an RA 1. Which BOQ lines belong to which Work Order is
# decided by the annexure GROUPS the engineer ticks for it (their sheet rows); every line not ticked for a named
# Work Order falls in the one remainder Work Order. Nothing is inferred from the item text.
_BAD_NAME = re.compile(r"[/\\?#%›\[\]:*]")


def _bases_view(d, store):
    """[{base, split, units}] for every BOQ service (the home screen groups a split service's units)."""
    pk = store.get("packages") or {}
    out = []
    for b in _bases(d):
        plist = pk.get(b) or []
        out.append({"base": b, "split": bool(plist),
                    "units": [_ukey(b, p["name"]) for p in plist] if plist else [b]})
    return out


def _unit_state(store, unit):
    return {"ras": sum(1 for r in store["ras"] if r["service"] == unit),
            "challans": len(store["challans"].get(unit, [])) + len(((store.get("wire") or {}).get(unit) or {}).get("challans", []))}


def _group_view(d, store, base, blines):
    """The annexure groups of the whole service (from its saved groups when it is not split, else from the sheet's
    own structure) with the Work Order each one sits in. Used by the 'Work Orders' editor."""
    plist = store["packages"].get(base) or []
    saved = None if plist else store["groups"].get(base)
    groups = saved or suggest_groups(d, blines)
    out = []
    for g in groups:
        mine = [l for l in blines if l["src_row"] and g["from"] <= l["src_row"] <= g["to"]
                and (not g.get("sheet") or g["sheet"] == l["sheet"])]
        names = {(_pkg_of(plist, l) if plist else None) for l in mine}
        out.append({"sr": g.get("sr") or "", "name": g.get("name") or "Items", "sheet": g.get("sheet"),
                    "from": g["from"], "to": g["to"], "lines": len(mine),
                    "value": sum(l["rate"] * l["boq_qty"] for l in mine),
                    "pkg": (next(iter(names)) if len(names) == 1 else None) if plist else None,
                    "mixed": len(names) > 1})
    return out


@router.get("/{slug}/packages/{base}")
def get_packages(slug: str, base: str):
    d = sp._need(slug)
    if base not in _bases(d):
        raise HTTPException(404, "this service is not in this project")
    store = _load(d)
    plist = store["packages"].get(base) or []
    blines = _base_lines(d, base)
    return {"base": base, "split": bool(plist), "groups": _group_view(d, store, base, blines),
            "items": [{"name": p["name"], "rest": bool(p.get("rest")), "unit": _ukey(base, p["name"]),
                       **_unit_state(store, _ukey(base, p["name"]))} for p in plist],
            "plain": _unit_state(store, base)}


def _restrict_snap(snap, ulines):
    """A locked bill moved into one Work Order keeps only the annexure groups that hold that Work Order's lines."""
    groups = snap.get("groups") or []
    keep = [i for i in range(len(groups)) if any(_group_of(groups, l) == i for l in ulines)]
    remap = {old: new for new, old in enumerate(keep)}
    snap["groups"] = [groups[i] for i in keep]
    for row in snap.get("lines") or []:
        gi = row.get("group")
        row["group"] = remap.get(gi) if gi is not None else None
    orph = [l for l in ulines if _group_of(groups, l) is None]
    if snap.get("other"):
        if orph:
            snap["other"]["wo_amount"] = sum(l["rate"] * l["boq_qty"] for l in orph)
        else:
            snap["other"] = None
    snap["contract_value"] = sum(l["rate"] * l["boq_qty"] for l in ulines)


def _rekey_all(store, mapping):
    """Rename units everywhere in one pass (so two names can swap safely)."""
    if not mapping:
        return
    for k in ("setup", "groups", "challans", "wire", "auto_skip"):
        if isinstance(store.get(k), dict):
            store[k] = {mapping.get(a, a): v for a, v in store[k].items()}
    for r in store["ras"]:
        r["service"] = mapping.get(r["service"], r["service"])
    for k in ("sac", "inv_desc", "work", "wo_of"):
        if isinstance(store["meta"].get(k), dict):
            store["meta"][k] = {mapping.get(a, a): v for a, v in store["meta"][k].items()}


@router.post("/{slug}/packages/{base}")
def save_packages(slug: str, base: str, payload: dict):
    """Body {items: [{name, was?, rest?: true, groups: [{sheet, from, to}]}]}: the Work Orders of one service.
    Exactly one item is the remainder (rest: true; it takes every group not ticked for another). Fewer than two items
    puts the service back to a single Work Order. A Work Order that already has RAs or challans keeps its lines: they
    are never moved (that would change a submitted bill)."""
    d = sp._need(slug)
    if base not in _bases(d):
        raise HTTPException(404, "this service is not in this project")
    store = _load(d)
    old = store["packages"].get(base) or []
    old_units = [_ukey(base, p["name"]) for p in old] or [base]
    items = payload.get("items") or []

    # ---- back to one Work Order
    if len(items) < 2:
        if not old:
            return {"ok": True, "units": [base], "removed_drafts": 0}
        busy = [u for u in old_units if any(_unit_state(store, u).values())]
        if busy:
            raise HTTPException(409, f"{_unit_text(busy[0])} already has RAs or challans, so the Work Orders cannot be merged back into one.")
        first = store["setup"].get(old_units[0])
        for u in old_units:
            for k in ("setup", "groups", "challans", "auto_skip"):
                store.get(k, {}).pop(u, None)
        if first:
            store["setup"][base] = first
        store["packages"].pop(base, None)
        _save(d, store)
        return {"ok": True, "units": [base], "removed_drafts": 0}

    # ---- check the new definition
    newlist, seen = [], set()
    rest_n = 0
    for it in items:
        name = re.sub(r"\s+", " ", str(it.get("name") or "")).strip()
        if not name:
            raise HTTPException(400, "Every Work Order needs a name.")
        if len(name) > 40 or _BAD_NAME.search(name):
            raise HTTPException(400, f"“{name}”: use up to 40 characters, without / \\ ? # % : * [ ] or ›.")
        if name.lower() in seen:
            raise HTTPException(400, f"Two Work Orders are called “{name}”. Give each its own name.")
        seen.add(name.lower())
        if it.get("rest"):
            rest_n += 1
            newlist.append({"name": name, "rest": True, "was": it.get("was")})
            continue
        rngs = []
        for g in it.get("groups") or []:
            try:
                a, b = int(g["from"]), int(g["to"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "every ticked group needs a 'from' and a 'to' row")
            if a < 1 or b < a:
                raise HTTPException(400, "the group rows are invalid")
            rngs.append({"sheet": g.get("sheet") or None, "from": a, "to": b})
        if not rngs:
            raise HTTPException(400, f"“{name}” has no group ticked. Tick the groups that belong to it, or make it the remainder.")
        newlist.append({"name": name, "ranges": rngs, "was": it.get("was")})
    if rest_n != 1:
        raise HTTPException(400, "Exactly one Work Order must be the remainder (it takes every group not ticked for another).")

    blines = _base_lines(d, base)
    clean = [{k: v for k, v in p.items() if k != "was"} for p in newlist]
    codes = {p["name"]: set() for p in clean}
    for ln in blines:
        hits = [p["name"] for p in clean if not p.get("rest") and any(
            ln["src_row"] and r["from"] <= ln["src_row"] <= r["to"] and (not r.get("sheet") or r["sheet"] == ln["sheet"])
            for r in p["ranges"])]
        if len(hits) > 1:
            raise HTTPException(400, f"A group is ticked for both “{hits[0]}” and “{hits[1]}”.")
        codes[_pkg_of(clean, ln)].add(ln["item_code"])
    for nm, cs in codes.items():
        if not cs:
            raise HTTPException(400, f"“{nm}” would have no billable line. Tick at least one group that has billable items.")

    # ---- who is who: existing Work Orders keep their RAs, challans and setup (`was` = the name they had)
    new_units = [_ukey(base, p["name"]) for p in clean]
    rename = {}
    if old:
        old_names = {p["name"] for p in old}
        for p in newlist:
            if p.get("was") in old_names:
                rename[_ukey(base, p["was"])] = _ukey(base, p["name"])
        if len(set(rename.values())) != len(rename):
            raise HTTPException(400, "Two Work Orders point to the same earlier one.")
        for u in old_units:
            st = _unit_state(store, u)
            if u not in rename and any(st.values()):
                raise HTTPException(409, f"{_unit_text(u)} already has RAs or challans, so it cannot be removed.")
            if u in rename:
                was_codes = {l["item_code"] for l in _lines(d, u)}
                if any(st.values()) and was_codes != codes[_uname(rename[u])]:
                    raise HTTPException(409, f"{_unit_text(u)} already has RAs or challans, so the groups in it cannot change. "
                                             "Delete or reopen those first.")
        for p in newlist:                       # a NEW Work Order may only take lines from units that bill nothing yet
            if p.get("was") in old_names:
                continue
            taken = codes[p["name"]]
            for u in old_units:
                if any(_unit_state(store, u).values()) and taken & {l["item_code"] for l in _lines(d, u)}:
                    raise HTTPException(409, f"Some of these groups already sit in {_unit_text(u)}, which has RAs or challans.")
    plain_state = _unit_state(store, base) if not old else {"ras": 0, "challans": 0}
    # Only real wire blocks the first split: a wire challan, or metres already given to point lines. The service's wire
    # record can exist without either (the source was chosen, a challan was typed and deleted, "Save metres" was pressed
    # with nothing given): that holds no data, so it must not stop the split. It is dropped below, as the base unit goes away.
    wire_base = (store.get("wire") or {}).get(base) or {}
    if not old and (wire_base.get("challans") or wire_base.get("alloc")):
        n_w = len(wire_base.get("challans") or [])
        what = f"{n_w} wire challan{'s' if n_w != 1 else ''}" if n_w else "wire metres given to point lines"
        raise HTTPException(409, f"{base} already has {what}, so it cannot be split into Work Orders now. "
                                 "Split it first, then enter the wire in the Work Order that holds the point wiring.")

    # ---- first split of a service that already has RAs / challans: plan everything before touching the store
    plan_ras, drop_ids, plan_ch = {}, [], {}
    if not old and any(plain_state.values()):
        unit_of = {c: _ukey(base, p["name"]) for p in clean for c in codes[p["name"]]}
        rest_unit = next(_ukey(base, p["name"]) for p in clean if p.get("rest"))
        for r in store["ras"]:
            if r["service"] != base:
                continue
            if r["status"] == "draft":
                if r.get("overrides") or r.get("inv_no") or r.get("inv_date"):
                    raise HTTPException(409, f"{_fmt_ra(r)} is an open draft with typed quantities or invoice details. "
                                             "Mark it invoiced or delete it first; then split the service.")
                drop_ids.append(r["id"])
                continue
            hit = {unit_of[row["item_code"]] for row in r["snap"]["lines"] if row["item_code"] in unit_of}
            if len(hit) > 1:
                raise HTTPException(409, f"{_fmt_ra(r)} (invoiced) has lines of more than one Work Order, so it cannot be moved into one. "
                                         "Reopen and delete it first, or keep this service as one Work Order.")
            plan_ras[r["id"]] = next(iter(hit)) if hit else rest_unit
        for c in store["challans"].get(base, []):
            for u in new_units:
                sub = {k: q for k, q in c["qty"].items() if unit_of.get(k) == u}
                if sub:
                    plan_ch.setdefault(u, []).append({**c, "qty": sub})

    # ---- apply
    removed = len(drop_ids)
    if drop_ids:
        store["ras"] = [r for r in store["ras"] if r["id"] not in drop_ids]
    _rekey_all(store, rename)
    for u in old_units:
        if old and u not in rename:                      # a Work Order taken out (it bills nothing, checked above)
            for k in ("setup", "groups", "challans", "auto_skip"):
                store.get(k, {}).pop(u, None)
    store["packages"][base] = [{k: v for k, v in p.items()} for p in clean]
    if not old:                                          # first split: what the service had moves into its Work Orders
        by_unit_lines = {_ukey(base, p["name"]): [l for l in blines if _pkg_of(clean, l) == p["name"]] for p in clean}
        for r in store["ras"]:
            if r["id"] in plan_ras:
                r["service"] = plan_ras[r["id"]]
                _restrict_snap(r["snap"], by_unit_lines[r["service"]])
        base_setup = store["setup"].pop(base, None)
        base_groups = store["groups"].pop(base, None)
        store["challans"].pop(base, None)
        (store.get("wire") or {}).pop(base, None)        # the empty wire record of the unit that no longer exists
        (store.get("auto_skip") or {}).pop(base, None)
        for u in new_units:
            if base_setup is not None:
                store["setup"][u] = json.loads(json.dumps(base_setup))
            if plan_ch.get(u):
                store["challans"][u] = plan_ch[u]
            if base_groups:
                ul = by_unit_lines[u]
                gs = []
                for g in base_groups:
                    mine = [l for l in ul if _group_of([g], l) == 0]
                    if not mine:
                        continue
                    full = [l for l in blines if _group_of([g], l) == 0]
                    gs.append({**g, "wo": g.get("wo") if len(full) == len(mine) else None})
                if gs:
                    store["groups"][u] = gs
            for k in ("sac", "inv_desc", "work"):
                m = store["meta"].get(k)
                if isinstance(m, dict) and base in m and u not in m:
                    m[u] = m[base]
    _save(d, store)
    return {"ok": True, "units": new_units, "removed_drafts": removed}


# -------------------------------------------------------------------- RAs
def _ready(slug, d, store, service, all_types=False):
    """What a NEW RA would carry, per series, without creating anything. `all_types`: every bill type
    (Supply, Installation, Together) in that order; otherwise only the types the service's default mode puts first."""
    s = store["setup"].get(service)
    out = {}
    if not s or not s.get("mode"):
        return out
    for series in (("supply", "install", "together") if all_types else _mode_series(s["mode"])):
        try:
            _need_setup(store, service, series)
        except HTTPException as e:
            out[series] = {"ready": None, "blocked": e.detail}
            continue
        ghost = {"id": "_preview", "service": service, "series": series, "no": _next_no(store, service, series),
                 "status": "draft", "overrides": {}}
        b = _bill(slug, d, store, ghost)
        od = _open_draft(store, service, series)
        out[series] = {"ready": b["this_total"], "blocked": None, "has_draft": od is not None,
                       "draft_label": SERIES_NAME[od["series"]] + f" RA {od['no']}" if od else None,
                       "next_no": ghost["no"]}
    return out


@router.get("/{slug}")
def overview(slug: str):
    d = sp._need(slug)
    store = _load(d)
    svcs = _services(d)
    ras, contract, billed, ready_total, notes = [], 0.0, 0.0, 0.0, []
    services = []
    for svc in svcs:
        lines = _lines(d, svc)
        cv = sum(l["rate"] * l["boq_qty"] for l in lines)
        contract += cv
        s = store["setup"].get(svc) or {}
        can = _ready(slug, d, store, svc, all_types=True)          # one pass; the default-mode view is a subset of it
        rdy = {k: can[k] for k in (_mode_series(s["mode"]) if s.get("mode") else ()) if k in can}
        for v in rdy.values():
            if v.get("ready") and not v.get("has_draft"):
                ready_total += max(v["ready"], 0)
        chs = _challans(store, svc)
        held = sum(1 for c in chs if c.get("ra") is None)
        services.append({"service": svc, "base": _base(svc), "package": _uname(svc),
                         "wo_no": ((store["meta"].get("wo_of") or {}).get(svc) or ""),
                         "mode": s.get("mode"), "supply_pct": _num(s.get("supply_pct")),
                         "install_pct": _num(s.get("install_pct")), "contract_value": cv, "lines": len(lines),
                         "ready": rdy, "can": can, "set": bool(s.get("mode")),
                         "supply_source": _source(store, svc), "source_locked": _last_locked_no(store, svc) > 0,
                         "challans": len(chs), "held": held,
                         "wire": _source(store, svc) == "challan" and any(_point_len(l["desc"]) for l in lines),
                         "open_no": _open_no(store, svc) if s.get("mode") else None,
                         "both_view": _combined_view(store, svc)})
        if held:
            notes.append({"kind": "hold", "ra": None,
                          "msg": f"{svc}: {held} challan{'s' if held != 1 else ''} on hold, not in any RA yet. Open Challans to tag {'them' if held != 1 else 'it'} to an RA."})
    for r in sorted(store["ras"], key=lambda r: (r["status"] != "draft", r["service"], r["series"], -r["no"])):
        if r["status"] == "locked":
            billed += r["snap"]["this_total"]
            ras.append(_ra_summary(r, r["snap"]["this_total"]) | {"lines": len(r["snap"]["lines"]), "up_to_date": None,
                                                                   "stage_this": _stage_this(r["snap"]),
                                                                   "extra_n": len(r["snap"].get("extra_lines") or [])})
        else:
            b = _bill(slug, d, store, r)
            for w in b["warnings"]:
                if w.get("over") is None:               # anything but quantity above the BOQ: that one is shown inside the draft
                    notes.append({"kind": "over", "ra": r["id"], "msg": w["msg"]})
            n_un = sum(b["unfilled"].values())
            if n_un:
                notes.append({"kind": "manual", "ra": r["id"],
                              "msg": f"{_fmt_ra(r)}: {n_un} lines need a quantity typed in (not linked to stock, or not safe to compute). Quantities are never guessed."})
            ras.append(_ra_summary(r, b["this_total"]) | {"lines": len(b["lines"]), "up_to_date": None,
                                                          "stage_this": _stage_this(b),
                                                          "over_n": len(b["excess"]), "extra_n": len(b["extra_lines"])})
    # up-to-date per RA = everything locked up to and including it in its stages (+ own draft)
    for r in ras:
        raw = _ra(store, r["id"])
        r["inv_missing"] = _cover_missing(store, raw)
        tot = 0.0
        for o in store["ras"]:
            if o["service"] != r["service"] or o["status"] != "locked":
                continue
            shares = bool(set(STAGES[o["series"]]) & set(STAGES[r["series"]]))
            if o["id"] == r["id"] or (shares and o["no"] < r["no"]) or (r["status"] == "draft" and shares):
                tot += o["snap"]["this_total"]
        r["up_to_date"] = tot + (r["this_total"] if r["status"] == "draft" else 0.0)
    return {"project": sp._slugify(slug), "services": services, "ras": ras, "notes": notes,
            "bases": _bases_view(d, store),
            "pb": {k: {"project": v.get("project"), "name": v.get("name"), "at": v.get("at"), "lines": v.get("lines")}
                   for k, v in (store.get("pb") or {}).items() if _pb_file(d, k).exists()},
            "contract_value": contract, "billed": billed, "ready_total": ready_total,
            "has_boq": bool(svcs)}


@router.get("/{slug}/activity-impact")
def activity_impact(slug: str, service: str, activity: str):
    """Site Progress asks this before deleting an activity AND its recorded work: what would each OPEN draft RA of that
    service become? Computed by the real bill code with those items' work set to zero (nothing is written).
    Invoiced (locked) RAs are snapshots: they never change, so they are only counted."""
    d = sp._need(slug)
    store = _load(d)
    codes = sp._activity_only_codes(d, service, activity)
    drafts, locked = [], 0
    for r in store["ras"]:
        if _base(r["service"]) != service or "install" not in STAGES[r["series"]]:
            continue
        if r["status"] != "draft":
            locked += 1
            continue
        now = _bill(slug, d, store, r)["this_total"]
        tok = _WHATIF_ZERO.set({service: set(codes)})
        try:
            then = _bill(slug, d, store, r)["this_total"]
        finally:
            _WHATIF_ZERO.reset(tok)
        drafts.append({"id": r["id"], "label": _fmt_ra(r), "service": r["service"], "now": now, "then": then})
    return {"items": len(codes), "drafts": drafts, "locked": locked}


@router.get("/{slug}/lines/{service}")
def service_lines(slug: str, service: str):
    """Every billable line of a service (for the '+ Add line' picker)."""
    d = sp._need(slug)
    if service not in _services(d):
        raise HTTPException(404, "this service is not in this project")
    return {"lines": [{k: ln[k] for k in ("item_code", "desc", "unit", "boq_qty", "rate", "internal_ref")}
                      for ln in _lines(d, service)]}


# ------------------------------------------------------- overall annexure
# One service, every Work Order, outside any single RA: what each annexure group is worth, what is already
# invoiced, what still sits in an open draft. Nothing is typed here -- it only adds up the RAs.
def _overall(slug, d, store, base):
    if base not in _bases(d):
        raise HTTPException(404, "this service is not in this project")
    units = []
    for u in [x for x in _services(d) if _base(x) == base]:
        lines = _lines(d, u)
        groups, _ = _groups_for(d, store, u, lines)
        n = len(groups)
        wo, inv, drf = [0.0] * (n + 1), [0.0] * (n + 1), [0.0] * (n + 1)      # last slot = lines in no group
        for ln in lines:
            gi = _group_of(groups, ln)
            wo[n if gi is None else gi] += ln["rate"] * ln["boq_qty"]
        ras = []
        for r in sorted([x for x in store["ras"] if x["service"] == u], key=lambda x: (x["no"], x["series"])):
            b = _get_bill(slug, d, store, r)
            tgt = inv if r["status"] == "locked" else drf
            for row in b["lines"]:
                gi = _group_of(groups, row)
                tgt[n if gi is None else gi] += sum(((row.get(st) or {}).get("amount") or 0.0) for st in STAGES[r["series"]])
            ras.append({"id": r["id"], "no": r["no"], "series": r["series"], "status": r["status"],
                        "label": f"{SERIES_NAME[r['series']]} RA {r['no']}", "this_total": b["this_total"]})
        rows = []
        for i, g in enumerate(groups):
            w = g["wo"] if g.get("wo") is not None else wo[i]
            rows.append({"sr": g.get("sr") or "", "name": g.get("name") or "Items", "wo": w, "invoiced": inv[i],
                         "draft": drf[i], "todate": inv[i] + drf[i]})
        if wo[n] or inv[n] or drf[n]:
            rows.append({"sr": "", "name": "Other items (not in any group)", "wo": wo[n], "invoiced": inv[n],
                         "draft": drf[n], "todate": inv[n] + drf[n]})
        for x in rows:
            x["pct"] = (x["todate"] / x["wo"] * 100.0) if x["wo"] else None
            x["billed"] = abs(x["todate"]) > 1e-9
        t_wo, t_inv, t_drf = sum(x["wo"] for x in rows), sum(x["invoiced"] for x in rows), sum(x["draft"] for x in rows)
        latest = None
        if ras:
            top = sorted(ras, key=lambda x: (x["no"], x["status"] == "draft"))[-1]
            latest = {"id": top["id"], "no": top["no"], "status": top["status"], "label": top["label"]}
        meta = store["meta"]
        units.append({"unit": u, "package": _uname(u), "wo_no": _wo_no(meta, u) or "", "work": (meta.get("work") or {}).get(u) or "",
                      "wo_value": t_wo, "invoiced": t_inv, "draft": t_drf, "todate": t_inv + t_drf,
                      "pct": ((t_inv + t_drf) / t_wo * 100.0) if t_wo else None,
                      "latest": latest, "ras": ras, "rows": rows, "source": _source(store, u),
                      "set": bool((store["setup"].get(u) or {}).get("mode"))})
    tw, ti, td = (sum(x[k] for x in units) for k in ("wo_value", "invoiced", "draft"))
    return {"base": base, "split": len(units) > 1 or any(SEP in x["unit"] for x in units), "units": units,
            "totals": {"wo_value": tw, "invoiced": ti, "draft": td, "todate": ti + td,
                       "pct": ((ti + td) / tw * 100.0) if tw else None}}


@router.get("/{slug}/overall/{base}")
def overall(slug: str, base: str):
    d = sp._need(slug)
    return _overall(slug, d, _load(d), base)


def overall_xlsx(slug, base, drafts=True):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    d = sp._need(slug)
    store = _load(d)
    o = _overall(slug, d, store, base)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Overall annexure"
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    BOLD, PEACH = Font(name="Calibri", bold=True, size=11), PatternFill("solid", fgColor="FCE4D6")
    meta = store["meta"]
    ws["A1"] = meta.get("client") or ""
    ws["A1"].font = Font(name="Calibri", bold=True, size=13)
    ws["A2"], ws["B2"] = "Work", f"{base}, overall annexure"
    ws["A3"], ws["B3"] = "Contractor", meta.get("contractor") or ""
    ws["A4"], ws["B4"] = "As on", dt.date.today().strftime("%d.%m.%Y")
    ws["A5"], ws["B5"] = "Includes", "Invoiced RAs and open drafts" if drafts else "Invoiced RAs only"
    for r_ in range(2, 6):
        ws.cell(r_, 1).font = BOLD
    heads = ["Sr.No.", "Description", "Work Order amount", "Invoiced"] + (["In draft"] if drafts else []) + ["Billed to date", "Done %"]
    ncol = len(heads)
    row = 7
    totals = []                                             # rows of every Work Order's subtotal

    def hdr(r_):
        for i, h in enumerate(heads, start=1):
            c = ws.cell(r_, i, h)
            c.font, c.fill, c.border = BOLD, PEACH, BOX
            c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")

    for u in o["units"]:
        title = (f"Work Order: {u['package']}" if u["package"] else f"Work Order: {base}")
        if u["wo_no"]:
            title += f" (W/O {u['wo_no']})"
        if u["latest"]:
            title += f" · {u['latest']['label']} · " + ("Invoiced" if u["latest"]["status"] == "locked" else "Draft")
        ws.cell(row, 1, title).font = BOLD
        row += 1
        hdr(row)
        row += 1
        first = row
        for x in u["rows"]:
            cells = [x["sr"], x["name"], x["wo"], x["invoiced"]]
            if drafts:
                cells.append(x["draft"])
            todate = f"=D{row}" + (f"+E{row}" if drafts else "")
            cells += [todate, None]
            for i, v in enumerate(cells, start=1):
                c = ws.cell(row, i, v)
                c.border = BOX
                if i >= 3:
                    c.number_format = "#,##0.00"
                    c.alignment = Alignment(horizontal="right")
            pc = ws.cell(row, ncol, f'=IF(C{row}=0,"",{"F" if drafts else "E"}{row}/C{row})')
            pc.number_format, pc.border = "0.0%", BOX
            row += 1
        last = row - 1
        ws.cell(row, 2, "Subtotal").font = BOLD
        for ci in range(3, ncol):
            col = ws.cell(1, ci).column_letter
            c = ws.cell(row, ci, f"=SUM({col}{first}:{col}{last})")
            c.number_format, c.font = "#,##0.00", BOLD
        tcol = "F" if drafts else "E"
        pc = ws.cell(row, ncol, f'=IF(C{row}=0,"",{tcol}{row}/C{row})')
        pc.number_format = "0.0%"
        for i in range(1, ncol + 1):
            ws.cell(row, i).border, ws.cell(row, i).fill = BOX, PEACH
            ws.cell(row, i).font = BOLD
        totals.append(row)
        row += 2
    ws.cell(row, 2, f"{base}, all Work Orders").font = BOLD
    for ci in range(3, ncol):
        col = ws.cell(1, ci).column_letter
        c = ws.cell(row, ci, "=" + "+".join(f"{col}{t}" for t in totals))
        c.number_format, c.font = "#,##0.00", BOLD
    tcol = "F" if drafts else "E"
    pc = ws.cell(row, ncol, f'=IF(C{row}=0,"",{tcol}{row}/C{row})')
    pc.number_format = "0.0%"
    for i in range(1, ncol + 1):
        ws.cell(row, i).border, ws.cell(row, i).fill = BOX, PEACH
        ws.cell(row, i).font = BOLD
    for col, w in zip("ABCDEFG", (9, 60, 20, 18, 18, 18, 10)):
        ws.column_dimensions[col].width = w
    ws.sheet_view.showGridLines = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/{slug}/overall/{base}/xlsx")
def overall_download(slug: str, base: str, drafts: int = 1):
    data = overall_xlsx(slug, base, bool(drafts))
    safe = lambda t: re.sub(r"[^A-Za-z0-9._-]+", "_", t)
    fname = f"{safe(sp._slugify(slug))}_{safe(base)}_overall_annexure{'' if drafts else '_invoiced'}.xlsx"
    return Response(content=data, media_type=_XLSX, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def _new_ra(store, service, series, cutoff=None, auto=False):
    no = _next_no(store, service, series)
    store["seq"] += 1
    ra = {"id": f"ra{store['seq']}", "service": service, "series": series, "no": no, "status": "draft",
          "cutoff": (cutoff or dt.date.today().isoformat()), "created": dt.datetime.now().isoformat(timespec="seconds"),
          "overrides": {}}
    if auto:
        ra["auto"] = True                      # opened by the app, not typed in by a person
    store["ras"].append(ra)
    return ra


def _auto_series(setup):
    """The draft the app opens by itself once installation shows up: one Together RA for a service that is
    usually billed in one invoice, an Installation RA for one billed separately."""
    return "together" if setup.get("mode") == "together" else "install"


@router.post("/{slug}/auto")
def auto_drafts(slug: str):
    """Open a draft for every service whose installation has moved in Site Progress and has no open draft yet.
    The draft is the same live draft a person would have started with 'New RA bill': its quantities follow
    Site Progress every time it is opened. A person deleting that draft is remembered (see delete_ra), so it is
    not reopened until more installation is logged. A service can switch this off in Billing setup."""
    d = sp._need(slug)
    store = _load(d)
    made = []
    for svc in _services(d):
        s = store["setup"].get(svc)
        if not s or not s.get("mode") or s.get("auto_draft") is False:
            continue
        series = _auto_series(s)
        try:
            _need_setup(store, svc, series)
        except HTTPException:
            continue
        if _open_draft(store, svc, series):
            continue
        ghost = {"id": "_preview", "service": svc, "series": series, "no": _next_no(store, svc, series),
                 "status": "draft", "overrides": {}}
        amt = _bill(slug, d, store, ghost)["totals"]["install"]["this"]
        seen = (store.get("auto_skip") or {}).get(svc)
        if amt <= 0 or (seen is not None and amt <= seen + 1e-6):
            continue
        ra = _new_ra(store, svc, series, auto=True)
        (store.get("auto_skip") or {}).pop(svc, None)
        made.append({"id": ra["id"], "label": _fmt_ra(ra), "service": svc, "install": amt})
    if made:
        _save(d, store)
    return {"created": made}


@router.post("/{slug}/ra")
def create_ra(slug: str, payload: dict):
    d = sp._need(slug)
    store = _load(d)
    service, series = payload.get("service"), payload.get("series")
    if service not in _services(d):
        raise HTTPException(400, "this service is not in this project")
    if series not in STAGES:
        raise HTTPException(400, "series must be 'supply', 'install' or 'together'")
    _need_setup(store, service, series)
    od = _open_draft(store, service, series)
    if od:
        raise HTTPException(409, f"A {SERIES_NAME[od['series']]} draft is already open for {service}"
                                 + ("" if od["series"] == series else f", and it carries the same stage as a {SERIES_NAME[series]} RA")
                                 + ". Mark it invoiced or delete it first.")
    ra = _new_ra(store, service, series, payload.get("cutoff"))
    _save(d, store)
    return {"id": ra["id"], "label": _fmt_ra(ra)}


@router.get("/{slug}/ra/{ra_id}")
def get_ra(slug: str, ra_id: str):
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    b = _get_bill(slug, d, store, ra)
    b["ra"]["inv_missing"] = _cover_missing(store, ra)
    return b


@router.post("/{slug}/ra/{ra_id}/line")
def set_line(slug: str, ra_id: str, payload: dict):
    """Type a qty for one line/stage (with a reason), or clear it (qty null)."""
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "draft":
        raise HTTPException(409, "A locked RA cannot be changed. Reopen it first.")
    code, stage = str(payload.get("item_code")), payload.get("stage")
    if stage not in STAGES[ra["series"]]:
        raise HTTPException(400, "this RA has no such stage")
    ln = next((l for l in _lines(d, ra["service"]) if l["item_code"] == code), None)
    if ln is None:
        raise HTTPException(400, "this line is not billable (no quantity or rate)")
    ov = ra.setdefault("overrides", {})
    if payload.get("qty") is None or payload.get("qty") == "":
        ov.get(code, {}).pop(stage, None)
        if code in ov and not ov[code]:
            ov.pop(code)
    else:
        q = _num(payload["qty"])
        if q is None:
            raise HTTPException(400, "qty must be a number")
        reason = (payload.get("reason") or "").strip()
        prev = _prev(store, ra["service"], stage, ra["id"]).get(code, (0.0, 0.0))[0]
        if prev + q > max(ln["boq_qty"], prev) + 1e-9 and not _accepted(ra, code, stage):
            if not payload.get("accept_excess"):
                raise HTTPException(400, f"BOQ qty is {ln['boq_qty']:g} and {prev:g} is already billed. More than that cannot be billed.")
            ra.setdefault("accept_excess", {}).setdefault(code, {})[stage] = True      # the person accepted the extra in the pop-up
        ov.setdefault(code, {})[stage] = {"qty": q, "reason": reason}
        if code in (ra.get("removed") or ()):           # "+ Add line" on a line that was taken out: it is back in the draft
            ra["removed"] = [c for c in ra["removed"] if c != code]
    _save(d, store)
    return {"ok": True}


@router.post("/{slug}/ra/{ra_id}/accept-excess")
def accept_excess(slug: str, ra_id: str, payload: dict):
    """Body {items: [{item_code, stage}], accept?: true|false}. Accept = bill the quantity above the BOQ in THIS draft
    (same rate and %, shown as Extra); accept=false takes it back to the BOQ quantity. Nothing is accepted by default.
    Only a draft changes: the BOQ, Site Progress and invoiced RAs are never touched. A later RA starts after what this one
    billed, so the extra is never billed twice."""
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "draft":
        raise HTTPException(409, "A locked RA cannot be changed. Reopen it first.")
    items = payload.get("items") or []
    if not items:
        raise HTTPException(400, "Nothing to accept.")
    accept = payload.get("accept", True)
    codes = {l["item_code"] for l in _lines(d, ra["service"])}
    acc = ra.setdefault("accept_excess", {})
    n = 0
    for it in items:
        code, stage = str(it.get("item_code")), it.get("stage")
        if code not in codes or stage not in STAGES[ra["series"]]:
            raise HTTPException(400, "this line or stage is not part of this RA")
        if accept:
            acc.setdefault(code, {})[stage] = True
        else:
            acc.get(code, {}).pop(stage, None)
            if code in acc and not acc[code]:
                acc.pop(code)
            # a typed quantity that only stood because of the acceptance goes too: the line is back to the BOQ qty
            ln = next(l for l in _lines(d, ra["service"]) if l["item_code"] == code)
            o = ((ra.get("overrides") or {}).get(code) or {}).get(stage)
            prev = _prev(store, ra["service"], stage, ra["id"]).get(code, (0.0, 0.0))[0]
            if o is not None and prev + o["qty"] > max(ln["boq_qty"], prev) + 1e-9:
                ra["overrides"][code].pop(stage, None)
                if not ra["overrides"][code]:
                    ra["overrides"].pop(code)
        n += 1
    if not acc:
        ra.pop("accept_excess", None)
    _save(d, store)
    b = _bill(slug, d, store, ra)
    return {"ok": True, "n": n, "extra_total": b["extra_total"], "excess": len(b["excess"])}


@router.post("/{slug}/ra/{ra_id}/line/remove")
def remove_line(slug: str, ra_id: str, payload: dict):
    """Body {item_code, remove?: true|false}: take a line OUT of this draft (or put it back with remove=false).
    Only this draft changes: the BOQ, Site Progress, other RAs and invoiced RAs are not touched. A removed line has no
    quantity, no amount and is not counted as 'unfilled'; any quantity typed for it earlier is dropped, so putting it back
    starts clean. Typing a quantity for it with '+ Add line' also puts it back."""
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "draft":
        raise HTTPException(409, "A locked RA cannot be changed. Reopen it first.")
    code = str(payload.get("item_code"))
    if not any(l["item_code"] == code for l in _lines(d, ra["service"])):
        raise HTTPException(400, "this line is not a billable line of this service")
    cur = [c for c in (ra.get("removed") or []) if c != code]
    if payload.get("remove", True):
        cur.append(code)
        ov = ra.get("overrides") or {}
        ov.pop(code, None)
    if cur:
        ra["removed"] = cur
    else:
        ra.pop("removed", None)
    _save(d, store)
    return {"ok": True, "removed": len(cur)}


@router.post("/{slug}/ra/{ra_id}/lock")
def lock_ra(slug: str, ra_id: str):
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "draft":
        raise HTTPException(409, "this RA is already locked")
    _need_setup(store, ra["service"], ra["series"])
    b = _bill(slug, d, store, ra)
    ra["snap"] = {k: v for k, v in b.items() if k != "ra"}
    # the GST rates of the submitted invoice freeze with the bill (a later rate change must not move it)
    ra["inv_gst"] = {"sgst": store["meta"].get("sgst"), "cgst": store["meta"].get("cgst")}
    ra["status"] = "locked"
    ra["locked_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _save(d, store)
    return {"ok": True, "this_total": b["this_total"], "unfilled": b["unfilled"]}


@router.post("/{slug}/ra/{ra_id}/reopen")
def reopen_ra(slug: str, ra_id: str):
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "locked":
        raise HTTPException(409, "this RA is not locked")
    mine = set(STAGES[ra["series"]])
    same = [r for r in store["ras"] if r["service"] == ra["service"] and r["id"] != ra["id"] and mine & set(STAGES[r["series"]])]
    if any(r["no"] > ra["no"] for r in same):
        raise HTTPException(409, "only the last locked RA of a series can be reopened")
    if any(r["status"] == "draft" for r in same):
        raise HTTPException(409, "a draft of this series is already open")
    ra["status"] = "draft"
    ra.pop("snap", None)
    ra.pop("locked_at", None)
    ra.pop("inv_gst", None)
    _save(d, store)
    return {"ok": True}


@router.delete("/{slug}/ra/{ra_id}")
def delete_ra(slug: str, ra_id: str):
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    if ra["status"] != "draft":
        raise HTTPException(409, "A locked RA cannot be deleted. Reopen it first.")
    if "install" in STAGES[ra["series"]]:
        # deleting a draft means "not now": remember the installation value it showed, so it is only
        # reopened automatically once MORE installation is logged
        store.setdefault("auto_skip", {})[ra["service"]] = _bill(slug, d, store, ra)["totals"]["install"]["this"]
    store["ras"] = [r for r in store["ras"] if r["id"] != ra_id]
    _save(d, store)
    return {"ok": True}


# --------------------------------------------------------------- challans
# A challan is one delivery of supply to site. The engineer / store types it
# in once (number, date, qty per BOQ line) and tags the RA it is billed in;
# everything else -- certified qty per RA, previous / this / total qty and
# value, annexure rows, carry-forward to the next RA -- follows from that.
# A challan whose RA is locked is frozen (the submitted bill rests on it).
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%y")


def _iso_date(x):
    if isinstance(x, dt.datetime):
        return x.date().isoformat()
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, str) and x.strip():
        for f in _DATE_FORMATS:
            try:
                return dt.datetime.strptime(x.strip(), f).date().isoformat()
            except ValueError:
                pass
    return None


def _svc_check(d, service):
    if service not in _services(d):
        raise HTTPException(404, "this service is not in this project")


def _need_mode(store, service):
    if not (store["setup"].get(service) or {}).get("mode"):
        raise HTTPException(409, f"{service} has no billing setup yet. Set it in Billing setup first; challans are billed in its Supply RAs.")
    if _source(store, service) != "challan":
        raise HTTPException(409, f"{service} takes its supply quantity from the stock register. Choose 'Challans' for it in Billing setup first.")


def _fmt_challan(store, lines_by, service, c, reg=None):
    last = _last_locked_no(store, service)
    frozen = _challan_frozen(store, service, c)
    ra = c.get("ra")
    out = {"id": c["id"], "no": c["no"], "date": c["date"], "ra": ra, "note": c.get("note") or "",
           "frozen": frozen, "virtual": bool(c.get("virtual")),
           "state": "held" if ra is None else ("locked" if ra <= last else ("open" if ra == last + 1 else "later")),
           "qty_total": sum(c["qty"].values()),
           "lines": [{"item_code": k, "desc": (lines_by.get(k) or {}).get("desc", k), "unit": (lines_by.get(k) or {}).get("unit", ""),
                      "qty": q} for k, q in c["qty"].items()]}
    src = c.get("src")
    if src:
        out["src"] = {"kind": src.get("kind"), "material": src.get("material"), "unit": src.get("unit"),
                      "stock_qty": src.get("stock_qty"), "opening": bool(src.get("opening"))}
        out["no_missing"] = bool(c.get("no_missing"))
        if reg is not None and not frozen:
            now = reg.get(src.get("key"))
            if now is None:
                out["register"] = {"missing": True}
            elif abs(now - src.get("stock_qty", now)) > 1e-6:
                out["register"] = {"now": now, "now_counts": now / (src.get("factor") or 1.0)}
    return out


def _clean_challan(d, store, service, payload, exclude_id=None):
    no = str(payload.get("no") or "").strip()
    if not no:
        raise HTTPException(400, "Challan number is required.")
    for c in _challans(store, service):
        if c["id"] != exclude_id and c["no"].strip().lower() == no.lower():
            raise HTTPException(409, f"Challan {no} is already entered for {service}.")
    date = _iso_date(payload.get("date"))
    if not date:
        raise HTTPException(400, "Challan date is required (YYYY-MM-DD).")
    openn = _open_no(store, service)
    if "ra" not in payload or payload.get("ra") in ("", "open"):
        ra = openn
    elif payload["ra"] is None or payload["ra"] == "hold":
        ra = None
    else:
        try:
            ra = int(payload["ra"])
        except (TypeError, ValueError):
            raise HTTPException(400, "RA must be a number.")
        if ra < openn:
            raise HTTPException(409, f"RA-{ra:02d} is already locked. Use RA-{openn:02d} or later.")
    valid = {l["item_code"] for l in _lines(d, service)}
    qty = {}
    for ln in payload.get("lines") or []:
        code = str(ln.get("item_code"))
        if code not in valid:
            raise HTTPException(400, "A line on this challan is not a billable BOQ line of this service.")
        if code in qty:
            raise HTTPException(400, "The same BOQ line is on this challan twice. Enter it once with the full quantity.")
        q = _num(ln.get("qty"))
        if q is None or q <= 0:
            raise HTTPException(400, "Every line needs a quantity above 0.")
        qty[code] = q
    if not qty:
        raise HTTPException(400, "A challan needs at least one line.")
    wired = [k for k in qty if k in _wired_codes(store, service)]
    if wired:
        ln = next(l for l in _lines(d, service) if l["item_code"] == wired[0])
        raise HTTPException(409, f"“{ln['desc'][:60]}” already gets its quantity from wire metres (Wire → points). "
                                 "A line takes its supply either from wire or from challans, not both.")
    return {"no": no, "date": date, "ra": ra, "note": str(payload.get("note") or "").strip(), "qty": qty}


def _stock_check(slug, d, store, service, lines):
    """Lines whose challan total (every challan, whichever RA) differs from what
    the stock register says was received. Information only -- never a bill."""
    stock, _run = _stock_supply_qty(slug, d, service, lines)
    tot, _ = _challan_qty(store, service, 10 ** 9)
    allq = {}
    for c in _challans(store, service):
        for k, q in c["qty"].items():
            allq[k] = allq.get(k, 0.0) + q
    out = []
    for ln in lines:
        sq = stock.get(ln["item_code"], (None,))[0]
        if sq is None:
            continue
        cq = allq.get(ln["item_code"], 0.0)
        if abs(sq - cq) > 1e-6:
            out.append({"item_code": ln["item_code"], "desc": ln["desc"], "unit": ln["unit"], "challan": cq, "stock": sq,
                        "diff": cq - sq})
    del tot
    return out


@router.get("/{slug}/challans/{service}")
def list_challans(slug: str, service: str):
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    lines = _lines(d, service)
    by = {l["item_code"]: l for l in lines}
    last = _last_locked_no(store, service)
    chs = sorted(_challans(store, service), key=lambda c: (c["date"], c["no"]))
    reg = _register_qty(slug) if any(c.get("src") for c in chs) else None
    received = {}
    for c in chs:
        for k, q in c["qty"].items():
            received[k] = received.get(k, 0.0) + q
    over = [{"item_code": k, "desc": by[k]["desc"], "unit": by[k]["unit"], "challan": q, "boq_qty": by[k]["boq_qty"]}
            for k, q in received.items() if k in by and q > by[k]["boq_qty"] + 1e-9]
    return {"service": service, "source": _source(store, service), "mode": (store["setup"].get(service) or {}).get("mode"),
            "last_locked": last, "open_no": last + 1,
            "challans": [_fmt_challan(store, by, service, c, reg) for c in chs],
            "over": over, "received": received,
            "wire": {"available": _source(store, service) == "challan" and any(_point_len(l["desc"]) for l in lines)},
            "stock_check": _stock_check(slug, d, store, service, lines) if chs else []}


@router.post("/{slug}/challans/{service}")
def add_challan(slug: str, service: str, payload: dict):
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    _need_mode(store, service)
    data = _clean_challan(d, store, service, payload)
    store["seq"] += 1
    c = {"id": f"c{store['seq']}", **data, "created": dt.datetime.now().isoformat(timespec="seconds")}
    store["challans"].setdefault(service, []).append(c)
    _save(d, store)
    return {"ok": True, "id": c["id"]}


# ---- import of an existing challan sheet (the CHALLAN tab of the invoice workbook)
def _fold(x):
    return re.sub(r"[^0-9a-z]+", " ", str(x or "").lower()).strip()


def _ufold(x):
    u = re.sub(r"[^0-9a-z]+", "", str(x or "").lower())
    return u[:-1] if len(u) > 2 and u.endswith("s") else u


def parse_challan_workbook(raw):
    """Read a CHALLAN sheet laid out like the user's own invoice workbook:
    label rows 'Challan Date' / 'Challan No' above the challan columns, the RA
    tag ('RA-01') under the number, 'Qty' header row, then one row per BOQ line
    (Sr, Description, Unit, Qty in the first columns). Returns
    {sheet, challans: [{col, no, date, ra, qty: {row: qty}}], items: [{row, sr, desc, unit, qty}]}."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)

    def lab(v):
        return str(v).strip().lower() if isinstance(v, str) else ""

    best = None
    for ws in wb.worksheets:
        head = list(ws.iter_rows(min_row=1, max_row=15, max_col=70, values_only=True))
        if any(lab(v) == "challan no" for r in head for v in r):
            absd = bool(re.search(r"abtract|abstract", ws.title, re.I))
            if best is None or (best[1] and not absd):
                best = (ws, absd)
    if best is None:
        raise HTTPException(400, "No challan sheet found. The file needs a sheet with 'Challan Date' and 'Challan No' rows above the challan columns.")
    ws = best[0]
    rows = list(ws.iter_rows(min_row=1, max_row=4000, max_col=70, values_only=True))

    def cell(r, c):
        return rows[r][c] if 0 <= r < len(rows) and 0 <= c < len(rows[r]) else None

    rn = next(i for i, r in enumerate(rows[:15]) if any(lab(v) == "challan no" for v in r))
    cols = [j for j, v in enumerate(rows[rn]) if lab(v) == "challan no"]
    rd = next((i for i, r in enumerate(rows[:15]) if any(lab(v) == "challan date" for v in r)), None)
    tag_row = next((i for i in range(rn + 1, rn + 5)
                    if any(re.match(r"\s*ra[\s-]*0*\d+", str(cell(i, j) or ""), re.I) for j in cols)), None)
    qty_row = next((i for i in range(rn, rn + 8) if any(lab(cell(i, j)) == "qty" for j in cols)), None)
    start = (qty_row if qty_row is not None else (tag_row if tag_row is not None else rn + 1)) + 1
    hdr = rows[qty_row] if qty_row is not None else []
    pos = {"sr": 0, "desc": 1, "unit": 2, "qty": 3}
    got_qty = False
    for j, v in enumerate(hdr):
        t = lab(v)
        if t in ("sr. no", "sr no", "sr.no", "sr.no.", "sr. no.") and j not in cols:
            pos["sr"] = j
        elif t == "description":
            pos["desc"] = j
        elif t == "unit":
            pos["unit"] = j
        elif t == "qty" and j not in cols and not got_qty:
            pos["qty"], got_qty = j, True
    # The same workbook's B-BOQ sheet is row-aligned with the CHALLAN sheet. When the
    # description on the CHALLAN row was edited (e.g. "(Actual is 41000 CFM)" added),
    # the B-BOQ wording is a second chance to match -- accepted only if that row has
    # the same unit and quantity.
    alt_rows = {}
    for w2 in wb.worksheets:
        if w2.title != ws.title and re.search(r"b[\s-]*boq", w2.title, re.I) and not re.search(r"summary", w2.title, re.I):
            for i2, r2 in enumerate(w2.iter_rows(min_row=1, max_row=4000, max_col=max(pos.values()) + 1, values_only=True), start=1):
                if len(r2) > max(pos.values()) and isinstance(r2[pos["desc"]], str):
                    alt_rows[i2] = (r2[pos["desc"]], r2[pos["unit"]], _num(r2[pos["qty"]]))
            break
    items, challans = [], []
    for j in cols:
        m = re.match(r"\s*ra[\s-]*0*(\d+)", str(cell(tag_row, j) or ""), re.I) if tag_row is not None else None
        challans.append({"col": j, "no": str(cell(rn + 1, j)).strip() if cell(rn + 1, j) not in (None, "") else "",
                         "date": _iso_date(cell(rd + 1, j)) if rd is not None else None,
                         "ra": int(m.group(1)) if m else None, "qty": {}})
    for i in range(start, len(rows)):
        desc = cell(i, pos["desc"])
        if not isinstance(desc, str) or not desc.strip():
            continue
        unit = cell(i, pos["unit"])
        q = _num(cell(i, pos["qty"]))
        if unit not in (None, "") and q is not None and q > 0:
            it = {"row": i + 1, "sr": cell(i, pos["sr"]), "desc": desc, "unit": str(unit), "qty": q}
            a = alt_rows.get(i + 1)
            if a and _fold(a[0]) != _fold(desc) and _ufold(a[1]) == _ufold(unit) and a[2] is not None and abs(a[2] - q) < 1e-9:
                it["alt"] = a[0]
            items.append(it)
        for ch in challans:
            v = _num(cell(i, ch["col"]))
            if v is not None and v != 0:
                ch["qty"][i + 1] = v
    return {"sheet": ws.title, "challans": challans, "items": items,
            "desc_of": {i + 1: str(cell(i, pos["desc"]) or "") for i in range(start, len(rows)) if cell(i, pos["desc"])}}


def _pair_pass(sheet_items, lines, text):
    gs, gm, pairs = {}, {}, {}
    for it in sheet_items:
        gs.setdefault((_fold(text(it)), _ufold(it["unit"])), []).append(it)
    for ln in lines:
        gm.setdefault((_fold(ln["desc"]), _ufold(ln["unit"])), []).append(ln)
    for k, S in gs.items():
        M = gm.get(k) or []
        if not M:
            continue
        if len(S) == 1 and len(M) == 1:
            pairs[S[0]["row"]] = M[0]
        elif len(S) == len(M) and all(abs(a["qty"] - b["boq_qty"]) < 1e-9 for a, b in zip(S, M)):
            for a, b in zip(S, M):
                pairs[a["row"]] = b
        else:
            for q in {round(a["qty"], 6) for a in S}:
                Sq = [a for a in S if round(a["qty"], 6) == q]
                Mq = [b for b in M if round(b["boq_qty"], 6) == q]
                if len(Sq) == len(Mq):
                    for a, b in zip(Sq, Mq):
                        pairs[a["row"]] = b
    return pairs


def _pair_lines(sheet_items, lines):
    """{sheet row: MI line}. Exact, narrow rules only -- a wrong pairing is
    worse than a missed one (a missed one is listed and typed in by hand):
    same description + unit; if that is unique on both sides they pair; if
    several lines share it they pair in sheet order only when every quantity
    agrees, else only quantity-by-quantity where the counts agree. Rows left
    over get a second chance with the B-BOQ wording of the same row (if the
    workbook has one), against the lines not yet taken."""
    pairs = _pair_pass(sheet_items, lines, lambda it: it["desc"])
    left = [it for it in sheet_items if it["row"] not in pairs and it.get("alt")]
    if left:
        taken = {id(v) for v in pairs.values()}
        free = [ln for ln in lines if id(ln) not in taken]
        pairs.update(_pair_pass(left, free, lambda it: it["alt"]))
    return pairs


def _apply_opening(slug, d, store, service, upto):
    """Create RA 1..upto of the service as already-submitted (locked) Supply RAs,
    computed from the challans tagged to them. Returns {no: this_total}."""
    series = _supply_series(store, service)
    totals = {}
    for n in range(1, upto + 1):
        store["seq"] += 1
        chs = [c for c in _challans(store, service) if c.get("ra") == n]
        ra = {"id": f"ra{store['seq']}", "service": service, "series": series, "no": n, "status": "draft",
              "cutoff": max([c["date"] for c in chs] or [dt.date.today().isoformat()]),
              "created": dt.datetime.now().isoformat(timespec="seconds"), "overrides": {}, "imported": True}
        store["ras"].append(ra)
        b = _bill(slug, d, store, ra)
        ra["snap"] = {k: v for k, v in b.items() if k != "ra"}
        ra["status"] = "locked"
        ra["locked_at"] = dt.datetime.now().isoformat(timespec="seconds")
        totals[n] = b["this_total"]
    return totals


@router.post("/{slug}/challans/{service}/import")
async def import_challans(slug: str, service: str, file: UploadFile = File(...), commit: bool = Form(False),
                          lock_upto: int = Form(0)):
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    _need_mode(store, service)
    raw = await file.read()
    try:
        parsed = parse_challan_workbook(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "This file could not be read as an Excel workbook.")
    series = _supply_series(store, service)
    lines = _lines(d, service)
    pairs = _pair_lines(parsed["items"], lines)
    existing = {c["no"].strip().lower() for c in _challans(store, service)}
    if lock_upto:
        _need_setup(store, service, series)
        if (store["setup"].get(service) or {}).get("mode") != "separate":
            raise HTTPException(409, "Earlier RAs can be brought in only for a service billed as separate Supply and Installation RAs.")
        if any(r["service"] == service and "supply" in STAGES[r["series"]] for r in store["ras"]):
            raise HTTPException(409, f"{service} already has Supply RAs, so earlier RAs cannot be brought in.")
    openn = 1 if lock_upto else _open_no(store, service)     # lowest RA number a challan may be tagged with
    wired = _wired_codes(store, service)
    seen, outc, unmatched, problems, newc = set(), [], [], [], []
    for ch in parsed["challans"]:
        if not ch["qty"] and not ch["no"]:
            continue
        label = ch["no"] or "(no number)"
        if not ch["no"]:
            problems.append(f"A challan column has quantities but no challan number; skipped.")
            continue
        if not ch["date"]:
            problems.append(f"Challan {label}: date missing or not readable; skipped.")
            continue
        if ch["no"].lower() in seen:
            problems.append(f"Challan {label} appears twice in the file; the second one is skipped.")
            continue
        seen.add(ch["no"].lower())
        if ch["ra"] is None:
            ra, note = None, "no RA tag, kept on hold"
        elif ch["ra"] < openn:
            problems.append(f"Challan {label} is tagged RA-{ch['ra']:02d}, which is already locked in MI; skipped.")
            continue
        else:
            ra, note = ch["ra"], ""
        qty, miss = {}, 0
        for row, q in ch["qty"].items():
            ln = pairs.get(row)
            if ln is None:
                miss += 1
                unmatched.append({"challan": label, "row": row, "desc": parsed["desc_of"].get(row, ""), "qty": q})
            elif ln["item_code"] in wired:
                miss += 1
                problems.append(f"Challan {label}: “{ln['desc'][:50]}” takes its quantity from wire metres (Wire → points), so it was not imported.")
            else:
                qty[ln["item_code"]] = qty.get(ln["item_code"], 0.0) + q
        row = {"no": ch["no"], "date": ch["date"], "ra": ra, "lines": len(qty), "qty_total": sum(qty.values()),
               "unmatched": miss, "duplicate": ch["no"].lower() in existing, "note": note}
        outc.append(row)
        if qty and not row["duplicate"]:
            newc.append({"no": ch["no"], "date": ch["date"], "ra": ra, "note": "", "qty": qty})
    mism = []
    for it in parsed["items"]:
        ln = pairs.get(it["row"])
        if ln and abs(it["qty"] - ln["boq_qty"]) > 1e-9:
            mism.append({"row": it["row"], "desc": it["desc"], "sheet_qty": it["qty"], "boq_qty": ln["boq_qty"]})
    # what the imported challans come to, per RA (so it can be checked against the old bill),
    # worked out exactly as the bill does: cumulative, never above the BOQ qty
    setup = store["setup"].get(service) or {}
    pct = _num(setup.get("supply_pct"))
    by_line = {}
    for c in newc:
        if c["ra"] is None:
            continue
        for k, q in c["qty"].items():
            by_line.setdefault(k, {})
            by_line[k][c["ra"]] = by_line[k].get(c["ra"], 0.0) + q
    by_code = {l["item_code"]: l for l in lines}
    by_ra, over_boq = {}, []
    for code, per in by_line.items():
        ln, cum, billed = by_code[code], 0.0, 0.0
        for n in sorted(per):
            cum += per[n]
            utd = min(cum, ln["boq_qty"])
            e = by_ra.setdefault(n, {"lines": set(), "value": 0.0})
            e["lines"].add(code)
            e["value"] += ln["rate"] * (utd - billed) * (pct if pct is not None else 100.0) / 100.0
            billed = utd
        if cum > ln["boq_qty"] + 1e-9:
            over_boq.append({"desc": ln["desc"], "unit": ln["unit"], "challan": cum, "boq_qty": ln["boq_qty"]})
    res = {"sheet": parsed["sheet"], "challans": outc, "unmatched": unmatched, "problems": problems, "qty_differs": mism,
           "by_ra": [{"ra": n, "lines": len(v["lines"]), "value": v["value"] if pct is not None else None} for n, v in sorted(by_ra.items())],
           "over_boq": over_boq, "pct": pct, "new": len(newc), "skipped_duplicate": sum(1 for c in outc if c["duplicate"]),
           "committed": False, "opened": lock_upto}
    if not newc and commit:
        raise HTTPException(409, "Nothing new to import.")
    if commit or lock_upto:
        store2 = json.loads(json.dumps(store))
        for c in newc:
            store2["seq"] += 1
            store2["challans"].setdefault(service, []).append({"id": f"c{store2['seq']}", **c,
                                                                "created": dt.datetime.now().isoformat(timespec="seconds")})
        if lock_upto:
            res["opening_totals"] = _apply_opening(slug, d, store2, service, lock_upto)
        if commit:
            _save(d, store2)
            res["committed"] = True
    return res



# ------------------------------------------------------------------ one upload: typed quantities + challans
def parse_typed_sheet(raw):
    """Read the quantities a person TYPED into the downloaded RA workbook: the 'THIS BILL QTY' column of its B-BOQ sheet
    (one block for a Supply or an Installation RA, two for Supply + Installation). Only a plain number counts as typed;
    a cell that is still the workbook's own formula (a supply quantity that follows the challans) is left alone.
    The block's stage is read from its rate header ('70% RATE OF SUPPLY' / '30% RATE OF INSTALLATION') and its RA number
    from the 'RA-01' label above it. None when the file has no such sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False)

    def lab(v):
        return re.sub(r"\s+", " ", v).strip().lower() if isinstance(v, str) else ""

    for ws in wb.worksheets:
        rows = list(ws.iter_rows(min_row=1, max_row=4000, max_col=40, values_only=True))
        hr = next((i for i, r in enumerate(rows[:25]) if any(lab(v) == "this bill qty" for v in r) and any(lab(v) == "description" for v in r)), None)
        if hr is None:
            continue
        hdr = rows[hr]

        def cell(i, j):
            return rows[i][j] if 0 <= i < len(rows) and 0 <= j < len(rows[i]) else None

        pos = {"sr": 0, "desc": next(j for j, v in enumerate(hdr) if lab(v) == "description")}
        pos["unit"] = next((j for j, v in enumerate(hdr) if lab(v) == "unit"), 2)
        pos["qty"] = next((j for j, v in enumerate(hdr) if lab(v) == "qty"), 3)
        pos["sr"] = next((j for j, v in enumerate(hdr) if lab(v) in ("sr. no", "sr no", "sr.no", "sr.no.", "sr. no.")), 0)
        blocks = []
        for j, v in enumerate(hdr):
            if lab(v) != "this bill qty":
                continue
            rate = lab(cell(hr, j - 2))
            stage = "supply" if "supply" in rate else ("install" if "install" in rate else None)
            if stage is None:
                continue
            ra_no = None
            for i in range(hr - 1, max(hr - 4, -1), -1):
                m = re.match(r"\s*ra[\s-]*0*(\d+)", str(cell(i, j - 2) or ""), re.I)
                if m:
                    ra_no = int(m.group(1))
                    break
            blocks.append({"stage": stage, "ra": ra_no, "col": j})
        if not blocks:
            continue
        items, typed = [], {}
        for i in range(hr + 1, len(rows)):
            desc, unit, q = cell(i, pos["desc"]), cell(i, pos["unit"]), _num(cell(i, pos["qty"]))
            if not isinstance(desc, str) or not desc.strip() or unit in (None, "") or q is None or q <= 0:
                continue
            items.append({"row": i + 1, "sr": cell(i, pos["sr"]), "desc": desc, "unit": str(unit), "qty": q})
            for bi, b in enumerate(blocks):
                v = cell(i, b["col"])
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    typed[(bi, i + 1)] = float(v)
        return {"sheet": ws.title, "items": items, "blocks": blocks, "typed": typed}
    return None


def _typed_changes(slug, d, store, service, parsed, ra_id, accept=None):
    """What the typed quantities of an uploaded sheet would change. Nothing is written here.
    -> {sheet, ra, changes[], problems[], unpaired[], new_overrides: [(ra, code, stage, qty)]}.
    Rules (never a guess): the sheet's RA number and stage must match an OPEN draft of this service (and the draft the person
    came from, if any); a typed number counts only where it differs from what the draft shows now; a line is paired by exactly
    the same narrow rules as the challan import (_pair_lines); a quantity that would take the line above its BOQ quantity is
    listed as 'over' (extra) and is imported ONLY when the person accepted it (`accept`: a set of (ra id, item code, stage)),
    otherwise it is skipped and said so."""
    lines = _lines(d, service)
    pairs = _pair_lines(parsed["items"], lines)
    by_row = {it["row"]: it for it in parsed["items"]}
    changes, problems, unpaired, apply_, seen = [], [], [], [], set()
    bills = {}
    for bi, blk in enumerate(parsed["blocks"]):
        stage, nm = blk["stage"], STAGE_NAME[blk["stage"]]
        tag = f"RA-{blk['ra']:02d} {nm}" if blk["ra"] else nm
        cands = [r for r in store["ras"] if r["service"] == service and stage in STAGES[r["series"]] and (blk["ra"] is None or r["no"] == blk["ra"])]
        ra = next((r for r in cands if r["status"] == "draft" and (ra_id is None or r["id"] == ra_id)), None)
        if ra is None:
            locked = next((r for r in cands if r["status"] == "locked"), None)
            if ra_id and any(r["id"] == ra_id for r in store["ras"]) and not any(r["id"] == ra_id for r in cands):
                mine = _fmt_ra(_ra(store, ra_id))
                why = f"this sheet is for {tag}, but you are in {mine}"
            elif locked:
                why = f"{tag} is already invoiced, so it is not changed"
            else:
                why = f"there is no open draft for {tag}"
            if any(v for (b2, _r), v in parsed["typed"].items() if b2 == bi and v):
                problems.append(f"Quantities in the {tag} columns were not imported: {why}.")
            continue
        if ra["id"] not in bills:
            bills[ra["id"]] = _bill(slug, d, store, ra)
        b = bills[ra["id"]]
        cur = {r["item_code"]: r for r in b["lines"]}
        gone = {c for c in (ra.get("removed") or ())}
        prev = _prev(store, service, stage, ra["id"])
        for (b2, row), v in sorted(parsed["typed"].items(), key=lambda kv: kv[0][1]):
            if b2 != bi:
                continue
            ln = pairs.get(row)
            it = by_row[row]
            if ln is None:
                if v:
                    unpaired.append({"ra": tag, "row": row, "desc": it["desc"], "qty": v})
                continue
            code = ln["item_code"]
            if (ra["id"], code, stage) in seen:
                continue
            seen.add((ra["id"], code, stage))
            if code in gone:
                if v:
                    problems.append(f"{ln['desc'][:60]}: taken out of this draft, so the typed {v:g} was not imported.")
                continue
            if v < 0:
                problems.append(f"{ln['desc'][:60]}: a quantity cannot be below 0 ({v:g}); skipped.")
                continue
            now = ((cur.get(code) or {}).get(stage) or {}).get("this")
            now = 0.0 if now is None and code not in cur else now
            if now is not None and abs(now - v) < 1e-9:
                continue
            before = prev.get(code, (0.0, 0.0))[0]
            over = before + v > max(ln["boq_qty"], before) + 1e-9 and not _accepted(ra, code, stage)
            ch = {"ra": ra["id"], "ra_label": _fmt_ra(ra), "together": len(STAGES[ra["series"]]) > 1, "stage": stage, "stage_name": nm, "item_code": code,
                  "desc": ln["desc"], "unit": ln["unit"], "now": now, "sheet": v}
            if over:
                ch.update({"over": True, "boq_qty": ln["boq_qty"], "extra": round(before + v - max(ln["boq_qty"], before), 4)})
                if accept is not None and (ra["id"], code, stage) not in accept:
                    problems.append(f"{ln['desc'][:60]}: {v:g} is above the BOQ qty of {ln['boq_qty']:g}; not accepted, so it was not imported.")
                    continue
            changes.append(ch)
            apply_.append((ra["id"], code, stage, v, over))
    return {"sheet": parsed["sheet"], "changes": changes, "problems": problems, "unpaired": unpaired, "apply": apply_}


class _Bytes:
    """Just enough of an UploadFile for import_challans to read the bytes it was given."""
    def __init__(self, raw):
        self.raw = raw

    async def read(self):
        return self.raw


@router.post("/{slug}/import-sheet/{service}")
async def import_sheet(slug: str, service: str, file: UploadFile = File(...), commit: bool = Form(False),
                       lock_upto: int = Form(0), ra_id: str = Form(""), accept_over: str = Form("")):
    """ONE upload for the downloaded RA workbook, filled in. It brings back, together:
      - the challans of its CHALLAN sheet (only for a service billed from challans; exactly the challan import), and
      - the quantities typed into its 'THIS BILL QTY' column, for Supply and/or Installation, which become the typed
        quantity of that line in the matching open draft RA (the same thing as typing it on the RA screen).
    commit=false only reads and reports; commit=true saves. Re-uploading the same file adds nothing twice."""
    d = sp._need(slug)
    _svc_check(d, service)
    raw = await file.read()
    try:
        has_ch = True
        try:
            parse_challan_workbook(raw)
        except HTTPException as e:
            if e.status_code == 400 and str(e.detail).startswith("No challan sheet"):
                has_ch = False
            else:
                raise
        parsed = parse_typed_sheet(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "This file could not be read as an Excel workbook.")
    if not has_ch and parsed is None:
        raise HTTPException(400, "Nothing to read in this file. It needs the challan columns, or the THIS BILL QTY column of an RA workbook downloaded from Billing.")
    store = _load(d)
    chres, chnote = None, None
    if has_ch:
        ok = (store["setup"].get(service) or {}).get("mode") and _source(store, service) == "challan"
        if ok:
            chres = await import_challans(slug, service, _Bytes(raw), commit=False, lock_upto=lock_upto)
        else:
            chnote = f"The CHALLAN sheet in this file was not read: {service} takes its supply quantity from the stock register, not from challans."
    acc = None
    if commit:                                    # what the person ticked as "accept this extra over the BOQ" (JSON list); nothing ticked = nothing accepted
        try:
            acc = {(str(a["ra"]), str(a["item_code"]), str(a["stage"])) for a in json.loads(accept_over or "[]")}
        except Exception:
            raise HTTPException(400, "accept_over must be a JSON list of {ra, item_code, stage}.")
    typed = _typed_changes(slug, d, store, service, parsed, ra_id or None, acc) if parsed else None
    res = {"challans": chres, "challan_note": chnote, "committed": False,
           "typed": None if typed is None else {k: v for k, v in typed.items() if k != "apply"}, "applied": 0}
    if not commit:
        return res
    n_typed = len(typed["apply"]) if typed else 0
    if not ((chres and (chres["new"] or lock_upto)) or n_typed):
        raise HTTPException(409, "Nothing new to import.")
    if chres and (chres["new"] or lock_upto):
        res["challans"] = await import_challans(slug, service, _Bytes(raw), commit=True, lock_upto=lock_upto)
    if n_typed:
        store2 = _load(d)
        for rid, code, stage, v, over in typed["apply"]:
            r = _ra(store2, rid)
            if r["status"] != "draft":
                continue
            if over:
                r.setdefault("accept_excess", {}).setdefault(code, {})[stage] = True
            r.setdefault("overrides", {}).setdefault(code, {})[stage] = {"qty": v, "reason": "Typed in the uploaded Excel" + (" (extra over BOQ, accepted)" if over else "")}
        _save(d, store2)
        res["applied"] = n_typed
    res["committed"] = True
    return res


@router.post("/{slug}/challans/{service}/{cid}")
def edit_challan(slug: str, service: str, cid: str, payload: dict):
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    c = next((x for x in _challans(store, service) if x["id"] == cid), None)
    if c is None:
        raise HTTPException(404, "challan not found")
    if c.get("virtual"):
        raise HTTPException(409, f"{c['no']} is worked out from wire metres. Change it in Wire → points.")
    if _challan_frozen(store, service, c):
        raise HTTPException(409, f"Challan {c['no']} is in RA-{c['ra']:02d}, which is locked. Reopen that RA to change it.")
    data = _clean_challan(d, store, service, payload, exclude_id=cid)
    if c.get("no_missing") and data["no"] != c["no"]:
        c["no_missing"] = False                    # the engineer typed the real challan number
    c.update(data)
    _save(d, store)
    return {"ok": True}


@router.delete("/{slug}/challans/{service}/{cid}")
def delete_challan(slug: str, service: str, cid: str):
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    c = next((x for x in _challans(store, service) if x["id"] == cid), None)
    if c is None:
        raise HTTPException(404, "challan not found")
    if c.get("virtual"):
        raise HTTPException(409, f"{c['no']} is worked out from wire metres. Change it in Wire → points.")
    if _challan_frozen(store, service, c):
        raise HTTPException(409, f"Challan {c['no']} is in RA-{c['ra']:02d}, which is locked. Reopen that RA to delete it.")
    store["challans"][service] = [x for x in store["challans"][service] if x["id"] != cid]
    _save(d, store)
    return {"ok": True}


# ---- wire -> points: screens and writes
def _wire_ready(d, store, unit):
    _svc_check(d, unit)
    _need_mode(store, unit)


def _wfrozen(store, unit, c):
    return c.get("ra") is not None and c["ra"] <= _last_locked_no(store, unit)


def _clean_wire_challan(store, unit, w, payload, exclude_id=None):
    no = str(payload.get("no") or "").strip()
    if not no:
        raise HTTPException(400, "Challan number is required.")
    for c in w["challans"]:
        if c["id"] != exclude_id and c["no"].strip().lower() == no.lower():
            raise HTTPException(409, f"Wire challan {no} is already entered.")
    date = _iso_date(payload.get("date"))
    if not date:
        raise HTTPException(400, "Challan date is required (YYYY-MM-DD).")
    openn = _open_no(store, unit)
    if "ra" not in payload or payload.get("ra") in ("", "open"):
        ra = openn
    elif payload["ra"] is None or payload["ra"] == "hold":
        ra = None
    else:
        try:
            ra = int(payload["ra"])
        except (TypeError, ValueError):
            raise HTTPException(400, "RA must be a number.")
        if ra < openn:
            raise HTTPException(409, f"{raL_(ra)} is already locked. Use {raL_(openn)} or later.")
    colours = {k for k, _ in _wscheme(store)["colours"]}
    qty = {}
    for blk in payload.get("sizes") or []:
        size = _wsize(blk.get("size"))
        if size in qty:
            raise HTTPException(400, f"Wire size {size} sq mm is on this challan twice. Enter it once.")
        cols = {}
        for col, m in (blk.get("colours") or {}).items():
            if col not in colours:
                raise HTTPException(400, f"“{col}” is not a wire colour of the colour code chosen for this project.")
            v = _num(m)
            if v is None or v < 0:
                raise HTTPException(400, "Metres must be a number, 0 or more.")
            if v > 0:
                cols[col] = v
        if cols:
            qty[size] = cols
    if not qty:
        raise HTTPException(400, "Enter the metres of at least one wire colour.")
    return {"no": no, "date": date, "ra": ra, "note": str(payload.get("note") or "").strip(), "qty": qty}


def _size_key(s):
    return float(s)


def _wire_state(d, store, unit):
    w = _wunit(store, unit)
    sch = _wscheme(store)
    ph = set(sch["phases"])
    lines = _lines(d, unit)
    setup = store["setup"].get(unit) or {}
    last = _last_locked_no(store, unit)
    openn = last + 1
    pts_all, pts_before = _w_pts(w), _w_pts(w, openn - 1)
    by_code = {}
    for a in w["alloc"]:
        by_code.setdefault(a["item_code"], []).append(a)
    out_lines, elig, free = [], [], []
    supply_this = supply_to = pts_this_n = 0.0
    for ln in lines:
        code = ln["item_code"]
        pl = _point_len(ln["desc"])
        rows = by_code.get(code, [])
        if pl or rows:
            per = rows[-1]["per"] if rows else pl["per"]
            pct = _stage_pct(setup, code, "supply")
            tot = pts_all.get(code, 0)
            this_pts = tot - pts_before.get(code, 0)
            val_this = this_pts * ln["rate"] * pct / 100.0 if pct is not None else None
            val_to = tot * ln["rate"] * pct / 100.0 if pct is not None else None
            by_size = {}
            for a in rows:
                r = by_size.setdefault(a["size"], {"size": a["size"], "earlier": 0.0, "this": 0.0})
                r["earlier" if a["ra"] < openn else "this"] += a["metres"]
            item = {"item_code": code, "ref": code, "desc": ln["desc"], "unit": ln["unit"],
                    "per": per, "upto": bool(pl and pl["upto"]),
                    "quote": (pl["quote"] if pl else (rows[-1].get("quote") if rows else None)),
                    "typed": not pl,
                    "boq_qty": ln["boq_qty"], "rate": ln["rate"], "pct": pct,
                    "rows": sorted(by_size.values(), key=lambda r: _size_key(r["size"])),
                    "metres": sum(a["metres"] for a in rows), "points": tot, "points_this": this_pts,
                    "supply_this": val_this, "supply_to_date": val_to}
            if rows:
                out_lines.append(item)
                supply_this += val_this or 0.0
                supply_to += val_to or 0.0
                pts_this_n += this_pts
            if pl:
                elig.append({"item_code": code, "ref": code, "desc": ln["desc"], "per": pl["per"], "upto": pl["upto"],
                             "quote": pl["quote"], "boq_qty": ln["boq_qty"], "points": tot, "rate": ln["rate"],
                             "pct": _stage_pct(setup, code, "supply")})
        elif re.search(r"\bpoints?\b", ln["desc"], re.I):
            free.append({"item_code": code, "ref": code, "desc": ln["desc"], "boq_qty": ln["boq_qty"], "rate": ln["rate"],
                         "pct": _stage_pct(setup, code, "supply")})
    got, gave = _w_counted(store, w), _w_given(w)
    sizes = sorted(set(got) | set(gave), key=_size_key)
    wst = _wire_stock(d.name, d, store, unit)
    src_eff = w.get("source") or ("typed" if w["challans"] or not wst["available"] else "stock")
    chs = []
    for c in sorted(w["challans"], key=lambda c: (c["date"], c["no"])):
        chs.append({"id": c["id"], "no": c["no"], "date": c["date"], "ra": c.get("ra"), "note": c.get("note") or "",
                    "frozen": _wfrozen(store, unit, c),
                    "state": "held" if c.get("ra") is None else ("locked" if c["ra"] <= last else ("open" if c["ra"] == openn else "later")),
                    "sizes": c["qty"], "src": c.get("src"), "counts": _w_counts(w, c), "no_missing": bool(c.get("no_missing"))})
    return {"unit": unit, "base": _base(unit), "ready": True, "last_locked": last, "open_no": openn,
            "scheme": {"id": (store.get("meta") or {}).get("wire_scheme") or "ryb", "label": sch["label"], "note": sch["note"],
                       "colours": [{"key": k, "label": lab, "counted": k in ph} for k, lab in sch["colours"]]},
            "schemes": [{"id": k, "label": v["label"]} for k, v in WIRE_SCHEMES.items()],
            "scheme_locked": any(x.get("challans") for x in (store.get("wire") or {}).values()),
            "challans": chs, "sizes": sizes,
            "size_status": [{"size": s, "counted": got.get(s, 0.0), "given": gave.get(s, 0.0), "left": got.get(s, 0.0) - gave.get(s, 0.0)} for s in sizes],
            "lines": out_lines, "eligible": elig, "free_lines": free,
            "supply_pct": _num(setup.get("supply_pct")),
            "totals": {"supply_this": supply_this, "supply_to_date": supply_to, "points_this": pts_this_n},
            "problems": [], "source": src_eff, "source_set": w.get("source") is not None,
            "stock": {"available": wst["available"], "run": wst["run"], "materials": wst["materials"],
                      "confirmed": sum(1 for m in wst["materials"] if m["confirmed"]),
                      "unconfirmed": sum(1 for m in wst["materials"] if not m["confirmed"])}}


@router.get("/{slug}/wire/{unit}")
def wire_get(slug: str, unit: str):
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    return _wire_state(d, store, unit)


def _wire_put(d, store, unit, w):
    err = _w_check(store, w)
    if err:
        raise HTTPException(409, err)
    store.setdefault("wire", {})[unit] = w


@router.post("/{slug}/wire/{unit}/challans")
def wire_add_challan(slug: str, unit: str, payload: dict):
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    w = json.loads(json.dumps(_wunit(store, unit)))
    if w.get("source") == "stock":
        raise HTTPException(409, "This unit takes its wire deliveries from the stock register. Switch to “Type challans” to type one.")
    data = _clean_wire_challan(store, unit, w, payload)
    store["seq"] += 1
    w["challans"].append({"id": f"k{store['seq']}", **data, "created": dt.datetime.now().isoformat(timespec="seconds")})
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True}


@router.post("/{slug}/wire/{unit}/challans/{cid}")
def wire_edit_challan(slug: str, unit: str, cid: str, payload: dict):
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    w = json.loads(json.dumps(_wunit(store, unit)))
    c = next((x for x in w["challans"] if x["id"] == cid), None)
    if c is None:
        raise HTTPException(404, "wire challan not found")
    if _wfrozen(store, unit, c):
        raise HTTPException(409, f"Wire challan {c['no']} is in {raL_(c['ra'])}, which is locked. Reopen that RA to change it.")
    new = _clean_wire_challan(store, unit, w, payload, exclude_id=cid)
    if c.get("no_missing") and new["no"] != c["no"]:
        c["no_missing"] = False
    c.update(new)
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True}


@router.delete("/{slug}/wire/{unit}/challans/{cid}")
def wire_delete_challan(slug: str, unit: str, cid: str):
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    w = json.loads(json.dumps(_wunit(store, unit)))
    c = next((x for x in w["challans"] if x["id"] == cid), None)
    if c is None:
        raise HTTPException(404, "wire challan not found")
    if _wfrozen(store, unit, c):
        raise HTTPException(409, f"Wire challan {c['no']} is in {raL_(c['ra'])}, which is locked. Reopen that RA to delete it.")
    w["challans"] = [x for x in w["challans"] if x["id"] != cid]
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True}


@router.put("/{slug}/wire/{unit}/alloc")
def wire_alloc(slug: str, unit: str, payload: dict):
    """Body {rows: [{item_code, size, metres, per?}]}: the metres given to point lines in the OPEN RA (earlier RAs
    are frozen with their bills). `per` is read only for a line whose BOQ text states no average point length."""
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    openn = _open_no(store, unit)
    by = {l["item_code"]: l for l in _lines(d, unit)}
    w = json.loads(json.dumps(_wunit(store, unit)))
    keep = [a for a in w["alloc"] if a["ra"] < openn]
    old_per = {a["item_code"]: a["per"] for a in keep}
    new, seen = [], set()
    manual = {k for c in store["challans"].get(unit, []) for k in c["qty"]}
    for r in payload.get("rows") or []:
        code = str(r.get("item_code"))
        ln = by.get(code)
        if ln is None:
            raise HTTPException(400, "A line here is not a billable BOQ line of this service.")
        size = _wsize(r.get("size"))
        m = _num(r.get("metres"))
        if m is None or m <= 0:
            raise HTTPException(400, "Metres given must be above 0 (remove the row instead).")
        if (code, size) in seen:
            raise HTTPException(400, f"{ln['desc'][:50]}: wire {size} sq mm is on twice. Enter it once.")
        seen.add((code, size))
        if code in manual:
            raise HTTPException(409, f"“{ln['desc'][:60]}” already has challan quantity typed in by hand. "
                                     "A line takes its supply either from wire or from challans, not both.")
        pl = _point_len(ln["desc"])
        per = old_per.get(code) or (pl["per"] if pl else _num(r.get("per")))
        if not per or per <= 0:
            raise HTTPException(400, f"“{ln['desc'][:60]}” has no average point length in the BOQ. Type the metres per point for it.")
        new.append({"item_code": code, "size": size, "ra": openn, "metres": m, "per": per, "typed": not pl,
                    "quote": pl["quote"] if pl else "typed by the engineer"})
    w["alloc"] = keep + new
    pts = _w_pts(w)
    for code, p in pts.items():
        if p > by[code]["boq_qty"] + 1e-9:
            ln = by[code]
            per = _w_line_metres(w)[1][code]
            raise HTTPException(409, f"“{ln['desc'][:60]}”: the BOQ has only {ln['boq_qty']:g} points, but these metres make {p}. "
                                     f"Give at most {ln['boq_qty'] * per:g} m in all to this line.")
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True}


@router.put("/{slug}/wire-scheme")
def wire_scheme(slug: str, payload: dict):
    d = sp._need(slug)
    store = _load(d)
    sid = payload.get("scheme")
    if sid not in WIRE_SCHEMES:
        raise HTTPException(400, "Unknown wire colour code.")
    if any(x.get("challans") for x in store["wire"].values()) and sid != ((store["meta"].get("wire_scheme")) or "ryb"):
        raise HTTPException(409, "Wire challans are already entered in this colour code. Change the colour code before entering wire.")
    store["meta"]["wire_scheme"] = sid
    _save(d, store)
    return {"ok": True}


# ------------------------------------------- challans straight from the stock register
# Material reaches site, the store enters it in the stock register, and a challan is made from what arrived. So a
# unit that bills on delivery challans can take its challans FROM the register instead of retyping them:
#   * the register's dated IN rows (daily.parquet of the latest forecast run) are an INBOX of proposed deliveries;
#   * nothing becomes a challan until the engineer adds it to an RA (or marks it Not billable / Hold);
#   * an added delivery is a frozen copy (date, qty, line, factor) tagged src={"kind":"stock","key":...}; it locks
#     with its RA exactly like a typed challan. If the register changes later, an OPEN RA shows the difference and
#     the engineer decides (resync); a locked RA never moves;
#   * the register carries NO challan number: it is left blank ("No. missing") with a label for the invoice;
#     never invented;
#   * a delivery is one challan only: it leaves the inbox once added, and a typed challan with the same date, line
#     and quantity is flagged before a duplicate is added;
#   * the line comes from the existing stock LINK (Link stock), with its factor. A material with no link is never
#     matched by name; it shows "Link it". A link with no factor and a different unit cannot be added.
#   * wire materials (size + colour in the name) are not individual challans: they feed Wire -> points, see below.
@functools.lru_cache(maxsize=8)
def _stock_rows_cached(path_str, mtime_ns):
    df = sp._read_forecast_parquet_cached(path_str, mtime_ns)
    if not {"service", "material", "unit", "date", "qty_in"} <= set(df.columns):
        return ()
    out = []
    for (svc, mat, unit), g in df.groupby(["service", "material", "unit"], sort=False):
        dts = pd.to_datetime(g["date"], errors="coerce")
        ok = dts.notna()
        ins = g[ok & (g["qty_in"].fillna(0) > 0)]
        for dte, q in zip(pd.to_datetime(ins["date"]).dt.date, ins["qty_in"]):
            iso = dte.isoformat()
            out.append({"key": f"{svc}|{mat}|{unit}|{iso}|i", "service": str(svc), "material": str(mat), "unit": str(unit),
                        "date": iso, "qty": float(q), "opening": False})
        if "opening" in g.columns and ok.any():
            op = g["opening"].dropna()
            if len(op) and float(op.iloc[0]) > 0:
                iso = dts[ok].min().date().isoformat()
                out.append({"key": f"{svc}|{mat}|{unit}|{iso}|o", "service": str(svc), "material": str(mat), "unit": str(unit),
                            "date": iso, "qty": float(op.iloc[0]), "opening": True})
    return tuple(out)


def _stock_rows(slug):
    """(rows, run name) -- every dated IN (and each material's opening balance) of the latest forecast run."""
    run = sp._latest_run_for(slug)
    if run is None:
        return (), None
    p = run / "daily.parquet"
    try:
        return _stock_rows_cached(str(p), p.stat().st_mtime_ns), run.name
    except Exception:
        return (), run.name


def _register_qty(slug):
    return {r["key"]: r["qty"] for r in _stock_rows(slug)[0]}


def _fdate(iso):
    try:
        return dt.date.fromisoformat(iso).strftime("%d-%b-%y")
    except (TypeError, ValueError):
        return str(iso)


def _link_index(d, base, lines):
    """{normalised material: [(item_code, factor|None)]} of the stock links of THIS unit's lines."""
    codes = {l["item_code"] for l in lines}
    idx = {}
    for code, entries in sp._load_links(d).get(base, {}).items():
        if code not in codes:
            continue
        for e in sp._norm_link_entries(entries):
            idx.setdefault(linkage._norm(e["material"]), []).append((code, e["factor"]))
    return idx


def _has_point_lines(lines):
    return any(_point_len(l["desc"]) for l in lines)


_WSIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:SQ\.?\s*MM|MM2)", re.I)


def _wire_parse(name, scheme):
    """{size, colour} when a stock material name states a wire size and ends in a colour of the project's colour code
    ("1.5SQMM FRLS WIRE RED"); else None. Deterministic; the engineer still confirms it once."""
    s = str(name or "").upper()
    if "WIRE" not in s:
        return None
    m = _WSIZE.search(s)
    if not m:
        return None
    toks = re.findall(r"[A-Z]+", s[m.end():])
    if not toks:
        return None
    keys = {k for k, _ in scheme["colours"]}
    for cand in ([toks[-2] + "_" + toks[-1]] if len(toks) > 1 else []) + [toks[-1]]:
        if cand in keys:
            return {"size": f"{float(m.group(1)):g}", "colour": cand}
    return None


def _since(store, unit):
    """Deliveries dated up to the cut-off of the last LOCKED supply RA belong to a bill already made."""
    cuts = [r.get("cutoff") for r in store["ras"]
            if r["service"] == unit and "supply" in STAGES[r["series"]] and r["status"] == "locked" and r.get("cutoff")]
    return max(cuts) if cuts else None


def _accepted_keys(store, base):
    out = set()
    for u, chs in store["challans"].items():
        if _base(u) == base:
            for c in chs:
                k = (c.get("src") or {}).get("key")
                if k:
                    out.add(k)
    return out


def _dup(store, base, code, date, qty):
    for u, chs in store["challans"].items():
        if _base(u) != base:
            continue
        for c in chs:
            if c["date"] == date and code in c["qty"] and abs(c["qty"][code] - qty) < 1e-6:
                return {"no": c["no"], "ra": c.get("ra"), "unit": u}
    return None


def _inbox(slug, d, store, unit, show_earlier=False):
    base = _base(unit)
    lines = _lines(d, unit)
    by = {l["item_code"]: l for l in lines}
    rows, run = _stock_rows(slug)
    label = sp._forecast_service(base)
    idx = _link_index(d, base, lines)
    taken = _accepted_keys(store, base)
    held = set(store["stock_hold"].get(base, []))
    skip = set(store["stock_skip"].get(base, []))
    wired = _wired_codes(store, unit)
    scheme = _wscheme(store)
    wok = _has_point_lines(lines)
    since = _since(store, unit)
    entries, earlier = [], 0
    wire = {"entries": 0, "metres": 0.0, "from": None, "to": None}
    last_in = None
    for r in rows:
        if r["key"] in taken:
            continue
        this = r["service"] == label
        if this and (last_in is None or r["date"] > last_in) and not r["opening"]:
            last_in = r["date"]
        if this and wok and _wire_parse(r["material"], scheme):
            wire["entries"] += 1
            wire["metres"] += r["qty"]
            wire["from"] = r["date"] if wire["from"] is None else min(wire["from"], r["date"])
            wire["to"] = r["date"] if wire["to"] is None else max(wire["to"], r["date"])
            continue
        if since and r["date"] <= since and not show_earlier:
            earlier += 1
            continue
        mnorm = linkage._norm(r["material"])
        skey = f"{r['service']}|{mnorm}"
        state = "dismissed" if skey in skip else ("held" if r["key"] in held else "waiting")
        cands, seen = [], set()
        if this:
            for code, f in idx.get(mnorm, []):
                if code in seen:
                    continue
                seen.add(code)
                ln = by[code]
                factor = f
                if factor is None:
                    same = bool(ln["unit"].strip()) and str(r["unit"]).strip().upper() == ln["unit"].strip().upper()
                    factor = 1.0 if same else None
                prob = None
                if code in wired:
                    prob = "takes its supply from wire (Wire → points)"
                elif not factor:
                    prob = "factor not set"
                counts = round(r["qty"] / factor, 4) if factor else None
                cands.append({"item_code": code, "desc": ln["desc"], "unit": ln["unit"], "boq_qty": ln["boq_qty"],
                              "factor": factor, "counts_as": counts, "problem": prob,
                              "dup": _dup(store, base, code, r["date"], counts) if (counts is not None and not prob) else None})
        entries.append({"key": r["key"], "date": r["date"], "material": r["material"], "unit": r["unit"], "qty": r["qty"],
                        "service": r["service"], "opening": r["opening"], "state": state,
                        "scope": "this" if this else "other",
                        "kind": "other" if not this else ("line" if cands else "unlinked"),
                        "lines": cands, "item_code": cands[0]["item_code"] if len(cands) == 1 else None})
    entries.sort(key=lambda e: (e["date"], e["material"]), reverse=True)
    mine = [e for e in entries if e["scope"] == "this"]
    return {"unit": unit, "base": base, "ready": True, "source": "challan", "run": run, "service_label": label,
            "since": since, "earlier": earlier, "last_in": last_in,
            "open_no": _open_no(store, unit), "mode_set": bool((store["setup"].get(unit) or {}).get("mode")),
            "counts": {"waiting": sum(1 for e in mine if e["state"] == "waiting"),
                       "held": sum(1 for e in mine if e["state"] == "held"),
                       "dismissed": sum(1 for e in mine if e["state"] == "dismissed"),
                       "other": sum(1 for e in entries if e["scope"] == "other" and e["state"] == "waiting")},
            "wire": {"available": wok, **wire},
            "entries": entries}


@router.get("/{slug}/stock-inbox/{unit}")
def stock_inbox(slug: str, unit: str, earlier: int = 0):
    d = sp._need(slug)
    _svc_check(d, unit)
    store = _load(d)
    if _source(store, unit) != "challan":
        return {"unit": unit, "ready": False, "source": _source(store, unit), "entries": [],
                "reason": "This service takes its supply quantity straight from the stock register (Billing setup), so there are no challans to add."}
    return _inbox(slug, d, store, unit, show_earlier=bool(earlier))


def _stock_label(store, unit, e):
    base = "Stock opening" if e["opening"] else f"Stock IN {_fdate(e['date'])}"
    taken = {c["no"].strip().lower() for c in _challans(store, unit)}
    no, n = base, 1
    while no.lower() in taken:
        n += 1
        no = f"{base} ({n})"
    return no


@router.post("/{slug}/stock-inbox/{unit}/accept")
def stock_accept(slug: str, unit: str, payload: dict):
    """Body {items: [{key, item_code?, no?, force?}], ra?: "open"|number|null, force?}. All or nothing."""
    d = sp._need(slug)
    _svc_check(d, unit)
    store = _load(d)
    _need_mode(store, unit)
    inbox = _inbox(slug, d, store, unit, show_earlier=True)
    by_key = {e["key"]: e for e in inbox["entries"]}
    items = payload.get("items") or []
    if not items:
        raise HTTPException(400, "Choose at least one delivery.")
    seen, made = set(), []
    for it in items:
        key = str(it.get("key"))
        if key in seen:
            raise HTTPException(400, "The same delivery is selected twice.")
        seen.add(key)
        e = by_key.get(key)
        if e is None:
            raise HTTPException(409, "This delivery is no longer waiting in the stock register inbox (already added, or the register changed). Refresh the list.")
        if e["state"] == "dismissed":
            raise HTTPException(409, f"{e['material']} is marked Not billable. Bring it back first.")
        if e["kind"] == "other":
            raise HTTPException(409, f"{e['material']} belongs to another service in the register ({e['service']}).")
        if e["kind"] == "unlinked":
            raise HTTPException(409, f"{e['material']} is not linked to a BOQ line yet. Use Link it first.")
        code = str(it.get("item_code") or e.get("item_code") or "")
        cand = next((c for c in e["lines"] if c["item_code"] == code), None)
        if cand is None:
            raise HTTPException(400, f"Choose the BOQ line {e['material']} feeds.")
        if cand["problem"]:
            raise HTTPException(409, f"{e['material']}: {cand['problem']}.")
        if cand["dup"] and not (it.get("force") or payload.get("force")):
            du = cand["dup"]
            raise HTTPException(409, f"{e['material']} on {_fdate(e['date'])} looks like challan {du['no']} that is already entered "
                                     f"(same date, line and quantity). A delivery counts once, from stock or typed, never both.")
        no_given = str(it.get("no") or "").strip()
        data = _clean_challan(d, store, unit, {
            "no": no_given or _stock_label(store, unit, e), "date": e["date"],
            "ra": payload["ra"] if "ra" in payload else "open",
            "note": f"{e['material']} · from stock register",
            "lines": [{"item_code": code, "qty": cand["counts_as"]}]})
        store["seq"] += 1
        c = {"id": f"c{store['seq']}", **data, "created": dt.datetime.now().isoformat(timespec="seconds"),
             "src": {"kind": "stock", "key": key, "material": e["material"], "unit": e["unit"], "stock_qty": e["qty"],
                     "factor": cand["factor"], "service": e["service"], "opening": e["opening"], "run": inbox["run"]},
             "no_missing": not no_given}
        store["challans"].setdefault(unit, []).append(c)
        made.append(c["id"])
    base = _base(unit)
    store["stock_hold"][base] = [k for k in store["stock_hold"].get(base, []) if k not in seen]
    _save(d, store)
    return {"ok": True, "added": len(made), "ids": made}


@router.post("/{slug}/stock-inbox/{unit}/mark")
def stock_mark(slug: str, unit: str, payload: dict):
    """Body {action: hold|unhold|skip|unskip, keys: [...]}. skip = 'Not billable': the whole MATERIAL stops showing."""
    d = sp._need(slug)
    _svc_check(d, unit)
    store = _load(d)
    act = payload.get("action")
    if act not in ("hold", "unhold", "skip", "unskip"):
        raise HTTPException(400, "Unknown action.")
    keys = {str(k) for k in (payload.get("keys") or [])}
    inbox = _inbox(slug, d, store, unit, show_earlier=True)
    sel = [e for e in inbox["entries"] if e["key"] in keys]
    if not sel:
        raise HTTPException(404, "That delivery is no longer in the inbox.")
    base = _base(unit)
    if act in ("hold", "unhold"):
        cur = set(store["stock_hold"].get(base, []))
        cur = (cur | keys) if act == "hold" else (cur - keys)
        store["stock_hold"][base] = sorted(cur)
    else:
        cur = set(store["stock_skip"].get(base, []))
        mk = {f"{e['service']}|{linkage._norm(e['material'])}" for e in sel}
        cur = (cur | mk) if act == "skip" else (cur - mk)
        store["stock_skip"][base] = sorted(cur)
    _save(d, store)
    return {"ok": True}


@router.post("/{slug}/stock-inbox/{unit}/link")
def stock_link(slug: str, unit: str, payload: dict):
    """Body {key, item_code, factor?}: link the stock material of an inbox delivery to a BOQ line of this unit -- the
    same item-master link (links.json) as Site Progress > Link stock, so forecasts see it too. The factor is
    register units per 1 BOQ unit, needed only when the two units differ; it is never guessed."""
    d = sp._need(slug)
    _svc_check(d, unit)
    store = _load(d)
    base = _base(unit)
    inbox = _inbox(slug, d, store, unit, show_earlier=True)
    e = next((x for x in inbox["entries"] if x["key"] == payload.get("key")), None)
    if e is None:
        raise HTTPException(404, "That delivery is no longer in the inbox.")
    if e["scope"] != "this":
        raise HTTPException(409, f"{e['material']} belongs to another service in the register ({e['service']}). Link it from that service.")
    ln = next((l for l in _lines(d, unit) if l["item_code"] == str(payload.get("item_code"))), None)
    if ln is None:
        raise HTTPException(400, "Choose a billable BOQ line of this service.")
    same = bool(ln["unit"].strip()) and str(e["unit"]).strip().upper() == ln["unit"].strip().upper()
    f = _num(payload.get("factor"))
    if not same and (f is None or f <= 0):
        raise HTTPException(400, f"The register counts {e['material']} in {e['unit']} and the BOQ line in {ln['unit']}. "
                                 f"Enter how many {e['unit']} make 1 {ln['unit']}.")
    links = sp._load_links(d)
    cur = sp._norm_link_entries(links.get(base, {}).get(ln["item_code"], []))
    norm = linkage._norm(e["material"])
    cur = [x for x in cur if linkage._norm(x["material"]) != norm]
    cur.append({"material": e["material"], "factor": None if same and f is None else f})
    links.setdefault(base, {})[ln["item_code"]] = cur
    (d / "links.json").write_text(json.dumps(links, ensure_ascii=False))
    return {"ok": True}


@router.post("/{slug}/challans/{service}/{cid}/resync")
def resync_challan(slug: str, service: str, cid: str):
    """Take the register's CURRENT quantity into a challan that was added from stock (open RA only)."""
    d = sp._need(slug)
    _svc_check(d, service)
    store = _load(d)
    c = next((x for x in store["challans"].get(service, []) if x["id"] == cid), None)
    if c is None or not c.get("src"):
        raise HTTPException(404, "This is not a challan added from the stock register.")
    if _challan_frozen(store, service, c):
        raise HTTPException(409, f"Challan {c['no']} is in RA-{c['ra']:02d}, which is locked. Reopen that RA to change it.")
    now = _register_qty(slug).get(c["src"]["key"])
    if now is None:
        raise HTTPException(409, "That delivery is no longer in the stock register. Delete the challan, or keep it as it is.")
    f = c["src"].get("factor") or 1.0
    code = next(iter(c["qty"]))
    c["qty"][code] = round(now / f, 4)
    c["src"]["stock_qty"] = now
    _save(d, store)
    return {"ok": True}


# ---- wire from the stock register
def _wsource(w):
    return w.get("source")


def _wire_stock(slug, d, store, unit):
    """The wire in the register for THIS service: its materials (parsed size + colour), which the engineer has
    confirmed, and the wire challans they make, one per delivery date."""
    base = _base(unit)
    label = sp._forecast_service(base)
    scheme = _wscheme(store)
    ph = set(scheme["phases"])
    rows, run = _stock_rows(slug)
    cmap = store["wire_map"].get(base, {})
    mats, desired = {}, {}
    for r in rows:
        if r["service"] != label:
            continue
        p = _wire_parse(r["material"], scheme)
        if not p:
            continue
        mkey = f"{r['service']}|{linkage._norm(r['material'])}"
        m = mats.setdefault(mkey, {"key": mkey, "material": r["material"], "size": p["size"], "colour": p["colour"],
                                   "counted": p["colour"] in ph, "confirmed": mkey in cmap, "metres": 0.0})
        m["metres"] += r["qty"]
        if mkey in cmap:
            k = r["date"] + ("|o" if r["opening"] else "")
            dd = desired.setdefault(k, {"date": r["date"], "opening": r["opening"], "qty": {}})
            cols = dd["qty"].setdefault(p["size"], {})
            cols[p["colour"]] = cols.get(p["colour"], 0.0) + r["qty"]
    return {"available": bool(mats), "run": run, "materials": sorted(mats.values(), key=lambda m: (float(m["size"]), m["colour"])),
            "desired": desired}


def _wire_stock_label(w, dd):
    base = "Stock opening" if dd["opening"] else f"Stock IN {_fdate(dd['date'])}"
    taken = {c["no"].strip().lower() for c in w["challans"]}
    no, n = base, 1
    while no.lower() in taken:
        n += 1
        no = f"{base} ({n})"
    return no


@router.put("/{slug}/wire/{unit}/source")
def wire_source(slug: str, unit: str, payload: dict):
    """Body {source: "stock"|"typed"}: where this unit's wire deliveries come from. Challans already in earlier RAs
    keep counting; from this RA on only the chosen source counts, so a delivery is never counted twice."""
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    new = payload.get("source")
    if new not in ("stock", "typed"):
        raise HTTPException(400, "Source must be 'stock' or 'typed'.")
    w = json.loads(json.dumps(_wunit(store, unit)))
    if _wsource(w) != new:
        w["source"] = new
        w["switch_ra"] = _open_no(store, unit)
        openn = w["switch_ra"]
        gave = sum(_w_given(w, openn).values()) - sum(_w_given(w, openn - 1).values()) if openn > 1 else sum(_w_given(w, openn).values())
        if gave > 0 and _w_check(store, w):
            old = "the stock register" if new == "typed" else "the typed wire challans"
            raise HTTPException(409, f"{raL_(openn)} already has {gave:g} m of wire given to point lines, and those metres come from {old}. "
                                     "Switching now would leave them without wire. Remove the metres given in Wire → points (step 2), "
                                     "switch, then give them again.")
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True}


@router.put("/{slug}/wire/{unit}/stock-map")
def wire_stock_map(slug: str, unit: str, payload: dict):
    """Body {confirm: [material, ...], unconfirm: [material, ...]}: the engineer confirms once which stock materials
    are which wire size and colour (read from the name, never guessed)."""
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    base = _base(unit)
    st = _wire_stock(slug, d, store, unit)
    by_name = {m["material"].strip().upper(): m for m in st["materials"]}
    cm = store["wire_map"].setdefault(base, {})
    for name in payload.get("confirm") or []:
        m = by_name.get(str(name).strip().upper())
        if m is None:
            raise HTTPException(400, f"“{name}” is not a wire material of this service in the stock register.")
        cm[m["key"]] = {"material": m["material"], "size": m["size"], "colour": m["colour"]}
    for name in payload.get("unconfirm") or []:
        m = by_name.get(str(name).strip().upper())
        if m is not None:
            cm.pop(m["key"], None)
    _save(d, store)
    return {"ok": True}


@router.post("/{slug}/wire/{unit}/stock-sync")
def wire_stock_sync(slug: str, unit: str):
    """Make / refresh one wire challan per stock IN date from the confirmed wire materials. A challan in a locked RA
    is never changed (a difference is only reported); one in an open RA follows the register."""
    d = sp._need(slug)
    store = _load(d)
    _wire_ready(d, store, unit)
    w = json.loads(json.dumps(_wunit(store, unit)))
    if _wsource(w) != "stock":
        if w.get("challans") or _wsource(w) is not None:
            raise HTTPException(409, "Switch this unit's wire source to the stock register first.")
        w["source"] = "stock"
        w["switch_ra"] = _open_no(store, unit)
    st = _wire_stock(slug, d, store, unit)
    if not any(m["confirmed"] for m in st["materials"]):
        raise HTTPException(409, "Confirm the wire materials first (size and colour of each stock material).")
    openn = _open_no(store, unit)
    existing = {(c.get("src") or {}).get("key"): c for c in w["challans"] if (c.get("src") or {}).get("kind") == "stock"}
    added = updated = removed = 0
    frozen_diff = []
    for k, dd in st["desired"].items():
        qty = {sz: {col: round(m, 4) for col, m in cols.items() if m > 0} for sz, cols in dd["qty"].items()}
        qty = {sz: cols for sz, cols in qty.items() if cols}
        if not qty:
            continue
        c = existing.get(k)
        if c is None:
            store["seq"] += 1
            w["challans"].append({"id": f"k{store['seq']}", "no": _wire_stock_label(w, dd), "date": dd["date"], "ra": openn,
                                  "note": "From stock register", "qty": qty,
                                  "src": {"kind": "stock", "key": k, "opening": dd["opening"], "run": st["run"]},
                                  "no_missing": True, "created": dt.datetime.now().isoformat(timespec="seconds")})
            added += 1
        elif c["qty"] != qty:
            if _wfrozen(store, unit, c):
                frozen_diff.append(c["no"])
            else:
                c["qty"] = qty
                updated += 1
    for k, c in existing.items():
        if k not in st["desired"] and not _wfrozen(store, unit, c):
            w["challans"] = [x for x in w["challans"] if x["id"] != c["id"]]
            removed += 1
    _wire_put(d, store, unit, w)
    _save(d, store)
    return {"ok": True, "added": added, "updated": updated, "removed": removed, "locked_differs": frozen_diff,
            "typed_kept": sum(1 for c in w["challans"] if not c.get("src") and _w_counts(w, c))}


# ------------------------------------------------------------- Excel export
# The bill goes INTO the user's own multi-sheet BOQ workbook (the saved
# boq.xlsx): original columns A..U are never touched; the service sheet gets
# payment-term columns, one qty + one ₹ column per RA per stage, and a
# "to date" summary; every RA stage gets its own annexure tab; an "RA
# Register" tab lists them all; an invoice tab is a placeholder until the
# invoice format is supplied. Locked RAs are written as VALUES (a submitted
# bill must not move), drafts as live formulas.
_INR2 = '[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;##,##0.00'
_INR0 = '[>=10000000]##\\,##\\,##\\,##0;[>=100000]##\\,##\\,##0;##,##0'
# Column X (Lumpsum Amount) is NOT used: it carries the BOQ's real "Overhead" lumpsum
# amounts. Reasons for typed quantities go in cell comments, flags in AE.
SUMMARY = [("V", "Supply %"), ("W", "Install %"), ("Z", "Supply qty to date"),
           ("AA", "Install qty to date"), ("AB", "Billed to date ₹"), ("AC", "Balance to bill ₹"),
           ("AD", "% billed"), ("AE", "Bill check")]
FIRST_BLOCK_COL = 32     # AF


def _blocks(store, service):
    """[(ra, stage)] of this service, supply stages first, then installation,
    each by RA number -- the column order on the service sheet."""
    out = []
    for stage in ("supply", "install"):
        ras = sorted([r for r in store["ras"] if r["service"] == service and stage in STAGES[r["series"]]],
                     key=lambda r: r["no"])
        out += [(r, stage) for r in ras]
    return out


def _tab_name(service, stage, ra):
    return f"{_unit_text(service)} {'Supply' if stage == 'supply' else 'Install'} RA-{ra['no']:02d}"[:31]


def export_xlsx(slug):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.formatting.rule import Rule
    from openpyxl.styles.differential import DifferentialStyle
    from openpyxl.styles.numbers import NumberFormat
    from openpyxl.comments import Comment
    from openpyxl.worksheet.properties import PageSetupProperties

    d = sp._need(slug)
    store = _load(d)
    src = d / "boq.xlsx"
    if not src.exists():
        raise HTTPException(404, "The BOQ Excel for this project is not saved. Upload the BOQ again.")
    if not store["ras"]:
        raise HTTPException(409, "No RA has been created yet, so there is nothing to export.")
    wb = openpyxl.load_workbook(str(src))

    GREEN = PatternFill("solid", fgColor="C6E0B4")
    LOCK_H, DRAFT_H = PatternFill("solid", fgColor="BFBFBF"), PatternFill("solid", fgColor="FFD966")
    LOCK_C, DRAFT_C = PatternFill("solid", fgColor="F2F2F2"), PatternFill("solid", fgColor="FFF2CC")
    MANUAL_C = PatternFill("solid", fgColor="FBE5D6")
    PEACH = PatternFill("solid", fgColor="FCE4D6")
    BOLD, NORM = Font(name="Calibri", bold=True, size=11), Font(name="Calibri", size=11)
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    dash = Rule(type="cellIs", operator="equal", formula=["0"],
                dxf=DifferentialStyle(numFmt=NumberFormat(numFmtId=170, formatCode='"-"')))
    meta = store["meta"]

    def head(ws, col, text, fill=GREEN, width=14):
        c = ws[f"{col}1"]
        c.value, c.fill, c.font = text, fill, Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[col].width = width

    bills, tabs, reg_rows = {}, {}, []
    used_blocks = {}                 # sheet -> RA column blocks already written there (Work Orders of one service share a sheet)
    for service in dict.fromkeys(r["service"] for r in store["ras"]):
        lines = _lines(d, service)
        by_sheet = {}
        for ln in lines:
            if not ln["sheet"] or not ln["src_row"] or ln["sheet"] not in wb.sheetnames:
                raise HTTPException(409, f"{service}: Internal Ref / sheet row could not be found. Upload the BOQ once more.")
            by_sheet.setdefault(ln["sheet"], []).append(ln)
        setup = store["setup"].get(service) or {}
        blocks = _blocks(store, service)
        cols = {}
        off = max([used_blocks.get(sh, 0) for sh in by_sheet] or [0])
        for sh in by_sheet:
            used_blocks[sh] = off + len(blocks)
        for i, (ra, stage) in enumerate(blocks):
            cols[(ra["id"], stage)] = (L(FIRST_BLOCK_COL + 2 * (off + i)), L(FIRST_BLOCK_COL + 2 * (off + i) + 1))
        for ra, stage in blocks:
            bills[(ra["id"])] = bills.get(ra["id"]) or _get_bill(slug, d, store, ra)
        for sheet, slines in by_sheet.items():
            ws = wb[sheet]
            # V..AE are ProjectBase's import columns (Bundled Rate, Lumpsum Rate,
            # Model No ...). They are empty in the exported BOQ, so billing re-uses
            # them -- but a column that actually holds data is never overwritten.
            for col, text in SUMMARY:
                if ws[f"{col}1"].value != text:
                    used = next((r for r in range(2, ws.max_row + 1)
                                 if ws[f"{col}{r}"].value not in (None, "")), None)
                    if used is not None:
                        raise HTTPException(409, f"Sheet '{sheet}' already has data in column {col} (row {used}); "
                                                 f"billing will not overwrite it.")
                head(ws, col, text, width=10 if col in "VW" else (26 if col == "AE" else 16))
            ws.row_dimensions[1].height = 32
            for ra, stage in blocks:
                qc, rc = cols[(ra["id"], stage)]
                locked = ra["status"] == "locked"
                tag = "invoiced" if locked else "draft"
                wo_tag = f" · {_uname(service)}" if SEP in service else ""
                head(ws, qc, f"{STAGE_NAME[stage]} RA-{ra['no']:02d} qty ({tag}){wo_tag}", LOCK_H if locked else DRAFT_H)
                head(ws, rc, f"{STAGE_NAME[stage]} RA-{ra['no']:02d} ₹ ({tag}){wo_tag}", LOCK_H if locked else DRAFT_H, 16)
            sup_q = [cols[(r['id'], s)][0] for r, s in blocks if s == "supply"]
            ins_q = [cols[(r['id'], s)][0] for r, s in blocks if s == "install"]
            all_r = [cols[(r['id'], s)][1] for r, s in blocks]
            for ln in slines:
                r = ln["src_row"]
                sp_ = _stage_pct(setup, ln["item_code"], "supply")
                ip_ = _stage_pct(setup, ln["item_code"], "install")
                if sp_ is not None:
                    ws[f"V{r}"] = sp_
                if ip_ is not None:
                    ws[f"W{r}"] = ip_
                flags = []
                for ra, stage in blocks:
                    qc, rc = cols[(ra["id"], stage)]
                    locked = ra["status"] == "locked"
                    row = next((x for x in bills[ra["id"]]["lines"] if x["item_code"] == ln["item_code"]), None)
                    c = row.get(stage) if row else None
                    ws[f"{qc}{r}"].fill = LOCK_C if locked else DRAFT_C
                    ws[f"{rc}{r}"].fill = LOCK_C if locked else DRAFT_C
                    ws[f"{rc}{r}"].number_format = _INR2
                    if c is None or c.get("this") is None:
                        if not locked and stage == "supply":
                            ws[f"{qc}{r}"].fill = MANUAL_C
                        if not locked:
                            ws[f"{rc}{r}"] = f'=IF({qc}{r}="","",$G{r}*{qc}{r}*${"V" if stage == "supply" else "W"}{r}/100)'
                        continue
                    ws[f"{qc}{r}"] = c["this"]
                    if locked:
                        ws[f"{rc}{r}"] = c["amount"]
                    else:
                        ws[f"{rc}{r}"] = f'=IF({qc}{r}="","",$G{r}*{qc}{r}*${"V" if stage == "supply" else "W"}{r}/100)'
                    if c.get("overridden") and c.get("reason"):
                        ws[f"{qc}{r}"].comment = Comment(c["reason"], "MI")
                    if c["this"] < 0:
                        flags.append(f"Over-billed ({STAGE_NAME[stage]} RA-{ra['no']:02d})")
                if sup_q:
                    ws[f"Z{r}"] = f'=IF(COUNT({",".join(q + str(r) for q in sup_q)})=0,"",SUM({",".join(q + str(r) for q in sup_q)}))'
                if ins_q:
                    ws[f"AA{r}"] = f'=IF(COUNT({",".join(q + str(r) for q in ins_q)})=0,"",SUM({",".join(q + str(r) for q in ins_q)}))'
                rr = ",".join(x + str(r) for x in all_r)
                ws[f"AB{r}"] = f'=IF(COUNT({rr})=0,"",SUM({rr}))'
                ws[f"AC{r}"] = f'=IF(AB{r}="","",F{r}*G{r}-AB{r})'
                ws[f"AD{r}"] = f'=IF(AB{r}="","",AB{r}/(F{r}*G{r}))'
                for col in ("AB", "AC"):
                    ws[f"{col}{r}"].number_format = _INR2
                ws[f"AD{r}"].number_format = "0%"
                if flags:
                    ws[f"AE{r}"] = "; ".join(flags)
                    ws[f"AE{r}"].font = Font(color="A02622", bold=True)

        # ---- annexure tabs
        groups, _ = _groups_for(d, store, service, lines)
        first_sheet = next(iter(by_sheet))
        for ra, stage in blocks:
            name = _tab_name(service, stage, ra)
            n_ = 2
            while name.lower() in {x.lower() for x in wb.sheetnames}:
                name = _tab_name(service, stage, ra)[:28] + f" {n_}"
                n_ += 1
            prev_cols = [cols[(r2["id"], stage)][1] for r2 in store["ras"]
                         if r2["service"] == service and r2["status"] == "locked" and stage in STAGES[r2["series"]]
                         and r2["no"] < ra["no"]]
            this_col = cols[(ra["id"], stage)][1]
            pct = bills[ra["id"]]["pct"].get(stage)
            ws = wb.create_sheet(name)
            ws.sheet_properties.tabColor = "70AD47"
            for col, w in zip("ABCDEF", (12, 62, 19, 19, 19, 19)):
                ws.column_dimensions[col].width = w
            ws.merge_cells("A1:F1")
            ws["A1"] = meta.get("client") or ""
            ws["A2"], ws["B2"] = "WO. No.", _wo_no(meta, service) or "..................."
            ws.merge_cells("B2:C2")
            ws["A3"] = "Work"
            ws.merge_cells("B3:C3")
            ws["B3"] = (meta.get("work") or {}).get(service) or f"{_unit_text(service)} works"
            ws.merge_cells("D3:F3")
            ws["A4"] = "Contractor"
            ws.merge_cells("B4:C4")
            ws["B4"] = meta.get("contractor") or ""
            ws.merge_cells("D4:F4")
            ws["D4"] = f"RA-{ra['no']:02d}"
            ws.merge_cells("D5:F5")
            ws["D5"] = f"{STAGE_NAME[stage].upper()}" + (f" @ {pct:g}%" if pct is not None else "")
            for rng in ("A5:A6", "B5:B6", "C5:C6"):
                ws.merge_cells(rng)
            ws["A5"], ws["B5"], ws["C5"] = "Sr.No.", "Description of Material", "WO Amount"
            ws["D6"], ws["E6"], ws["F6"] = "PREVIOUS BILL VALUE", "THIS BILL VALUE", "TOTAL BILL VALUE"
            ws.row_dimensions[6].height = 48
            for rr_ in range(1, 7):
                for cc in range(1, 7):
                    cell = ws.cell(rr_, cc)
                    cell.border, cell.font = BOX, (BOLD if rr_ in (1, 4, 5, 6) or cc == 1 else NORM)
                    cell.alignment = Alignment(wrap_text=True, vertical="center",
                                               horizontal="center" if (rr_ >= 4 and cc >= 4) or rr_ in (5, 6) else "left")
                    if rr_ in (5, 6):
                        cell.fill = PEACH
            ws["B7"] = ws["B3"].value
            for cc in range(1, 7):
                ws.cell(7, cc).border = BOX
            row, first = 8, 8
            for g in groups:
                sh = g.get("sheet") or first_sheet
                if sh not in by_sheet:
                    continue
                ref = f"'{sh}'"
                a, b = g["from"], g["to"]
                ws[f"A{row}"], ws[f"B{row}"] = g.get("sr") or "", g.get("name") or ""
                ws[f"C{row}"] = g["wo"] if g.get("wo") is not None else f"=SUMPRODUCT({ref}!$F${a}:$F${b},{ref}!$G${a}:$G${b})"
                ws[f"D{row}"] = ("=" + "+".join(f"SUM({ref}!{pc}{a}:{pc}{b})" for pc in prev_cols)) if prev_cols else 0
                ws[f"E{row}"] = f"=SUM({ref}!{this_col}{a}:{this_col}{b})"
                ws[f"F{row}"] = f"=D{row}+E{row}"
                row += 1
            # lines that fall in no group still count -- listed by cell so the total is never short
            orphans = [ln for ln in lines if _group_of(groups, ln) is None]
            if orphans:
                ws[f"B{row}"] = "Other items (not in any group)"
                refs = lambda col: ",".join(f"'{ln['sheet']}'!{col}{ln['src_row']}" for ln in orphans)
                ws[f"C{row}"] = "=" + "+".join(f"'{ln['sheet']}'!F{ln['src_row']}*'{ln['sheet']}'!G{ln['src_row']}" for ln in orphans[:150])
                ws[f"D{row}"] = ("=" + "+".join(f"SUM({refs(pc)})" for pc in prev_cols)) if prev_cols else 0
                ws[f"E{row}"] = f"=SUM({refs(this_col)})"
                ws[f"F{row}"] = f"=D{row}+E{row}"
                row += 1
            last = row - 1
            for rr_ in range(first, last + 1):
                for cc in range(1, 7):
                    cell = ws.cell(rr_, cc)
                    cell.border, cell.font = BOX, NORM
                    if cc >= 3:
                        cell.number_format = _INR0 if cc == 3 else _INR2
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif cc == 1:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    else:
                        cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws[f"B{row}"] = "Grand Total"
            for col in "CDEF":
                ws[f"{col}{row}"] = f"=SUM({col}{first}:{col}{last})"
                ws[f"{col}{row}"].number_format = _INR0 if col == "C" else _INR2
                ws[f"{col}{row}"].alignment = Alignment(horizontal="right", vertical="center")
            for cc in range(1, 7):
                ws.cell(row, cc).font, ws.cell(row, cc).border, ws.cell(row, cc).fill = BOLD, BOX, PEACH
            ws.conditional_formatting.add(f"C8:F{row}", dash)
            ws.print_area = f"A1:F{row}"
            ws.page_setup.orientation, ws.page_setup.paperSize = "portrait", ws.PAPERSIZE_A4
            ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
            ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
            ws.sheet_view.showGridLines = False
            tabs[(ra["id"], stage)] = (name, row)
            reg_rows.append((service, STAGE_NAME[stage], f"RA-{ra['no']:02d}", ra.get("cutoff") or "",
                             "Invoiced" if ra["status"] == "locked" else "Draft", pct, name, row))

    # ---- register
    reg = wb.create_sheet("RA Register", 0)
    reg.sheet_properties.tabColor = "1F3864"
    reg["A1"] = "RA Register"
    reg["A1"].font = Font(bold=True, size=14)
    reg["A2"] = "Every RA of every service. RA-wise columns are on the service sheet, the annexure is on its own tab."
    reg["A2"].font = Font(italic=True, color="595959")
    for i, h in enumerate(["Service", "Series", "RA", "Cut-off", "Status", "Rate %", "This bill ₹",
                           "Previous ₹", "Total ₹", "Annexure sheet"], start=1):
        c = reg.cell(4, i, h)
        c.font, c.fill, c.border = BOLD, PEACH, BOX
    r = 5
    for svc, st, ra_l, cut, stt, pct, nm, g in reg_rows:
        vals = [svc, st, ra_l, cut, stt, pct, f"='{nm}'!E{g}", f"='{nm}'!D{g}", f"='{nm}'!F{g}", None]
        for i, v in enumerate(vals, start=1):
            c = reg.cell(r, i, v)
            c.border, c.font = BOX, NORM
            if i in (7, 8, 9):
                c.number_format = _INR2
        reg.cell(r, 10).value = f'=HYPERLINK("#\'{nm}\'!A1","{nm}")'
        reg.cell(r, 10).font = Font(color="0563C1", underline="single")
        if stt == "Draft":
            for i in range(1, 11):
                reg.cell(r, i).fill = DRAFT_C
        r += 1
    done = {x[0] for x in reg_rows}
    for svc in _services(d):
        if svc in done:
            continue
        s = store["setup"].get(svc)
        note = "No RA yet" if s and s.get("mode") else "Billing setup pending (payment term not set)"
        for i, v in enumerate([svc, "—", "—", "—", note, "—", "—", "—", "—", ""], start=1):
            c = reg.cell(r, i, v)
            c.border, c.font = BOX, Font(color="7F7F7F")
        r += 1
    reg.cell(r + 1, 6, "Draft total (ready to bill)").font = BOLD
    reg.cell(r + 1, 7, f'=SUMIF(E5:E{r - 1},"Draft",G5:G{r - 1})').number_format = _INR2
    for col, w in zip("ABCDEFGHIJ", (12, 14, 9, 14, 38, 8, 18, 18, 18, 30)):
        reg.column_dimensions[col].width = w
    reg.conditional_formatting.add(f"G5:I{r}", dash)
    reg.sheet_view.showGridLines = False

    # ---- Tax Invoice of every RA (the user's invoice format), just before that RA's annexure tab(s)
    cover_of = {}                                  # ra id -> tab name
    for ra in store["ras"]:
        keys = [k for k in tabs if k[0] == ra["id"]]
        if not keys:
            continue
        tag = {"supply": "Supply", "install": "Install", "together": "S+I"}[ra["series"]]
        tail = f" RA-{ra['no']:02d} INVOICE"
        nm = _sheet_safe(f"{_unit_text(ra['service'])[:31 - len(tail) - len(tag) - 1].strip()} {tag}{tail}")
        i = 2
        while nm.lower() in {x.lower() for x in wb.sheetnames}:
            nm = _sheet_safe(nm[:28] + f" {i}")
            i += 1
        ws_i = wb.create_sheet(nm)
        cover_of[ra["id"]] = nm
        ref = "+".join(f"'{tabs[k][0]}'!E{tabs[k][1]}" for k in sorted(keys, key=lambda k: k[1]))
        _cover_for(ws_i, store, ra, (bills.get(ra["id"]) or _get_bill(slug, d, store, ra))["this_total"], ref)

    # ---- CHALLAN ABSTRACT + CHALLAN of every service whose supply comes from challans, named by
    # service (the latest supply RA of the service carries every earlier RA and every challan)
    extra = []
    for service in dict.fromkeys(r["service"] for r in store["ras"]):
        sras = [r for r in store["ras"] if r["service"] == service and "supply" in STAGES[r["series"]]]
        if not sras or _source(store, service) != "challan":
            continue
        last = max(sras, key=lambda r: r["no"])
        room = 31 - len(" CHALLAN ABSTRACT")
        base = _sheet_safe(_unit_text(service)[:room])
        nm = {"invoice": f"~i{len(extra)}", "summary": f"~s{len(extra)}", "boq": f"~b{len(extra)}",
              "abstract": _sheet_safe(f"{base} CHALLAN ABSTRACT"), "challan": _sheet_safe(f"{base} CHALLAN")}
        for k in ("abstract", "challan"):
            while nm[k].lower() in {x.lower() for x in wb.sheetnames}:
                nm[k] = _sheet_safe(nm[k][:28] + " 2")
        _write_ra(wb, slug, d, store, last, nm, {"abstract", "challan"})
        extra += [nm["abstract"], nm["challan"]]

    order = ["RA Register"] + [n for n in wb.sheetnames if n not in ("RA Register",)
                               and n not in {t[0] for t in tabs.values()} and n not in set(cover_of.values())
                               and n not in set(extra)]
    for key, (nm_, _row) in tabs.items():
        if key[0] in cover_of and cover_of[key[0]] not in order:
            order.append(cover_of[key[0]])
        order.append(nm_)
    order += extra
    wb._sheets = [wb[n] for n in order]
    wb.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/{slug}/export")
def export_bill(slug: str):
    data = export_xlsx(slug)
    fname = f"{sp._slugify(slug)}_RA_bills_{dt.date.today().isoformat()}.xlsx"
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------------- invoice workbook
# One RA of one service, in the user's own invoice-workbook layout:
#   Annexure B-Summary BOQ  .  Annexure B-BOQ  .  CHALLAN ABTRACT  .  CHALLAN
# Every service gets the same four sheets (the last two when its supply comes
# from challans). The three row-aligned sheets share one row layout, exactly
# like the user's workbook, so a BOQ row is the same row number everywhere.
# A draft RA is written with live formulas (change a challan qty and the bill
# moves); a locked RA is written as values (a submitted bill must not move).
_BASE_COLS = 6      # A..F of Annexure B-BOQ: Sr, Description, Unit, Qty, Rate, Amt
_BLOCK = 7          # per stage: rate%, previous qty, this qty, total qty, previous value, this value, total value
_FIRST_DATA_ROW = 9
_CH_FIRST_COL = 6   # CHALLAN: first challan column (F)


def _prev_upto(store, service, series, stage, no):
    """{item_code: (qty, amount)} billed for one stage in the LOCKED RAs of the service
    numbered below `no`, whichever bill type carried that stage (from their snapshots)."""
    prev = {}
    for r in store["ras"]:
        if r["service"] != service or r["status"] != "locked" or r["no"] >= no:
            continue
        if stage not in STAGES[r["series"]]:
            continue
        for row in r["snap"]["lines"]:
            c = row.get(stage)
            if c and c.get("this") is not None:
                q, a = prev.get(row["item_code"], (0.0, 0.0))
                prev[row["item_code"]] = (q + c["this"], a + (c.get("amount") or 0.0))
    return prev


def _invoice_layout(d, store, service, lines, groups):
    """Rows of the annexure in sheet order: headings, items (billable ones carry
    their line), and one total row closing each group."""
    line_at = {(l["sheet"], l["src_row"]): l for l in lines if l["sheet"] and l["src_row"]}
    sheets = list(dict.fromkeys(g.get("sheet") or next(iter({l["sheet"] for l in lines if l["sheet"]}), None) for g in groups))
    raw = _sheet_rows(d, [s for s in sheets if s])
    out, used = [], set()
    for gi, g in enumerate(groups):
        sheet = g.get("sheet") or next(iter({l["sheet"] for l in lines if l["sheet"]}), None)
        first = len(out)
        rows = raw.get(sheet)
        if rows:
            for r in range(g["from"], min(g["to"], len(rows)) + 1):
                t = rows[r - 1]
                text = t[2] if len(t) > 2 else None
                unit = t[3] if len(t) > 3 else None
                ln = line_at.get((sheet, r))
                if ln is not None:
                    out.append({"kind": "item", "sr": t[1] if len(t) > 1 and t[1] is not None else "", "desc": ln["desc"],
                                "unit": ln["unit"], "qty": ln["boq_qty"], "line": ln})
                    used.add(ln["item_code"])
                elif unit not in (None, "") and isinstance(text, str) and text.strip():
                    q = t[5] if len(t) > 5 and t[5] is not None else (t[4] if len(t) > 4 else None)
                    out.append({"kind": "item", "sr": t[1] if len(t) > 1 and t[1] is not None else "", "desc": text.strip(),
                                "unit": str(unit), "qty": q, "line": None})
                elif isinstance(text, str) and text.strip() and not _SUBTOTAL.match(text) \
                        and not re.match(r"\s*notes?\b", text, re.I) and t[0] != "Overhead":
                    out.append({"kind": "head", "sr": t[1] if len(t) > 1 and t[1] is not None else "",
                                "desc": re.sub(r"\s+", " ", text).strip()})
        else:
            for ln in sorted((l for l in lines if _group_of(groups, l) == gi), key=lambda l: l["src_row"] or 0):
                out.append({"kind": "item", "sr": "", "desc": ln["desc"], "unit": ln["unit"], "qty": ln["boq_qty"], "line": ln})
                used.add(ln["item_code"])
        # a group that produced no item rows is not shown
        if not any(x["kind"] == "item" for x in out[first:]):
            del out[first:]
            continue
        out.append({"kind": "sub", "group": gi, "name": g.get("name") or "Items", "first": first, "last": len(out) - 1})
    orphans = [l for l in lines if l["item_code"] not in used]
    if orphans:
        first = len(out)
        out.append({"kind": "head", "sr": "", "desc": "Other items (not in any group)"})
        for ln in orphans:
            out.append({"kind": "item", "sr": "", "desc": ln["desc"], "unit": ln["unit"], "qty": ln["boq_qty"], "line": ln})
        out.append({"kind": "sub", "group": None, "name": "Other items", "first": first, "last": len(out) - 1})
    return out


SHEET_KEYS = ("invoice", "summary", "boq", "abstract", "challan")
_SERIES_PHRASE = {"supply": "Supply of", "install": "Installation of", "together": "Supply and Installation of"}
_SERIES_GST = {"supply": "Supply Amount", "install": "Installation Amount", "together": "Supply and Installation Amount"}


def _sheet_safe(t):
    """A legal Excel sheet name (no : \\ / ? * [ ] and no apostrophes, 31 characters at most)."""
    return re.sub(r"\s+", " ", re.sub(r"[:\\/?*\[\]']", " ", t)).strip()[:31] or "Sheet"


def _sheet_names(ra=None, taken=(), plain=True, tag=False):
    """The names of one RA's five sheets. plain=True: the user's own names ("Annexure B-BOQ"...);
    otherwise "<service> RA01 <sheet>", so several services / RAs can share one workbook."""
    if plain:
        return {"invoice": f"RA-{ra['no']:02d} INVOICE", "summary": "Annexure B-Summary BOQ", "boq": "Annexure B-BOQ",
                "abstract": "CHALLAN ABTRACT", "challan": "CHALLAN"}
    sfx = {"supply": "S", "install": "I", "together": ""}[ra["series"]] if tag else ""
    room = 31 - len(f" RA{ra['no']:02d}{sfx}") - len(" CH ABSTRACT")
    pre = f"{_unit_text(ra['service'])[:max(room, 4)].strip()} RA{ra['no']:02d}{sfx}"
    names = {"invoice": f"{pre} INVOICE", "summary": f"{pre} Summary", "boq": f"{pre} BOQ",
             "abstract": f"{pre} CH ABSTRACT", "challan": f"{pre} CHALLAN"}
    out = {}
    for k, n in names.items():
        n = _sheet_safe(n)
        i = 2
        while n.lower() in {t.lower() for t in taken} | {t.lower() for t in out.values()}:
            n = _sheet_safe(names[k])[:28] + f" {i}"
            i += 1
        out[k] = n
    return out


def _cover_for(ws, store, ra, amount, amount_ref=None):
    """Fill `ws` with the Tax Invoice of one RA (the user's invoice format). `amount` is this
    RA's bill; on a draft `amount_ref` (a formula) lets it follow the annexure sheet."""
    from . import billing_cover
    m = store["meta"]
    live = ra["status"] == "draft"
    service, series = ra["service"], ra["series"]
    gst = (ra.get("inv_gst") if not live else None) or {"sgst": m.get("sgst"), "cgst": m.get("cgst")}
    return billing_cover.build(ws, {
        "live": live, "amount": amount, "amount_ref": amount_ref,
        "client": m.get("client"), "address": m.get("client_address"), "place": m.get("place_of_supply"),
        "gstin": m.get("client_gstin"), "project": m.get("project_name"), "state": m.get("client_state"),
        "state_code": m.get("client_state_code"), "inv_no": ra.get("inv_no"), "inv_date": ra.get("inv_date"),
        "wo": _wo_no(m, service), "ra_no": ra["no"], "service": service,
        "desc": (m.get("inv_desc") or {}).get(service) or f"{_SERIES_PHRASE[series]} {_unit_paren(service)} Work",
        "sac": (m.get("sac") or {}).get(service), "gst_label": _SERIES_GST[series],
        "sgst": _num(gst.get("sgst")), "cgst": _num(gst.get("cgst")),
        "contractor": m.get("contractor"),
        "supplier": {"gstin": m.get("supplier_gstin"), "state": m.get("supplier_state"),
                     "state_code": m.get("supplier_state_code"), "pan": m.get("supplier_pan"),
                     "category": m.get("supplier_category")}})


def invoice_xlsx(slug, ra_id, want=None):
    """One RA as a workbook in the user's own layout: Tax Invoice cover, Annexure B-Summary BOQ,
    Annexure B-BOQ, CHALLAN ABTRACT, CHALLAN. `want`: the sheets to keep (default all)."""
    import openpyxl
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_ra(wb, slug, d, store, ra, _sheet_names(ra), set(want) if want else set(SHEET_KEYS))
    if not wb.sheetnames:
        raise HTTPException(400, "Tick at least one sheet.")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _style_challan_sheets(ws_ch, ws_abs, nC, this_c, abs_tot, n_ra, last_row):
    """The look of the user's own CHALLAN / CHALLAN ABTRACT tabs: a boxed header block
    (Challan Date / Challan No / RA tag over every challan column, the three bill columns
    boxed to match), centred bold labels, the row heights of his sheet, centred quantities.
    Formatting only -- no value or formula is touched."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as L
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    NOFILL = PatternFill(fill_type=None)
    B10, N10 = Font(name="Calibri", bold=True, size=10), Font(name="Calibri", size=10)
    mid = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def box(w, r1, c1, r2, c2, font=B10, fill=NOFILL):
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                x = w.cell(r, c)
                x.border, x.alignment, x.font, x.fill = BOX, mid, font, fill

    def labels(w):
        for r in (2, 3, 4, 6):
            w.cell(r, 1).font = B10
            w.cell(r, 1).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for r in (2, 3, 4):
            w.cell(r, 2).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        w.cell(6, 2).alignment = Alignment(vertical="center", wrap_text=True)
        w["A1"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    def data_rows(w, lastc, bold_from):
        for r in range(_FIRST_DATA_ROW, last_row + 1):
            for c in range(3, lastc + 1):
                x = w.cell(r, c)
                x.alignment = Alignment(horizontal="center", vertical="center")
                if c >= bold_from:
                    x.font = B10

    # ---- CHALLAN
    labels(ws_ch)
    for r, h in ((3, 32), (4, 22), (5, 22), (6, 26), (7, 22), (8, 22)):
        ws_ch.row_dimensions[r].height = h
    box(ws_ch, 8, 1, 8, this_c)                                   # Sr. No ... Qty, spacer, every challan, the three bill columns
    for c in range(_CH_FIRST_COL, this_c + 1):
        box(ws_ch, 3, c, 7, c)
        ws_ch.cell(2, c).alignment = Alignment(horizontal="center")
    for c in (_CH_FIRST_COL + nC, _CH_FIRST_COL + nC + 1, _CH_FIRST_COL + nC + 2):
        ws_ch.cell(8, c).font = B10
    # the block with the work details gets one outer frame, like the original
    for r in range(1, 8):
        for c in range(1, 6):
            x = ws_ch.cell(r, c)
            x.border = Border(left=thin if c == 1 else None, right=thin if c == 5 else None,
                              top=thin if r == 1 else None, bottom=thin if r == 7 else None)
    data_rows(ws_ch, this_c, _CH_FIRST_COL + nC)

    # ---- CHALLAN ABTRACT
    labels(ws_abs)
    ws_abs.row_dimensions[7].height = 32
    ws_abs.row_dimensions[8].height = 22
    box(ws_abs, 8, 1, 8, abs_tot)
    box(ws_abs, 7, 5, 7, 4 + n_ra)                                # CERTIFIED QTY over the RA columns
    box(ws_abs, 7, abs_tot, 7, abs_tot)
    if n_ra > 1:
        ws_abs.merge_cells(start_row=7, start_column=5, end_row=7, end_column=4 + n_ra)
    ws_abs.cell(8, abs_tot).value = None                          # TOTAL QTY is said once, over both header rows
    ws_abs.merge_cells(start_row=7, start_column=abs_tot, end_row=8, end_column=abs_tot)
    data_rows(ws_abs, abs_tot, abs_tot)


def _write_ra(wb, slug, d, store, ra, names, want):
    """Write the sheets of one RA into `wb` under `names`. Sheets not in `want` are built
    (the figures chain through them) and then dropped; a draft's formulas that pointed at
    a dropped sheet are written as values instead, so nothing is left broken."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.formatting.rule import Rule
    from openpyxl.styles.differential import DifferentialStyle
    from openpyxl.styles.numbers import NumberFormat
    from openpyxl.comments import Comment
    from openpyxl.worksheet.properties import PageSetupProperties
    service, series, no = ra["service"], ra["series"], ra["no"]
    stages = STAGES[series]
    live = ra["status"] == "draft"
    setup = store["setup"].get(service) or {}
    meta = store["meta"]
    bill = _get_bill(slug, d, store, ra)
    lines = _lines(d, service)
    if not lines:
        raise HTTPException(409, f"{service} has no billable lines.")
    groups, _ = _groups_for(d, store, service, lines)
    if not groups:
        groups = [{"sr": "", "name": f"{_unit_text(service)} works", "sheet": None, "from": 1, "to": 10 ** 6, "wo": None}]
    layout = _invoice_layout(d, store, service, lines, groups)
    brow = {r["item_code"]: r for r in bill["lines"]}
    use_ch = "supply" in stages and _source(store, service) == "challan"
    ras_n = list(range(1, no + 1))
    challans = sorted([c for c in _challans(store, service) if c.get("ra") is not None and c["ra"] <= no],
                      key=lambda c: (c["ra"], c["date"], c["no"])) if use_ch else []

    # ---- figures per line / stage
    fig = {}
    prevs = {s: _prev_upto(store, service, series, s, no) for s in stages}
    for ln in lines:
        code = ln["item_code"]
        fig[code] = {}
        for s in stages:
            row = brow.get(code)
            c = row.get(s) if row else None
            pq, pa = prevs[s].get(code, (0.0, 0.0))
            pct = c["pct"] if c else _stage_pct(setup, code, s)
            rate = row["rate"] if row else ln["rate"]
            this_q = c["this"] if c and c["this"] is not None else 0.0
            this_v = c["amount"] if c and c["amount"] is not None else 0.0
            fig[code][s] = {"pct": pct, "rate": rate, "pq": pq, "pa": pa, "tq": this_q, "tv": this_v,
                            "xq": (c or {}).get("extra") or 0.0, "xv": (c or {}).get("extra_amount") or 0.0,
                            "acc": bool((c or {}).get("accepted")),
                            "ov": bool(c and c["overridden"]), "reason": (c or {}).get("reason", ""),
                            "ratepct": None if pct is None else rate * pct / 100.0}

    GREEN = PatternFill("solid", fgColor="C6E0B4")
    PEACH = PatternFill("solid", fgColor="FCE4D6")
    DRAFT_F = PatternFill("solid", fgColor="FFF2CC")
    EXTRA_F = PatternFill("solid", fgColor="FFE699")
    extra_rows = []
    BOLD, NORM = Font(name="Calibri", bold=True, size=11), Font(name="Calibri", size=11)
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    dash = Rule(type="cellIs", operator="equal", formula=["0"],
                dxf=DifferentialStyle(numFmt=NumberFormat(numFmtId=170, formatCode='"-"')))
    QTY = "#,##0.00"

    INV_N, SUM_N, BOQ_N, ABS_N, CH_N = (names[k] for k in SHEET_KEYS)
    ws_inv = wb.create_sheet(INV_N)
    ws_sum = wb.create_sheet(SUM_N)
    ws = wb.create_sheet(BOQ_N)
    ws_abs = wb.create_sheet(ABS_N) if use_ch else None
    ws_ch = wb.create_sheet(CH_N) if use_ch else None
    ws_wire = wb.create_sheet(_wire_sheet_name(names, wb.sheetnames)) if use_ch and "challan" in want and _wunit(store, service).get("challans") else None
    # sheets that will be dropped: a formula pointing at one of them becomes a value
    dead = [names[k] for k in SHEET_KEYS if k not in want]
    dead_tok = [f"'{n}'!" for n in dead]

    def put(w, ref, val, f=None, fmt=None, bold=False):
        w[ref] = ("=" + f) if (live and f is not None and not any(t in f for t in dead_tok)) else val
        if fmt:
            w[ref].number_format = fmt
        if bold:
            w[ref].font = BOLD

    work = (meta.get("work") or {}).get(service) or f"{_unit_text(service)} works"

    def header(w, lastcol):
        w["A1"] = meta.get("client") or ""
        w.merge_cells("A1:B1")
        w["A2"], w["B2"] = "WO. No.", _wo_no(meta, service) or "…..............."
        w["A3"], w["B3"] = "Work", work
        w["A4"], w["B4"] = "Contractor", meta.get("contractor") or ""
        for r_ in range(1, 5):
            w.cell(r_, 1).font = BOLD
        w["A1"].font = Font(name="Calibri", bold=True, size=13)
        w["A6"], w["B6"] = "Annexure B", f"{_unit_text(service)} Works"
        w["A6"].font = w["B6"].font = BOLD
        if live:
            w.cell(5, 2, "DRAFT — not invoiced yet, numbers can still change").font = Font(name="Calibri", bold=True, color="A02622")

    # ---- layout row numbers
    n = len(layout)
    row_of = {i: _FIRST_DATA_ROW + i for i in range(n)}
    last_row = _FIRST_DATA_ROW + n - 1

    # ---- CHALLAN
    nC = len(challans)
    lc = L(_CH_FIRST_COL + nC - 1) if nC else L(_CH_FIRST_COL)
    tot_c, prev_c, this_c = _CH_FIRST_COL + nC, _CH_FIRST_COL + nC + 1, _CH_FIRST_COL + nC + 2
    cqty_by_ra = {k: {} for k in ras_n}      # {ra: {code: qty}}
    if use_ch:
        header(ws_ch, this_c)
        for j, c in enumerate(challans):
            col = L(_CH_FIRST_COL + j)
            put(ws_ch, f"{col}2", sum(c["qty"].values()), f"SUM({col}{_FIRST_DATA_ROW}:{col}{max(last_row, _FIRST_DATA_ROW)})", QTY, True)
            ws_ch[f"{col}3"], ws_ch[f"{col}4"] = "Challan Date", dt.date.fromisoformat(c["date"])
            ws_ch[f"{col}4"].number_format = "dd-mmm-yy"
            ws_ch[f"{col}5"], ws_ch[f"{col}6"] = "Challan No", (int(c["no"]) if c["no"].isdigit() else c["no"])
            ws_ch[f"{col}7"], ws_ch[f"{col}8"] = f"RA-{c['ra']:02d}", "Qty"
            ws_ch.column_dimensions[col].width = 11
            for k, q in c["qty"].items():
                cqty_by_ra[c["ra"]][k] = cqty_by_ra[c["ra"]].get(k, 0.0) + q
        for cc_, t in ((tot_c, "Total QTY"), (prev_c, "Previous Bill QTY"), (this_c, "This Bill QTY")):
            ws_ch.cell(3, cc_, t)
            ws_ch.cell(8, cc_, "Qty")
            ws_ch.column_dimensions[L(cc_)].width = 13
        ws_ch.cell(7, this_c, f"RA-{no:02d}")
        for col_i, t in enumerate(("Sr. No", "Description", "Unit", "Qty"), start=1):
            ws_ch.cell(8, col_i, t)
        # ABSTRACT headers
        header(ws_abs, 0)
        ws_abs["E7"] = "CERTIFIED QTY"
        for i, k in enumerate(ras_n):
            ws_abs.cell(8, 5 + i, f"RA {k:02d}")
            ws_abs.column_dimensions[L(5 + i)].width = 11
        abs_tot = 5 + len(ras_n)
        ws_abs.cell(7, abs_tot, "TOTAL QTY")
        ws_abs.cell(8, abs_tot, "TOTAL QTY")
        ws_abs.column_dimensions[L(abs_tot)].width = 12
        for col_i, t in enumerate(("Sr. No", "Description", "Unit", "Qty"), start=1):
            ws_abs.cell(8, col_i, t)
    else:
        abs_tot = None

    if ws_wire is not None:
        header(ws_wire, 12)
        _fill_wire_sheet(ws_wire, store, service, no, lines, BOLD)

    # ---- B-BOQ headers
    header(ws, 0)
    for col_i, t in enumerate(("Sr. No", "Description", "Unit", "Qty", "Rate", "Amt"), start=1):
        ws.cell(8, col_i, t)
    bcol = {s: 7 + _BLOCK * i for i, s in enumerate(stages)}     # first column of each stage's block
    for s in stages:
        c0 = bcol[s]
        pct = bill["pct"].get(s)
        pcts = f"{pct:g}% " if pct is not None else ""
        ws.merge_cells(start_row=7, start_column=c0, end_row=7, end_column=c0 + _BLOCK - 1)
        ws.cell(7, c0, f"RA-{no:02d}" + (f" · {STAGE_NAME[s].upper()}" if len(stages) > 1 else ""))
        for k, t in enumerate((f"{pcts}RATE OF {STAGE_NAME[s].upper()}", "PREVIOUS BILL QTY", "THIS BILL QTY", "TOTAL QTY",
                               "PREVIOUS BILL VALUE", "THIS BILL VALUE", "TOTAL BILL VALUE")):
            ws.cell(8, c0 + k, t)
    last_col = 6 + _BLOCK * len(stages)

    sub_rows = {}
    pyv = {}        # python-side sums for value mode: sub index -> {stage: [pv, tv]}, and F sum
    for i, e in enumerate(layout):
        r = row_of[i]
        if e["kind"] == "head":
            for w in [ws] + ([ws_abs, ws_ch] if use_ch else []):
                w.cell(r, 1, e["sr"] if e["sr"] != "" else None)
                w.cell(r, 2, e["desc"]).font = BOLD if len(e["desc"]) <= 110 else NORM    # long spec paragraphs stay plain
                w.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
                w.cell(r, 1).alignment = Alignment(vertical="top")
            continue
        if e["kind"] == "sub":
            sub_rows[i] = r
            a, b = row_of[e["first"]], r - 1
            title = f"TOTAL FOR {e['name']}"
            for w in [ws] + ([ws_abs, ws_ch] if use_ch else []):
                w.cell(r, 2, title).font = BOLD
                w.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
            items = [x for x in layout[e["first"]:i] if x["kind"] == "item" and x["line"]]
            put(ws, f"F{r}", sum(x["line"]["rate"] * x["line"]["boq_qty"] for x in items), f"SUM(F{a}:F{b})", _INR2, True)
            for s in stages:
                c0 = bcol[s]
                for k, key in ((4, "pa"), (5, "tv")):
                    col = L(c0 + k)
                    put(ws, f"{col}{r}", sum(fig[x['line']['item_code']][s][key] for x in items), f"SUM({col}{a}:{col}{b})", _INR2, True)
                col = L(c0 + 6)
                put(ws, f"{col}{r}", sum(fig[x['line']['item_code']][s]['pa'] + fig[x['line']['item_code']][s]['tv'] for x in items),
                    f"SUM({col}{a}:{col}{b})", _INR2, True)
            continue
        ln = e["line"]
        for w in [ws] + ([ws_abs, ws_ch] if use_ch else []):
            w.cell(r, 1, e["sr"] if e["sr"] != "" else None)
            w.cell(r, 2, e["desc"])
            w.cell(r, 3, e["unit"])
            w.cell(r, 4, e["qty"])
            w.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
            w.cell(r, 1).alignment = Alignment(vertical="top")
        if ln is None:
            continue
        ws.cell(r, 4).number_format = QTY
        ws.cell(r, 5, ln["rate"]).number_format = _INR2
        put(ws, f"F{r}", ln["rate"] * ln["boq_qty"], f"E{r}*D{r}", _INR2)
        code = ln["item_code"]
        for s in stages:
            f_, c0 = fig[code][s], bcol[s]
            G, H, I, J, K, Lc, M = (L(c0 + k) for k in range(7))
            if f_["pct"] is None:
                continue
            pctlit = f"{f_['pct']:g}"
            put(ws, f"{G}{r}", f_["ratepct"], f"E{r}*{pctlit}/100", _INR2)
            ws[f"{H}{r}"] = f_["pq"]
            chain = (use_ch and s == "supply" and not f_["ov"])
            if chain:
                # the cap is the BOQ qty (or what an earlier RA already billed); a line the person accepted above the BOQ has none
                if f_["acc"]:
                    put(ws, f"{I}{r}", f_["tq"], f"'{ABS_N}'!{L(abs_tot)}{r}-{H}{r}")
                elif f_["pq"] > ln["boq_qty"] + 1e-9:
                    put(ws, f"{I}{r}", f_["tq"], f"MIN(MAX(D{r},{H}{r}),'{ABS_N}'!{L(abs_tot)}{r})-{H}{r}")
                else:
                    put(ws, f"{I}{r}", f_["tq"], f"MIN(D{r},'{ABS_N}'!{L(abs_tot)}{r})-{H}{r}")
            else:
                ws[f"{I}{r}"] = f_["tq"]
            put(ws, f"{J}{r}", f_["pq"] + f_["tq"], f"{H}{r}+{I}{r}")
            if abs(f_["pa"] - f_["pq"] * (f_["ratepct"] or 0.0)) < 0.005:
                put(ws, f"{K}{r}", f_["pa"], f"{H}{r}*{G}{r}")
            else:
                ws[f"{K}{r}"] = f_["pa"]
                ws[f"{K}{r}"].comment = Comment("Earlier RAs were billed at a different rate or %, so this is their actual billed value.", "MI")
            put(ws, f"{Lc}{r}", f_["tv"], f"{I}{r}*{G}{r}")
            put(ws, f"{M}{r}", f_["pa"] + f_["tv"], f"{K}{r}+{Lc}{r}")
            for col in (H, I, J):
                ws[f"{col}{r}"].number_format = QTY
            for col in (K, Lc, M):
                ws[f"{col}{r}"].number_format = _INR2
            if f_["ov"] and f_["reason"]:
                ws[f"{I}{r}"].comment = Comment(f_["reason"], "MI")
            if f_["xq"] > 0:                                   # extra over the BOQ: yellow, with a note saying how much
                for col in (I, Lc):
                    ws[f"{col}{r}"].fill = EXTRA_F
                note = f"Extra over BOQ: {f_['xq']:g} {ln['unit']} above the BOQ quantity of {ln['boq_qty']:g}. Accepted in Material Intelligence."
                ws[f"{I}{r}"].comment = Comment(note + (f"\n{f_['reason']}" if f_["ov"] and f_["reason"] else ""), "MI")
                extra_rows.append((e["sr"], e["desc"], e["unit"], ln["boq_qty"], f_["pq"] + f_["tq"], f_["xq"], f_["ratepct"], f_["xv"], s))
            if f_["tq"] < 0:
                ws[f"{I}{r}"].font = Font(color="A02622", bold=True)
        if use_ch:
            ch_range = f"${L(_CH_FIRST_COL)}$7:${lc}$7"
            for j, c in enumerate(challans):
                q = c["qty"].get(code)
                if q is not None:
                    ws_ch.cell(r, _CH_FIRST_COL + j, q).number_format = QTY
            put(ws_ch, f"{L(tot_c)}{r}", sum(cqty_by_ra[k].get(code, 0.0) for k in ras_n),
                f"SUM({L(_CH_FIRST_COL)}{r}:{lc}{r})" if nC else None, QTY)
            before = [k for k in ras_n if k < no]
            put(ws_ch, f"{L(prev_c)}{r}", sum(cqty_by_ra[k].get(code, 0.0) for k in before),
                ("+".join(f'SUMIF({ch_range},"RA-{k:02d}",{L(_CH_FIRST_COL)}{r}:{lc}{r})' for k in before)) if (before and nC) else None, QTY)
            put(ws_ch, f"{L(this_c)}{r}", cqty_by_ra[no].get(code, 0.0), f"{L(tot_c)}{r}-{L(prev_c)}{r}", QTY)
            for k_i, k in enumerate(ras_n):
                put(ws_abs, f"{L(5 + k_i)}{r}", cqty_by_ra[k].get(code, 0.0),
                    f'SUMIF(\'{CH_N}\'!{ch_range},"RA-{k:02d}",\'{CH_N}\'!{L(_CH_FIRST_COL)}{r}:{lc}{r})' if nC else None, QTY)
            put(ws_abs, f"{L(abs_tot)}{r}", sum(cqty_by_ra[k].get(code, 0.0) for k in ras_n),
                f"SUM(E{r}:{L(abs_tot - 1)}{r})", QTY)
            ws_abs.cell(r, 4).number_format = QTY
            ws_ch.cell(r, 4).number_format = QTY

    # ---- widths / look (B-BOQ, ABSTRACT, CHALLAN)
    sheets_ = [(ws, last_col)] + ([(ws_abs, abs_tot), (ws_ch, this_c)] if use_ch else [])
    for w, lastc in sheets_:
        for col, wd in zip("ABCD", (10, 78, 7.5, 9)):
            w.column_dimensions[col].width = wd
        w.freeze_panes = "A9"
        w.sheet_view.showGridLines = False
        for r_ in range(8, 9):
            for cc_ in range(1, lastc + 1):
                cell = w.cell(r_, cc_)
                cell.font, cell.fill, cell.border = BOLD, PEACH, BOX
                cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        w.row_dimensions[8].height = 44
        for r_ in range(_FIRST_DATA_ROW, last_row + 1):
            for cc_ in range(1, lastc + 1):
                w.cell(r_, cc_).border = BOX
        w.conditional_formatting.add(f"D{_FIRST_DATA_ROW}:{L(lastc)}{last_row}", dash)
        w.page_setup.orientation, w.page_setup.paperSize = "landscape", w.PAPERSIZE_A4
        w.page_setup.fitToWidth, w.page_setup.fitToHeight = 1, 0
        w.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        w.print_title_rows = "8:8"
        w.print_area = f"A1:{L(lastc)}{last_row}"
    if use_ch:
        _style_challan_sheets(ws_ch, ws_abs, nC, this_c, abs_tot, len(ras_n), last_row)
    for s in stages:
        c0 = bcol[s]
        ws.cell(7, c0).font = BOLD
        ws.cell(7, c0).alignment = Alignment(horizontal="center")
        for k in range(_BLOCK):
            ws.column_dimensions[L(c0 + k)].width = 15 if k in (0, 4, 5, 6) else 12.5
            ws.cell(7, c0 + k).fill = GREEN
    ws.column_dimensions["E"].width = 13
    ws.column_dimensions["F"].width = 15

    # ---- Summary
    w = ws_sum
    header(w, 0)
    w.column_dimensions["A"].width = 11
    w.column_dimensions["B"].width = 60
    w.column_dimensions["C"].width = 16
    ng = 1 + len(stages) + (1 if len(stages) > 1 else 0)    # blocks: one per stage (+ combined)
    blocks = [(s, 4 + 3 * i) for i, s in enumerate(stages)]
    if len(stages) > 1:
        blocks.append(("both", 4 + 3 * len(stages)))
    for s, c0 in blocks:
        w.merge_cells(start_row=5, start_column=c0, end_row=5, end_column=c0 + 2)
        pct = bill["pct"].get(s) if s != "both" else None
        w.cell(5, c0, ("TOTAL" if s == "both" else f"{STAGE_NAME[s].upper()}" + (f" @ {pct:g}%" if pct is not None else "")))
        for k, t in enumerate(("PREVIOUS BILL VALUE", "THIS BILL VALUE", "TOTAL BILL VALUE")):
            w.cell(6, c0 + k, t)
            w.column_dimensions[L(c0 + k)].width = 18
    w.merge_cells(start_row=4, start_column=4, end_row=4, end_column=3 + 3 * len(blocks))
    w.cell(4, 4, f"RA-{no:02d}")
    w["A6"], w["B6"], w["C6"] = "Sr.No.", "Description of Material", "WO Amount"
    w["B7"] = work
    lastc_s = 3 + 3 * len(blocks)
    for r_ in range(4, 7):
        for cc_ in range(1, lastc_s + 1):
            cell = w.cell(r_, cc_)
            cell.font, cell.border = BOLD, BOX
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            if r_ >= 5:
                cell.fill = PEACH
    w.row_dimensions[6].height = 32
    srow = 8
    gfirst = srow
    seq = 0
    bq = f"'{BOQ_N}'!"
    for i, e in enumerate(layout):
        if e["kind"] != "sub":
            continue
        seq += 1
        g = groups[e["group"]] if e["group"] is not None else None
        items = [x for x in layout[e["first"]:i] if x["kind"] == "item" and x["line"]]
        wo_calc = sum(x["line"]["rate"] * x["line"]["boq_qty"] for x in items)
        r = sub_rows[i]
        w.cell(srow, 1, (g.get("sr") if g and g.get("sr") else seq))
        w.cell(srow, 2, e["name"])
        if g and g.get("wo") is not None:
            w.cell(srow, 3, g["wo"])
        else:
            put(w, f"C{srow}", wo_calc, f"{bq}F{r}")
        w.cell(srow, 3).number_format = _INR0
        for s, c0 in blocks:
            if s == "both":
                for k in range(3):
                    cols = [L(bb + k) for ss, bb in blocks if ss != "both"]
                    put(w, f"{L(c0 + k)}{srow}", sum(w.cell(srow, bb + k).value if not isinstance(w.cell(srow, bb + k).value, str) else 0
                                                     for ss, bb in blocks if ss != "both"),
                        "+".join(f"{c}{srow}" for c in cols), _INR2)
                continue
            k0 = bcol[s]
            vals = [sum(fig[x["line"]["item_code"]][s]["pa"] for x in items), sum(fig[x["line"]["item_code"]][s]["tv"] for x in items)]
            for k, (src, v) in enumerate(((4, vals[0]), (5, vals[1]), (6, vals[0] + vals[1]))):
                put(w, f"{L(c0 + k)}{srow}", v, f"{bq}{L(k0 + src)}{r}", _INR2)
        srow += 1
    glast = srow - 1
    w.cell(srow, 2, "Grand Total")
    for cc_ in range(3, lastc_s + 1):
        col = L(cc_)
        tot_v = sum((w.cell(r_, cc_).value if not isinstance(w.cell(r_, cc_).value, str) else 0) or 0 for r_ in range(gfirst, glast + 1))
        put(w, f"{col}{srow}", tot_v, f"SUM({col}{gfirst}:{col}{glast})", _INR0 if cc_ == 3 else _INR2)
    for r_ in range(gfirst, srow + 1):
        for cc_ in range(1, lastc_s + 1):
            cell = w.cell(r_, cc_)
            cell.border = BOX
            cell.font = BOLD if r_ == srow else NORM
            if r_ == srow:
                cell.fill = PEACH
            if cc_ >= 3:
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif cc_ == 2:
                cell.alignment = Alignment(wrap_text=True, vertical="center")
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center")
    w.conditional_formatting.add(f"C{gfirst}:{L(lastc_s)}{srow}", dash)
    w.sheet_view.showGridLines = False
    w.page_setup.orientation, w.page_setup.paperSize = "landscape", w.PAPERSIZE_A4
    w.page_setup.fitToWidth, w.page_setup.fitToHeight = 1, 0
    w.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    if live:
        for s_ in (ws_sum, ws, ws_abs, ws_ch):
            if s_ is not None:
                s_.sheet_properties.tabColor = "FFD966"

    # ---- Tax Invoice cover (the user's invoice format), just before the annexures
    amount = sum(fig[code][s]["tv"] for code in fig for s in stages)
    this_col = L(blocks[-1][1] + 1)
    _cover_for(ws_inv, store, ra, amount, f"'{SUM_N}'!{this_col}{srow}" if "summary" in want else None)
    for k in SHEET_KEYS:
        if k not in want and names[k] in wb.sheetnames:
            del wb[names[k]]
    if ws_wire is not None and live:
        ws_wire.sheet_properties.tabColor = "FFD966"
    if extra_rows and "boq" in want:
        _fill_extra_sheet(wb.create_sheet(_extra_sheet_name(names, wb.sheetnames)), extra_rows, stages, bill, service, meta, live)


def _extra_sheet_name(names, taken):
    """'Extra over BOQ' in the plain layout; else the BOQ tab's own prefix + ' EXTRA' (never a clash, 31 characters at most)."""
    base = "Extra over BOQ" if names["boq"] == "Annexure B-BOQ" else _sheet_safe(names["boq"][:-len("BOQ")].rstrip() + " EXTRA")
    n, i, low = base, 2, {x.lower() for x in taken}
    while n.lower() in low:
        n = base[:28] + f" {i}"
        i += 1
    return n


def _fill_extra_sheet(ws, rows, stages, bill, service, meta, live, subject=None):
    """One page for the client: the lines billed above their BOQ quantity in this RA (values only, it is a record, not a calculation).
    Only exists when something was accepted."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    BOLD = Font(name="Calibri", bold=True, size=11)
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["A1"] = meta.get("client") or ""
    ws["A1"].font = Font(name="Calibri", bold=True, size=13)
    ws["A2"], ws["B2"] = "Work", f"{_unit_text(service)} works"
    ws["A3"], ws["B3"] = "Subject", subject or f"Quantity billed above the BOQ quantity, RA-{bill['ra']['no']:02d}"
    ws["A4"], ws["B4"] = "Contractor", meta.get("contractor") or ""
    for r_ in (2, 3, 4):
        ws.cell(r_, 1).font = BOLD
    if live:
        ws.cell(5, 2, "DRAFT — not invoiced yet, numbers can still change").font = Font(name="Calibri", bold=True, color="A02622")
    heads = ["Sr. No", "Description", "Unit", "BOQ qty", "Billed up to this bill", "Extra over BOQ", "Rate (as billed)", "Extra amount"]
    multi = len(stages) > 1
    if multi:
        heads.insert(3, "Stage")
    for j, t in enumerate(heads, start=1):
        c = ws.cell(7, j, t)
        c.font, c.border = BOLD, BOX
        c.fill = PatternFill("solid", fgColor="FCE4D6")
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    r = 8
    total = 0.0
    for sr, desc, unit, boq, billed, xq, ratepct, xv, stage in rows:
        vals = [sr if sr != "" else None, desc, unit] + ([STAGE_NAME[stage]] if multi else []) + [boq, billed, xq, ratepct, xv]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(r, j, v)
            c.border = BOX
            c.alignment = Alignment(wrap_text=(j == 2), vertical="top")
        base_j = 4 + (1 if multi else 0)
        for j in (base_j, base_j + 1, base_j + 2):
            ws.cell(r, j).number_format = "#,##0.00"
        ws.cell(r, base_j + 2).fill = PatternFill("solid", fgColor="FFE699")
        ws.cell(r, base_j + 3).number_format = _INR2
        ws.cell(r, base_j + 4).number_format = _INR2
        total += xv or 0.0
        r += 1
    last = len(heads)
    ws.cell(r, 2, "Total extra").font = BOLD
    ws.cell(r, last, total).number_format = _INR2
    for j in range(1, last + 1):
        ws.cell(r, j).border = BOX
        ws.cell(r, j).font = BOLD
    for col, wd in zip("ABCDEFGHI", (8, 70, 8, 11, 14, 14, 14, 15, 15)):
        ws.column_dimensions[col].width = wd
    ws.row_dimensions[7].height = 34
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "FFD966"


def _wire_sheet_name(names, taken):
    """'WIRE CHALLAN' in the plain layout; else the CHALLAN tab's own prefix + ' WIRE' (never a clash, 31 characters at most)."""
    base = "WIRE CHALLAN" if names["challan"] == "CHALLAN" else _sheet_safe(names["challan"][:-len("CHALLAN")].rstrip() + " WIRE")
    n, i, low = base, 2, {x.lower() for x in taken}
    while n.lower() in low:
        n = base[:28] + f" {i}"
        i += 1
    return n


def _fill_wire_sheet(ws, store, unit, no, lines, BOLD):
    """The WIRE CHALLAN sheet of an RA workbook, laid out like the site's own: wire received per colour per challan
    (one column per challan, tagged with its RA), then the metres given to each BOQ line, the metres per point the BOQ
    states, and the points claimed. Values only: it is the record of what the bill rests on."""
    from openpyxl.utils import get_column_letter as L
    w, sch = _wunit(store, unit), _wscheme(store)
    ph = set(sch["phases"])
    chs = sorted([c for c in w["challans"] if c.get("ra") is not None and c["ra"] <= no], key=lambda c: (c["ra"], c["date"], c["no"]))
    first = 4
    tot = first + len(chs) + 1
    claim = tot + 1                                         # claim metres | per point | points | BOQ line ref | wire size
    for j, c in enumerate(chs):
        col = first + j
        ws.cell(1, col, "Challan Date")
        ws.cell(2, col, dt.date.fromisoformat(c["date"])).number_format = "dd-mmm-yy"
        ws.cell(3, col, "Challan No")
        ws.cell(4, col, int(c["no"]) if c["no"].isdigit() else c["no"])
        ws.cell(5, col, raL_(c["ra"]))
        ws.cell(6, col, "Qty")
        ws.column_dimensions[L(col)].width = 11
    for k, h in enumerate(("TOTAL QTY", "CLAIM QTY IN RMT", "PER POINT RMT", "NOS OF POINT CLAIM", "BOQ LINE ITEM REF", "WIRE SQ MM")):
        ws.cell(6, tot + k, h).font = BOLD
        ws.column_dimensions[L(tot + k)].width = 16
    ws.cell(6, 2, "Counted for points?").font = BOLD
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 14
    sizes = sorted({s for c in chs for s in c["qty"]} | {a["size"] for a in w["alloc"] if a["ra"] <= no}, key=_size_key)
    r = 7
    for size in sizes:
        ws.cell(r, 3, f"WIRE {size}").font = BOLD
        r += 1
        for key, lab in sch["colours"]:
            ws.cell(r, 3, lab.upper())
            ws.cell(r, 2, "Yes (phase wire)" if key in ph else "No (runs beside the phase wire)")
            total = 0.0
            for j, c in enumerate(chs):
                m = (c["qty"].get(size) or {}).get(key)
                if m:
                    ws.cell(r, first + j, m).number_format = "#,##0.##"
                    total += m
            ws.cell(r, tot, total).number_format = "#,##0.##"
            r += 1
        r += 1
    # the metres given to BOQ lines (to date at this RA) and the points they make
    by_code = {}
    for a in w["alloc"]:
        if a["ra"] <= no:
            by_code.setdefault(a["item_code"], []).append(a)
    pts = _w_pts(w, no)
    r = 7
    for code in by_code:
        rows = {}
        for a in by_code[code]:
            rows[a["size"]] = rows.get(a["size"], 0.0) + a["metres"]
        per = by_code[code][-1]["per"]
        items = sorted(rows.items(), key=lambda kv: _size_key(kv[0]))
        for i, (size, m) in enumerate(items):
            ws.cell(r, claim, m).number_format = "#,##0.##"
            ws.cell(r, claim + 1, per)
            if i == len(items) - 1:
                ws.cell(r, claim + 2, pts.get(code, 0))
            ws.cell(r, claim + 3, code)
            ws.cell(r, claim + 4, float(size))
            r += 1
    ws.freeze_panes = "D7"


def _xlsx_name(slug, ra):
    safe = lambda t: re.sub(r"[^A-Za-z0-9._-]+", "_", t)
    return (f"{safe(sp._slugify(slug))}_{safe(_unit_text(ra['service'], '-'))}_{SERIES_NAME[ra['series']].replace(' + ', '+').replace(' ', '')}"
            f"_RA-{ra['no']:02d}{'_DRAFT' if ra['status'] == 'draft' else ''}.xlsx")


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{slug}/invoice/{ra_id}")
def invoice_book(slug: str, ra_id: str):
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    data = invoice_xlsx(slug, ra_id)
    return Response(content=data, media_type=_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{_xlsx_name(slug, ra)}"'})


# ------------------------------------------------- Supply + Installation together, for us
# An INTERNAL view, not a bill: the latest Supply RA and the latest Installation RA of one service, side by side. No RA is
# created, no invoice number is used, nothing is locked. The client still gets the Supply and the Installation invoices
# separately. Every figure is read from those two RAs (the same snapshot / live bill the separate workbooks use).
_INTERNAL_NOTE = "INTERNAL COPY, not an invoice. The client receives the Supply and Installation invoices separately."


def _combined_pick(store, service):
    """{stage: the latest RA of the service that carries that stage, or None}. A draft counts as well as an invoiced RA."""
    pick = {}
    for st in ("supply", "install"):
        c = [r for r in store["ras"] if r["service"] == service and st in STAGES[r["series"]]]
        pick[st] = max(c, key=lambda r: r["no"]) if c else None
    return pick


def _combined_view(store, service):
    """What the home / Download dialog needs to offer the view: which RA each stage would come from."""
    pick = _combined_pick(store, service)
    return {st: ({"id": r["id"], "label": _fmt_ra(r), "no": r["no"], "status": r["status"]} if r else None) for st, r in pick.items()}


def _stage_fig(slug, d, store, ra, stage, lines, cache):
    """{item_code: figures} of ONE stage of one RA -- the same figures `_write_ra` reads (previous, this, total, extra)."""
    if ra["id"] not in cache:
        cache[ra["id"]] = _get_bill(slug, d, store, ra)
    bill = cache[ra["id"]]
    brow = {r["item_code"]: r for r in bill["lines"]}
    setup = store["setup"].get(ra["service"]) or {}
    prev = _prev_upto(store, ra["service"], ra["series"], stage, ra["no"])
    out = {}
    for ln in lines:
        code = ln["item_code"]
        row = brow.get(code)
        c = row.get(stage) if row else None
        pq, pa = prev.get(code, (0.0, 0.0))
        pct = c["pct"] if c else _stage_pct(setup, code, stage)
        rate = row["rate"] if row else ln["rate"]
        out[code] = {"pct": pct, "rate": rate, "pq": pq, "pa": pa,
                     "tq": c["this"] if c and c["this"] is not None else 0.0,
                     "tv": c["amount"] if c and c["amount"] is not None else 0.0,
                     "xq": (c or {}).get("extra") or 0.0, "xv": (c or {}).get("extra_amount") or 0.0,
                     "ov": bool(c and c["overridden"]), "reason": (c or {}).get("reason", ""),
                     "ratepct": None if pct is None else rate * pct / 100.0}
    return out, bill


def combined_name(slug, service, pick):
    safe = lambda t: re.sub(r"[^A-Za-z0-9._-]+", "_", t)
    tag = lambda st, r: f"{'Supply' if st == 'supply' else 'Install'}-" + (f"RA{r['no']:02d}" if r else "none")
    return (f"{safe(sp._slugify(slug))}_{safe(_unit_text(service, '-'))}_Supply+Installation_TOGETHER_"
            f"{tag('supply', pick['supply'])}_{tag('install', pick['install'])}_INTERNAL.xlsx")


def combined_xlsx(slug, service):
    """(file name, bytes): Internal Summary, Annexure B-Summary BOQ, Annexure B-BOQ (supply block, then installation block)
    and, when anything was accepted above the BOQ, Extra over BOQ. All values: it is a record for us, not a calculation."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.formatting.rule import Rule
    from openpyxl.styles.differential import DifferentialStyle
    from openpyxl.styles.numbers import NumberFormat
    from openpyxl.comments import Comment
    from openpyxl.worksheet.properties import PageSetupProperties
    from . import billing_cover
    d = sp._need(slug)
    store = _load(d)
    if service not in _services(d):
        raise HTTPException(404, "this service is not in this project")
    pick = _combined_pick(store, service)
    if not pick["supply"] and not pick["install"]:
        raise HTTPException(409, f"{service} has no Supply or Installation RA yet. Create one first; this view reads from the RAs.")
    lines = _lines(d, service)
    if not lines:
        raise HTTPException(409, f"{service} has no billable lines.")
    groups, _ = _groups_for(d, store, service, lines)
    if not groups:
        groups = [{"sr": "", "name": f"{_unit_text(service)} works", "sheet": None, "from": 1, "to": 10 ** 6, "wo": None}]
    layout = _invoice_layout(d, store, service, lines, groups)
    meta = store["meta"]
    stages = ["supply", "install"]
    cache, fig, bills = {}, {}, {}
    for st in stages:
        if pick[st]:
            fig[st], bills[st] = _stage_fig(slug, d, store, pick[st], st, lines, cache)
    work = (meta.get("work") or {}).get(service) or f"{_unit_text(service)} works"
    wo_no = _wo_no(meta, service) or "…..............."

    PEACH, GREEN = PatternFill("solid", fgColor="FCE4D6"), PatternFill("solid", fgColor="C6E0B4")
    EXTRA_F = PatternFill("solid", fgColor="FFE699")
    BOLD, NORM = Font(name="Calibri", bold=True, size=11), Font(name="Calibri", size=11)
    RED = Font(name="Calibri", bold=True, color="A02622")
    thin = Side(style="thin", color="000000")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    dash = Rule(type="cellIs", operator="equal", formula=["0"], dxf=DifferentialStyle(numFmt=NumberFormat(numFmtId=170, formatCode='"-"')))
    QTY = "#,##0.00"
    state = lambda r: "Invoiced" if r["status"] == "locked" else "Draft"
    who = lambda st: (f"{STAGE_NAME[st]} RA-{pick[st]['no']:02d} · {state(pick[st])}" if pick[st] else f"{STAGE_NAME[st]}: no RA yet")

    wb = openpyxl.Workbook()
    ws_in = wb.active
    ws_in.title = "Internal Summary"
    ws_sum = wb.create_sheet("Annexure B-Summary BOQ")
    ws = wb.create_sheet("Annexure B-BOQ")

    def header(w):
        w["A1"] = meta.get("client") or ""
        w["A1"].font = Font(name="Calibri", bold=True, size=13)
        w["A2"], w["B2"] = "WO. No.", wo_no
        w["A3"], w["B3"] = "Work", work
        w["A4"], w["B4"] = "Contractor", meta.get("contractor") or ""
        for r_ in (2, 3, 4):
            w.cell(r_, 1).font = BOLD
        w["A5"] = _INTERNAL_NOTE
        w["A5"].font = RED
        w["A6"], w["B6"] = "Annexure B", f"{_unit_text(service)} Works"
        w["A6"].font = w["B6"].font = BOLD

    # ---- Annexure B-BOQ
    header(ws)
    for col_i, t in enumerate(("Sr. No", "Description", "Unit", "Qty", "Rate", "Amt"), start=1):
        ws.cell(8, col_i, t)
    bcol = {st: 7 + _BLOCK * i for i, st in enumerate(stages)}
    for st in stages:
        c0 = bcol[st]
        pct = bills[st]["pct"].get(st) if st in bills else None
        pcts = f"{pct:g}% " if pct is not None else ""
        ws.merge_cells(start_row=7, start_column=c0, end_row=7, end_column=c0 + _BLOCK - 1)
        ws.cell(7, c0, (f"RA-{pick[st]['no']:02d} · {STAGE_NAME[st].upper()}" + (f" · {state(pick[st]).upper()}" if pick[st]["status"] == "draft" else "")) if pick[st]
                else f"{STAGE_NAME[st].upper()} · NO RA YET")
        for k, t in enumerate((f"{pcts}RATE OF {STAGE_NAME[st].upper()}", "PREVIOUS BILL QTY", "THIS BILL QTY", "TOTAL QTY",
                               "PREVIOUS BILL VALUE", "THIS BILL VALUE", "TOTAL BILL VALUE")):
            ws.cell(8, c0 + k, t)
    last_col = 6 + _BLOCK * len(stages)
    n = len(layout)
    row_of = {i: _FIRST_DATA_ROW + i for i in range(n)}
    last_row = _FIRST_DATA_ROW + n - 1
    sub_rows, extra_rows, gsum = {}, [], {}
    for i, e in enumerate(layout):
        r = row_of[i]
        if e["kind"] == "head":
            ws.cell(r, 1, e["sr"] if e["sr"] != "" else None)
            ws.cell(r, 2, e["desc"]).font = BOLD if len(e["desc"]) <= 110 else NORM
            ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(r, 1).alignment = Alignment(vertical="top")
            continue
        if e["kind"] == "sub":
            sub_rows[i] = r
            ws.cell(r, 2, f"TOTAL FOR {e['name']}").font = BOLD
            ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
            items = [x for x in layout[e["first"]:i] if x["kind"] == "item" and x["line"]]
            ws.cell(r, 6, sum(x["line"]["rate"] * x["line"]["boq_qty"] for x in items)).number_format = _INR2
            ws.cell(r, 6).font = BOLD
            gsum[i] = {}
            for st in stages:
                if st not in fig:
                    continue
                c0 = bcol[st]
                pa = sum(fig[st][x["line"]["item_code"]]["pa"] for x in items)
                tv = sum(fig[st][x["line"]["item_code"]]["tv"] for x in items)
                gsum[i][st] = (pa, tv)
                for k, v in ((4, pa), (5, tv), (6, pa + tv)):
                    ws.cell(r, c0 + k, v).number_format = _INR2
                    ws.cell(r, c0 + k).font = BOLD
            continue
        ln = e["line"]
        ws.cell(r, 1, e["sr"] if e["sr"] != "" else None)
        ws.cell(r, 2, e["desc"])
        ws.cell(r, 3, e["unit"])
        ws.cell(r, 4, e["qty"])
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(r, 1).alignment = Alignment(vertical="top")
        if ln is None:
            continue
        ws.cell(r, 4).number_format = QTY
        ws.cell(r, 5, ln["rate"]).number_format = _INR2
        ws.cell(r, 6, ln["rate"] * ln["boq_qty"]).number_format = _INR2
        code = ln["item_code"]
        for st in stages:
            if st not in fig:
                continue
            f_, c0 = fig[st][code], bcol[st]
            if f_["pct"] is None:
                continue
            G, H, I, J, K, Lc, M = (L(c0 + k) for k in range(7))
            for ref, v, fmt in ((G, f_["ratepct"], _INR2), (H, f_["pq"], QTY), (I, f_["tq"], QTY), (J, f_["pq"] + f_["tq"], QTY),
                                (K, f_["pa"], _INR2), (Lc, f_["tv"], _INR2), (M, f_["pa"] + f_["tv"], _INR2)):
                ws[f"{ref}{r}"] = v
                ws[f"{ref}{r}"].number_format = fmt
            if f_["ov"] and f_["reason"]:
                ws[f"{I}{r}"].comment = Comment(f_["reason"], "MI")
            if f_["xq"] > 0:
                for col in (I, Lc):
                    ws[f"{col}{r}"].fill = EXTRA_F
                note = f"Extra over BOQ: {f_['xq']:g} {ln['unit']} above the BOQ quantity of {ln['boq_qty']:g}. Accepted in Material Intelligence."
                ws[f"{I}{r}"].comment = Comment(note + (f"\n{f_['reason']}" if f_["ov"] and f_["reason"] else ""), "MI")
                extra_rows.append((e["sr"], e["desc"], e["unit"], ln["boq_qty"], f_["pq"] + f_["tq"], f_["xq"], f_["ratepct"], f_["xv"], st))
            if f_["tq"] < 0:
                ws[f"{I}{r}"].font = Font(color="A02622", bold=True)
    for col, wd in zip("ABCD", (10, 78, 7.5, 9)):
        ws.column_dimensions[col].width = wd
    ws.column_dimensions["E"].width = 13
    ws.column_dimensions["F"].width = 15
    for st in stages:
        c0 = bcol[st]
        ws.cell(7, c0).font = BOLD
        ws.cell(7, c0).alignment = Alignment(horizontal="center")
        for k in range(_BLOCK):
            ws.column_dimensions[L(c0 + k)].width = 15 if k in (0, 4, 5, 6) else 12.5
            ws.cell(7, c0 + k).fill = GREEN
    for cc_ in range(1, last_col + 1):
        cell = ws.cell(8, cc_)
        cell.font, cell.fill, cell.border = BOLD, PEACH, BOX
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    ws.row_dimensions[8].height = 44
    for r_ in range(_FIRST_DATA_ROW, last_row + 1):
        for cc_ in range(1, last_col + 1):
            ws.cell(r_, cc_).border = BOX
    ws.conditional_formatting.add(f"D{_FIRST_DATA_ROW}:{L(last_col)}{last_row}", dash)
    ws.freeze_panes = "A9"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation, ws.page_setup.paperSize = "landscape", ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_title_rows = "8:8"
    ws.print_area = f"A1:{L(last_col)}{last_row}"

    # ---- Annexure B-Summary BOQ: Supply | Installation | Total per group
    w = ws_sum
    header(w)
    w.column_dimensions["A"].width = 11
    w.column_dimensions["B"].width = 60
    w.column_dimensions["C"].width = 16
    blocks = [("supply", 4), ("install", 7), ("both", 10)]
    w["A5"] = None
    w["B5"] = _INTERNAL_NOTE
    w["B5"].font = RED
    w.merge_cells(start_row=4, start_column=4, end_row=4, end_column=12)
    w.cell(4, 4, " · ".join(who(st) for st in stages))
    for st, c0 in blocks:
        w.merge_cells(start_row=5, start_column=c0, end_row=5, end_column=c0 + 2)
        pct = bills[st]["pct"].get(st) if st in bills else None
        w.cell(5, c0, "TOTAL" if st == "both" else f"{STAGE_NAME[st].upper()}" + (f" @ {pct:g}%" if pct is not None else ""))
        for k, t in enumerate(("PREVIOUS BILL VALUE", "THIS BILL VALUE", "TOTAL BILL VALUE")):
            w.cell(6, c0 + k, t)
            w.column_dimensions[L(c0 + k)].width = 18
    w["A6"], w["B6"], w["C6"] = "Sr.No.", "Description of Material", "WO Amount"
    w["B7"] = work
    for r_ in range(4, 7):
        for cc_ in range(1, 13):
            cell = w.cell(r_, cc_)
            cell.font, cell.border = (RED if (r_, cc_) == (5, 2) else BOLD), BOX
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            if r_ >= 5:
                cell.fill = PEACH
    w.row_dimensions[6].height = 32
    srow, seq = 8, 0
    gfirst = srow
    for i, e in enumerate(layout):
        if e["kind"] != "sub":
            continue
        seq += 1
        g = groups[e["group"]] if e["group"] is not None else None
        items = [x for x in layout[e["first"]:i] if x["kind"] == "item" and x["line"]]
        w.cell(srow, 1, (g.get("sr") if g and g.get("sr") else seq))
        w.cell(srow, 2, e["name"])
        wo = g["wo"] if g and g.get("wo") is not None else sum(x["line"]["rate"] * x["line"]["boq_qty"] for x in items)
        w.cell(srow, 3, wo).number_format = _INR0
        both = [0.0, 0.0]
        for st, c0 in blocks[:2]:
            if st not in gsum[i]:
                continue
            pa, tv = gsum[i][st]
            for k, v in enumerate((pa, tv, pa + tv)):
                w.cell(srow, c0 + k, v).number_format = _INR2
            both[0] += pa
            both[1] += tv
        if any(st in gsum[i] for st in stages):
            for k, v in enumerate((both[0], both[1], both[0] + both[1])):
                w.cell(srow, 10 + k, v).number_format = _INR2
        srow += 1
    glast = srow - 1
    w.cell(srow, 2, "Grand Total")
    for cc_ in range(3, 13):
        vals = [w.cell(r_, cc_).value for r_ in range(gfirst, glast + 1)]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            w.cell(srow, cc_, sum(vals)).number_format = _INR0 if cc_ == 3 else _INR2
    for r_ in range(gfirst, srow + 1):
        for cc_ in range(1, 13):
            cell = w.cell(r_, cc_)
            cell.border = BOX
            cell.font = BOLD if r_ == srow else NORM
            if r_ == srow:
                cell.fill = PEACH
            cell.alignment = (Alignment(horizontal="right", vertical="center") if cc_ >= 3
                              else Alignment(wrap_text=True, vertical="center") if cc_ == 2 else Alignment(horizontal="center", vertical="center"))
    w.conditional_formatting.add(f"C{gfirst}:L{srow}", dash)
    w.sheet_view.showGridLines = False
    w.page_setup.orientation, w.page_setup.paperSize = "landscape", w.PAPERSIZE_A4
    w.page_setup.fitToWidth, w.page_setup.fitToHeight = 1, 0
    w.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    # ---- Internal Summary: the two invoices as the client receives them, and the sum
    ci = ws_in
    ci["A1"] = meta.get("client") or ""
    ci["A1"].font = Font(name="Calibri", bold=True, size=13)
    ci["A2"] = _INTERNAL_NOTE
    ci["A2"].font = RED
    for r_, (k, v) in enumerate((("Project", meta.get("project_name") or ""), ("WO. No.", wo_no), ("Work", work),
                                 ("Contractor", meta.get("contractor") or "")), start=4):
        ci.cell(r_, 1, k).font = BOLD
        ci.cell(r_, 2, v)

    def gst_of(r):
        return (r.get("inv_gst") if r["status"] == "locked" else None) or {"sgst": meta.get("sgst"), "cgst": meta.get("cgst")}

    amt = {st: (sum(fig[st][c]["tv"] for c in fig[st]) if st in fig else None) for st in stages}
    one = pick["supply"] is not None and pick["supply"] is pick["install"]          # one Together RA carries both stages
    money = {}
    for st in stages:
        if amt[st] is None:
            continue
        g_ = gst_of(pick[st])
        sg, cg = _num(g_.get("sgst")), _num(g_.get("cgst"))
        money[st] = billing_cover.money(amt[st], sg, cg) if sg is not None and cg is not None else None
    present = [st for st in stages if amt[st] is not None]
    both_amt = sum(amt[st] for st in present) if present else None
    if one:
        g_ = gst_of(pick["supply"])
        sg, cg = _num(g_.get("sgst")), _num(g_.get("cgst"))
        m_both = billing_cover.money(both_amt, sg, cg) if sg is not None and cg is not None else None
        money = {st: None for st in stages}
    else:
        m_both = None
        if present and all(money.get(st) for st in present):
            m_both = {k: sum(money[st][k] for st in present) for k in ("amount", "sgst", "cgst", "gst", "invoice", "round", "total")}
    hr = 9
    for j, t in enumerate(("", "Supply", "Installation", "Both together")):
        c = ci.cell(hr, 1 + j, t)
        c.font, c.fill, c.border = BOLD, PEACH, BOX
        c.alignment = Alignment(horizontal="center", vertical="center")
    ci.cell(hr, 1).value = "Particulars"
    rows = [("Which RA", [who("supply"), who("install"), "—"], None),
            ("Invoice no.", [(pick[st].get("inv_no") or "—") if pick[st] else "—" for st in stages] + ["—"], None),
            ("Value of services", [amt["supply"], amt["install"], both_amt], _INR2)]
    g_pcts = {}
    for st in present:
        gg = gst_of(pick[st])
        g_pcts[st] = (_num(gg.get("sgst")), _num(gg.get("cgst")))
    same = len({v for v in g_pcts.values()}) <= 1 and bool(g_pcts)
    lab_s = f"SGST {g_pcts[present[0]][0]:g}%" if same and g_pcts[present[0]][0] is not None else "SGST"
    lab_c = f"CGST {g_pcts[present[0]][1]:g}%" if same and g_pcts[present[0]][1] is not None else "CGST"
    mm = lambda st, k: (money[st][k] if st in money and money[st] else None)
    rows += [(lab_s, [mm("supply", "sgst"), mm("install", "sgst"), m_both["sgst"] if m_both else None], _INR2),
             (lab_c, [mm("supply", "cgst"), mm("install", "cgst"), m_both["cgst"] if m_both else None], _INR2),
             ("Round off", [mm("supply", "round"), mm("install", "round"), m_both["round"] if m_both else None], _INR2),
             ("Invoice total", [mm("supply", "total"), mm("install", "total"), m_both["total"] if m_both else None], _INR2)]
    for i_, (lab, vals, fmt) in enumerate(rows):
        r_ = hr + 1 + i_
        ci.cell(r_, 1, lab).font = BOLD if lab in ("Invoice total", "Value of services") else NORM
        for j, v in enumerate(vals):
            c = ci.cell(r_, 2 + j, v if v is not None else "—")
            if fmt and isinstance(v, (int, float)):
                c.number_format = fmt
            c.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
            c.font = BOLD if lab in ("Invoice total", "Value of services") else NORM
        for j in range(4):
            ci.cell(r_, 1 + j).border = BOX
    last_in = hr + len(rows)
    for j in range(4):
        ci.cell(last_in, 1 + j).fill = PEACH
    note_r = last_in + 2
    if m_both:
        ci.cell(note_r, 1, "Amount in words").font = BOLD
        ci.cell(note_r, 2, billing_cover.words(m_both["total"]))
        ci.merge_cells(start_row=note_r, start_column=2, end_row=note_r, end_column=4)
        note_r += 1
    else:
        ci.cell(note_r, 1, "GST").font = BOLD
        ci.cell(note_r, 2, "SGST / CGST % is not set in Billing setup, so no GST or invoice total is shown. Nothing is guessed.")
        ci.merge_cells(start_row=note_r, start_column=2, end_row=note_r, end_column=4)
        note_r += 1
    if one:
        ci.cell(note_r, 1, "Note").font = BOLD
        ci.cell(note_r, 2, f"Both stages are on one Supply + Installation RA (RA-{pick['supply']['no']:02d}): one invoice, so GST is shown once, under Both together.")
        ci.merge_cells(start_row=note_r, start_column=2, end_row=note_r, end_column=4)
    elif m_both:
        ci.cell(note_r, 1, "Note").font = BOLD
        ci.cell(note_r, 2, "Each invoice is rounded to the rupee on its own, so Both together is the sum of the two invoice totals.")
        ci.merge_cells(start_row=note_r, start_column=2, end_row=note_r, end_column=4)
    for rr in range(note_r - 1, note_r + 1):
        ci.cell(rr, 1).alignment = Alignment(vertical="top")
        ci.cell(rr, 2).alignment = Alignment(wrap_text=True, vertical="top")
        ci.row_dimensions[rr].height = 32
    ci.column_dimensions["A"].width = 22
    for col in "BCD":
        ci.column_dimensions[col].width = 34
    ci.sheet_view.showGridLines = False
    ci.page_setup.orientation, ci.page_setup.paperSize = "portrait", ci.PAPERSIZE_A4
    ci.page_setup.fitToWidth, ci.page_setup.fitToHeight = 1, 0
    ci.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ci.sheet_properties.tabColor = "A02622"

    # ---- Extra over BOQ: only when something was accepted above the BOQ
    if extra_rows:
        wx = wb.create_sheet("Extra over BOQ")
        _fill_extra_sheet(wx, extra_rows, stages, {"ra": {"no": 0}}, service, meta, False,
                          subject="Quantity billed above the BOQ quantity, " + " and ".join(
                              f"{STAGE_NAME[st]} RA-{pick[st]['no']:02d}" for st in stages if pick[st]))
        wx["A5"] = _INTERNAL_NOTE
        wx["A5"].font = RED
    buf = io.BytesIO()
    wb.save(buf)
    return combined_name(slug, service, pick), buf.getvalue()


@router.get("/{slug}/combined")
def combined_book(slug: str, service: str):
    name, data = combined_xlsx(slug, service)
    return Response(content=data, media_type=_XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ----------------------------------------------------------- ProjectBase sheet
# The person exports a Billing Request from ProjectBase ONCE per service ("Connect"); MI keeps that file and, for any RA, hands
# back the same file with only the "Now" column filled (see projectbase.py). MI never writes into ProjectBase: the person
# imports the file there. Matching is by Internal Ref No only.
def _pb_dir(d):
    p = d / "projectbase"
    p.mkdir(exist_ok=True)
    return p


def _pb_file(d, base):
    return d / "projectbase" / (re.sub(r"[^A-Za-z0-9._-]+", "_", base) + ".xlsx")


def _pb_pending(d, base):
    return d / "projectbase" / (re.sub(r"[^A-Za-z0-9._-]+", "_", base) + ".pending.xlsx")


_PB_PARSE = {}


def _pb_read(path):
    """projectbase.read(), remembered per file version (the export is ~130 KB and read on every preview)."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _PB_PARSE:
        if len(_PB_PARSE) > 8:
            _PB_PARSE.clear()
        try:
            _PB_PARSE[key] = pbx.read(path.read_bytes())
        except pbx.LayoutError as e:
            raise HTTPException(400, str(e))
    return _PB_PARSE[key]


def _pb_units(d, base):
    return [u for u in _services(d) if _base(u) == base]


def _pb_analysis(d, store, base, parsed, ok):
    units, warns, ask, missing = [], [], [], []
    pcts = {}
    for st in pbx.STAGES:
        vals = [g["pct"].get(st) for g in parsed["groups"].values() if g["pct"].get(st) is not None]
        pcts[st] = max(set(vals), key=vals.count) if vals else None
    for u in _pb_units(d, base):
        lines = _lines(d, u)
        m = pbx.match(lines, parsed, ok)
        units.append({"unit": u, "package": _uname(u), "total": len(lines), "matched": m["matched"]})
        ask += [{**a, "unit": u} for a in m["ask"]]
        missing += [{**x, "unit": u} for x in m["missing"]]
        s = store["setup"].get(u) or {}
        for st, nm in (("supply", "Supply"), ("install", "Installation")):
            mine = _num(s.get(st + "_pct"))
            if pcts[st] is not None and mine is not None and abs(pcts[st] - mine) > 1e-9:
                w = f"{u}: this file says {nm} {pcts[st]:g}%, Billing setup says {mine:g}%."
                if w not in warns:
                    warns.append(w)
    return {"project": parsed["project"], "lines": len(parsed["groups"]), "pct": pcts, "units": units,
            "matched": sum(x["matched"] for x in units), "total": sum(x["total"] for x in units),
            "ask": ask, "missing": missing[:30], "n_missing": len(missing), "warnings": warns,
            "odd": sum(1 for g in parsed["groups"].values() if g["odd"])}


@router.post("/{slug}/projectbase/{base}/inspect")
async def pb_inspect(slug: str, base: str, file: UploadFile = File(...)):
    """Read an exported Billing Request and say what MI found in it. Nothing is connected until /connect."""
    d = sp._need(slug)
    if base not in _bases(d):
        raise HTTPException(404, f"No service called {base}.")
    raw = await file.read()
    try:
        parsed = pbx.read(raw)
    except pbx.LayoutError as e:
        raise HTTPException(400, str(e))
    store = _load(d)
    ok = ((store["pb"].get(base) or {}).get("ok")) or {}
    an = _pb_analysis(d, store, base, parsed, ok)
    if an["total"] and not an["matched"] and not an["ask"]:
        raise HTTPException(400, f"None of {base}'s lines were found in this file. Is this the Billing Request of {base}? "
                                 "Export it from the ProjectBase project of this service.")
    _pb_dir(d)
    _pb_pending(d, base).write_bytes(raw)
    an["name"] = file.filename
    return an


@router.post("/{slug}/projectbase/{base}/connect")
def pb_connect(slug: str, base: str, payload: dict):
    """Keep the file read by /inspect. payload.confirm = {item_code: internal_ref} the person said are the same line."""
    d = sp._need(slug)
    p = _pb_pending(d, base)
    if not p.exists():
        raise HTTPException(409, "Choose the ProjectBase file again.")
    parsed = _pb_read(p)
    store = _load(d)
    groups = parsed["groups"]
    ok = dict(((store["pb"].get(base) or {}).get("ok")) or {})
    for code, ref in (payload.get("confirm") or {}).items():
        if str(ref) in groups:
            ok[str(code)] = str(ref)
    dest = _pb_file(d, base)
    p.replace(dest)
    store["pb"][base] = {"project": parsed["project"], "name": payload.get("name") or dest.name, "lines": len(groups),
                         "at": dt.datetime.now().isoformat(timespec="seconds"), "ok": ok}
    _save(d, store)
    return {"ok": True, "project": parsed["project"]}


@router.delete("/{slug}/projectbase/{base}")
def pb_disconnect(slug: str, base: str):
    d = sp._need(slug)
    store = _load(d)
    store["pb"].pop(base, None)
    _save(d, store)
    for p in (_pb_file(d, base), _pb_pending(d, base)):
        p.unlink(missing_ok=True)
    return {"ok": True}


def _pb_sheet(slug, d, store, ra):
    """(xlsx bytes, report) for one RA: the connected file with this RA's quantities in the Now column."""
    base = _base(ra["service"])
    meta = (store.get("pb") or {}).get(base)
    f = _pb_file(d, base)
    if not meta or not f.exists():
        raise HTTPException(409, f"No ProjectBase sheet is connected for {base}. Connect it first.")
    parsed = _pb_read(f)
    bill = _get_bill(slug, d, store, ra)
    stages = [s for s in STAGES[ra["series"]] if s in pbx.FILLABLE]
    lines = _lines(d, ra["service"])
    m = pbx.match(lines, parsed, meta.get("ok") or {})
    wanted, amounts, neg, manual = {}, {}, [], []
    for row in bill["lines"]:
        code = row["item_code"]
        for st in stages:
            c = row.get(st)
            if not c:
                continue
            if c["this"] is None:
                manual.append(code)
            elif c["this"] != 0:
                wanted.setdefault(code, {})[st] = c["this"]
                amounts[(code, st)] = c["amount"] or 0.0
                if c["this"] < 0:
                    neg.append(code)
    data, rep = pbx.fill(f.read_bytes(), parsed, m["ref_of"], wanted, stages)
    not_found = [{"item_code": code, "desc": next((l["desc"][:90] for l in lines if l["item_code"] == code), ""),
                  "qty": q} for code, per in wanted.items() if code not in m["ref_of"] for q in per.values()]
    filled_codes = sorted({x[0] for x in rep["filled"]})
    expect = sum(amounts[(code, st)] for code, _ref, st, _q in rep["filled"])
    sent = [{"item_code": code, "ref": ref, "stage": st, "qty": q} for code, ref, st, q in rep["filled"]]
    return data, {"id": ra["id"], "label": _fmt_ra(ra), "base": base, "unit": ra["service"], "name": meta.get("name"),
                  "file": _xlsx_name(slug, ra).replace(sp._slugify(slug) + "_", sp._slugify(slug) + "_ProjectBase_", 1),
                  "lines_in_ra": len(wanted), "filled": len(filled_codes), "not_found": not_found, "ask": len(m["ask"]),
                  "cleared": rep["cleared"], "left": rep["left"], "odd": rep["odd"], "negative": neg, "manual": manual,
                  "expect_total": expect, "ra_total": bill["this_total"], "sent": sent,
                  "rates": {row["item_code"]: {"rate": row["rate"], "unit": row["unit"], "desc": row["desc"],
                                               "pct": {st: (row.get(st) or {}).get("pct") for st in stages}} for row in bill["lines"]}}


@router.get("/{slug}/projectbase/preview")
def pb_preview(slug: str, ras: str = ""):
    """What the ProjectBase file of each ticked RA would hold, without downloading it (the Download dialog's right-hand panel)."""
    d = sp._need(slug)
    store = _load(d)
    out = []
    for rid in [x for x in ras.split(",") if x]:
        ra = _ra(store, rid)
        if _base(ra["service"]) not in (store.get("pb") or {}):
            out.append({"id": rid, "label": _fmt_ra(ra), "base": _base(ra["service"]), "connected": False})
            continue
        _data, rep = _pb_sheet(slug, d, store, ra)
        rep.pop("rates"), rep.pop("sent")
        out.append({**rep, "connected": True})
    return {"ras": out}


@router.post("/{slug}/ra/{ra_id}/pb-check")
def pb_check(slug: str, ra_id: str, payload: dict):
    """After importing in ProjectBase the person types the Subtotal it shows; compare it with what MI put in the file."""
    d = sp._need(slug)
    store = _load(d)
    ra = _ra(store, ra_id)
    try:
        theirs = float(payload.get("subtotal"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Type the Subtotal ProjectBase shows.")
    _data, rep = _pb_sheet(slug, d, store, ra)
    mine = rep["expect_total"]
    diff = round(mine - theirs, 2)
    suspects = []
    if abs(diff) > 0.5:
        for s in rep["sent"]:
            info = rep["rates"].get(s["item_code"]) or {}
            pct = (info.get("pct") or {}).get(s["stage"])
            if not info or pct is None:
                continue
            step = info["rate"] * pct / 100.0
            if step <= 0:
                continue
            k = round(diff / step)
            if k == 0 or abs(k * step - diff) > 0.01 or s["qty"] - k < -1e-9:
                continue
            suspects.append({"item_code": s["item_code"], "desc": (info.get("desc") or "")[:80], "unit": info.get("unit"),
                             "sent": s["qty"], "would_be": round(s["qty"] - k, 4), "difference": k, "step": step})
        suspects.sort(key=lambda x: abs(x["difference"]))
    return {"mi": mine, "pb": theirs, "diff": diff, "match": abs(diff) <= 0.5, "suspects": suspects[:3],
            "not_found": len(rep["not_found"])}


# ---------------------------------------------------------------- download
# One entry point for everything the Download dialog offers:
#   mode=files : one workbook per ticked RA, in the user's own names
#   mode=one   : every ticked RA in ONE workbook, sheets named "<service> RA01 <sheet>"
#   combined=1 : the old "All services Excel" (BOQ sheet with RA blocks side by side)
# More than one file comes back as a .zip, a single file as a plain .xlsx.
def download_files(slug, ra_ids, mode="files", want=None, combined=False, pb=False, together=()):
    import openpyxl
    d = sp._need(slug)
    store = _load(d)
    want = {k for k in (want if want is not None else SHEET_KEYS) if k in SHEET_KEYS}
    ras = []
    for rid in ra_ids:
        ras.append(_ra(store, rid))
    ras.sort(key=lambda r: (_services(d).index(r["service"]) if r["service"] in _services(d) else 99, r["no"],
                            {"supply": 0, "install": 1, "together": 2}[r["series"]]))
    out = []
    if ras and want:
        if mode == "one":
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            taken = set()
            both = {(r["service"], r["no"]) for r in ras if sum(1 for x in ras if (x["service"], x["no"]) == (r["service"], r["no"])) > 1}
            for r in ras:
                nm = _sheet_names(r, taken, plain=False, tag=(r["service"], r["no"]) in both)
                taken |= set(nm.values())
                _write_ra(wb, slug, d, store, r, nm, want)
            if not wb.sheetnames:
                raise HTTPException(400, "Tick at least one sheet.")
            buf = io.BytesIO()
            wb.save(buf)
            out.append((f"{sp._slugify(slug)}_RA_invoices_{dt.date.today().isoformat()}.xlsx", buf.getvalue()))
        else:
            used = set()
            for r in ras:
                nm = _xlsx_name(slug, r)
                if nm in used:
                    raise HTTPException(409, "Two RAs would get the same file name.")
                used.add(nm)
                out.append((nm, invoice_xlsx(slug, r["id"], want)))
    if pb:                                        # the connected ProjectBase sheet of each ticked RA (Now column filled)
        pbs = [_ra(store, rid) for rid in pb]
        pbs.sort(key=lambda r: (_services(d).index(r["service"]) if r["service"] in _services(d) else 99, r["no"]))
        for r in pbs:
            data, rep = _pb_sheet(slug, d, store, r)
            out.append((rep["file"], data))
    if combined:
        out.append((f"{sp._slugify(slug)}_RA_bills_{dt.date.today().isoformat()}.xlsx", export_xlsx(slug)))
    for svc in together:                          # the internal Supply + Installation view of each ticked service
        out.append(combined_xlsx(slug, svc))
    if not out:
        raise HTTPException(400, "Nothing is ticked to download.")
    return out


@router.get("/{slug}/download")
def download(slug: str, ras: str = "", mode: str = "files", sheets: str = "", combined: int = 0, pb: int = 0, invoice: int = 1,
             pbras: str = "", both: list[str] = Query(default=[])):
    """both (repeat the parameter): services whose internal Supply + Installation view is added. pb=1 adds the connected ProjectBase sheet of each RA in `pbras` (default: the ticked `ras`). invoice=0 leaves the
    invoice workbook out, so a ProjectBase-only download needs no sheets ticked."""
    ids = [x for x in ras.split(",") if x]
    want = [x for x in sheets.split(",") if x] if sheets else None
    if mode not in ("files", "one"):
        raise HTTPException(400, "mode must be 'files' or 'one'")
    pb_ids = ([x for x in pbras.split(",") if x] or ids) if pb else []
    files = download_files(slug, ids if invoice else [], mode, want, bool(combined), pb_ids, [x for x in both if x])
    if len(files) == 1:
        name, data = files[0]
        return Response(content=data, media_type=_XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    zname = f"{sp._slugify(slug)}_billing_{dt.date.today().isoformat()}.zip"
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zname}"'})

