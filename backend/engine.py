"""Material Forecast Engine - core.

Two adapters (wide site register, ProjectBase long export) both emit the same
canonical movement table, and one forecast engine runs on that table only.
"""
import re
import datetime as dt
import numpy as np
import pandas as pd

from . import rate as rate_mod

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
    mv = mv.copy()
    # A missing unit must not make the whole material disappear: pandas groupby
    # silently drops rows whose key is NaN, which would zero-out that item's
    # received/issued totals. Give unit-less rows a stable placeholder instead.
    mv["unit"] = mv["unit"].astype("object").fillna("-").replace("", "-")
    g = (mv.groupby(["service", "material", "unit", "date"], as_index=False)
           .agg(qty_in=("qty_in", "sum"), qty_out=("qty_out", "sum"),
                balance=("balance", "last"), opening=("opening", "first")))
    g = g.sort_values(["material", "date"])
    return g


def forecast(daily, asof=None, window=14, lead_time=7, buffer=2, today=None,
             lead_time_by_material=None):
    """lead_time_by_material: optional {material: real_lead_days} (from
    leadtime.py, built off actual PO->GRN history). When given, a material
    found in it uses ITS OWN lead time for the reorder point and order_by
    date instead of the one global `lead_time` number - this is what actually
    changes which status bucket (RED/AMBER/GREEN/...) a material lands in,
    not just a cosmetic label. A material absent from the lookup (no usable
    PO/GRN history) falls back to `lead_time` exactly as before - nothing here
    ever invents a number for a material with no real data behind it. Passing
    nothing at all (the default) reproduces the old single-global-number
    behaviour byte-for-byte.
    """
    asof = pd.Timestamp(asof or daily.date.max()).normalize()
    # "today" is the real calendar day the user is viewing on. It is usually
    # later than asof (the last date present in the register), because sites
    # don't always enter data every day. Reading the forecast from today - not
    # from a stale asof - is what stops "runs out 25 Jul" showing on the 27th
    # while the stock is visibly still on the shelf.
    today = pd.Timestamp(today).normalize() if today is not None else asof
    if today < asof:
        today = asof
    gap = (today - asof).days
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

        rate, basis, basis_days, trend = rate_mod.estimate_rate(g, asof, window)

        idle = (today - last_out).days if pd.notna(last_out) else None

        # Real per-material lead time when we have usable PO->GRN history for
        # it; otherwise the one global number, unchanged from before.
        mat_lt = lead_time_by_material.get(mat) if lead_time_by_material else None
        if mat_lt is not None:
            lt, lt_basis, lt_n = float(mat_lt["lead_days"]), mat_lt["basis"], mat_lt["n"]
            lt_confident = bool(mat_lt.get("confident", False))
        else:
            lt, lt_basis, lt_n = float(lead_time), "default", None
            lt_confident = False

        if basis_days >= 8:
            conf, band = "HIGH", 0.15
        elif basis_days >= 4:
            conf, band = "MEDIUM", 0.35
        elif basis_days >= 1:
            conf, band = "LOW", 0.60
        else:
            conf, band = "NONE", np.nan

        # Widen the band for spiky consumption: a material issued 5, then 500,
        # then 5 is less predictable than one issued 50 every day, even with the
        # same day count. Only applied when we have a real rate.
        if conf != "NONE" and rate > 0:
            cv = float(cons.qty_out.std() / cons.qty_out.mean()) if n_days >= 2 else 0.0
            if cv > 1.5:
                band = min(band * 1.6, 0.85)
            elif cv > 1.0:
                band = min(band * 1.3, 0.85)


        # status: exhausted first, then rate-based
        if stock <= 0 and rate > 0:
            status, days_left = "STOCKED_OUT", 0.0
        elif rate <= 0:
            status = "DEAD_STOCK" if total_out == 0 else "NO_RECENT_USE"
            days_left = np.nan
        else:
            days_left = stock / rate
            reorder = lt + buffer
            # optimistic days_left: if consumption is uncertain (wide band) or
            # slowing, give the material the benefit of the doubt before RED.
            band_mult = 1.0 + (band if isinstance(band, float) and not np.isnan(band) else 0.0)
            if trend == "decelerating":
                band_mult = max(band_mult, 1.25)
            days_left_opt = days_left * band_mult
            # Thin evidence (1-2 issue days) should not raise a hard RED on its
            # own; the rate is one spike away from noise. Hold at AMBER unless
            # stock is already critically low in absolute terms.
            thin = basis_days <= 2
            # RED only if even the optimistic estimate lands near the reorder
            # point. Otherwise the risk is real but not yet urgent -> AMBER.
            if days_left_opt <= reorder and not (thin and days_left > reorder * 0.5):
                status = "RED"
            elif days_left <= reorder * 2:
                status = "AMBER"
            elif days_left > 180:
                status = "OVERSTOCK"
            else:
                status = "GREEN"

            # Stale-rate guard. If this material has not moved for longer than
            # the forecast claims it has left, the activity that consumed it has
            # paused - the rate is a memory of past work, not current reality.
            # A material idle 12 days cannot be "2 days from empty". Surface it
            # as paused so the team sees work stopped, not a false shortage.
            if (status in ("RED", "AMBER") and idle is not None
                    and idle >= reorder and idle > days_left):
                status = "NO_RECENT_USE"
                days_left = np.nan

            # "Past-due but still here" guard. The forecast is anchored at asof
            # (the last date in the file). If enough calendar days have passed
            # since then that the material should already be empty - yet stock is
            # still on hand - then reality has diverged from the rate: it was not
            # consumed as predicted. Showing a runs-out date now in the past
            # ("out 25 Jul" seen on the 27th) destroys trust. Treat it as paused.
            if (status in ("RED", "AMBER") and gap > 0
                    and days_left < gap and stock > 0):
                status = "NO_RECENT_USE"
                days_left = np.nan

        if np.isnan(days_left) or conf == "NONE" or status == "NO_RECENT_USE":
            edate = elo = ehi = pd.NaT
        else:
            # Count remaining life from today, not from the stale asof, so the
            # date shown is always in the future relative to the viewer.
            edate = today + pd.Timedelta(days=float(days_left))
            elo = today + pd.Timedelta(days=float(days_left) * (1 - band))
            ehi = today + pd.Timedelta(days=float(days_left) * (1 + band))

        order_by = (edate - pd.Timedelta(days=lt + buffer)
                    if pd.notna(edate) else pd.NaT)

        out.append(dict(
            service=svc, material=mat, unit=unit,
            stock=round(stock, 2), rate_per_day=round(rate, 2),
            days_left=(None if np.isnan(days_left) else round(days_left, 1)),
            status=status, confidence=conf, basis=basis,
            consumption_days=basis_days, total_consumed=round(total_out, 2),
            trend=trend,
            days_idle=idle,
            exhaust_date=edate, exhaust_earliest=elo, exhaust_latest=ehi,
            order_by=order_by,
            lead_time_days=round(lt, 1), lead_time_basis=lt_basis,
            lead_time_n=lt_n, lead_time_confident=lt_confident))

    df = pd.DataFrame(out)
    prio = {"STOCKED_OUT": 0, "RED": 1, "AMBER": 2, "OVERSTOCK": 3,
            "GREEN": 4, "NO_RECENT_USE": 5, "DEAD_STOCK": 6}
    df["_p"] = df.status.map(prio)
    return (df.sort_values(["_p", "rate_per_day"], ascending=[True, False])
              .drop(columns="_p").reset_index(drop=True))
