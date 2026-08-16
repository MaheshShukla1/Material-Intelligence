"""pytest for boq.py's ProjectBase parser: detection, rate/qty extraction,
the whole-project-total quantity convention, and the merge-time dedup fix
that keeps a rate mapped to the right item even when two sheets collide on
a code.

subcat.py isn't in this sandbox (see prior handoff notes) -- boq.py imports
it unconditionally, so a minimal fake module is injected into sys.modules
for THIS TEST PROCESS ONLY, never written to disk. It must never ship as a
real subcat.py file: that would silently shadow the real subcategory
classifier in the actual repo.

Run: cd tests && python -m pytest test_boq_projectbase.py -v
"""
import os
import sys
import types

_subcat_stub = types.ModuleType("subcat")
_subcat_stub.classify = lambda name: "Other"
sys.modules.setdefault("subcat", _subcat_stub)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd
import pytest

import boq

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SINGLE = os.path.join(FIXTURES, "thoth_mall_electrical_singlesheet.xlsx")
MULTI = os.path.join(FIXTURES, "thoth_mall_multisheet.xlsx")


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def test_is_projectbase_true_for_a_real_export():
    xl = pd.ExcelFile(SINGLE)
    raw = xl.parse("BOQ Details", header=None, dtype=object)
    assert boq.is_projectbase(raw) is True


def test_is_projectbase_false_for_a_raw_mepf_sheet():
    # a plain raw BOQ shape: no "BOQ Unit"/"Design Quantity"/"Client Ref No"
    # signature at all -- must never be misdetected as ProjectBase
    raw = pd.DataFrame([
        ["Item No.", "Description of Work", "Unit", "Qty"],
        ["1", "25mm PVC conduit", "Mtr", "210"],
        ["2", "20mm PVC conduit", "Mtr", "150"],
    ])
    assert boq.is_projectbase(raw) is False


def test_is_projectbase_false_for_an_unrelated_sheet():
    raw = pd.DataFrame([["Groups", "Brands", "Units"], ["Cable", "X", "Mtr"]])
    assert boq.is_projectbase(raw) is False


# --------------------------------------------------------------------------
# real-file: single-sheet export
# --------------------------------------------------------------------------
def test_real_single_sheet_electrical_export():
    parsed, skipped = boq.parse_workbook(SINGLE)
    assert len(parsed) == 1
    r = next(iter(parsed.values()))
    # ground truth from a direct pandas pass over the same file: 339 rows
    # have a real BOQ Unit + Design Quantity + Rate
    assert len(r["items"]) == 339
    assert len(r["rates"]) == 339
    assert r["source_format"] == "projectbase"
    assert r["qty_is_total"] is True
    # the "Masters" reference sheet must be skipped, not treated as a BOQ
    assert any(s["sheet"] == "Masters" for s in skipped)


def test_real_single_sheet_known_line_item_values():
    parsed, _ = boq.parse_workbook(SINGLE)
    r = next(iter(parsed.values()))
    items = r["items"]
    row = items[items["item_code"] == "1.1"].iloc[0]
    assert row["unit"] == "Nos"
    assert row["qty"] == pytest.approx(2.0)
    assert r["rates"]["1.1"] == pytest.approx(22175760.00)


def test_real_single_sheet_no_section_or_subtotal_rows_leaked():
    """The bug this test would have caught: str(float('nan')) is the
    non-empty string "nan", so a blank-check written as `== ""` silently
    treats an empty cell as real content. Every row that made it through
    must have both a real unit and a genuine BOQ Unit -- no section
    headers, "Sub Total", or "Notes:" rows among them.

    Real-data footnote: a handful of rows here (e.g. "5.16") have Design
    Quantity = the literal text "RO" (Rate Only / Run-On -- a real
    construction billing term for scope priced without a fixed quantity,
    like "obtain statutory approvals"). _to_qty("RO") correctly returns
    None for these -- that's honest (there genuinely is no quantity), not a
    parsing failure, so they're allowed through with qty=None rather than
    silently dropped or invented a number for. Tracking progress/₹ against
    a quantity-less item is a real, separate problem for a later session --
    today's fix is that these rows parse at all, with a real unit and rate,
    instead of the "RO" text breaking anything."""
    parsed, _ = boq.parse_workbook(SINGLE)
    items = next(iter(parsed.values()))["items"]
    assert items["unit"].apply(lambda u: isinstance(u, str) and u.strip() != "").all()
    assert not items["description"].str.contains(r"^Sub Total", regex=True, na=False).any()
    assert not items["description"].str.contains(r"^Notes", regex=True, na=False).any()
    # every row has a real unit; only genuine "RO"-style rows lack a qty
    no_qty = items[items["qty"].isna()]
    assert len(no_qty) <= 15, f"FAIL: unexpectedly many no-qty rows ({len(no_qty)}) -- check for a real regression, not just RO items"
    assert len(items) - len(no_qty) >= 320, "FAIL: most real line items should still have a real quantity"


def test_ro_rate_only_items_get_a_real_rate_despite_no_quantity():
    """The "RO" rows above still carry a real Rate -- a Lumpsum/PC-sum
    scope item can be priced even with no fixed quantity. Confirms the
    parser doesn't drop the rate just because qty came back None."""
    parsed, _ = boq.parse_workbook(SINGLE)
    r = next(iter(parsed.values()))
    row = r["items"][r["items"]["item_code"] == "5.16"]
    assert len(row) == 1
    assert pd.isna(row.iloc[0]["qty"])
    assert r["rates"]["5.16"] == pytest.approx(4000000.0)


# --------------------------------------------------------------------------
# real-file: multi-sheet export (the user's proposed one-workbook-per-project
# format -- Electrical + FFTG as separate tabs, Masters as a reference sheet)
# --------------------------------------------------------------------------
def test_real_multi_sheet_workbook_routes_each_sheet_to_its_service():
    parsed, skipped = boq.parse_workbook(MULTI)
    assert set(parsed.keys()) == {"Electrical", "Fire"}   # "FFTG" sheet -> Fire, via existing _SVC_RULES
    assert any(s["sheet"] == "Masters" for s in skipped)
    assert parsed["Electrical"]["source_format"] == "projectbase"
    assert parsed["Fire"]["source_format"] == "projectbase"
    assert parsed["Electrical"]["qty_is_total"] is True
    assert parsed["Fire"]["qty_is_total"] is True


def test_real_multi_sheet_rate_and_qty_counts_match_single_sheet_source():
    # same underlying Electrical data as the single-sheet file -- counts
    # must match exactly, proving the multi-sheet workbook path (the format
    # the user is proposing to standardise on) parses identically to the
    # already-verified single-sheet path
    parsed, _ = boq.parse_workbook(MULTI)
    assert len(parsed["Electrical"]["items"]) == 339
    assert len(parsed["Electrical"]["rates"]) == 339
    assert parsed["Electrical"]["rates"]["1.1"] == pytest.approx(22175760.00)


# --------------------------------------------------------------------------
# merge-time dedup correctness -- the bug caught before it shipped: a rate
# dict keyed by a sheet's OWN item_code would point at the wrong item the
# moment merging renames a colliding code to "1.1#2"
# --------------------------------------------------------------------------
def _pb_sheet(rows, header_extra=()):
    """Build a minimal synthetic ProjectBase-shaped sheet (header row + data
    rows) as a raw (no-header) DataFrame, for isolated merge tests that
    don't need a real file."""
    header = ["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit",
              "Order Quantity", "Design Quantity", "Rate"]
    data = [header] + [
        [i + 1, code, desc, unit, qty, qty, rate]
        for i, (code, desc, unit, qty, rate) in enumerate(rows)
    ]
    return pd.DataFrame(data)


def test_merge_two_projectbase_sheets_colliding_codes_keep_correct_rates():
    sheet_a = _pb_sheet([("1.1", "Cable type A", "Mtr", 100, 500.0)])
    sheet_b = _pb_sheet([("1.1", "Cable type B", "Mtr", 200, 900.0)])   # same code, different item!
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name
    with pd.ExcelWriter(path) as writer:
        sheet_a.to_excel(writer, sheet_name="Electrical A", header=False, index=False)
        sheet_b.to_excel(writer, sheet_name="Electrical B", header=False, index=False)
    try:
        parsed, _ = boq.parse_workbook(path)
        r = parsed["Electrical"]
        items = r["items"]
        assert len(items) == 2
        codes = set(items["item_code"])
        assert codes == {"1.1", "1.1#2"}, f"FAIL: expected a deduped '1.1#2', got {codes}"
        # the critical assertion: each FINAL code's rate must match ITS OWN
        # item, not get silently swapped or overwritten by the collision
        row_a = items[items["description"] == "Cable type A"].iloc[0]
        row_b = items[items["description"] == "Cable type B"].iloc[0]
        assert r["rates"][row_a["item_code"]] == pytest.approx(500.0), \
            "FAIL: Cable type A's rate got mixed up with Cable type B's after the merge-dedup"
        assert r["rates"][row_b["item_code"]] == pytest.approx(900.0), \
            "FAIL: Cable type B's rate got mixed up with Cable type A's after the merge-dedup"
    finally:
        os.unlink(path)


def test_merge_projectbase_plus_raw_sheet_is_marked_mixed_not_silently_total():
    """A workbook where ONE service's sheets combine a ProjectBase export
    with a genuine raw MEPF sheet (e.g. an addendum tab someone typed by
    hand) must not silently inherit qty_is_total=True from its ProjectBase
    sibling -- that would wrongly skip room-multiplication for the raw
    sheet's items too. qty_is_total must be None (unresolved) so the
    engineer is asked, never guessed for."""
    pb_sheet = _pb_sheet([("1.1", "Cable type A", "Mtr", 100, 500.0)])
    raw_sheet = pd.DataFrame([
        ["Item No.", "Description of Work", "Unit", "Qty"],
        ["2.1", "25mm PVC conduit", "Mtr", "50"],
    ])
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name
    with pd.ExcelWriter(path) as writer:
        pb_sheet.to_excel(writer, sheet_name="Electrical PB", header=False, index=False)
        raw_sheet.to_excel(writer, sheet_name="Electrical Raw", header=False, index=False)
    try:
        parsed, _ = boq.parse_workbook(path)
        r = parsed["Electrical"]
        assert r["source_format"] == "mixed"
        assert r["qty_is_total"] is None
        # the ProjectBase item's real rate still comes through
        assert any(v == pytest.approx(500.0) for v in r["rates"].values())
        # the raw item has no rate -- never invented
        raw_code = [c for c in r["items"]["item_code"] if str(c).startswith("2.")]
        assert raw_code and raw_code[0] not in r["rates"]
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------
# a raw sheet, even one that happens to have a few ProjectBase-ish header
# words, must still fall through to the generic parser rather than being
# force-fit through the ProjectBase path and coming up empty
# --------------------------------------------------------------------------
def test_near_miss_signature_falls_through_to_generic_parser():
    # only 1 of the 4 signature words present -- below _PB_MIN_SIGNATURE_HITS
    raw = pd.DataFrame([
        ["Item No.", "Description of Work", "Unit", "Rate"],
        ["1", "25mm PVC conduit", "Mtr", "210"],
    ])
    assert boq.is_projectbase(raw) is False
    res = boq.parse_sheet(raw, service="Electrical")
    # falls through to the generic path; "Rate" isn't a recognised generic
    # qty/unit synonym so this specific sheet still won't parse as a BOQ --
    # the point of this test is that it doesn't CRASH or silently misroute,
    # not that this particular malformed sheet succeeds
    assert res is None or res.get("source_format") != "projectbase"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
