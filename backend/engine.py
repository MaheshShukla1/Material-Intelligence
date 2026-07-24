"""Material Forecast Engine - core.

Two adapters (wide site register, ProjectBase long export) both emit the same
canonical movement table, and one forecast engine runs on that table only.
"""
import re
import datetime as dt
import numpy as np
import pandas as pd

CANON = ["date", "service", "material", "unit", "qty_in", "qty_out", "balance"]


# ---------------------------------------------------------------- date repair
def _try_date(v):
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return pd.Timestamp(v).normalize(), True
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return pd.Timestamp(year=y, month=mo, day=d), False
        except ValueError:
            return pd.NaT, False
    return pd.NaT, False


def _flip(ts):
    try:
        return pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
    except ValueError:
        return pd.NaT


def count_parseable_dates(values):
    """How many of these cells look like a date? Used to locate the date row."""
    return sum(1 for v in values if pd.notna(_try_date(v)[0]))


def repair_date_sequence(raw_values):
    """Column dates run left->right in time order. Excel silently swaps day and
    month whenever both are <= 12, so those cells are ambiguous.

    Repair by anchoring: a date whose day > 12 could not have been swapped, so
    it is trusted. Ambiguous cells pick whichever reading (as-is or flipped)
    sits closest to the straight line between the nearest trusted anchors.
    """
    parsed = [_try_date(v)[0] for v in raw_values]
    n = len(parsed)
    trusted = [i for i, t in enumerate(parsed)
               if pd.notna(t) and (t.day > 12 or t.month == t.day)]
    if not trusted:
        return parsed, 0

    def target(i):
        before = [j for j in trusted if j <= i]
        after = [j for j in trusted if j >= i]
        if before and after and before[-1] != after[0]:
            a, b = before[-1], after[0]
            f = (i - a) / (b - a)
            return parsed[a] + (parsed[b] - parsed[a]) * f
        j = (before or after)[-1 if before else 0]
        return parsed[j] + pd.Timedelta(days=i - j)

    out, swaps = [], 0
    for i, ts in enumerate(parsed):
        if pd.isna(ts):
            out.append(pd.NaT)
            continue
        tgt = target(i)
        cands = [(abs((ts - tgt).days), ts, False)]
        fl = _flip(ts)
        if pd.notna(fl) and ts.day <= 12:
            cands.append((abs((fl - tgt).days), fl, True))
        cands.sort(key=lambda x: x[0])
        _, best, was_swap = cands[0]
        swaps += int(was_swap)
        out.append(best)
    return out, swaps


# ------------------------------------------------------- adapter A: site sheet
def parse_site_register(path, sheet_map=None, skip_sheets=()):
    """Wide layout: item columns, then IN/OUT/(BALANCE) repeating per date.

    Column names differ from project to project, so every sheet is inspected by
    schema.detect() rather than assumed. Sheets that are not registers (item
    masters, blank tabs) are skipped and reported, never guessed at.
    """
    from . import schema

    xl = pd.ExcelFile(path)
    names = list(xl.sheet_names) if sheet_map is None else list(sheet_map)
    frames = []
    report = {"sheets": [], "skipped": [], "date_swaps": 0}

    for sheet in names:
        if sheet in skip_sheets:
            report["skipped"].append({"sheet": sheet, "why": "excluded by name"})
            continue

        raw = xl.parse(sheet, header=None)
        if raw.empty:
            report["skipped"].append({"sheet": sheet, "why": "empty sheet"})
            continue

        m = schema.detect(raw)
        if m is None:
            report["skipped"].append(
                {"sheet": sheet, "why": "no material / in / out columns found"})
            continue

        dates, swaps = repair_date_sequence(
            [raw.iloc[m["date_row"]][c] for c in m["in_cols"]])
        report["date_swaps"] += swaps

        body = raw.iloc[m["header_row"] + 1:].reset_index(drop=True)
        mats = body.iloc[:, m["col_material"]]
        keep = mats.notna() & (mats.astype(str).str.strip() != "")
        body = body[keep]
        if body.empty:
            report["skipped"].append({"sheet": sheet, "why": "no data rows"})
            continue

        material = (body.iloc[:, m["col_material"]].astype(str)
                    .str.replace(r"\s+", " ", regex=True).str.strip().str.upper())
        unit = (body.iloc[:, m["col_unit"]].astype(str).str.strip().str.upper()
                if m["col_unit"] is not None else pd.Series("", index=body.index))

        # Service comes from the sheet's own Groups column when it has one;
        # that is per-row truth and beats guessing from the tab name.
        default_service = schema.clean_service(
            (sheet_map or {}).get(sheet) or sheet, fallback="Other")
        if m["col_group"] is not None:
            grp = body.iloc[:, m["col_group"]]
            service = grp.map(lambda v: schema.clean_service(v, default_service))
        else:
            service = pd.Series(default_service, index=body.index)

        opening = (pd.to_numeric(body.iloc[:, m["col_opening"]], errors="coerce")
                   if m["col_opening"] is not None
                   else pd.Series(np.nan, index=body.index))

        blocks = []
        for c, d in zip(m["in_cols"], dates):
            if pd.isna(d):
                continue
            qi = pd.to_numeric(body.iloc[:, c], errors="coerce")
            qo = pd.to_numeric(body.iloc[:, c + m["out_offset"]], errors="coerce")
            if m["bal_offset"] is not None and c + m["bal_offset"] < body.shape[1]:
                bal = pd.to_numeric(body.iloc[:, c + m["bal_offset"]], errors="coerce")
            else:
                bal = pd.Series(np.nan, index=body.index)
            live = qi.notna() | qo.notna() | bal.notna()
            if not live.any():
                continue
            blocks.append(pd.DataFrame({
                "date": d, "service": service[live], "material": material[live],
                "unit": unit[live], "qty_in": qi[live].fillna(0.0),
                "qty_out": qo[live].fillna(0.0), "balance": bal[live],
                "opening": opening[live]}))

        if not blocks:
            report["skipped"].append({"sheet": sheet, "why": "no dated movement columns"})
            continue

        df = pd.concat(blocks, ignore_index=True)
        good = [d for d in dates if pd.notna(d)]
        report["sheets"].append({
            "sheet": sheet, "materials": int(df.material.nunique()),
            "rows": int(len(df)), "date_columns": len(good),
            "date_from": str(min(good).date()), "date_to": str(max(good).date()),
            "columns": m["names"],
            "header_row": m["header_row"] + 1,
            "has_balance": m["bal_offset"] is not None,
        })
        frames.append(df)

    out = (pd.concat(frames, ignore_index=True) if frames
           else pd.DataFrame(columns=CANON + ["opening"]))
    return out, report


# ------------------------------------------------ adapter B: ProjectBase export
def parse_projectbase_movement(path):
    """Long layout: one row per transaction, negative qty = issue."""
    df = pd.read_excel(path)
    need = {"Material", "Unit", "Quantity", "Document Date", "Document Type"}
    if not need.issubset(df.columns):
        raise ValueError(f"missing columns: {need - set(df.columns)}")
    df = df[df["Material"].notna()].copy()
    df["date"] = pd.to_datetime(df["Document Date"], errors="coerce").dt.normalize()
    df = df[df["date"].notna()]
    df["material"] = (df["Material"].astype(str)
                      .str.replace(r"\s+", " ", regex=True).str.strip().str.upper())
    df["unit"] = df["Unit"].astype(str).str.strip().str.upper()
    df["service"] = df.get("Item Category", "").astype(str)
    q = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    df["qty_in"] = q.clip(lower=0)
    df["qty_out"] = (-q).clip(lower=0)
    df["balance"] = np.nan
    df["opening"] = np.nan
    return df[CANON + ["opening"]].copy(), {"rows": len(df)}


# ------------------------------------------------------------------- engine
def build_daily(mv):
    """Collapse to one row per material per day, then rebuild running balance."""
    g = (mv.groupby(["service", "material", "unit", "date"], as_index=False)
           .agg(qty_in=("qty_in", "sum"), qty_out=("qty_out", "sum"),
                balance=("balance", "last"), opening=("opening", "first")))
    g = g.sort_values(["material", "date"])
    return g


def forecast(daily, asof=None, window=14, lead_time=7, buffer=2):
    asof = pd.Timestamp(asof or daily.date.max()).normalize()
    out = []
    for (svc, mat, unit), g in daily.groupby(["service", "material", "unit"]):
        g = g.sort_values("date")
        bal_rows = g[g.balance.notna()]
        if len(bal_rows):
            stock = float(bal_rows.iloc[-1].balance)
        else:
            op = g.opening.dropna()
            stock = (float(op.iloc[0]) if len(op) else 0.0) + \
                    g.qty_in.sum() - g.qty_out.sum()

        cons = g[g.qty_out > 0]
        total_out = float(g.qty_out.sum())
        n_days = int(cons.date.nunique())
        last_out = cons.date.max() if n_days else pd.NaT

        recent = cons[cons.date > asof - pd.Timedelta(days=window)]
        if len(recent):
            rate = float(recent.qty_out.sum()) / window
            basis, basis_days = f"last {window}d", int(recent.date.nunique())
        elif n_days:
            span = max((asof - cons.date.min()).days, 1)
            rate, basis, basis_days = total_out / span, "project-to-date", n_days
        else:
            rate, basis, basis_days = 0.0, "no consumption", 0

        idle = (asof - last_out).days if pd.notna(last_out) else None

        # status: exhausted first, then rate-based
        if stock <= 0 and rate > 0:
            status, days_left = "STOCKED_OUT", 0.0
        elif rate <= 0:
            status = "DEAD_STOCK" if total_out == 0 else "NO_RECENT_USE"
            days_left = np.nan
        else:
            days_left = stock / rate
            reorder = lead_time + buffer
            if days_left <= reorder:
                status = "RED"
            elif days_left <= reorder * 2:
                status = "AMBER"
            elif days_left > 180:
                status = "OVERSTOCK"
            else:
                status = "GREEN"

        if basis_days >= 8:
            conf, band = "HIGH", 0.15
        elif basis_days >= 4:
            conf, band = "MEDIUM", 0.35
        elif basis_days >= 1:
            conf, band = "LOW", 0.60
        else:
            conf, band = "NONE", np.nan

        if np.isnan(days_left) or conf == "NONE":
            edate = elo = ehi = pd.NaT
        else:
            edate = asof + pd.Timedelta(days=float(days_left))
            elo = asof + pd.Timedelta(days=float(days_left) * (1 - band))
            ehi = asof + pd.Timedelta(days=float(days_left) * (1 + band))

        order_by = (edate - pd.Timedelta(days=lead_time + buffer)
                    if pd.notna(edate) else pd.NaT)

        out.append(dict(
            service=svc, material=mat, unit=unit,
            stock=round(stock, 2), rate_per_day=round(rate, 2),
            days_left=(None if np.isnan(days_left) else round(days_left, 1)),
            status=status, confidence=conf, basis=basis,
            consumption_days=basis_days, total_consumed=round(total_out, 2),
            days_idle=idle,
            exhaust_date=edate, exhaust_earliest=elo, exhaust_latest=ehi,
            order_by=order_by))

    df = pd.DataFrame(out)
    prio = {"STOCKED_OUT": 0, "RED": 1, "AMBER": 2, "OVERSTOCK": 3,
            "GREEN": 4, "NO_RECENT_USE": 5, "DEAD_STOCK": 6}
    df["_p"] = df.status.map(prio)
    return (df.sort_values(["_p", "rate_per_day"], ascending=[True, False])
              .drop(columns="_p").reset_index(drop=True))
