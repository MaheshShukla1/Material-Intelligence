"""ProjectBase "Billing Request" sheet: read its layout, match MI's billing lines to it, fill the "Now" column.

The sheet is what ProjectBase exports from a Billing Request ("BOQ Details", one sheet, 11 columns):
    Project | Internal Ref No | Client Ref No | BOQ | Billing Terms | Billing Line | Unit | Order Qty | Calculated | Previously | Now
Every BOQ line is 4 consecutive rows with the same Internal Ref No, one per payment milestone of the contract
(75% supply, 15% installation, 5% testing, 5% handover). Section headings are single rows with no Billing Line.

MI only ever writes the "Now" column, and only on the stage rows that RA bills (Supply = the delivery row, Installation = the
installation row). Calculated / Previously, the Testing and Handover rows, and every other cell stay exactly as exported.

Matching is by Internal Ref No only (exact). A line MI cannot match is never guessed: it is reported, and where exactly ONE
file line has the same Client Ref No, unit and quantity it is offered for the person to confirm.

Nothing here talks to FastAPI; billing.py wires it up.
"""
import io
import re

import openpyxl

HEADERS = ["Project", "Internal Ref No", "Client Ref No", "BOQ", "Billing Terms", "Billing Line", "Unit", "Order Qty",
           "Calculated", "Previously", "Now"]
_NEED = ("Internal Ref No", "Client Ref No", "Billing Line", "Unit", "Order Qty", "Now")
STAGES = ("supply", "install", "testing", "handover")
FILLABLE = ("supply", "install")           # the two stages MI bills; Testing / Handover are never written


class LayoutError(ValueError):
    """The file is not a ProjectBase Billing Request export (the message says what is missing, in plain words)."""


def _norm(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def stage_of(text):
    """Which payment milestone a 'Billing Line' text is. Words, not position, so a reordered export still reads right."""
    t = _norm(text)
    if not t:
        return None
    if "handing over" in t or "handover" in t or "hand over" in t:
        return "handover"
    if "testing" in t and "commission" in t:
        return "testing"
    if "installation" in t:
        return "install"
    if "delivery" in t or "supply" in t:
        return "supply"
    return None


def lead_pct(text):
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*%", str(text or ""))
    return float(m.group(1)) if m else None


def _cols(ws):
    hdr = [_norm(c.value) for c in ws[1]]
    idx = {}
    for name in HEADERS:
        k = _norm(name)
        if k in hdr:
            idx[name] = hdr.index(k) + 1
    miss = [n for n in _NEED if n not in idx]
    if miss:
        raise LayoutError("This is not a ProjectBase Billing Request export: column " + ", ".join(miss) + " is missing.")
    return idx


def _sheet(wb):
    for n in wb.sheetnames:
        if _norm(n) == "boq details":
            return wb[n]
    return wb[wb.sheetnames[0]]


def _key(v):
    """An Internal Ref No / Client Ref No as text, whether Excel stored it as 479 or '479' or 479.0."""
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = str(v).strip()
    return s or None


def read(raw):
    """Parse the export. Returns {project, header_ok, groups: {internal_ref: {...}}, rows, other_now}.
    group = {client, unit, qty, rows: {stage: excel_row}, pct: {stage: leading %}, odd: bool}.
    'odd' = the 4 rows could not be told apart by their text, so nothing is written for that line."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw))
    except Exception:
        raise LayoutError("This file could not be opened as an Excel workbook.")
    ws = _sheet(wb)
    c = _cols(ws)
    groups, project = {}, None
    for r in range(2, ws.max_row + 1):
        bl = ws.cell(r, c["Billing Line"]).value
        if bl is None or str(bl).strip() == "":
            continue                                   # a heading / blank row
        ref = _key(ws.cell(r, c["Internal Ref No"]).value)
        if ref is None:
            continue
        if project is None and "Project" in c:
            project = _key(ws.cell(r, c["Project"]).value)
        g = groups.setdefault(ref, {"client": _key(ws.cell(r, c["Client Ref No"]).value),
                                    "unit": _key(ws.cell(r, c["Unit"]).value),
                                    "qty": ws.cell(r, c["Order Qty"]).value, "rows": {}, "pct": {}, "odd": False, "n": 0,
                                    "desc": _desc(ws.cell(r, c["BOQ"]).value) if "BOQ" in c else ""})
        g["n"] += 1
        st = stage_of(bl)
        if st is None or st in g["rows"]:
            g["odd"] = True
            continue
        g["rows"][st] = r
        g["pct"][st] = lead_pct(bl)
    if not groups:
        raise LayoutError("No billing lines were found in this file (the Billing Line column is empty).")
    return {"project": project, "groups": groups, "cols": c}


def _desc(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:140]


def project_of(raw):
    return read(raw)["project"]


def _unit_n(u):
    return re.sub(r"[^a-z0-9]", "", str(u or "").lower())


def _code(item_code):
    """MI keeps duplicate codes apart with a '#n' suffix ('5.1#2'). The file has the plain code on both, so a suffixed code
    can never be matched by code: it must be matched by Internal Ref No."""
    return None if "#" in str(item_code) else str(item_code).strip()


def match(lines, parsed, ok=None):
    """Match MI's lines to the file by Internal Ref No.
    lines: dicts with item_code, desc, unit, boq_qty, internal_ref.   ok: {item_code: internal_ref} the person already confirmed.
    Returns {ref_of: {item_code: internal_ref}, matched, ask: [...], missing: [...]}."""
    groups = parsed["groups"]
    ok = ok or {}
    ref_of, ask, missing = {}, [], []
    taken = set()
    for ln in lines:
        ir = _key(ln.get("internal_ref"))
        if ir is not None and ir in groups:
            ref_of[ln["item_code"]] = ir
            taken.add(ir)
    for ln in lines:
        code = ln["item_code"]
        if code in ref_of:
            continue
        if code in ok and ok[code] in groups and ok[code] not in taken:
            ref_of[code] = ok[code]
            taken.add(ok[code])
            continue
        # not matched by Internal Ref No. One narrow, checkable proposal: the same Client Ref No, the same unit and the
        # same quantity, exactly one such line in the file that no other MI line already uses.
        cc = _code(code)
        cands = []
        if cc is not None:
            for ref, g in groups.items():
                if ref in taken or g["client"] != cc or _unit_n(g["unit"]) != _unit_n(ln.get("unit")):
                    continue
                try:
                    if abs(float(g["qty"]) - float(ln["boq_qty"])) > 1e-6:
                        continue
                except (TypeError, ValueError):
                    continue
                cands.append(ref)
        if len(cands) == 1:
            g = groups[cands[0]]
            ask.append({"item_code": code, "mi": {"desc": str(ln.get("desc") or "")[:140], "unit": ln.get("unit"), "qty": ln["boq_qty"]},
                        "file": {"ref": cands[0], "client": g["client"], "desc": g["desc"], "unit": g["unit"], "qty": g["qty"]}})
        else:
            missing.append({"item_code": code, "desc": str(ln.get("desc") or "")[:140],
                            "why": "no Internal Ref No in MI for this line" if ir is None else "its Internal Ref No is not in this file"})
    return {"ref_of": ref_of, "matched": len(ref_of), "ask": ask, "missing": missing}


def _num(q):
    q = round(float(q), 4)
    return int(q) if abs(q - round(q)) < 1e-9 else q


def fill(raw, parsed, ref_of, wanted, stages):
    """Write the 'Now' column for one RA.
    wanted: {item_code: {stage: qty}} the quantities this RA bills.   stages: the stages this RA carries ('supply'/'install').
    Every Supply / Installation row this RA does NOT bill is blanked when it holds an old value (a placeholder typed to create
    the Billing Request, an earlier draft, or the other stage of a split bill): left in, ProjectBase would import it as part of
    this bill and the total would not match. Each one is reported. Testing / Handover rows are never touched, only reported.
    Returns (xlsx bytes, report)."""
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    ws = _sheet(wb)
    c = parsed["cols"]
    now = c["Now"]
    by_ref = {ref: code for code, ref in ref_of.items()}
    filled, cleared, odd_skipped = [], [], []
    for ref, g in parsed["groups"].items():
        code = by_ref.get(ref)
        for st in FILLABLE:
            r = g["rows"].get(st)
            if r is None:
                continue
            cell = ws.cell(r, now)
            q = (wanted.get(code) or {}).get(st) if (code is not None and st in stages) else None
            if q is not None and not g["odd"]:
                cell.value = _num(q)
                filled.append((code, ref, st, q))
            else:
                if cell.value not in (None, ""):
                    cleared.append({"ref": ref, "client": g["client"], "stage": st, "was": cell.value})
                    cell.value = None
        if g["odd"] and code is not None and any(st in wanted.get(code, {}) for st in stages):
            odd_skipped.append(code)
    # Testing / Handover are never written; say if the file has quantities typed there
    left = []
    for ref, g in parsed["groups"].items():
        for st in ("testing", "handover"):
            r = g["rows"].get(st)
            if r is not None and ws.cell(r, now).value not in (None, ""):
                left.append({"ref": ref, "client": g["client"], "stage": st, "value": ws.cell(r, now).value})
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), {"filled": filled, "cleared": cleared, "left": left, "odd": odd_skipped}
