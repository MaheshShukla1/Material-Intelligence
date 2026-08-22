"""Proves dpr.py generates the report FROM captured system events -- not
from copied example text. Uses a different project (Meridian Towers),
different floors, different activities than the site team's own jpeg, run
through the real structure.py Structure class and the real record_change /
group_for_export / build_workbook functions.
"""
import sys
sys.path.insert(0, "/home/claude/backend")
import structure
import dpr


def test_record_change_is_idempotent_same_day():
    log = []
    dpr.record_change(log, "2026-08-21", "Electrical", "9TH", "Wire Pulling", "3")
    dpr.record_change(log, "2026-08-21", "Electrical", "9TH", "Wire Pulling", "3")  # same slider dragged twice
    assert len(log) == 1, "dragging the same room's slider twice in a day must not duplicate the row"


def test_entries_for_range_filters_by_date():
    log = [
        {"date": "2026-08-20", "service": "Electrical", "floor": "9TH", "activity": "Wire Pulling", "room": "1"},
        {"date": "2026-08-21", "service": "Electrical", "floor": "9TH", "activity": "Wire Pulling", "room": "2"},
        {"date": "2026-08-22", "service": "Electrical", "floor": "9TH", "activity": "Wire Pulling", "room": "3"},
    ]
    out = dpr.entries_for_range(log, "2026-08-20", "2026-08-21")
    assert set(out.keys()) == {"2026-08-20", "2026-08-21"}
    assert len(out["2026-08-20"]) == 1 and len(out["2026-08-21"]) == 1


def test_full_pipeline_on_a_different_project_than_the_jpeg():
    # a real structure.py tree -- Meridian Towers, floors 9TH/11TH, rooms
    # 1-12 -- nothing here resembles the site team's own hand-typed example.
    s = structure.hotel("Meridian Towers", floors=["9TH", "11TH"],
                        room_labels=[str(i) for i in range(1, 13)])
    assert s.count_rooms() == 24

    # simulate a day of real slider drags -- this is what siteprogress.py's
    # existing progress-set route will call record_change() from, once wired.
    log = []
    today = "2026-08-21"
    drags = [
        ("Electrical", "9TH", "Wire Pulling", "3"),
        ("Electrical", "9TH", "Wire Pulling", "7"),
        ("Electrical", "9TH", "Wire Pulling", "12"),
        ("Electrical", "11TH", "Cable Tray Fixing", "1"),
        ("Plumbing", "9TH", "Conduit Laying", "3"),
        ("Plumbing", "9TH", "Conduit Laying", "4"),
    ]
    for svc, floor, act, room in drags:
        dpr.record_change(log, today, svc, floor, act, room)
    assert len(log) == 6

    by_date = dpr.entries_for_range(log, today)
    grouped = dpr.group_for_export(by_date[today], all_services=["Electrical", "HVAC", "Plumbing"])

    assert grouped["HVAC"] == []                       # untouched today -> NO ACTIVITY in the sheet
    assert grouped["Electrical"][0][:2] == ("9TH", "WIRE PULLING IN ROOM NO 3,7,12")
    assert grouped["Electrical"][1][:2] == ("11TH", "CABLE TRAY FIXING IN ROOM NO 1")
    assert grouped["Plumbing"][0][:2] == ("9TH", "CONDUIT LAYING IN ROOM NO 3,4")

    wb = dpr.build_workbook([(today, grouped)])
    ws = wb.active
    assert ws.cell(1, 2).value == today
    assert ws.cell(3, 1).value == "ELECTRICAL"
    assert ws.cell(5, 1).value == "9TH"
    assert ws.cell(5, 2).value == "WIRE PULLING IN ROOM NO 3,7,12"
    wb.save("/home/claude/dpr/DPR_meridian_21Aug2026.xlsx")


def test_multi_day_range_stacks_blocks_in_one_sheet():
    log = []
    dpr.record_change(log, "2026-08-20", "Electrical", "9TH", "Wire Pulling", "1")
    dpr.record_change(log, "2026-08-21", "Plumbing", "11TH", "Conduit Laying", "5")
    by_date = dpr.entries_for_range(log, "2026-08-20", "2026-08-21")
    days = [(d, dpr.group_for_export(by_date.get(d, []), ["Electrical", "Plumbing"]))
           for d in ["2026-08-20", "2026-08-21"]]
    wb = dpr.build_workbook(days)
    ws = wb.active
    dates_found = [c.value for row in ws.iter_rows() for c in row if c.value in ("2026-08-20", "2026-08-21")]
    assert dates_found == ["2026-08-20", "2026-08-21"], "both day blocks present, in order"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-v", __file__]))
