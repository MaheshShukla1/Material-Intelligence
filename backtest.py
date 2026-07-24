"""Back-test: hide the future, forecast, then check against what really happened.

Usage:  python backtest.py <file.xlsx> --end 2026-07-22 --cutoffs 2026-06-10,...

Two metrics, because they answer different questions:
  recall  - of the materials that actually ran out, how many did we warn about?
            (the business metric: shortages prevented)
  error   - for the ones we did call, how far off was the date?
            (the credibility metric)
"""
import argparse
import numpy as np
import pandas as pd
from backend import engine
from backend.api import detect, sheet_plan


def load(path):
    if detect(path) == "projectbase":
        mv, _ = engine.parse_projectbase_movement(path)
    else:
        plan, _ = sheet_plan(path)
        mv, _ = engine.parse_site_register(path, plan)
    return engine.build_daily(mv)


def actual_stockouts(daily, cut, end):
    d = daily[(daily.date > cut) & (daily.date <= end)]
    out = {}
    for m, g in d.groupby("material"):
        z = g[(g.balance.notna()) & (g.balance <= 0)].date.min()
        if pd.notna(z):
            out[m] = z
    return out


def run(path, end, cutoffs, lead=7):
    daily = load(path)
    end = pd.Timestamp(end)
    rows = []
    for cs in cutoffs:
        cut = pd.Timestamp(cs)
        horizon = (end - cut).days
        f = engine.forecast(daily[daily.date <= cut], asof=cut, lead_time=lead)
        fi = f.set_index("material")
        actual = actual_stockouts(daily, cut, end)

        ran_out = [m for m in actual
                   if m in fi.index and fi.loc[m, "status"] != "STOCKED_OUT"]
        warned = [m for m in ran_out
                  if fi.loc[m, "status"] in ("RED", "AMBER")]
        alerts = int(fi.status.isin(["RED", "AMBER"]).sum())

        err = [(actual[m] - fi.loc[m, "exhaust_date"]).days
               for m in warned if pd.notna(fi.loc[m, "exhaust_date"])]
        err = pd.Series(err, dtype="float64")

        rows.append(dict(
            cutoff=cs, horizon_days=horizon,
            actually_ran_out=len(ran_out),
            warned_in_advance=len(warned),
            recall_pct=round(100 * len(warned) / max(len(ran_out), 1)),
            alerts_raised=alerts,
            precision_pct=round(100 * len(warned) / max(alerts, 1)),
            median_error_days=(round(err.median()) if len(err) else None),
            within_7_days_pct=(round(100 * (err.abs() <= 7).mean())
                               if len(err) else None)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--end", required=True)
    ap.add_argument("--cutoffs", required=True)
    ap.add_argument("--lead", type=int, default=7)
    a = ap.parse_args()
    df = run(a.path, a.end, a.cutoffs.split(","), a.lead)
    print(df.to_string(index=False))
