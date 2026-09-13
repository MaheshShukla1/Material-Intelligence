"""Issue 3 regression test: an item with room_qty_groups but no item_rooms.json
entry was silently charging the item's raw per-room `qty` against every OTHER
room in the project, not just the rooms actually ticked in the Zones modal.

Real reported scenario (Thoth Mall, "25 x 6mm GI Strip", item 13.2.1):
  - 21 zones total in the project (B3: 7, B2: 7, B1: 7)
  - engineer ticked 14 of them and saved a room_qty_group at 535.7 Rmt/zone
    -> real total should be 14 x 535.7 = 7,499.8 Rmt (~7,500, matches the
       user's own manual calculation)
  - item_rooms.json had NO entry for this item at all
  - UI showed 85,900 Rmt planned instead (~11.4x too much)

Solving 85,900 = 14*535.7 + 7*Q for the OTHER 7 zones' contribution gives
Q ~= 11,200.03 -- this is what the item's own BOQ-level per-room `qty`
must have been, and it is exactly what the buggy code fell back to for
every zone outside the group. This test reproduces that exact math.
"""
import sys
import pandas as pd
import pytest

sys.path.insert(0, "/home/claude/work")
import itemprog


B3 = [f"z_b3_{i}" for i in range(1, 8)]
B2 = [f"z_b2_{i}" for i in range(1, 8)]
B1 = [f"z_b1_{i}" for i in range(1, 8)]
ALL_ZONES = B3 + B2 + B1                      # 21 zones total
GROUPED_ZONES = ALL_ZONES[:14]                # the 14 zones actually ticked
UNGROUPED_ZONES = ALL_ZONES[14:]              # the other 7, never touched

ITEM_CODE = "13.2.1"
GROUP_QTY = 535.7                              # real per-zone qty entered
RAW_BOQ_QTY = round((85900 - 14 * GROUP_QTY) / 7, 2)   # ~11,200.03 -- derived
                                                         # from the exact reported
                                                         # buggy total, this is
                                                         # what the item's own
                                                         # qty_per_room must be


def _items_df():
    return pd.DataFrame([
        {"item_code": ITEM_CODE, "description": "25 x 6 mm GI Strip",
         "unit": "RMT", "qty": RAW_BOQ_QTY},
    ])


def _room_qty_groups():
    return {ITEM_CODE: [{"rooms": GROUPED_ZONES, "qty": GROUP_QTY}]}


class TestIssue3ComputePlannedTotal:
    def test_no_item_rooms_entry_does_not_leak_into_ungrouped_zones(self):
        """The exact reported bug: no item_rooms.json entry for this item,
        only a room_qty_group covering 14 of the 21 zones. planned_total
        must be just the group's own total, not inflated by the other 7
        zones picking up the raw BOQ qty."""
        prog_svc = {}                      # no progress recorded yet
        rooms_svc = {}                     # <-- the actual bug condition:
                                            #     NOTHING here for ITEM_CODE
        df = itemprog.compute(
            _items_df(), prog_svc, rooms_svc, ALL_ZONES,
            planned_over={}, room_qty_groups=_room_qty_groups(),
        )
        row = df[df.item_code == ITEM_CODE].iloc[0]

        expected_planned = round(14 * GROUP_QTY, 3)          # 7,499.8
        buggy_planned = round(14 * GROUP_QTY + 7 * RAW_BOQ_QTY, 3)  # ~85,900

        assert row.planned_total == pytest.approx(expected_planned, abs=0.01)
        assert row.planned_total != pytest.approx(buggy_planned, abs=1.0)
        assert row.planned_total == pytest.approx(7499.8, abs=0.5)

    def test_ungrouped_zones_are_excluded_from_applicability(self):
        df = itemprog.compute(
            _items_df(), {}, {}, ALL_ZONES,
            planned_over={}, room_qty_groups=_room_qty_groups(),
        )
        row = df[df.item_code == ITEM_CODE].iloc[0]
        assert row.rooms == 14   # not 21


class TestIssue3BackwardCompatibility:
    """Nothing changes for the cases this fix must NOT touch."""

    def test_explicit_item_rooms_entry_still_respected(self):
        """When item_rooms.json DOES restrict an item to a specific set of
        rooms, that explicit list must still win exactly as before --
        groups only add exceptions on top of it, never replace it."""
        rooms_svc = {ITEM_CODE: GROUPED_ZONES + ["extra_explicit_zone"]}
        all_rooms = ALL_ZONES + ["extra_explicit_zone"]
        df = itemprog.compute(
            _items_df(), {}, rooms_svc, all_rooms,
            planned_over={}, room_qty_groups=_room_qty_groups(),
        )
        row = df[df.item_code == ITEM_CODE].iloc[0]
        # explicit rooms ∪ group rooms = 14 grouped + 1 extra explicit room
        # the extra explicit room falls back to the item's own raw qty,
        # exactly as documented ("groups are exceptions on top of the
        # item's normal applicability")
        assert row.rooms == 15
        assert row.planned_total == pytest.approx(
            14 * GROUP_QTY + RAW_BOQ_QTY, abs=0.01)

    def test_no_room_qty_groups_at_all_is_unaffected(self):
        """The common case (no groups) must be byte-for-byte unchanged:
        uniform qty_per_room x every applicable room, same as before this
        fix existed."""
        df = itemprog.compute(
            _items_df(), {}, {}, ALL_ZONES,
            planned_over={}, room_qty_groups=None,
        )
        row = df[df.item_code == ITEM_CODE].iloc[0]
        assert row.rooms == 21
        assert row.planned_total == pytest.approx(RAW_BOQ_QTY * 21, abs=0.01)


class TestIssue3RoomBuckets:
    def test_buckets_total_matches_group_not_whole_project(self):
        prog_svc = {ITEM_CODE: {r: 1.0 for r in GROUPED_ZONES[:5]}}  # 5 done
        buckets = itemprog.room_buckets(
            ITEM_CODE, prog_svc, {}, ALL_ZONES,
            room_qty_groups=_room_qty_groups(),
        )
        assert buckets["total"] == 14           # not 21
        assert buckets["done"] == 5
        assert buckets["not_started"] == 9


class TestIssue3ProjectRoomStatus:
    def test_ungrouped_zones_not_marked_seen_for_this_item(self):
        services_data = [(
            _items_df(), {ITEM_CODE: {r: 1.0 for r in GROUPED_ZONES}},
            {}, {ITEM_CODE}, _room_qty_groups(),
        )]
        status = itemprog.project_room_status(services_data, ALL_ZONES)
        # only the 14 grouped zones were ever touched by this item
        assert status["done"] == 14
        assert status["not_started"] == 7
