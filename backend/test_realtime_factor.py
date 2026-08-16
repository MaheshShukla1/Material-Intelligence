"""pytest for realtime.combine_item()'s conversion-factor handling -- the
real gap this closes: the docstring always promised "a per-link factor can
be set; we never fabricate one", but the code always silently used
factor=1.0 for every link, regardless of whether the BOQ item's unit and
the linked material's unit actually matched. A BOQ line in "Nos" linked to
a material measured in "Rmt" would compute a "need" in Rmt using the raw
Nos count -- a number with no real basis. This is now per-row: a safe
1.0 only when the two unit strings genuinely match, an explicit factor
when the engineer sets one, and an honest "can't compute yet" (verdict
UNKNOWN_FACTOR) otherwise -- never a silent, wrong guess.

Run: cd tests && python -m pytest test_realtime_factor.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest

import realtime


def _item(unit="Nos", remaining=10.0):
    return {"item_code": "5.5", "unit": unit, "planned_total": 15.0,
            "used": 5.0, "remaining": remaining, "progress_pct": 33.3}


# --------------------------------------------------------------------------
# units match -> safe default factor=1.0, exactly the old behaviour
# --------------------------------------------------------------------------
def test_matching_units_no_factor_needed_defaults_to_one():
    stock = [{"material": "25MM PIPE", "unit": "Rmt", "stock": 50,
              "rate_per_day": 2, "days_left": 25, "total_consumed": 100}]
    item = _item(unit="Rmt", remaining=10.0)
    res = realtime.combine_item(item, stock)
    row = res["links"][0]
    assert row["units_match"] is True
    assert row["factor"] is None            # no EXPLICIT factor was given
    assert row["need"] == pytest.approx(10.0)   # but the effective factor used was 1.0
    assert row["verdict"] == "ENOUGH"
    assert res["verdict"] == "ENOUGH"


# --------------------------------------------------------------------------
# units differ, no factor given -> the actual bug this closes
# --------------------------------------------------------------------------
def test_mismatched_units_no_factor_never_guesses_need():
    stock = [{"material": "25MM PVC PIPE", "unit": "Rmt", "stock": 500,
              "rate_per_day": 20, "days_left": 25, "total_consumed": 1000}]
    item = _item(unit="Nos", remaining=10.0)    # 10 Nos of light points, NOT 10 Rmt of pipe
    res = realtime.combine_item(item, stock)
    row = res["links"][0]
    assert row["units_match"] is False
    assert "need" not in row, "FAIL: must never compute a Rmt need from a raw Nos count"
    assert "shortfall" not in row
    assert "days_to_finish" not in row
    assert row["verdict"] == "UNKNOWN_FACTOR"
    assert res["verdict"] == "UNKNOWN_FACTOR"
    # on_hand/rate/consumed are still real, informational facts about the
    # material -- unit mismatch hides the COMPARISON, not the raw data
    assert row["on_hand"] == 500
    assert row["total_consumed"] == 1000


def test_mismatched_units_sentence_explains_and_suggests_a_fix():
    stock = [{"material": "25MM PVC PIPE", "unit": "Rmt", "stock": 500,
              "rate_per_day": 20, "days_left": 25, "total_consumed": 1000}]
    item = _item(unit="Nos", remaining=10.0)
    res = realtime.combine_item(item, stock)
    msg = realtime.sentence(res)
    assert "conversion factor" in msg.lower()
    assert "25MM PVC PIPE" in msg
    assert "Rmt per Nos" in msg or "per Nos" in msg


# --------------------------------------------------------------------------
# units differ, but a real factor IS given -> computed correctly
# --------------------------------------------------------------------------
def test_mismatched_units_with_explicit_factor_computes_correctly():
    # 10 Nos of light points, 3 Rmt of pipe needed per point -> need 30 Rmt
    stock = [{"material": "25MM PVC PIPE", "unit": "Rmt", "stock": 20,
              "rate_per_day": 5, "days_left": 4, "total_consumed": 100, "factor": 3.0}]
    item = _item(unit="Nos", remaining=10.0)
    res = realtime.combine_item(item, stock)
    row = res["links"][0]
    assert row["factor"] == pytest.approx(3.0)
    assert row["need"] == pytest.approx(30.0)
    assert row["shortfall"] == pytest.approx(10.0)   # need 30, only 20 on hand
    assert row["verdict"] == "SHORTAGE"
    assert res["verdict"] == "SHORTAGE"
    assert res["order_qty"] == pytest.approx(10.0)


def test_explicit_factor_overrides_even_when_units_happen_to_match():
    # units match (both Rmt), but the engineer set an explicit factor anyway
    # (e.g. 2 Rmt of cable per Rmt of conduit run, a real "bundle" ratio) --
    # the explicit factor must win over the naive 1:1 default
    stock = [{"material": "CABLE", "unit": "Rmt", "stock": 100,
              "rate_per_day": 10, "days_left": 10, "total_consumed": 50, "factor": 2.0}]
    item = _item(unit="Rmt", remaining=10.0)
    res = realtime.combine_item(item, stock)
    row = res["links"][0]
    assert row["units_match"] is True
    assert row["need"] == pytest.approx(20.0), "FAIL: explicit factor=2.0 must override the units-match default of 1.0"


# --------------------------------------------------------------------------
# multiple linked materials, mixed factor availability
# --------------------------------------------------------------------------
def test_one_material_ok_one_unknown_factor_overall_reflects_the_gap():
    stock = [
        {"material": "WIRE", "unit": "Rmt", "stock": 500, "rate_per_day": 10,
         "days_left": 50, "total_consumed": 100},          # units differ, no factor
        {"material": "GANG BOX", "unit": "Nos", "stock": 20, "rate_per_day": 1,
         "days_left": 20, "total_consumed": 5},             # units match Nos
    ]
    item = _item(unit="Nos", remaining=10.0)
    res = realtime.combine_item(item, stock)
    verdicts = {L["material"]: L["verdict"] for L in res["links"]}
    assert verdicts["WIRE"] == "UNKNOWN_FACTOR"
    assert verdicts["GANG BOX"] == "ENOUGH"
    # overall must surface the gap, not silently report ENOUGH just because
    # one of the two links happened to resolve
    assert res["verdict"] == "UNKNOWN_FACTOR"


def test_shortage_still_wins_over_unknown_factor_in_overall_verdict():
    stock = [
        {"material": "WIRE", "unit": "Rmt", "stock": 500, "rate_per_day": 10,
         "days_left": 50, "total_consumed": 100},                     # UNKNOWN_FACTOR
        {"material": "GANG BOX", "unit": "Nos", "stock": 1, "rate_per_day": 1,
         "days_left": 1, "total_consumed": 5},                        # real shortage
    ]
    item = _item(unit="Nos", remaining=10.0)
    res = realtime.combine_item(item, stock)
    assert res["verdict"] == "SHORTAGE", "FAIL: a real shortage elsewhere must not be masked by an unrelated unknown-factor link"


# --------------------------------------------------------------------------
# no unit info at all on either side -> never assume a match
# --------------------------------------------------------------------------
def test_missing_unit_on_either_side_is_never_treated_as_a_match():
    stock = [{"material": "SOMETHING", "stock": 10, "rate_per_day": 1, "days_left": 10}]  # no "unit" key
    item = _item(unit="Nos", remaining=5.0)
    res = realtime.combine_item(item, stock)
    assert res["links"][0]["units_match"] is False
    assert res["verdict"] == "UNKNOWN_FACTOR"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
