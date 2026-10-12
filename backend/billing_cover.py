"""The Tax Invoice cover sheet of a billing workbook (billing.py uses it).

The layout is the user's own invoice format (Invoice_format_ra_number.xlsx),
kept byte-for-byte as a small embedded template so fonts, borders, column
widths, row heights and merges are exactly theirs. Only the cells below are
ever filled; everything else is the format's own text.

    B2 client company      E3 invoice no     A14 item no.   B14 service line
    B3 client address      E4 date           D14 SAC code   E14 amount (this RA)
    B4 place of supply     E5 W/O            E21 total      E24/E25 SGST / CGST
    B5 client GSTIN        E6 RA no          E28 GST total  E30 invoice amount
    B6 project             E7 service        E31 round-off  E32 total invoice
    B8/B9 state / code     A33 amount in words
    B36..B40 our GSTIN / state / code / PAN / category      A41 "For <company>"

A value that is not known is left EMPTY -- nothing is ever guessed.
"""
import io
import base64
import datetime as dt
from copy import copy
from decimal import Decimal, ROUND_HALF_UP

_TEMPLATE = (
    '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'
    'AAAJAAkAPgIAABUdAAAAAA=='
)

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_1000(n):
    s = ""
    if n >= 100:
        s = _ONES[n // 100] + " Hundred"
        n %= 100
        if n:
            s += " "
    return s + (_below_100(n) if n else "")


def words(n):
    """A whole number of rupees in Indian words: 11215801 -> "One Crore Twelve Lakh ... Only."."""
    n = int(n)
    if n == 0:
        return "Zero Only."
    neg, n = n < 0, abs(n)
    parts = []
    for div, name in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (1000, "Thousand")):
        q, n = divmod(n, div)
        if q:
            parts.append(_below_1000(q) + " " + name)
    if n:
        parts.append(_below_1000(n))
    return ("Minus " if neg else "") + " ".join(parts) + " Only."


def money(amount, sgst, cgst):
    """(amount, sgst value, cgst value, gst total, invoice amount, round-off, total) -- the same
    arithmetic the sheet's own formulas do (ROUND half away from zero to the rupee)."""
    a = float(amount or 0.0)
    s = a * (sgst or 0.0) / 100.0
    c = a * (cgst or 0.0) / 100.0
    inv = a + (s + c)
    tot = float(Decimal(repr(inv)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"amount": a, "sgst": s, "cgst": c, "gst": s + c, "invoice": inv, "round": tot - inv, "total": tot}


def build(ws, v):
    """Fill worksheet `ws` (an empty sheet of the target workbook) with the invoice cover.
    v: dict, see the keys read below. v["live"]: write formulas (a draft RA) or values (locked)."""
    import openpyxl
    src = openpyxl.load_workbook(io.BytesIO(base64.b64decode("".join(_TEMPLATE)))).active
    for row in src.iter_rows(min_row=1, max_row=42, min_col=1, max_col=7):
        for c in row:
            t = ws.cell(c.row, c.column, c.value)
            if c.has_style:
                t.font, t.border, t.fill = copy(c.font), copy(c.border), copy(c.fill)
                t.alignment, t.number_format, t.protection = copy(c.alignment), c.number_format, copy(c.protection)
    for rng in src.merged_cells.ranges:
        ws.merge_cells(str(rng))
    for k, dim in src.column_dimensions.items():
        ws.column_dimensions[k].width = dim.width
    for k, dim in src.row_dimensions.items():
        if dim.height:
            ws.row_dimensions[k].height = dim.height
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation, ws.page_setup.paperSize = "portrait", ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    from openpyxl.worksheet.properties import PageSetupProperties
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_area = "A1:E42"

    live = bool(v.get("live"))
    m = money(v.get("amount"), v.get("sgst"), v.get("cgst"))
    put = lambda ref, val: setattr(ws[ref], "value", val if val not in ("", None) else None)
    put("B2", v.get("client"))
    put("B3", v.get("address"))
    put("B4", v.get("place"))
    put("B5", v.get("gstin"))
    put("B6", v.get("project"))
    put("B8", v.get("state"))
    put("B9", v.get("state_code"))
    put("E3", v.get("inv_no"))
    d = v.get("inv_date")
    put("E4", dt.date.fromisoformat(d) if isinstance(d, str) and d else d)
    ws["E4"].number_format = "dd-mm-yyyy"      # explicit: the format's own mm-dd-yy follows the PC's region
    put("E5", v.get("wo"))
    put("E6", v.get("ra_no"))
    put("E7", v.get("service"))
    put("B14", v.get("desc"))
    sac = str(v.get("sac") or "").strip()
    put("D14", int(sac) if sac.isdigit() else (sac or None))
    put("A22", "GST Charged on " + (v.get("gst_label") or "Amount"))
    # amounts: formulas on a draft (they follow the Summary sheet), values once locked
    if live:
        ws["E14"] = ("=" + v["amount_ref"]) if v.get("amount_ref") else m["amount"]
        ws["D24"], ws["D25"] = (v["sgst"] / 100.0 if v.get("sgst") is not None else None), (v["cgst"] / 100.0 if v.get("cgst") is not None else None)
        ws["E24"] = "=+E21*D24" if v.get("sgst") is not None else None
        ws["E25"] = "=+E21*D25" if v.get("cgst") is not None else None
        ws["E31"] = "=ROUND(E30,0)-E30"
    else:
        ws["E14"] = m["amount"]
        ws["E21"], ws["E28"], ws["E30"], ws["E32"] = m["amount"], m["gst"], m["invoice"], m["total"]
        ws["D24"], ws["D25"] = (v["sgst"] / 100.0 if v.get("sgst") is not None else None), (v["cgst"] / 100.0 if v.get("cgst") is not None else None)
        ws["E24"] = m["sgst"] if v.get("sgst") is not None else None
        ws["E25"] = m["cgst"] if v.get("cgst") is not None else None
        ws["E31"] = m["round"]
    for ref in ("D24", "D25"):
        ws[ref].number_format = "0.00%"
    ws["A33"] = "Amount in Words :  " + words(m["total"])
    sp = v.get("supplier") or {}
    put("B36", sp.get("gstin"))
    put("B37", sp.get("state"))
    put("B38", sp.get("state_code"))
    put("B39", sp.get("pan"))
    put("B40", sp.get("category"))
    ws["A41"] = ("For " + v["contractor"]) if v.get("contractor") else None
    if live:
        ws["A1"] = "DRAFT — not invoiced yet, numbers can still change"
        from openpyxl.styles import Font
        ws["A1"].font = Font(name="Arial Narrow", bold=True, size=10, color="A02622")
        ws.sheet_properties.tabColor = "FFD966"
    return m
