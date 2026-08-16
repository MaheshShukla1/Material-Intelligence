"""pytest reproducing the exact contradiction seen on real Hyatt data:
Overall's ring showed "94% value complete" (pct_value_done, from the one
rated service) right next to a waste caveat that said "Only 0.0% of work
is recorded as done project-wide" -- because that caveat used a raw
item-count average across EVERY service, most of which are unrated and
have nothing to do with the waste figure at all.

siteprogress.py's `/overall` route can't be imported standalone here (it
needs `subcat`, a real package layout, and a data directory this sandbox
doesn't have -- see the handoff notes). So this test exercises the exact
fix at the level that actually matters: pnl.waste_summary()'s own
per-service caveat, and the join/prefix logic `/overall` now uses to
surface it, replicated verbatim from the patched route so a regression
here would also break the route.
Run: cd tests && python -m pytest test_waste_caveat.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd
import pytest

import itemprog
import pnl


def _service_item_pnl(planned_total, used, actual_consumed, rate=100.0, unit="MTR"):
    """One rated item, shaped like pnl.compute_item_pnl()'s output, with a
    real gap between `used` (Site Progress's own recorded fraction) and
    `actual_consumed` (the stock register's real OUT-to-date) -- the exact
    setup that makes waste balloon when progress hasn't been entered yet."""
    used_df = pd.DataFrame([{
        "item_code": "X.1", "description": "test item", "unit": unit,
        "qty_per_room": planned_total, "planned_total": planned_total,
        "used": used, "remaining": max(planned_total - used, 0.0),
        "progress_pct": round(100.0 * used / planned_total, 1) if planned_total else 0.0,
    }])
    return pnl.compute_item_pnl(used_df, rates={"X.1": rate},
                                 actual_consumed={"X.1": actual_consumed})


def _overall_waste_caveat(per_service_waste):
    """Verbatim replica of the fixed `/overall` route's caveat assembly:
    join every service's own non-null caveat, prefixed with its name --
    never a fresh project-wide recomputation from an unrelated basis."""
    parts = [f"{svc}: {w['caveat']}" for svc, w in per_service_waste.items() if w.get("caveat")]
    return " | ".join(parts) if parts else None


# --------------------------------------------------------------------------
def test_reproduces_the_real_contradiction_and_fixes_it():
    # Plumbing: rated, barely any progress ENTERED (used tiny vs planned) but
    # a lot of real stock consumption -- the low-progress waste caveat should
    # fire, based on PLUMBING'S OWN weighted %, not a project-wide fudge.
    plumbing_ip = _service_item_pnl(planned_total=900.0, used=40.0, actual_consumed=800.0)
    plumbing_waste = pnl.waste_summary(plumbing_ip)
    assert plumbing_waste["available"]
    assert plumbing_waste["progress_pct"] == pytest.approx(4.4, abs=0.1)   # 40/900, under the 5% threshold
    assert plumbing_waste["caveat"] is not None
    assert "4.4%" in plumbing_waste["caveat"]

    # Electrical/FAPA/Fire/HVAC: unrated -> compute_item_pnl never produces a
    # "waste_value" column at all (no actual_consumed supplied, matching real
    # data where these services simply have no rate/linkage yet) -> waste
    # unavailable, no caveat, and -- critically -- their 0% must NOT get
    # averaged into whatever basis decides Plumbing's caveat.
    unrated_ip = _service_item_pnl(planned_total=500.0, used=0.0, actual_consumed=0.0)
    unrated_ip = unrated_ip.drop(columns=["actual_qty", "waste_qty", "waste_value",
                                          "saving_qty", "saving_value"], errors="ignore")
    unrated_waste = pnl.waste_summary(unrated_ip)
    assert unrated_waste["available"] is False

    per_service = {
        "Plumbing": {"wasted": (800.0 - 40.0) * 100.0, "caveat": plumbing_waste["caveat"]},
        "Electrical": {"wasted": 0.0, "caveat": None},
        "FAPA": {"wasted": 0.0, "caveat": None},
        "Fire": {"wasted": 0.0, "caveat": None},
        "HVAC": {"wasted": 0.0, "caveat": None},
    }
    overall_caveat = _overall_waste_caveat(per_service)

    # the fixed behaviour: one clear, correctly-scoped, service-named caveat
    assert overall_caveat is not None
    assert overall_caveat.startswith("Plumbing: Only 4.4%")
    # the OLD bug: a project-wide item-mean across 5 services (4 of them
    # unrated, sitting at a flat 0%) would read close to 0%, producing text
    # like "Only 0.0% of work is recorded as done project-wide" -- while a
    # ₹-value ring elsewhere on the same page could independently show 94%
    # (computed only from the tiny rated Plumbing slice). Assert the fixed
    # caveat text is anchored to Plumbing's real 4.4%, not a fabricated 0.0%.
    assert "0.0%" not in overall_caveat
    assert "project-wide" not in overall_caveat   # no longer a vague, unscoped claim


def test_no_waste_no_caveat_even_with_unrated_zero_progress_services():
    # a project where NOTHING is rated anywhere must show no waste and no
    # caveat -- never invent one from services that have no waste at all.
    per_service = {"Electrical": {"wasted": 0.0, "caveat": None},
                   "Plumbing": {"wasted": 0.0, "caveat": None}}
    assert _overall_waste_caveat(per_service) is None


def test_two_rated_services_both_low_progress_both_named():
    a = _service_item_pnl(planned_total=200.0, used=2.0, actual_consumed=150.0)
    b = _service_item_pnl(planned_total=300.0, used=3.0, actual_consumed=250.0)
    wa, wb = pnl.waste_summary(a), pnl.waste_summary(b)
    per_service = {"Fire": {"wasted": 1.0, "caveat": wa["caveat"]},
                   "HVAC": {"wasted": 1.0, "caveat": wb["caveat"]}}
    caveat = _overall_waste_caveat(per_service)
    assert caveat.count(":") == 2          # both services named, not merged into one vague line
    assert "Fire: Only" in caveat and "HVAC: Only" in caveat


def test_healthy_progress_service_never_gets_a_caveat():
    # a service with real, substantial progress recorded (>=5%) must not
    # get a low-progress caveat even if its waste value is non-trivial --
    # matches pnl.waste_summary()'s own 5% threshold, unchanged.
    ip = _service_item_pnl(planned_total=900.0, used=500.0, actual_consumed=650.0)
    w = pnl.waste_summary(ip)
    assert w["progress_pct"] == pytest.approx(55.6, abs=0.1)
    assert w["caveat"] is None
    per_service = {"Plumbing": {"wasted": w["wasted_value"], "caveat": w["caveat"]}}
    assert _overall_waste_caveat(per_service) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
