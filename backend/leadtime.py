"""Lead time: real PO -> GRN elapsed days, per material, from Tally exports.

Today the engine's "Order now" timing is one hand-typed global number
(`lead_time`, currently 14 in the header) applied to every material. Real
lead time varies hugely by supplier and material. This module reads Mahesh's
actual Purchase Order and Goods Receipt Note registers and turns them into a
per-material lead time the engine can use instead - same "never invent, gate
on sample size, median over mean" discipline as the rest of this codebase
(health.py's block gate, rate.py's robust stats, boq.py/linkage.py's "no
match beats a wrong match").

File shape (both real Tally exports, same repeating-block layout schema.py
already knows how to read): row 0 = title, row 1 = date range, row 2 = real
header row, then repeating blocks of one master row (a real date in the Date
column - the voucher header) followed by one or more item-detail rows (Date
blank, Particulars = "<item code> - <description>").

Confirmed scope with Mahesh: NO site/project filtering. Every PO+GRN pair is
pooled together regardless of which site it was for - a given supplier's
dispatch time for a given material does not depend on which site it is going
to, and pooling gives far more data per material than any per-site split
would. `Other References` (the project/trade tag) is never read here.
"""
import re
from pathlib import Path

import pandas as pd

try:
    from . import schema, linkage, subcat
except ImportError:                         # standalone (tests / scripts)
    import schema, linkage, subcat


# --- sample-size gates, same spirit as health.py's MIN_DAYS_FOR_HIGH_CONF ---
# A lead time from 1-2 PO/GRN pairs is a coincidence, not a fact. Three tiers,
# each coarser (and so needing more evidence to trust) than the last:
#   material   - this exact item code's own history
#   supplier   - everything that supplier shipped, pooled
#   subcategory- every code in the same subcat.py bucket (Pipe, Cable, ...),
#                pooled across every supplier. Coarsest signal, so it needs
#                the most samples before it is trusted over the plain default.
# Below all three, the caller's global lead_time applies - never invented.
MIN_SAMPLES_MATERIAL = 3
CONFIDENT_MATERIAL = 8          # mirrors health.py's MIN_DAYS_FOR_HIGH_CONF
MIN_SAMPLES_SUPPLIER = 5
CONFIDENT_SUPPLIER = 10
MIN_SAMPLES_SUBCATEGORY = 10
CONFIDENT_SUBCATEGORY = 20

_ORDER_REF_RE = re.compile(r"^(.*?)\s+dt\.(.+)$")
_CODE_SPLIT_RE = re.compile(r"^([A-Za-z0-9]+)\s*[-_]\s*(.+)$")


# --------------------------------------------------------------- raw parsing
def _hdr_col(hdr_vals, *names):
    """First column whose header text exactly matches one of `names`
    (case-insensitive). Column *meaning*, not position - the same rule
    schema.py/boq.py already follow, so a reordered export does not silently
    misread a quantity column as a date.
    """
    wanted = {n.strip().lower() for n in names}
    for i, v in enumerate(hdr_vals):
        if isinstance(v, str) and v.strip().lower() in wanted:
            return i
    return None


def _find_header_row(raw, max_scan=6):
    """Both registers put the real header on the first row whose first cell
    reads exactly 'Date' (row 2 in every file seen). Scanned, not assumed, so
    a differently-shaped export is reported as unparseable rather than misread.
    """
    for r in range(min(max_scan, len(raw))):
        v = raw.iat[r, 0] if raw.shape[1] else None
        if isinstance(v, str) and v.strip().lower() == "date":
            return r
    return None


def _is_date(v):
    return pd.notna(v) and hasattr(v, "year") and hasattr(v, "month")


def _master_rows(raw, start_row, date_col):
    return [r for r in range(start_row, len(raw)) if _is_date(raw.iat[r, date_col])]


def _detail_range(masters, i, n_rows):
    lo = masters[i] + 1
    hi = masters[i + 1] if i + 1 < len(masters) else n_rows
    return lo, hi


def parse_po_register(path, sheet=0):
    """Every PO line item: po_no, po_date, supplier, item_text, qty.

    One row per item-detail line (not per voucher), so a multi-line PO
    contributes one lead-time data point per material once joined to a GRN.
    """
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    hdr_row = _find_header_row(raw)
    if hdr_row is None:
        raise ValueError("PO register: header row not found (expected a "
                          "'Date' column header within the first few rows)")
    hdr = raw.iloc[hdr_row].tolist()
    c_date = _hdr_col(hdr, "Date")
    c_item = _hdr_col(hdr, "Particulars")
    c_no = _hdr_col(hdr, "Voucher No.", "Voucher No")
    c_qty = _hdr_col(hdr, "Quantity")
    if None in (c_date, c_item, c_no):
        raise ValueError("PO register: could not locate Date / Particulars / "
                          "Voucher No. columns by header text")

    start = hdr_row + 1
    masters = _master_rows(raw, start, c_date)
    out = []
    for i, m in enumerate(masters):
        po_no = raw.iat[m, c_no]
        po_no = None if pd.isna(po_no) else str(po_no).strip()
        po_date = pd.Timestamp(raw.iat[m, c_date])
        supplier = raw.iat[m, c_item]
        supplier = None if pd.isna(supplier) else str(supplier).strip()
        if not po_no:
            continue
        lo, hi = _detail_range(masters, i, len(raw))
        for r in range(lo, hi):
            v = raw.iat[r, c_item]
            if pd.isna(v) or not str(v).strip():
                continue
            qty = raw.iat[r, c_qty] if c_qty is not None else None
            out.append({"po_no": po_no, "po_date": po_date, "supplier": supplier,
                        "item_text": str(v).strip(), "qty": qty})
    return pd.DataFrame(out, columns=["po_no", "po_date", "supplier",
                                      "item_text", "qty"])


def parse_grn_register(path, sheet=0):
    """Every GRN line item: grn_date, po_no_ref (parsed from 'Order No. &
    Date', or None for local/cash buys with no PO), supplier, item_text, qty.
    """
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    hdr_row = _find_header_row(raw)
    if hdr_row is None:
        raise ValueError("GRN register: header row not found (expected a "
                          "'Date' column header within the first few rows)")
    hdr = raw.iloc[hdr_row].tolist()
    c_date = _hdr_col(hdr, "Date")
    c_item = _hdr_col(hdr, "Particulars")
    c_supplier = _hdr_col(hdr, "Supplier")
    c_ref = _hdr_col(hdr, "Order No. & Date", "Order No & Date")
    c_no = _hdr_col(hdr, "Voucher No.", "Voucher No")
    c_qty = _hdr_col(hdr, "Quantity")
    if None in (c_date, c_item, c_ref):
        raise ValueError("GRN register: could not locate Date / Particulars / "
                          "Order No. & Date columns by header text")

    start = hdr_row + 1
    masters = _master_rows(raw, start, c_date)
    out = []
    for i, m in enumerate(masters):
        grn_date = pd.Timestamp(raw.iat[m, c_date])
        grn_no = raw.iat[m, c_no] if c_no is not None else None
        grn_no = None if pd.isna(grn_no) else str(grn_no).strip()
        ref = raw.iat[m, c_ref]
        po_no_ref = None
        if pd.notna(ref):
            mm = _ORDER_REF_RE.match(str(ref).strip())
            if mm:
                po_no_ref = mm.group(1).strip()
        supplier = raw.iat[m, c_supplier] if c_supplier is not None else None
        supplier = None if pd.isna(supplier) else str(supplier).strip()
        lo, hi = _detail_range(masters, i, len(raw))
        for r in range(lo, hi):
            v = raw.iat[r, c_item]
            if pd.isna(v) or not str(v).strip():
                continue
            qty = raw.iat[r, c_qty] if c_qty is not None else None
            out.append({"grn_date": grn_date, "grn_no": grn_no,
                        "po_no_ref": po_no_ref, "supplier": supplier,
                        "item_text": str(v).strip(), "qty": qty})
    return pd.DataFrame(out, columns=["grn_date", "grn_no", "po_no_ref",
                                      "supplier", "item_text", "qty"])


def split_item_text(s):
    """"<code> - <description>" or "<code>_<description>" -> (code, desc).
    Falls back to (None, s) when the line carries no code prefix at all."""
    m = _CODE_SPLIT_RE.match(str(s).strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, str(s).strip()


# --------------------------------------------------------------- join + gate
def join_lead_days(po_df, grn_df):
    """PO date per voucher number, from its first occurrence (a voucher number
    can recur - part-billed over time - so 'first' is the actual order date,
    matching the join already validated against the real data). Join GRN's
    parsed PO reference against it; one row per GRN item-detail line that
    carries a resolvable PO reference.

    Negative lead_days (GRN dated before its PO - a real backdated-paperwork
    quirk on these sites) are dropped, never clamped to an invented positive
    number. Returns (joined_df, n_excluded_negative).
    """
    if po_df.empty or grn_df.empty:
        return grn_df.iloc[0:0].assign(po_date=pd.NaT, lead_days=pd.NA), 0

    po_date_by_no = po_df.groupby("po_no")["po_date"].first()

    g = grn_df[grn_df["po_no_ref"].notna()].copy()
    g["po_date"] = g["po_no_ref"].map(po_date_by_no)
    g = g[g["po_date"].notna()].copy()
    g["lead_days"] = (g["grn_date"] - g["po_date"]).dt.days

    neg = int((g["lead_days"] < 0).sum())
    g = g[g["lead_days"] >= 0].copy()

    code_desc = g["item_text"].map(split_item_text)
    g["item_code"] = code_desc.map(lambda t: t[0])
    g["item_desc"] = code_desc.map(lambda t: t[1])
    # One voucher (one PO->one GRN event) can carry many line items - those
    # lines are NOT independent confirmations of the supplier's lead time,
    # they are one delivery event counted once per SKU it happened to
    # contain. A 29-line bulk PO must not outweigh five real, separate
    # deliveries as "29 samples vs 5". voucher_key identifies that one event,
    # so every aggregation tier below can de-duplicate to it before counting.
    g["voucher_key"] = (g["grn_no"].fillna("") + "|" +
                        g["po_no_ref"].fillna("") + "|" +
                        g["grn_date"].astype(str))
    return g, neg


# ------------------------------------------------------------- aggregation
def aggregate_by_code(joined):
    """Per item code: n, median lead_days, and whether it clears the
    material-level confidence gate. Median (not mean) - the real distribution
    is right-skewed with outliers up to ~90 days, same reasoning rate.py
    already applies to consumption rates.

    De-duplicated to one row per (code, voucher) first: if one PO/GRN voucher
    happened to split the same code across several lines (different rate
    batches), that is still ONE real delivery event for that code, not
    several - counting it several times would inflate n and falsely raise
    confidence without any new information.
    """
    rows = []
    has_code = joined[joined["item_code"].notna()]
    for code, g in has_code.groupby("item_code"):
        g = g.drop_duplicates(subset=["voucher_key"])
        n = len(g)
        # most common description text for this code, for later display/match
        desc = g["item_desc"].mode().iat[0] if len(g["item_desc"].mode()) else ""
        rows.append({
            "item_code": code, "description": desc, "n": n,
            "lead_days_median": float(g["lead_days"].median()),
            "confident": bool(n >= CONFIDENT_MATERIAL),
            "usable": bool(n >= MIN_SAMPLES_MATERIAL),
        })
    return pd.DataFrame(rows, columns=["item_code", "description", "n",
                                       "lead_days_median", "confident",
                                       "usable"])


def aggregate_by_supplier(joined):
    """Fallback tier 2: per supplier, pooled across every material they supply.
    Coarser than a per-material number but still real, still better than the
    one hand-typed global default for a material with too few data points of
    its own.

    De-duplicated to one row per voucher first. A supplier's n here counts
    real, separate delivery EVENTS - not line items. Without this, a single
    29-line bulk PO reads as "29 confirmations" of that supplier's lead time
    and clears the sample-size gate on one order; after dedup it correctly
    counts as the one data point it actually is.
    """
    has_sup = joined[joined["supplier"].notna() & (joined["supplier"] != "")]
    has_sup = has_sup.drop_duplicates(subset=["supplier", "voucher_key"])
    rows = []
    for sup, g in has_sup.groupby("supplier"):
        n = len(g)
        rows.append({
            "supplier": sup, "n": n,
            "lead_days_median": float(g["lead_days"].median()),
            "confident": bool(n >= CONFIDENT_SUPPLIER),
            "usable": bool(n >= MIN_SAMPLES_SUPPLIER),
        })
    return pd.DataFrame(rows, columns=["supplier", "n", "lead_days_median",
                                       "confident", "usable"])


def aggregate_by_subcategory(joined):
    """Fallback tier 3 (coarsest): every code whose *description* falls in the
    same subcat.py bucket (Pipe, Cable, Fastener, ...), pooled across every
    supplier and every specific item. Still real PO->GRN data - just a wider
    pool than one exact item code or one exact supplier - so it beats the flat
    global default whenever a material has no confident code/supplier match of
    its own but a same-type item elsewhere in the register does have history.
    "Other" is excluded: it is a catch-all for names with no clear type word,
    so pooling it would mix genuinely unrelated materials into one number.

    De-duplicated to one row per (subcategory, voucher): a voucher touching
    both pipes and wires still gives one real Pipe observation and one real
    Wire observation (genuinely different signals), but five pipe SKUs inside
    the same voucher collapse to the one delivery event they actually are.
    """
    tmp = joined.copy()
    tmp["subcategory"] = tmp["item_desc"].map(subcat.classify)
    tmp = tmp[tmp["subcategory"] != "Other"]
    tmp = tmp.drop_duplicates(subset=["subcategory", "voucher_key"])
    rows = []
    for cat, g in tmp.groupby("subcategory"):
        n = len(g)
        rows.append({
            "subcategory": cat, "n": n,
            "lead_days_median": float(g["lead_days"].median()),
            "confident": bool(n >= CONFIDENT_SUBCATEGORY),
            "usable": bool(n >= MIN_SAMPLES_SUBCATEGORY),
        })
    return pd.DataFrame(rows, columns=["subcategory", "n", "lead_days_median",
                                       "confident", "usable"])


def build_aggregates(po_path, grn_path):
    """One call: parse both registers, join, gate. Returns a dict with the
    per-code and per-supplier tables plus a small report (same spirit as
    schema.detect()'s report) so the numbers can be shown to Mahesh before
    anything downstream trusts them."""
    po_df = parse_po_register(po_path)
    grn_df = parse_grn_register(grn_path)
    joined, n_negative = join_lead_days(po_df, grn_df)

    by_code = aggregate_by_code(joined)
    by_supplier = aggregate_by_supplier(joined)
    by_subcategory = aggregate_by_subcategory(joined)

    report = {
        "po_lines": int(len(po_df)),
        "grn_lines": int(len(grn_df)),
        "grn_vouchers_with_po_ref": int(grn_df["po_no_ref"].notna().sum()),
        "joined_lines": int(len(joined)) + n_negative,
        "matched_lines": int(len(joined)),
        "excluded_negative": int(n_negative),
        "distinct_codes": int(by_code["item_code"].nunique()) if len(by_code) else 0,
        "codes_meeting_material_gate": int(by_code["usable"].sum()) if len(by_code) else 0,
        "subcategories_meeting_gate": int(by_subcategory["usable"].sum()) if len(by_subcategory) else 0,
        "median_lead_days_overall": float(joined["lead_days"].median()) if len(joined) else None,
    }
    # sup_by_code: which supplier shipped a given code most often - baked in
    # here so match_materials() needs no access to the raw `joined` rows at
    # lookup time (the JSON file is self-contained, nothing re-parsed).
    sup_by_code = {}
    if len(joined):
        for code, g in joined[joined.item_code.notna()].groupby("item_code"):
            m = g["supplier"].mode()
            sup_by_code[code] = m.iat[0] if len(m) else None

    return {"by_code": by_code, "by_supplier": by_supplier,
            "by_subcategory": by_subcategory, "joined": joined,
            "sup_by_code": sup_by_code, "report": report}


def to_json_dict(agg):
    """Aggregates -> a plain, human-readable JSON-serialisable dict. This is
    the artifact that actually ships with the app - a static snapshot of real
    PO->GRN history, baked in at build time. No upload, no runtime parsing of
    the Tally exports; every forecast run just reads this file."""
    return {
        "by_code": agg["by_code"].to_dict(orient="records"),
        "by_supplier": agg["by_supplier"].to_dict(orient="records"),
        "by_subcategory": agg["by_subcategory"].to_dict(orient="records"),
        "sup_by_code": agg["sup_by_code"],
        "report": agg["report"],
    }


def load_aggregates_json(path):
    """The mirror of to_json_dict() - read the baked JSON file back into the
    same shape match_materials() expects. Returns None (never raises) if the
    file is missing or unreadable, so a missing/corrupt data file just means
    every material falls back to the caller's global lead_time, same as
    before this feature existed."""
    import json
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        by_code = pd.DataFrame(d["by_code"],
                               columns=["item_code", "description", "n",
                                       "lead_days_median", "confident", "usable"])
        by_supplier = pd.DataFrame(d["by_supplier"],
                                   columns=["supplier", "n", "lead_days_median",
                                           "confident", "usable"])
        by_subcategory = pd.DataFrame(d.get("by_subcategory", []),
                                      columns=["subcategory", "n",
                                              "lead_days_median", "confident",
                                              "usable"])
        return {"by_code": by_code, "by_supplier": by_supplier,
                "by_subcategory": by_subcategory,
                "sup_by_code": d.get("sup_by_code", {}), "report": d.get("report", {})}
    except Exception:
        return None


# ------------------------------------------------- match to stock materials
def _code_salient_cache(by_code):
    return [(row.item_code, linkage.salient(row.description))
            for row in by_code.itertuples()]


def match_materials(materials, aggregates, cutoff=linkage.CONFIDENT):
    """materials: iterable of stock-register material names (as the forecast
    engine knows them - e.g. daily.material.unique()).

    Returns {material_name: {"lead_days": float, "n": int, "basis": str,
                             "matched_code": str|None, "score": float}}
    for materials where a usable number exists. A material that clears none
    of the three tiers is simply absent from the result - the engine's own
    global `lead_time` fallback applies to it, exactly like an unmatched item
    never gets an invented rate elsewhere in this codebase.

    Three tiers, coarsest last: a confident per-material (per-code) match
    first; if the code matched but is too thin (< MIN_SAMPLES_MATERIAL), that
    code's supplier's pooled median; if the supplier is too thin too (or no
    code matched with confidence at all), the material's OWN sub-category
    (subcat.py - Pipe, Cable, Fastener, ...) pooled median. Each tier is
    tried only after the one above it fails its own sample-size gate - a
    material never skips a finer, more specific real number in favour of a
    coarser one just because the coarser one happens to be available.
    """
    by_code = aggregates["by_code"]
    by_supplier = aggregates["by_supplier"]
    by_subcat = aggregates.get("by_subcategory")
    out = {}
    if by_code.empty:
        return out

    code_sal = _code_salient_cache(by_code)
    code_rows = {row.item_code: row for row in by_code.itertuples()}

    # supplier lookup: which supplier(s) shipped a given code, pooled median.
    # Precomputed by build_aggregates()/baked into the JSON file - so a
    # lookup loaded via load_aggregates_json() needs no raw PO/GRN rows at
    # match time, only the small aggregate tables + this small dict.
    sup_by_code = aggregates.get("sup_by_code") or {}
    if not sup_by_code and "joined" in aggregates and len(aggregates["joined"]):
        joined = aggregates["joined"]
        for code, g in joined[joined.item_code.notna()].groupby("item_code"):
            m = g["supplier"].mode()
            sup_by_code[code] = m.iat[0] if len(m) else None
    sup_rows = {row.supplier: row for row in by_supplier.itertuples()}
    subcat_rows = ({row.subcategory: row for row in by_subcat.itertuples()}
                   if by_subcat is not None and len(by_subcat) else {})

    def _subcat_fallback(mat_subcat, matched_code, score):
        """The material's own sub-category (from its own name, not the
        matched code's) - a completely independent signal from the code/
        supplier tiers above, so it still works even when no code matched
        with confidence at all."""
        if mat_subcat == "Other":
            return None
        srow = subcat_rows.get(mat_subcat)
        if srow is None or not srow.usable:
            return None
        return {"lead_days": srow.lead_days_median, "n": srow.n,
                "basis": "subcategory", "matched_code": matched_code,
                "score": score, "confident": bool(srow.confident)}

    for mat in materials:
        a = linkage.salient(mat)
        scored = sorted(((code, linkage.score(a, sb)) for code, sb in code_sal),
                        key=lambda x: x[1], reverse=True)
        best_code, best_score = scored[0] if scored else (None, 0.0)

        if best_score < cutoff:
            # no confident code match at all - only the sub-category tier
            # is possible for this material
            fb = _subcat_fallback(a["subcat"], None, None)
            if fb is not None:
                out[mat] = fb
            continue

        row = code_rows[best_code]
        if row.usable:
            out[mat] = {"lead_days": row.lead_days_median, "n": row.n,
                        "basis": "material", "matched_code": best_code,
                        "score": best_score, "confident": bool(row.confident)}
            continue

        # code matched but too thin on its own - try the code's own supplier
        sup = sup_by_code.get(best_code)
        srow = sup_rows.get(sup) if sup else None
        if srow is not None and srow.usable:
            out[mat] = {"lead_days": srow.lead_days_median, "n": srow.n,
                        "basis": "supplier", "matched_code": best_code,
                        "score": best_score, "confident": bool(srow.confident)}
            continue

        # supplier too thin as well - last resort: the material's own
        # sub-category, pooled across every code/supplier in that bucket
        fb = _subcat_fallback(a["subcat"], best_code, best_score)
        if fb is not None:
            out[mat] = fb
        # else: leave unmatched -> caller's global default applies
    return out
