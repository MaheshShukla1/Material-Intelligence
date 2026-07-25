"""Consumption-rate estimation.

The original engine used one number: total issues in the last 14 calendar days
divided by 14. That is honest but blunt. It mixes idle days (rain, Sundays) into
the denominator, so it reads low; and it reacts equally to old and new days, so
it lags when a material's usage speeds up as an activity ramps.

This module keeps the same inputs and returns a single rate plus a short reason,
so the forecast stays explainable. No new data is needed - only better use of
the issue history already parsed from the register.
"""
import numpy as np
import pandas as pd


def estimate_rate(g, asof, window=14):
    """g: one material's daily rows (date, qty_out, ...), sorted or not.
    Returns (rate_per_day, basis_label, basis_days, trend).

    Approach, in order of preference:
      1. If there are enough active days, blend an active-day rate (idle days
         excluded) with a recency-weighted rate, and lean toward whichever is
         higher when usage is clearly accelerating - running out early costs far
         more than ordering a little early.
      2. Fall back to project-to-date average when the window is empty.
    """
    g = g.sort_values("date")
    cons = g[g.qty_out > 0]
    n_days = int(cons.date.nunique())
    if n_days == 0:
        return 0.0, "no consumption", 0, "none"

    win_start = asof - pd.Timedelta(days=window)
    recent = cons[cons.date > win_start]

    # --- active-day rate: total issued / number of days that actually moved.
    # This answers "on a day when this material is used, how much goes?" and is
    # immune to idle days padding the denominator.
    if len(recent):
        active_days = int(recent.date.nunique())
        active_rate = float(recent.qty_out.sum()) / max(active_days, 1)
        basis_days = active_days
    else:
        # window empty - material paused. Use its own history, not zero.
        span = max((asof - cons.date.min()).days, 1)
        return float(cons.qty_out.sum()) / span, "project-to-date", n_days, "paused"

    # --- recency-weighted (EWMA) rate over the daily series inside the window,
    # including zero days so a genuine slowdown still shows. Half-life ~5 days.
    span_days = (asof - win_start).days
    idx = pd.date_range(win_start + pd.Timedelta(days=1), asof, freq="D")
    daily_out = (recent.set_index("date")["qty_out"]
                 .groupby(level=0).sum().reindex(idx, fill_value=0.0))
    ewma = daily_out.ewm(halflife=5, adjust=False).mean().iloc[-1]
    ewma_rate = float(ewma)

    # --- trend: compare the last 3 active days to the window's active rate.
    last3 = cons[cons.date > asof - pd.Timedelta(days=3)]
    last3_rate = (float(last3.qty_out.sum()) / max(int(last3.date.nunique()), 1)
                  if len(last3) else 0.0)

    last3_days = int(last3.date.nunique()) if len(last3) else 0
    if last3_days >= 2 and last3_rate > active_rate * 1.5:
        # accelerating: fresh usage clearly above the window. Blend rather than
        # jump fully to the spike, so one busy stretch does not overpredict.
        surge = 0.5 * last3_rate + 0.5 * active_rate
        return round(surge, 3), "recent surge", basis_days, "accelerating"
    if ewma_rate < active_rate * 0.5:
        # clearly slowing: EWMA has decayed well below the active rate.
        return round(max(ewma_rate, active_rate * 0.5), 3), "slowing", basis_days, "decelerating"

    # calendar rate: issues spread over every day in the window, idle days
    # included. This is what actually recurs over a long horizon.
    cal_rate = float(recent.qty_out.sum()) / max(span_days, 1)

    # stable: lean on active-day (so we do not warn too late) but anchor a third
    # of the weight to the calendar rate (so we do not warn far too early on
    # long horizons). EWMA keeps a genuine slowdown visible.
    blended = 0.45 * active_rate + 0.25 * ewma_rate + 0.30 * cal_rate
    return round(blended, 3), f"last {window}d", basis_days, "stable"
