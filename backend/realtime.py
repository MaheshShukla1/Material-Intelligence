"""Realistic forecast: progress + BOQ + stock, combined.

The plain forecast (Forecast tab) answers one question from the register alone:
"at the current burn rate, when does this material run out?" It never knows how
much work is actually left.

Site Progress knows that. For a BOQ item the engineer has linked to one or more
stock materials, we can combine THREE signals:

    A. remaining work   = BOQ_planned_total - used        (from progress)
    B. stock on hand     = the register's closing stock    (read-only, engine)
    C. burn rate         = the register's rate_per_day      (read-only, engine)

and answer the question that actually matters on site:

    "Is the stock on hand enough to FINISH the planned work — and if not, how
     much to order and by when?"

        shortfall      = remaining_need - on_hand           (order this much)
        days_to_finish = remaining_need / burn_rate
        SHORTAGE if the stock runs out (days_left) before the work finishes.

Unit note: a BOQ line's unit (e.g. Rmt of "wire-in-conduit") usually matches the
linked stock unit (metres of pipe / wire), so the default conversion factor is
1. When they differ, a per-link factor can be set; we never fabricate one — if
units clearly mismatch and no factor is given, we fall back to the rate-only
comparison and say so. Nothing here writes to the engine; it only reads the run.
"""


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f            # NaN -> None
    except (TypeError, ValueError):
        return None


def combine_item(item, stock_rows, rooms=None):
    """One BOQ item's realistic verdict.

    item        : {planned_total, used, remaining, progress_pct, unit}
    stock_rows  : list of the item's linked stock rows, each
                  {material, stock, rate_per_day, days_left, status, unit,
                  total_consumed, factor} -- total_consumed is the engine's
                  real OUT-to-date figure, already present on every forecast
                  run row; nothing extra to upload for this. `factor` is the
                  BOQ-unit -> this material's-unit multiplier for THIS link
                  specifically (e.g. 3 Rmt of pipe per Nos of light point) --
                  None when not set.

    rooms       : optional itemprog.room_buckets() result for this item --
                  {"done","in_progress","not_started","total"}. Purely for
                  `sentence()`'s wording below ("...needed for the remaining
                  N rooms"); it changes NO number here. `remaining` is
                  already exactly the sum of each not-fully-done room's own
                  shortfall -- a fully-done room contributes zero to
                  `remaining` by construction (used = planned there), so
                  restricting the sum to only outstanding rooms can never
                  change the total. Passed through unchanged as res["rooms"].

    Unit handling -- this used to default `factor` to 1.0 for every link,
    always, regardless of whether the BOQ item's own unit and the linked
    material's unit actually matched. A BOQ line like "15 Nos of emergency
    light point" linked to "25MM PVC PIPE" (unit Rmt) would then compute
    "need = 15 x 1.0 = 15 Rmt of pipe" -- a number with no real basis,
    silently wrong the moment the two units aren't the same thing. Per-row
    now: `effective_factor` is the given `factor` if one was set, else 1.0
    ONLY when the two unit strings literally match (a real, safe default,
    not a guess), else None -- and a None factor means this row's `need`,
    `shortfall`, and `days_to_finish` are never computed at all (not
    estimated with a wrong assumption). The row still shows on_hand/rate/
    consumed as plain facts about the material; it just can't be compared
    against BOQ demand until a real factor is entered, and says so via
    verdict "UNKNOWN_FACTOR" rather than silently guessing "ENOUGH".

    Returns a dict with the per-material breakdown (including `received` =
    total_consumed + on_hand, i.e. everything that ever came in and either
    got used or is still sitting on the shelf) and one overall verdict.
    """
    remaining = _num(item.get("remaining"))
    planned_total = _num(item.get("planned_total"))
    item_unit = (item.get("unit") or "").strip().upper()

    links, worst = [], "ENOUGH"
    order_total = 0.0
    order_optimistic_total = 0.0
    consumed_total = 0.0   # for the ONE consolidated "X issued" figure in sentence()
    optimistic_computable = True   # false the moment any SHORTAGE row can't compute it
    for s in stock_rows:
        on_hand = _num(s.get("stock"))
        rate = _num(s.get("rate_per_day"))
        dleft = _num(s.get("days_left"))
        consumed = _num(s.get("total_consumed"))
        given_factor = _num(s.get("factor"))
        stock_unit = (s.get("unit") or "").strip().upper()
        units_match = bool(item_unit) and bool(stock_unit) and item_unit == stock_unit
        effective_factor = given_factor if given_factor is not None else (1.0 if units_match else None)
        need = None if (remaining is None or effective_factor is None) else remaining * effective_factor

        # received to date = real OUT to date + what's still on hand. Both
        # numbers already live in the same forecast run every other figure
        # here comes from -- no separate PO/GRN parse needed. Only computed
        # when both halves are real (never invent one side and guess).
        received = (consumed + on_hand) if (consumed is not None and on_hand is not None) else None
        row = {"material": s.get("material"), "unit": s.get("unit"),
               "on_hand": on_hand, "rate_per_day": rate,
               "engine_days_left": dleft, "status": s.get("status"),
               "order_by": s.get("order_by"),
               "total_consumed": consumed, "received": received,
               "factor": given_factor, "units_match": units_match}

        if need is None and remaining is not None and not units_match:
            # the one case this whole redesign exists for: linked, real
            # stock data available, but no honest way to compare it to BOQ
            # demand yet -- say so plainly instead of picking ENOUGH by
            # default (silence would read as "nothing to worry about").
            row["verdict"] = "UNKNOWN_FACTOR"
        else:
            # quantity verdict: is there enough on hand to finish the work?
            if need is not None and on_hand is not None:
                shortfall = round(max(need - on_hand, 0.0), 2)
                row["need"] = round(need, 2)
                row["shortfall"] = shortfall
                if shortfall > 0:
                    row["verdict"] = "SHORTAGE"
                    order_total += shortfall

                    # Optimistic order: max(planned_total*factor - received, 0).
                    # Algebraically this equals shortfall MINUS (issued to
                    # date - expected for work marked done) -- i.e. it credits
                    # the over-issued gap, on the ASSUMPTION that gap is
                    # staged material sitting on site, not lost. This is
                    # never asserted as the real number: real waste, a stale
                    # progress entry, or genuine staging can each explain the
                    # same gap, and only the engineer on site can tell which.
                    # Surfaced as a second, clearly-labelled bound inside
                    # sentence()'s ONE consolidated message -- never in place
                    # of the safe order_qty, and never silently substituted
                    # (see rate.py's own "running out early costs far more
                    # than ordering a little early" -- the same principle
                    # applies to shrinking an order on an assumption).
                    if received is not None and planned_total is not None and effective_factor is not None:
                        opt = round(max(planned_total * effective_factor - received, 0.0), 2)
                        if opt < shortfall:
                            row["optimistic_shortfall"] = opt
                            row["staged_gap"] = round(shortfall - opt, 2)
                        order_optimistic_total += opt
                        if consumed is not None:
                            consumed_total += consumed
                    else:
                        optimistic_computable = False
                else:
                    row["verdict"] = "ENOUGH"
            # rate verdict: does stock run out before the work is done?
            if need is not None and rate and rate > 0:
                row["days_to_finish"] = round(need / rate, 1)
                if dleft is not None and dleft < row["days_to_finish"]:
                    row["verdict"] = "SHORTAGE"

        v = row.get("verdict")
        if v == "SHORTAGE":
            worst = "SHORTAGE"
        elif v == "UNKNOWN_FACTOR" and worst != "SHORTAGE":
            worst = "UNKNOWN_FACTOR"
        elif v is None and worst == "ENOUGH":
            worst = "UNKNOWN"                    # linked but no stock/rate figures
        links.append(row)

    if not stock_rows:
        overall = "NOT_LINKED"
    elif worst == "SHORTAGE":
        overall = "SHORTAGE"
    elif worst == "UNKNOWN_FACTOR":
        overall = "UNKNOWN_FACTOR"
    elif worst == "UNKNOWN":
        overall = "UNKNOWN"
    else:
        overall = "ENOUGH"

    order_qty = round(order_total, 2) if overall == "SHORTAGE" else 0.0
    # Only surfaced when it's a REAL, fully-computable, meaningfully-lower
    # bound for every shortage row -- a partial picture (computable for some
    # materials, not others) is not shown at all rather than mixing a real
    # figure with a silently-skipped gap.
    show_optimistic = (overall == "SHORTAGE" and optimistic_computable
                       and order_optimistic_total < order_total)
    order_qty_optimistic = round(order_optimistic_total, 2) if show_optimistic else None
    # the same "issued vs expected" gap used to be a SEPARATE warning
    # elsewhere in the drawer, repeating this exact number in a second
    # place with a second framing -- now it lives only here, one number,
    # inside sentence()'s single consolidated message.
    staged_gap = round(order_qty - order_qty_optimistic, 2) if show_optimistic else None
    issued_to_date = round(consumed_total, 2) if show_optimistic else None

    return {
        "item_code": item.get("item_code"),
        "unit": item.get("unit"),
        "planned_total": planned_total,
        "used": _num(item.get("used")),
        "remaining": remaining,
        "progress_pct": _num(item.get("progress_pct")),
        "order_qty": order_qty,
        "order_qty_optimistic": order_qty_optimistic,
        "staged_gap": staged_gap,
        "issued_to_date": issued_to_date,
        "verdict": overall,
        "links": links,
        "rooms": rooms,
    }


def _outstanding_rooms(res):
    """Rooms that still need this item, from res["rooms"] (a room_buckets()
    dict) -- in_progress + not_started. None when rooms weren't supplied or
    the project has no rooms yet, so sentence() can fall back cleanly."""
    rooms = res.get("rooms")
    if not rooms or not rooms.get("total"):
        return None
    return rooms.get("in_progress", 0) + rooms.get("not_started", 0)


def sentence(res):
    """A one-line, plain-language summary for the drawer. Mentions the count
    of outstanding rooms when room_buckets() data was supplied (see
    combine_item's `rooms` arg) -- the same underlying `remaining`/`order_qty`
    figures either way, just worded against the rooms still waiting on this
    item rather than a bare quantity."""
    u = res.get("unit") or ""
    n = _outstanding_rooms(res)
    rooms_phrase = f" the remaining {n} rooms" if n is not None else " work remains"
    if res["verdict"] == "NOT_LINKED":
        return "Not linked to stock yet — link a register material to forecast this item."
    if res["verdict"] == "UNKNOWN_FACTOR":
        mats = ", ".join(L["material"] for L in res.get("links", [])
                         if L.get("verdict") == "UNKNOWN_FACTOR" and L.get("material"))
        who = f" for {mats}" if mats else ""
        return (f"{res['remaining']:.0f} {u} of work remains, but this item's unit ({u or '—'}) "
                f"doesn't match the linked stock's unit{who} — enter a conversion factor "
                "(e.g. how many Rmt per Nos) in Link stock to see whether stock is enough.")
    if res["verdict"] == "UNKNOWN":
        if n is not None:
            return (f"{res['remaining']:.0f} {u} needed for{rooms_phrase}, but the "
                    "linked stock has no rate/on-hand figures yet.")
        return (f"{res['remaining']:.0f} {u} of work remains, but the linked "
                "stock has no rate/on-hand figures yet.")
    if res["verdict"] == "SHORTAGE":
        opt = res.get("order_qty_optimistic")
        # ONE consolidated line -- the safe order plus, only when it's real
        # and fully computable, the one clarifying parenthetical about an
        # over-issued gap possibly being staged material. Never a second
        # separate paragraph repeating the same numbers with a different
        # framing (that used to live here AND in a standalone "issued vs
        # expected" warning elsewhere in the drawer -- the same 1,766-style
        # gap, said twice, was noise, not clarity).
        opt_note = (f" ({res['issued_to_date']:.0f} {u} already issued is {res['staged_gap']:.0f} {u} "
                   f"more than this progress should have used — if that's staged material on "
                   f"site, {opt:.0f} {u} more would be enough instead; verify first.)"
                   if opt is not None else "")
        if n is not None:
            return (f"{res['remaining']:.0f} {u} needed for{rooms_phrase} — order "
                    f"{res['order_qty']:.0f} {u} to be safe.{opt_note}")
        return (f"{res['remaining']:.0f} {u} of work remains — order "
                f"{res['order_qty']:.0f} {u} to be safe.{opt_note}")
    if n is not None:
        return (f"{res['remaining']:.0f} {u} needed for{rooms_phrase} — stock on "
                "hand is enough at the current rate.")
    return (f"{res['remaining']:.0f} {u} of work remains; stock on hand is enough "
            "to finish at the current rate.")
