"""Real-logic pytest suite for realtime.py's optimistic order bound.

The safe order_qty (need - on_hand) assumes any already-issued material
beyond what the recorded progress explains is gone for good. That's the
right DEFAULT (never runs the site short), but when a real "issued vs
expected" gap exists, some of it is often genuinely staged material sitting
on site, not lost -- and the engineer, not the system, is the one who can
actually tell which. order_qty_optimistic surfaces that second, clearly
lower-bound number, computed from the exact same inputs, never in place of
the safe one, only when it's a real and fully-computable gap.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import realtime   # noqa: E402


def _item(planned_total, used, remaining, unit="MTR"):
    return {"item_code": "QI1", "unit": unit, "planned_total": planned_total,
           "used": used, "remaining": remaining, "progress_pct": 50}


def _stock(material="MATERIAL A", stock=0, consumed=0, unit="MTR", factor=None,
           rate=None, days_left=None, status="GREEN"):
    return {"material": material, "stock": stock, "rate_per_day": rate,
           "days_left": days_left, "status": status,
           "total_consumed": consumed, "unit": unit, "factor": factor}


class TestOptimisticOrderMatchesRealScenario:
    """Locks in the exact real-world numbers this feature was built from:
    QI1 KITEC PE-AL-PEX PIPE COIL, Hyatt Hotel -- planned 8160, used 4240,
    remaining 3920, on-hand 494, issued 6006. Safe order 3426, optimistic
    1660 (== planned - received, the engineer's own back-of-envelope check)."""

    def test_safe_and_optimistic_orders_match_hand_calculation(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006)]
        res = realtime.combine_item(item, stock)
        assert res["order_qty"] == 3426.0
        assert res["order_qty_optimistic"] == 1660.0
        # algebraic identity this whole feature rests on:
        # optimistic == planned_total - received (== planned - (consumed+on_hand))
        assert res["order_qty_optimistic"] == pytest.approx(8160 - (6006 + 494))

    def test_sentence_includes_the_optimistic_note(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006)]
        res = realtime.combine_item(item, stock)
        msg = realtime.sentence(res)
        assert "order 3426" in msg
        assert "1660" in msg
        assert "1766" in msg   # the staged/credited gap, shown explicitly
        assert "verify" in msg.lower()   # never framed as asserted fact

    def test_sentence_with_rooms_phrase_also_includes_it(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006)]
        rooms = {"done": 106, "in_progress": 98, "not_started": 0, "total": 204}
        res = realtime.combine_item(item, stock, rooms=rooms)
        msg = realtime.sentence(res)
        assert "98 rooms" in msg
        assert "1660" in msg

    def test_the_gap_is_stated_exactly_once_not_duplicated(self):
        """The whole point of this consolidation: one number, one place --
        not the same 1,766-style gap repeated in two separate paragraphs
        with two different framings."""
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006)]
        res = realtime.combine_item(item, stock)
        msg = realtime.sentence(res)
        assert msg.count("1766") == 1
        assert msg.count("1660") == 1
        assert msg.count("3426") == 1

    def test_new_fields_exposed_for_the_frontend(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006)]
        res = realtime.combine_item(item, stock)
        assert res["staged_gap"] == 1766.0
        assert res["issued_to_date"] == 6006.0


class TestOptimisticOrderNeverShownWithoutARealGap:
    def test_no_over_issuance_no_optimistic_note(self):
        """received exactly matches what's expected for the work done --
        there is no gap to credit, so optimistic must not appear at all
        (it would equal the safe number, adding nothing but noise)."""
        item = _item(planned_total=1000, used=500, remaining=500)
        stock = [_stock(stock=200, consumed=500)]   # received = 700 = planned-300... let's compute
        res = realtime.combine_item(item, stock)
        # need=500, on_hand=200 -> shortfall=300. optimistic = planned(1000)-received(700) = 300 = shortfall.
        assert res["order_qty"] == 300.0
        assert res["order_qty_optimistic"] is None
        msg = realtime.sentence(res)
        assert "staged" not in msg.lower()

    def test_under_issuance_never_produces_a_higher_optimistic_number(self):
        """If less was issued than expected (material genuinely short, not
        staged anywhere), optimistic must never come out ABOVE the safe
        order -- and per the "only show if meaningfully lower" rule, it's
        simply omitted."""
        item = _item(planned_total=1000, used=800, remaining=200)
        stock = [_stock(stock=50, consumed=100)]   # way under-issued vs 800 used
        res = realtime.combine_item(item, stock)
        assert res["order_qty_optimistic"] is None

    def test_enough_verdict_has_no_optimistic_figure(self):
        item = _item(planned_total=1000, used=900, remaining=100)
        stock = [_stock(stock=500, consumed=900)]
        res = realtime.combine_item(item, stock)
        assert res["verdict"] == "ENOUGH"
        assert res["order_qty_optimistic"] is None


class TestOptimisticOrderRequiresFullData:
    def test_missing_planned_total_omits_optimistic_but_keeps_safe_order(self):
        item = {"item_code": "QI1", "unit": "MTR", "planned_total": None,
               "used": 4240, "remaining": 3920, "progress_pct": 52}
        stock = [_stock(stock=494, consumed=6006)]
        res = realtime.combine_item(item, stock)
        assert res["order_qty"] == 3426.0   # safe order is completely unaffected
        assert res["order_qty_optimistic"] is None

    def test_missing_consumed_omits_optimistic(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=None)]
        res = realtime.combine_item(item, stock)
        assert res["order_qty"] == 3426.0
        assert res["order_qty_optimistic"] is None

    def test_multi_material_partial_data_omits_optimistic_entirely(self):
        """Two materials linked to one item; only one has full received
        data. A partial optimistic total (real for one material, silently
        skipped for the other) is never shown -- all or nothing."""
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(material="A", stock=200, consumed=3000),
                _stock(material="B", stock=294, consumed=None)]
        res = realtime.combine_item(item, stock)
        assert res["order_qty"] is not None and res["order_qty"] > 0   # safe order still whole
        assert res["order_qty_optimistic"] is None


class TestSafeOrderUnaffectedByThisFeature:
    """Pure regression: order_qty, verdict, shortfall, need must be byte-
    identical to before this feature existed, for every case."""

    def test_shortage_case(self):
        item = _item(planned_total=8160, used=4240, remaining=3920)
        stock = [_stock(stock=494, consumed=6006, rate=108.3, days_left=5, status="RED")]
        res = realtime.combine_item(item, stock)
        assert res["verdict"] == "SHORTAGE"
        assert res["links"][0]["need"] == 3920.0
        assert res["links"][0]["shortfall"] == 3426.0

    def test_enough_case(self):
        item = _item(planned_total=1000, used=900, remaining=100)
        stock = [_stock(stock=500, consumed=900)]
        res = realtime.combine_item(item, stock)
        assert res["verdict"] == "ENOUGH"
        assert res["order_qty"] == 0.0

    def test_not_linked_case(self):
        item = _item(planned_total=1000, used=0, remaining=1000)
        res = realtime.combine_item(item, [])
        assert res["verdict"] == "NOT_LINKED"
        assert res["order_qty_optimistic"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
