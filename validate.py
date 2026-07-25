"""Logic validator. Runs the engine on any register and checks every forecast
row against a set of invariants that must never be violated - a number that
contradicts itself is worse than no number.

Usage:  python validate.py path/to/register.xlsx

Exit code 0 = all invariants hold. Non-zero = at least one CRITICAL violation,
with the offending materials listed so they can be traced.

This exists so the forecast can be trusted without re-checking by hand. Every
time the rate logic or status rules change, run this on real files before
shipping. If a new failure mode appears in the field, add it here as a new
check - the validator is the memory of every bug we have already fixed.
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from backend import engine as ENG

SKIP = ["SAFETY", "PPE", "TOOL", "ITEMMASTER", "MASTER", "ITEM_MASTER",
        "SUMMARY", "INDEX", "SHEET1"]


def run(path):
    xl = pd.ExcelFile(path)
    keep = {s: s for s in xl.sheet_names
            if not any(h in s.upper().replace(" ", "") for h in SKIP)}
    mv, _ = ENG.parse_site_register(path, keep)
    daily = ENG.build_daily(mv)
    moved = daily[daily.qty_out > 0]
    asof = moved.date.max() if len(moved) else daily.date.max()
    return ENG.forecast(daily[daily.date <= asof], asof=asof), asof


def checks(R):
    """Each entry: (severity, description, offending_rows). CRITICAL failures
    mean the forecast is telling a lie a user could act on."""
    warned = R.status.isin(["RED", "AMBER"])
    out = []

    # A material idle far longer than its forecast, still shouting "order now",
    # is the stale-rate lie this project exists to avoid.
    out.append(("CRITICAL", "warned but idle >= 14d and idle > days_left",
        R[warned & R.days_idle.notna() & R.days_left.notna()
          & (R.days_idle >= 14) & (R.days_idle > R.days_left)]))

    # A predicted exhaustion date with no consumption behind it is fabricated.
    out.append(("CRITICAL", "exhaust_date present but rate == 0",
        R[R.exhaust_date.notna() & (R.rate_per_day == 0)]))

    # Empty stock must read as out, never as a healthy or future-dated row.
    out.append(("CRITICAL", "stock <= 0 but not flagged out/dead",
        R[(R.stock <= 0)
          & ~R.status.isin(["STOCKED_OUT", "DEAD_STOCK",
                            "NO_RECENT_USE", "INSUFFICIENT_DATA"])]))

    # Paused / no-confidence rows must not carry a date - the date is meaningless.
    out.append(("WARN", "paused/none-confidence row still shows a date",
        R[R.status.isin(["NO_RECENT_USE"]) & R.exhaust_date.notna()]))
    out.append(("WARN", "confidence NONE but shows a date",
        R[(R.confidence == "NONE") & R.exhaust_date.notna()]))

    # Order-by after the exhaust date would tell someone to order too late.
    ob = R[R.order_by.notna() & R.exhaust_date.notna()].copy()
    if len(ob):
        bad = ob[pd.to_datetime(ob.order_by) > pd.to_datetime(ob.exhaust_date)]
    else:
        bad = ob
    out.append(("CRITICAL", "order_by falls after exhaust_date", bad))

    out.append(("WARN", "negative days_left",
        R[R.days_left.notna() & (R.days_left < 0)]))

    # Category sanity: labels must match their own definition.
    out.append(("WARN", "DEAD_STOCK but has consumption history",
        R[(R.status == "DEAD_STOCK") & (R.total_consumed > 0)]))
    out.append(("WARN", "OVERSTOCK but under 180 days cover",
        R[(R.status == "OVERSTOCK") & R.days_left.notna() & (R.days_left < 180)]))
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python validate.py <register.xlsx>")
        sys.exit(2)
    path = sys.argv[1]
    fc, asof = run(path)
    print(f"{path}")
    print(f"  {len(fc)} materials, asof {asof.date()}\n")
    crit = 0
    for sev, desc, rows in checks(fc):
        n = len(rows)
        mark = "OK" if n == 0 else f"{sev}: {n}"
        print(f"  [{mark:>12}]  {desc}")
        if n and sev == "CRITICAL":
            crit += n
            for _, r in rows.head(8).iterrows():
                print(f"        - {r['material'][:40]:<40} "
                      f"status={r['status']} idle={r.get('days_idle')} "
                      f"left={r['days_left']} stock={r['stock']} rate={r['rate_per_day']}")
    print()
    if crit:
        print(f"FAILED: {crit} critical violation(s).")
        sys.exit(1)
    print("PASSED: no critical violations.")
    sys.exit(0)


if __name__ == "__main__":
    main()
