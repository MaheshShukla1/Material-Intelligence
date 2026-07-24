"""Data health gate. Runs before any forecast is allowed to reach the screen.

The rule this file exists to enforce: a number that cannot be trusted must not
be displayed. Sparse data produces a blocked run, not an optimistic guess.
"""
import pandas as pd

MIN_MATERIALS_WITH_HISTORY = 10
MIN_DAYS_FOR_HIGH_CONF = 8
STALE_AFTER_DAYS = 3


def check(mv, daily, asof):
    issues, stats = [], {}
    asof = pd.Timestamp(asof)

    stats["movement_rows"] = int(len(mv))
    stats["materials"] = int(daily.material.nunique())
    stats["date_from"] = str(daily.date.min().date())
    stats["date_to"] = str(daily.date.max().date())
    stats["asof"] = str(asof.date())

    cons = daily[daily.qty_out > 0]
    per = cons.groupby("material").date.nunique()
    stats["materials_with_consumption"] = int(cons.material.nunique())
    stats["median_consumption_days"] = float(per.median()) if len(per) else 0.0
    stats["materials_with_history"] = int((per >= MIN_DAYS_FOR_HIGH_CONF).sum())

    fut = daily[(daily.date > asof) & ((daily.qty_out > 0) | (daily.qty_in > 0))]
    if len(fut):
        issues.append({
            "level": "warn",
            "text": f"{len(fut)} movements are dated after {asof.date()}. "
                    "Future-dated entries usually mean a typo in the date cell."})

    units = daily.groupby("material").unit.nunique()
    multi = units[units > 1]
    if len(multi):
        issues.append({
            "level": "warn",
            "text": f"{len(multi)} materials appear under more than one unit "
                    f"({', '.join(list(multi.index[:3]))}). Quantities across "
                    "units cannot be added until a conversion is defined."})

    neg = daily[(daily.balance.notna()) & (daily.balance < 0)]
    if len(neg):
        issues.append({
            "level": "warn",
            "text": f"{neg.material.nunique()} materials go negative. An issue "
                    "was recorded without a matching receipt."})

    gap = int((asof - cons.date.max()).days) if len(cons) else None
    stats["days_since_last_entry"] = gap
    if gap is not None and gap > STALE_AFTER_DAYS:
        issues.append({
            "level": "warn",
            "text": f"No consumption recorded for {gap} days. The register is "
                    "stale, so burn rates are understated."})

    ready = stats["materials_with_history"] >= MIN_MATERIALS_WITH_HISTORY
    stats["forecast_ready"] = bool(ready)
    if not ready:
        issues.insert(0, {
            "level": "block",
            "text": f"Only {stats['materials_with_history']} materials have "
                    f"{MIN_DAYS_FOR_HIGH_CONF}+ days of recorded consumption. "
                    "Exhaustion dates are hidden until daily issues are logged "
                    "for about four weeks."})
    return issues, stats


def suppress(f):
    """Blank every predicted date when the gate has tripped."""
    import numpy as np
    for c in ("exhaust_date", "exhaust_earliest", "exhaust_latest", "order_by"):
        f[c] = pd.NaT
    f["days_left"] = np.nan
    mask = f.status.isin(["RED", "AMBER", "GREEN", "OVERSTOCK"])
    f.loc[mask, "status"] = "INSUFFICIENT_DATA"
    return f
