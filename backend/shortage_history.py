"""Shortage history: point-in-time snapshots of the Site-Progress-linked
forecast verdict (realtime.combine_item), captured once per new forecast
run, so "was a flagged shortage actually prevented" can be answered later
from real history -- never asked of an engineer, never guessed.

Why this can't be reconstructed retroactively from OLD runs: combine_item()
needs BOTH a forecast run's stock/rate (versioned, one snapshot per run) AND
Site Progress's remaining-work number (NOT versioned -- item_progress.json
is current-state-only, overwritten in place every time a room is ticked).
Recomputing an old run's verdict with TODAY's remaining would silently mix
a month-old stock reading with today's site progress -- that's not history,
it's a guess wearing the shape of a fact. So this tracks forward from
whenever it is first called, the same "never invent a number the data
doesn't actually support" discipline realtime.py and itemprog.py already
apply elsewhere in this codebase.

Storage: shortage_log.json, a flat list, one entry per (run_id, service,
item_code) -- never duplicated for the same run. snapshot_run() is
idempotent (same convention as dpr.record_change()): safe to call on every
/realistic/{service} hit, not just once, since a run already logged is a
no-op update rather than a duplicate row.

    {"date": "2026-08-20", "run_id": "run_...", "service": "Electrical",
     "item_code": "QI2", "verdict": "SHORTAGE", "remaining_need": 702,
     "on_hand": 172, "shortfall": 530, "rate": 45.5}
"""

NEAR_ZERO = 1e-6


def snapshot_run(log, date_str, run_id, service, item_code, verdict,
                 remaining_need=None, on_hand=None, shortfall=None, rate=None):
    """Record one item's combine_item() verdict for one run, once. Same
    (run_id, service, item_code) touched again UPDATES that entry in place
    rather than adding a duplicate -- calling this on every page load for a
    run that's already logged must never grow the log."""
    key = (run_id, service, str(item_code))
    for e in log:
        if (e["run_id"], e["service"], str(e["item_code"])) == key:
            e.update({"date": date_str, "verdict": verdict,
                     "remaining_need": remaining_need, "on_hand": on_hand,
                     "shortfall": shortfall, "rate": rate})
            return log
    log.append({"date": date_str, "run_id": run_id, "service": service,
               "item_code": str(item_code), "verdict": verdict,
               "remaining_need": remaining_need, "on_hand": on_hand,
               "shortfall": shortfall, "rate": rate})
    return log


def _sorted_snaps(log, service=None, item_code=None):
    out = [e for e in log
           if (service is None or e["service"] == service)
           and (item_code is None or str(e["item_code"]) == str(item_code))]
    return sorted(out, key=lambda e: (e["date"], e["run_id"]))


def episodes_for_item(log, service, item_code):
    """Chronological SHORTAGE episodes for one item: each is a maximal run
    of consecutive snapshots with verdict == "SHORTAGE", classified once it
    ends (or "ongoing" if it hasn't):

      "prevented"         : resolved to a non-SHORTAGE verdict while
                             remaining_need was still real (> NEAR_ZERO) --
                             a genuine save, whether via a delivery raising
                             on_hand or consumption slowing down. Either way
                             the material problem that was real got solved,
                             not sidestepped.
      "no_longer_needed"  : resolved because remaining_need itself dropped
                             to ~0 -- the WORK finished (or was reassigned),
                             not the stock problem. Never counted as money
                             saved: no crisis was actually averted by an
                             order, the need just disappeared.
      "materialized"      : at any point during the episode, on_hand
                             actually hit ~0 while remaining_need was still
                             real -- the shortage happened for real, whether
                             or not it later recovered. Takes priority over
                             "prevented" even if the episode later resolves,
                             since the point was to catch it BEFORE that.
      "ongoing"            : still SHORTAGE as of the latest snapshot.

    Never fabricates an outcome for a single isolated snapshot with no
    resolving data yet -- that episode is "ongoing", not guessed either way.
    """
    snaps = _sorted_snaps(log, service, item_code)
    episodes = []
    cur = None
    for i, s in enumerate(snaps):
        if s["verdict"] == "SHORTAGE":
            if cur is None:
                cur = {"start": s, "snaps": [s]}
            else:
                cur["snaps"].append(s)
        else:
            if cur is not None:
                cur["end"] = s
                cur["outcome"] = _classify(cur["snaps"], s)
                episodes.append(cur)
                cur = None
    if cur is not None:
        cur["end"] = None
        cur["outcome"] = "ongoing"
        episodes.append(cur)
    return episodes


def _classify(shortage_snaps, resolving_snap):
    for s in shortage_snaps:
        oh = s.get("on_hand")
        need = s.get("remaining_need")
        if oh is not None and oh <= NEAR_ZERO and need is not None and need > NEAR_ZERO:
            return "materialized"
    need_at_resolve = resolving_snap.get("remaining_need")
    if need_at_resolve is not None and need_at_resolve <= NEAR_ZERO:
        return "no_longer_needed"
    return "prevented"


def episode_value(episode):
    """₹ exposure that was real right before a "prevented" episode resolved
    -- the last SHORTAGE snapshot's shortfall x rate. None for every OTHER
    outcome (no_longer_needed / materialized / ongoing) -- guarded HERE, not
    left to every caller to remember, since a shortfall number always exists
    on the raw snapshot regardless of how the episode turned out; only
    "prevented" means it represents money an order genuinely saved. None
    also when either figure isn't known (never invents a rupee figure
    pnl.py itself wouldn't be able to price, same "unrated items are
    excluded, not guessed at" rule as pnl.compute_item_pnl)."""
    if episode.get("outcome") != "prevented":
        return None
    last = episode["snaps"][-1]
    shortfall, rate = last.get("shortfall"), last.get("rate")
    if shortfall is None or rate is None:
        return None
    return round(shortfall * rate, 2)


def month_summary(log, service=None, year_month=None):
    """{"flagged": N, "prevented": N, "materialized": N, "no_longer_needed": N,
    "ongoing": N, "value_protected": ₹} for one calendar month (default: the
    month of the latest snapshot in the log, so an empty/near-empty log still
    returns a sane all-zero shape rather than raising). One episode counts
    once, keyed by its START date falling in the month -- a shortage flagged
    on the 30th and resolved on the 2nd of next month is still "this month's"
    flag, matching how a person would describe it."""
    snaps = _sorted_snaps(log, service)
    if year_month is None:
        if not snaps:
            return {"flagged": 0, "prevented": 0, "materialized": 0,
                   "no_longer_needed": 0, "ongoing": 0, "value_protected": 0.0}
        year_month = snaps[-1]["date"][:7]

    items = sorted({(e["service"], e["item_code"]) for e in snaps})
    out = {"flagged": 0, "prevented": 0, "materialized": 0,
          "no_longer_needed": 0, "ongoing": 0, "value_protected": 0.0}
    for svc, code in items:
        for ep in episodes_for_item(log, svc, code):
            if ep["start"]["date"][:7] != year_month:
                continue
            out["flagged"] += 1
            out[ep["outcome"]] += 1
            v = episode_value(ep)
            if v is not None:
                out["value_protected"] += v
    out["value_protected"] = round(out["value_protected"], 2)
    return out


def month_episodes(log, service=None, year_month=None):
    """Every episode whose START date falls in the given month (default: the
    latest month tracked), across every (service, item_code) the log has --
    the actual drill-down list behind month_summary()'s aggregate counts, for
    a "1 flagged" ticker that's clickable rather than a dead-end number."""
    snaps = _sorted_snaps(log, service)
    if year_month is None:
        if not snaps:
            return []
        year_month = snaps[-1]["date"][:7]

    items = sorted({(e["service"], e["item_code"]) for e in snaps})
    out = []
    for svc, code in items:
        for ep in episodes_for_item(log, svc, code):
            if ep["start"]["date"][:7] != year_month:
                continue
            out.append({
                "service": svc, "item_code": code,
                "flagged_date": ep["start"]["date"],
                "resolved_date": ep["end"]["date"] if ep["end"] else None,
                "outcome": ep["outcome"],
                "value_protected": episode_value(ep),
            })
    out.sort(key=lambda e: e["flagged_date"], reverse=True)
    return out


def item_timeline(log, service, item_code):
    """The compact per-item view for the small popover (never the drawer) --
    one row per episode, oldest first, with outcome and, when it's a real
    "prevented" save, the ₹ value. This is ALL this module renders for a UI
    -- everything else (labels, icons, when to show the trigger at all) is a
    frontend decision, not this module's."""
    out = []
    for ep in episodes_for_item(log, service, item_code):
        out.append({
            "flagged_date": ep["start"]["date"],
            "resolved_date": ep["end"]["date"] if ep["end"] else None,
            "outcome": ep["outcome"],
            "value_protected": episode_value(ep),
        })
    return out


def items_with_history(log, service):
    """{item_code: {"episodes": N, "ongoing": bool}} for every item in this
    service that has AT LEAST one recorded episode -- the one bulk signal a
    frontend needs to decide which item rows get a small history trigger,
    without a separate request per item (most items will never appear here
    at all, since most items never had a shortage -- exactly why this is a
    sparse map, not a full item list)."""
    codes = sorted({e["item_code"] for e in log if e["service"] == service})
    out = {}
    for code in codes:
        eps = episodes_for_item(log, service, code)
        if eps:
            out[code] = {"episodes": len(eps),
                        "ongoing": any(e["outcome"] == "ongoing" for e in eps)}
    return out


def lifetime_summary(log, service=None):
    """Same aggregate shape as month_summary(), but across the WHOLE project
    history, not one month -- the standing "how much can you trust this
    system's shortage calls" signal. Unlike the monthly ticker, this must
    NEVER disappear just because a quiet month had nothing new to flag; a
    track record only means something if it persists.

    Adds "catch_rate": prevented / (prevented + materialized), as a whole
    percent -- deliberately excludes "no_longer_needed" (work just ended
    first, never a real test of whether the call was right) and "ongoing"
    (not resolved yet, nothing to score). None when nothing has resolved
    either way yet -- never fabricates a percentage from zero real
    outcomes, same "never invent" rule as episode_value()."""
    snaps = _sorted_snaps(log, service)
    items = sorted({(e["service"], e["item_code"]) for e in snaps})
    out = {"flagged": 0, "prevented": 0, "materialized": 0,
          "no_longer_needed": 0, "ongoing": 0, "value_protected": 0.0}
    for svc, code in items:
        for ep in episodes_for_item(log, svc, code):
            out["flagged"] += 1
            out[ep["outcome"]] += 1
            v = episode_value(ep)
            if v is not None:
                out["value_protected"] += v
    out["value_protected"] = round(out["value_protected"], 2)
    resolved = out["prevented"] + out["materialized"]
    out["catch_rate"] = round(100 * out["prevented"] / resolved) if resolved > 0 else None
    return out


def all_episodes(log, service=None):
    """Every episode ever recorded, newest first -- the drill-down behind
    lifetime_summary()'s cumulative counts (month_episodes() is the same
    idea scoped to one month instead)."""
    snaps = _sorted_snaps(log, service)
    items = sorted({(e["service"], e["item_code"]) for e in snaps})
    out = []
    for svc, code in items:
        for ep in episodes_for_item(log, svc, code):
            out.append({
                "service": svc, "item_code": code,
                "flagged_date": ep["start"]["date"],
                "resolved_date": ep["end"]["date"] if ep["end"] else None,
                "outcome": ep["outcome"],
                "value_protected": episode_value(ep),
            })
    out.sort(key=lambda e: e["flagged_date"], reverse=True)
    return out
