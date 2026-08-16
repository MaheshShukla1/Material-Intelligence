"""pytest for the rates.json / planned.json seeding logic that runs inside
siteprogress.py's upload_boq() route, and the /{slug}/qty-mode wizard
endpoint. siteprogress.py itself can't be imported standalone here (needs
main.py, subcat.py, and a real data directory this sandbox doesn't have --
see prior handoff notes). This replicates the exact seeding algorithm from
both route bodies verbatim against real boq.parse_workbook() output, so a
regression here would also break the routes.

Run: cd tests && python -m pytest test_boq_upload_seeding.py -v
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


def seed_rates_and_planned(parsed, all_rates=None, all_planned=None):
    """Verbatim replica of upload_boq()'s seeding loop."""
    all_rates = all_rates if all_rates is not None else {}
    all_planned = all_planned if all_planned is not None else {}
    needs_qty_mode, auto_rated = [], {}
    for svc, r in parsed.items():
        svc_rates = all_rates.setdefault(svc, {})
        new_rates = 0
        for code, rate in (r.get("rates") or {}).items():
            if code not in svc_rates:
                svc_rates[code] = rate
                new_rates += 1
        if new_rates:
            auto_rated[svc] = new_rates

        if r.get("qty_is_total") is True:
            svc_planned = all_planned.setdefault(svc, {})
            for it in r["items"].itertuples():
                code = str(it.item_code)
                qty = getattr(it, "qty", None)
                if code not in svc_planned and qty is not None and qty == qty:
                    svc_planned[code] = float(qty)
        elif r.get("source_format") != "raw" or r.get("qty_is_total") is None:
            needs_qty_mode.append(svc)
    return all_rates, all_planned, auto_rated, needs_qty_mode


def apply_qty_mode(items, mode, svc_planned=None):
    """Verbatim replica of the /qty-mode route body (minus the HTTP plumbing)."""
    svc_planned = svc_planned if svc_planned is not None else {}
    if mode == "per_room":
        return svc_planned, 0
    seeded = 0
    for it in items.itertuples():
        code = str(it.item_code)
        qty = getattr(it, "qty", None)
        if code not in svc_planned and qty is not None and qty == qty:
            svc_planned[code] = float(qty)
            seeded += 1
    return svc_planned, seeded


# --------------------------------------------------------------------------
# upload_boq() seeding, against the real Thoth Mall Electrical export
# --------------------------------------------------------------------------
def test_projectbase_upload_seeds_rates_and_planned_fully_automatically():
    parsed, _ = boq.parse_workbook(SINGLE)
    rates, planned, auto_rated, needs_mode = seed_rates_and_planned(parsed)
    svc = next(iter(parsed.keys()))
    assert len(rates[svc]) == 339
    assert rates[svc]["1.1"] == pytest.approx(22175760.00)
    # planned.json seeded from Design Quantity directly -- no room multiply
    assert planned[svc]["1.1"] == pytest.approx(2.0)
    assert auto_rated[svc] == 339
    assert needs_mode == [], "FAIL: a pure ProjectBase service should never need the qty-mode wizard step"


def test_projectbase_upload_never_overwrites_an_existing_manual_rate():
    parsed, _ = boq.parse_workbook(SINGLE)
    svc = next(iter(parsed.keys()))
    # engineer had already manually corrected this rate before a re-upload
    existing_rates = {svc: {"1.1": 99999999.0}}
    rates, _, auto_rated, _ = seed_rates_and_planned(parsed, all_rates=existing_rates)
    assert rates[svc]["1.1"] == pytest.approx(99999999.0), "FAIL: a manual rate edit must survive a BOQ re-upload"
    # every OTHER item still gets auto-seeded normally
    assert rates[svc]["1.2"] == pytest.approx(41598700.00)
    assert auto_rated[svc] == 338, "FAIL: exactly one fewer auto-seed since 1.1 was already set"


def test_projectbase_upload_never_overwrites_an_existing_manual_planned_qty():
    parsed, _ = boq.parse_workbook(SINGLE)
    svc = next(iter(parsed.keys()))
    existing_planned = {svc: {"1.1": 3.0}}   # engineer manually overrode this before re-upload
    _, planned, _, _ = seed_rates_and_planned(parsed, all_planned=existing_planned)
    assert planned[svc]["1.1"] == pytest.approx(3.0), "FAIL: a manual planned-qty override must survive a re-upload"
    assert planned[svc]["1.2"] == pytest.approx(1.0)   # still auto-seeded normally


def test_ro_rate_only_items_get_a_rate_but_no_planned_seed():
    """The real "RO" (Rate Only) rows -- a real unit and rate, no fixed
    quantity -- must get a rate seeded but must NOT get a planned.json
    entry with a fabricated qty (there genuinely isn't one)."""
    parsed, _ = boq.parse_workbook(SINGLE)
    svc = next(iter(parsed.keys()))
    rates, planned, _, _ = seed_rates_and_planned(parsed)
    assert "5.16" in rates[svc]
    assert rates[svc]["5.16"] == pytest.approx(4000000.0)
    assert "5.16" not in planned[svc], "FAIL: an RO item has no real quantity -- must never get an invented planned figure"


def test_multi_sheet_workbook_seeds_both_services_independently():
    parsed, _ = boq.parse_workbook(MULTI)
    rates, planned, auto_rated, needs_mode = seed_rates_and_planned(parsed)
    assert set(rates.keys()) == {"Electrical", "Fire"}
    assert len(rates["Electrical"]) == 339
    assert len(rates["Fire"]) == 131
    assert needs_mode == []


# --------------------------------------------------------------------------
# a raw (non-ProjectBase) service must be flagged for the wizard, never
# auto-seeded with a guess
# --------------------------------------------------------------------------
def _fake_raw_result(items_rows):
    items = pd.DataFrame(items_rows)
    return {"items": items, "rates": {}, "source_format": "raw", "qty_is_total": None}


def test_raw_service_needs_qty_mode_and_gets_no_auto_seed():
    parsed = {"Electrical": _fake_raw_result([
        {"item_code": "2.1", "description": "25mm PVC conduit", "unit": "Mtr", "qty": 210.0},
    ])}
    rates, planned, auto_rated, needs_mode = seed_rates_and_planned(parsed)
    assert rates.get("Electrical", {}) == {}
    assert planned.get("Electrical", {}) == {}
    assert auto_rated == {}
    assert needs_mode == ["Electrical"]


def test_mixed_service_also_needs_qty_mode():
    parsed = {"Electrical": {"items": pd.DataFrame([{"item_code": "1.1", "qty": 2.0}]),
                             "rates": {"1.1": 500.0}, "source_format": "mixed", "qty_is_total": None}}
    _, _, _, needs_mode = seed_rates_and_planned(parsed)
    assert needs_mode == ["Electrical"]


# --------------------------------------------------------------------------
# /qty-mode wizard endpoint
# --------------------------------------------------------------------------
def test_qty_mode_total_seeds_planned_from_boq_qty():
    items = pd.DataFrame([
        {"item_code": "2.1", "qty": 3200.0},
        {"item_code": "2.2", "qty": 480.0},
    ])
    svc_planned, seeded = apply_qty_mode(items, "total")
    assert svc_planned == {"2.1": 3200.0, "2.2": 480.0}
    assert seeded == 2


def test_qty_mode_per_room_is_a_true_no_op():
    items = pd.DataFrame([{"item_code": "2.1", "qty": 3200.0}])
    svc_planned, seeded = apply_qty_mode(items, "per_room")
    assert svc_planned == {}
    assert seeded == 0


def test_qty_mode_total_never_overwrites_an_existing_override():
    items = pd.DataFrame([{"item_code": "2.1", "qty": 3200.0}, {"item_code": "2.2", "qty": 480.0}])
    existing = {"2.1": 999.0}
    svc_planned, seeded = apply_qty_mode(items, "total", svc_planned=existing)
    assert svc_planned["2.1"] == 999.0, "FAIL: an existing manual override must survive"
    assert svc_planned["2.2"] == 480.0
    assert seeded == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
