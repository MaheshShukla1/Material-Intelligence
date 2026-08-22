"""Tests for the payment-term (supply vs installation) split in pnl.py.

Run standalone: pnl.py has zero package-relative imports, so no package
scaffolding / subcat stub is needed for these.
"""
import sys
sys.path.insert(0, "/home/claude/backend")
import pandas as pd
import pnl


def _used_df(rows):
    return pd.DataFrame(rows, columns=[
        "item_code", "description", "unit", "qty_per_room", "planned_total",
        "used", "remaining", "progress_pct", "mapped"])


def test_no_install_pct_configured_is_byte_identical_to_old_behaviour():
    """The core promise: a project that never touches payment terms sees
    NO change at all -- install_rate == rate, full_value == planned_value."""
    used = _used_df([["A1", "desc", "Nos", 10, 100.0, 40.0, 60.0, 40.0, True]])
    rates = {"A1": 50.0}
    out = pnl.compute_item_pnl(used, rates=rates)
    row = out.iloc[0]
    assert row.install_pct is None or pd.isna(row.install_pct)
    assert row.install_rate == 50.0
    assert row.planned_value == 100.0 * 50.0
    assert row.done_value == 40.0 * 50.0
    assert row.remaining_value == 60.0 * 50.0
    assert row.full_value == row.planned_value  # nothing to distinguish yet


def test_project_default_applies_when_no_item_override():
    used = _used_df([["Q12", "150x50x2mm cable tray", "MTR", 0, 1700.0, 100.0, 1600.0, 5.9, True]])
    rates = {"Q12": 97.0}
    out = pnl.compute_item_pnl(used, rates=rates, default_install_pct=15.0)
    row = out.iloc[0]
    assert row.install_pct == 15.0
    assert round(row.install_rate, 4) == round(97.0 * 0.15, 4)
    assert row.planned_value == round(1700.0 * 97.0 * 0.15, 2)
    assert row.done_value == round(100.0 * 97.0 * 0.15, 2)
    assert row.remaining_value == round(1600.0 * 97.0 * 0.15, 2)
    # clean installation P&L: done + remaining == planned, exactly
    assert round(row.done_value + row.remaining_value, 2) == row.planned_value
    # full contract value is untouched, still the old full-rate number
    assert row.full_value == round(1700.0 * 97.0, 2)


def test_per_item_override_wins_over_project_default():
    used = _used_df([
        ["Q12", "cable tray", "MTR", 0, 1000.0, 500.0, 500.0, 50.0, True],
        ["Q13", "cable tray", "MTR", 0, 1000.0, 500.0, 500.0, 50.0, True],
    ])
    rates = {"Q12": 100.0, "Q13": 100.0}
    install_pct = {"Q13": 20.0}   # Q13 has its own override; Q12 falls to default
    out = pnl.compute_item_pnl(used, rates=rates, install_pct=install_pct,
                               default_install_pct=15.0)
    q12 = out[out.item_code == "Q12"].iloc[0]
    q13 = out[out.item_code == "Q13"].iloc[0]
    assert q12.install_pct == 15.0
    assert q13.install_pct == 20.0
    assert q13.install_rate == 20.0
    assert q12.install_rate == 15.0


def test_unrated_item_gets_no_derived_columns():
    used = _used_df([["X1", "no rate set", "Nos", 1, 10.0, 5.0, 5.0, 50.0, True]])
    out = pnl.compute_item_pnl(used, rates={}, default_install_pct=15.0)
    row = out.iloc[0]
    assert row.rated == False
    for col in ("planned_value", "done_value", "remaining_value",
               "full_value", "install_rate", "install_pct"):
        assert pd.isna(row[col])


def test_invalid_override_falls_back_to_default_not_silently_clamped():
    used = _used_df([["A1", "d", "Nos", 1, 10.0, 5.0, 5.0, 50.0, True]])
    rates = {"A1": 10.0}
    bad = {"A1": 150.0}   # out of 0-100 range -> must be dropped, not clamped to 100
    out = pnl.compute_item_pnl(used, rates=rates, install_pct=bad, default_install_pct=15.0)
    row = out.iloc[0]
    assert row.install_pct == 15.0   # fell back to project default, not 100 or 150


def test_waste_stays_on_full_rate_not_install_scaled():
    used = _used_df([["A1", "d", "MTR", 0, 100.0, 40.0, 60.0, 40.0, True]])
    rates = {"A1": 50.0}
    actual = {"A1": 70.0}   # over-consumed vs used(40) -> waste_qty = 30
    out = pnl.compute_item_pnl(used, rates=rates, actual_consumed=actual,
                               default_install_pct=15.0)
    row = out.iloc[0]
    assert row.waste_qty == 30.0
    # must be 30 * FULL rate (50), not 30 * install rate (7.5)
    assert row.waste_value == 30.0 * 50.0


def test_rollup_and_project_pnl_carry_full_value():
    import activity
    used = _used_df([["Q12", "cable tray", "MTR", 0, 1700.0, 100.0, 1600.0, 5.9, True]])
    rates = {"Q12": 97.0}
    ip = pnl.compute_item_pnl(used, rates=rates, default_install_pct=15.0)
    m = activity.Mapping({"Electrical": {"Cable Tray": ["Q12"]}})
    rp = pnl.rollup_pnl(ip, m, "Electrical")
    assert rp["totals"]["full_value"] == round(1700.0 * 97.0, 2)
    assert rp["totals"]["planned_value"] == round(1700.0 * 97.0 * 0.15, 2)
    assert rp["by_activity"]["Cable Tray"]["full_value"] == rp["totals"]["full_value"]
    proj = pnl.project_pnl({"Electrical": rp})
    assert proj["full_value"] == rp["totals"]["full_value"]
    assert proj["planned_value"] == rp["totals"]["planned_value"]


def test_real_screenshot_numbers_q12_cable_tray():
    """Regression pinned to the actual Thoth Mall numbers from the
    zone_selected_but_which_zone_.png / total_i_have_selected_14_zones.png
    screenshots: rate 97, planned 1700 MTR, used 100 MTR, remaining 1600 MTR.
    At a 15% install default this is the exact new drawer figure Mahesh will
    see -- pinned here so a future change can't silently drift it."""
    used = _used_df([["Q12", "150X50X2MM CABLE TRAY", "MTR", 0,
                      1700.0, 100.0, 1600.0, 5.9, True]])
    out = pnl.compute_item_pnl(used, rates={"Q12": 97.0}, default_install_pct=15.0)
    row = out.iloc[0]
    assert row.full_value == 164900.0          # unchanged full contract value
    assert row.planned_value == 24735.0        # 1700 * 97 * 0.15
    assert row.done_value == 1455.0            # 100 * 97 * 0.15
    assert row.remaining_value == 23280.0       # 1600 * 97 * 0.15


if __name__ == "__main__":
    import subprocess, sys as _sys
    _sys.exit(subprocess.call(["pytest", "-v", __file__]))
