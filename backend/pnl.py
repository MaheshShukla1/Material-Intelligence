"""P&L and waste: turn quantities into rupees.

Two questions the MD actually asks:
  1. "How much work (in ₹) is done, and how much is left?"
        done_value      = progress_used x install_rate
        remaining_value = remaining     x install_rate
  2. "How much material did we waste?"
        waste_qty  = actual_consumed - progress_used   (over-consumed vs work done)
        saving_qty = planned_total  - actual_consumed  (finished under plan)
        x rate for the ₹ figure.

`rate` is ₹/unit per BOQ item, *user-entered* (or later, read from a PO) — the
BOQ sheets here ship the rate column blank. So this module never invents a
rate: an item with no rate contributes to *quantity* rollups but is listed
under `unrated` and left out of the ₹ totals, so a half-priced project never
shows a falsely precise number.

Payment-term split (supply vs. installation): a real BOQ `rate` is usually a
COMBINED supply+installation number ("Cost includes rate of..." on the sheet
itself) — but a site engineer's own progress reflects labour, not material
already sitting in store. `install_pct` (0-100, either a per-item override or
a project-level default — see `_install_pct_for`) is the installation share of
that combined rate. When it's set, `planned_value`/`done_value`/
`remaining_value` all move to this install-only rate consistently — so
`done_value + remaining_value == planned_value` still holds, just scoped to
installation value instead of the full contract. `full_value` is added
alongside as the UNSCALED reference figure (planned_total x the full combined
rate) — never removed, just no longer the headline. When no `install_pct` is
configured anywhere (the common case today), every item behaves exactly as
before this existed: install rate == full rate, nothing changes.

Waste/saving `₹` figures deliberately stay on the FULL combined rate, not the
install-scaped one — over/under-consumed MATERIAL is a material cost, and
whether/how that should also reflect a payment-term split is its own open
question (not decided here — see the material-waste naming/logic item), so
this module does not silently fold a labour-only split into a material-loss
number nobody asked it to touch.

Waste needs the *actual* quantity consumed from the stock register (via
linkage), which is a different signal from the progress-derived `used`. When
actual consumption is not supplied, ₹ done/remaining still compute; waste is
simply reported as unavailable rather than guessed.

Nothing here touches the forecast engine. It consumes the frame from
progress.used_by_item and a plain rates dict.
"""
import pandas as pd


def _rate_for(code, rates):
    if not rates:
        return None
    r = rates.get(str(code))
    if r is None:
        return None
    try:
        r = float(r)
    except (TypeError, ValueError):
        return None
    return r if r >= 0 else None


def _install_pct_for(code, install_pct, default_install_pct):
    """Effective installation percent (0-100) for one item's rate math.

    Precedence: this item's own override (`install_pct[code]`), else the
    project-level `default_install_pct`, else None — meaning "no payment-term
    split configured for this item", which callers treat as 100 (the item's
    full combined rate counts as installation value, i.e. today's behaviour,
    completely unchanged). Never invents a percent the same way `_rate_for`
    never invents a rate: an out-of-range or unparseable value is dropped,
    same as if it had never been set -- and, critically, that drop still
    falls through to the project default rather than resolving to None
    outright (a bad per-item override must not silently blank out a
    perfectly good project default; a real test caught exactly this
    ordering bug before it shipped)."""
    def _valid(x):
        if x is None:
            return None
        try:
            x = float(x)
        except (TypeError, ValueError):
            return None
        return x if 0.0 <= x <= 100.0 else None

    v = _valid(install_pct.get(str(code))) if install_pct else None
    if v is None:
        v = _valid(default_install_pct)
    return v


def compute_item_pnl(used_df, rates=None, actual_consumed=None,
                     install_pct=None, default_install_pct=None):
    """Add ₹ columns to a used_by_item frame.

    used_df         : from progress.used_by_item (item_code, planned_total,
                      used, remaining, ...)
    rates           : {item_code: ₹/unit}  (user-entered; may be partial/None)
    actual_consumed : {item_code: qty}     (real OUT from the register, optional)
    install_pct     : {item_code: 0-100}   (per-item payment-term override,
                      optional)
    default_install_pct : 0-100 project-level default, optional. Applied to
                      every rated item that has no override of its own.

    Returns a copy with: rate, rated, install_pct, install_rate,
    planned_value, done_value, remaining_value, full_value, and — when actual
    is present — actual_qty, waste_qty, waste_value, saving_qty, saving_value.

    `planned_value`/`done_value`/`remaining_value` use the effective
    INSTALL rate (rate x install_pct/100, or the full rate when no split is
    configured for that item). `full_value` is planned_total x the full,
    unscaled rate — always available as the supply+installation reference
    figure, regardless of any payment-term setting.
    """
    df = used_df.copy()
    df["rate"] = df["item_code"].map(lambda c: _rate_for(c, rates))
    df["rated"] = df["rate"].notna()

    df["install_pct"] = df["item_code"].map(
        lambda c: _install_pct_for(c, install_pct, default_install_pct))
    r_full = df["rate"].fillna(0.0)
    frac = df["install_pct"].fillna(100.0) / 100.0
    df["install_rate"] = (r_full * frac).round(4)

    r = df["install_rate"]
    df["planned_value"] = (df["planned_total"] * r).round(2)
    df["done_value"] = (df["used"] * r).round(2)
    df["remaining_value"] = (df["remaining"] * r).round(2)
    df["full_value"] = (df["planned_total"] * r_full).round(2)
    # value columns are 0 where unrated; `rated` tells the UI to show "— set rate"
    df.loc[~df["rated"], ["planned_value", "done_value", "remaining_value",
                          "full_value", "install_rate", "install_pct"]] = None

    if actual_consumed:
        df["actual_qty"] = df["item_code"].map(
            lambda c: float(actual_consumed[str(c)])
            if str(c) in actual_consumed else None)
        act = df["actual_qty"]
        # over-consumed vs the work actually done = leakage/waste
        df["waste_qty"] = (act - df["used"]).where(act.notna())
        df["waste_qty"] = df["waste_qty"].clip(lower=0)
        # finished under plan = saving (only meaningful when work ~complete)
        near_done = df["progress_pct"] >= 99.0
        df["saving_qty"] = (df["planned_total"] - act).where(act.notna() & near_done)
        df["saving_qty"] = df["saving_qty"].clip(lower=0)
        # deliberately r_full, not the install-scaled r -- see module docstring:
        # waste is a material-cost figure, untouched by the payment-term split.
        df["waste_value"] = (df["waste_qty"] * r_full).where(df["rated"]).round(2)
        df["saving_value"] = (df["saving_qty"] * r_full).where(df["rated"]).round(2)
    return df


def _sum(series):
    s = pd.to_numeric(series, errors="coerce")
    return round(float(s.sum(skipna=True)), 2)


def rollup_pnl(item_pnl, mapping, service):
    """Roll item ₹ up to each activity and to the service total.

    Returns {by_activity: {activity: {...}}, totals: {...},
             unrated: [item_code, ...], unmapped_value: {...}}. An item counts
    toward every activity it is mapped to; the service total counts each item
    once.

    `totals` counts ONLY items currently mapped to an activity — the same
    basis the % complete ring already uses. An item's slider state (used/
    progress) persists in item_progress.json even after its activity is
    deleted (that is real physical work, never erased), but once nothing maps
    to it, it has no home in the activity list and must not go on quietly
    inflating "Work done" / "Remaining" while showing 0% and no activities.
    Its money is not lost though — it is reported separately in
    `unmapped_value`, an explicit "N items, ₹X, not counted above" figure the
    UI can surface, rather than silently folding it into the headline."""
    # item -> activities
    item_acts = {}
    for act in mapping.activities(service):
        for code in mapping.get(service, act):
            item_acts.setdefault(str(code), set()).add(act)

    by_act = {}
    for act in mapping.activities(service):
        codes = set(map(str, mapping.get(service, act)))
        sub = item_pnl[item_pnl["item_code"].astype(str).isin(codes)]
        by_act[act] = {
            "planned_value": _sum(sub.get("planned_value")),
            "done_value": _sum(sub.get("done_value")),
            "remaining_value": _sum(sub.get("remaining_value")),
            "full_value": _sum(sub.get("full_value")),
            "items": int(len(sub)),
            "unrated": int((~sub["rated"]).sum()) if "rated" in sub else 0,
        }
        if "waste_value" in item_pnl:
            by_act[act]["waste_value"] = _sum(sub.get("waste_value"))
            by_act[act]["saving_value"] = _sum(sub.get("saving_value"))

    mapped_codes = set(item_acts.keys())
    is_mapped = item_pnl["item_code"].astype(str).isin(mapped_codes)
    mapped_df = item_pnl[is_mapped]
    unmapped_df = item_pnl[~is_mapped]

    totals = {
        "planned_value": _sum(mapped_df.get("planned_value")),
        "done_value": _sum(mapped_df.get("done_value")),
        "remaining_value": _sum(mapped_df.get("remaining_value")),
        "full_value": _sum(mapped_df.get("full_value")),
        "items": int(len(mapped_df)),
        "rated": int(mapped_df["rated"].sum()) if "rated" in mapped_df else 0,
    }
    if "waste_value" in item_pnl:
        totals["waste_value"] = _sum(mapped_df.get("waste_value"))
        totals["saving_value"] = _sum(mapped_df.get("saving_value"))
    unrated = mapped_df.loc[~mapped_df["rated"], "item_code"].astype(str).tolist() \
        if "rated" in mapped_df else []

    unmapped_value = {
        "items": int(len(unmapped_df)),
        "planned_value": _sum(unmapped_df.get("planned_value")),
        "done_value": _sum(unmapped_df.get("done_value")),
        "remaining_value": _sum(unmapped_df.get("remaining_value")),
        "full_value": _sum(unmapped_df.get("full_value")),
    }
    return {"by_activity": by_act, "totals": totals, "unrated": unrated,
            "unmapped_value": unmapped_value}


def project_pnl(rollups_by_service):
    """Sum service rollups into one project headline."""
    keys = ["planned_value", "done_value", "remaining_value",
            "full_value", "waste_value", "saving_value"]
    out = {k: 0.0 for k in keys}
    services = {}
    for svc, r in rollups_by_service.items():
        t = r["totals"]
        for k in keys:
            out[k] += float(t.get(k) or 0.0)
        services[svc] = t
    out = {k: round(v, 2) for k, v in out.items()}
    if out["planned_value"] > 0:
        out["pct_value_done"] = round(100.0 * out["done_value"]
                                      / out["planned_value"], 1)
    else:
        out["pct_value_done"] = 0.0
    out["by_service"] = services
    return out


def waste_summary(item_pnl, top=5):
    """Project-level waste headline + the items driving it. Needs actual
    consumption; returns available=False when it was not supplied.

    `caveat` fires when very little progress has been recorded yet (< 5%,
    quantity-weighted) while waste is still non-trivial. The formula itself
    (actual_consumed - used) is correct either way, but when `used` is near
    zero because progress hasn't been ENTERED into Site Progress yet — not
    because nothing happened on site — waste collapses to ~the full
    actual_consumed figure, which reads as a huge number that mostly reflects
    unrecorded progress, not real material loss. This is not a bug in the
    number; it is a real limitation of what the formula can know until real
    progress is recorded, so it is surfaced rather than hidden."""
    if "waste_value" not in item_pnl:
        return {"available": False,
                "reason": "actual consumption not linked yet — connect the "
                          "stock register via linkage to see waste"}
    wasted = _sum(item_pnl.get("waste_value"))
    saved = _sum(item_pnl.get("saving_value"))
    drivers = (item_pnl[item_pnl["waste_value"].fillna(0) > 0]
               .sort_values("waste_value", ascending=False)
               .head(top))
    top_items = [{"item_code": str(r.item_code),
                  "description": getattr(r, "description", None),
                  "waste_qty": None if pd.isna(getattr(r, "waste_qty", None)) else float(r.waste_qty),
                  "waste_value": float(r.waste_value)}
                 for r in drivers.itertuples()]

    qty_planned = pd.to_numeric(item_pnl.get("planned_total"), errors="coerce").fillna(0)
    pct = pd.to_numeric(item_pnl.get("progress_pct"), errors="coerce").fillna(0)
    total_planned = float(qty_planned.sum())
    weighted_pct = round(float((qty_planned * pct).sum() / total_planned), 1) if total_planned > 0 else 0.0
    caveat = None
    if weighted_pct < 5.0 and wasted > 0:
        caveat = (f"Only {weighted_pct:.1f}% of planned work is recorded as done so far. "
                  "This figure may mostly reflect real progress that hasn't been entered "
                  "into Site Progress yet, not necessarily true material loss — it becomes "
                  "reliable as more real progress is recorded.")

    return {"available": True, "wasted_value": wasted, "saved_value": saved,
            "net_value": round(wasted - saved, 2), "top_items": top_items,
            "progress_pct": weighted_pct, "caveat": caveat}
