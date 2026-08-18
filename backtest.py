"""Honest backtest: cut history at a date, forecast forward, compare to what
really happened after that date. Same harness runs old and new engines so the
only thing that changes is the rate logic. some changes have to be made for persistence"""
import sys, importlib
import numpy as np, pandas as pd
sys.path.insert(0, '.')
from backend import engine as ENG

def load_hyatt():
    import pandas as pd
    f = '/mnt/user-data/uploads/Hyatt_Hotel_Stock_2026__1_.xlsx'
    xl = pd.ExcelFile(f)
    keep = {s: s for s in xl.sheet_names
            if not any(h in s.upper().replace(' ', '')
                       for h in ['SAFETY','PPE','TOOL','ITEM_MASTER'])}
    mv, _ = ENG.parse_site_register(f, keep)
    return ENG.build_daily(mv)

def actual_outcomes(daily, cutoff, horizon):
    """For each material: did it actually reach ~0 stock within `horizon` days
    after cutoff, and on what date? Uses the real balance column after cutoff."""
    end = cutoff + pd.Timedelta(days=horizon)
    out = {}
    for (svc, mat, unit), g in daily.groupby(["service","material","unit"]):
        g = g.sort_values("date")
        after = g[(g.date > cutoff) & (g.date <= end)]
        if after.empty:
            continue
        bal = after[after.balance.notna()]
        ran_out, when = False, pd.NaT
        if len(bal):
            zero = bal[bal.balance <= 0]
            if len(zero):
                ran_out, when = True, zero.iloc[0].date
        out[mat] = {"ran_out": ran_out, "when": when}
    return out

def score(fc, actual, cutoff, horizon):
    """Compare a forecast table to actual outcomes."""
    warned = fc[fc.status.isin(["STOCKED_OUT","RED","AMBER"])]
    warned_set = set(warned.material)
    ran = {m for m,v in actual.items() if v["ran_out"]}
    if not ran:
        return None
    caught = warned_set & ran
    recall = len(caught) / len(ran)
    # date error on caught materials
    errs = []
    fcm = fc.set_index("material")
    for m in caught:
        pred = fcm.loc[m, "exhaust_date"] if m in fcm.index else pd.NaT
        act = actual[m]["when"]
        if pd.notna(pred) and pd.notna(act):
            errs.append(abs((pred - act).days))
    med_err = float(np.median(errs)) if errs else None
    within7 = (np.mean([e <= 7 for e in errs]) if errs else None)
    # precision: of those we warned AND that had a real balance record after, how many ran out
    testable = warned_set & set(actual.keys())
    prec = (len(caught) / len(testable)) if testable else None
    return {"ran_out": len(ran), "warned": len(warned_set),
            "caught": len(caught), "recall": recall,
            "precision": prec, "median_err": med_err, "within7": within7}

if __name__ == "__main__":
    daily = load_hyatt()
    print("loaded:", daily.material.nunique(), "materials,",
          daily.date.min().date(), "->", daily.date.max().date())
    cutoffs = [("2026-06-30",22),("2026-07-07",15),("2026-07-12",10)]
    for cs, hz in cutoffs:
        cutoff = pd.Timestamp(cs)
        hist = daily[daily.date <= cutoff]
        actual = actual_outcomes(daily, cutoff, hz)
        fc = ENG.forecast(hist, asof=cutoff)
        s = score(fc, actual, cutoff, hz)
        if s:
            print(f"\ncutoff {cs}  horizon {hz}d")
            print(f"  ran_out={s['ran_out']:>3}  warned={s['warned']:>3}  caught={s['caught']:>3}"
                  f"  recall={s['recall']*100:4.0f}%  prec={ (s['precision'] or 0)*100:4.0f}%"
                  f"  med_err={s['median_err']}  within7={(s['within7'] or 0)*100:3.0f}%")
