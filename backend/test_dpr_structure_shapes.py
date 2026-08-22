"""Proves the DPR pipeline is correct for all three real structure.py
shapes, not just hotel -- mall (Level > Zone) and hospital (Wing > Floor >
Room, one level deeper than hotel/mall) are the real edge cases.
"""
import sys
sys.path.insert(0, "/home/claude/backend")
import structure
import dpr


def _location_for(room, s):
    """What the caller (eventually siteprogress.py) will build for
    record_change()'s location string: the room's own container path,
    minus the project name at path[0], joined into one string."""
    return " · ".join(room["path"][1:])


def test_hotel_single_level_path_and_label():
    s = structure.hotel("Hyatt", floors=["13TH", "14TH"], room_labels=["5", "14"])
    room = s.rooms()[0]
    assert _location_for(room, s) == "13TH"
    assert dpr.LOCATION_LABEL[s.root["kind"]] == "FLOOR"


def test_mall_uses_level_not_floor():
    s = structure.mall("Thoth Mall", levels=["B1", "B2"], zone_labels=["Zone 1", "Zone 2"])
    room = s.rooms()[0]
    assert _location_for(room, s) == "B1"
    assert dpr.LOCATION_LABEL[s.root["kind"]] == "LEVEL"


def test_hospital_two_level_path_never_loses_the_wing():
    s = structure.hospital("City Hospital", wings=["Wing A", "Wing B"],
                           floors=["Floor 1", "Floor 2"], rooms_per_floor=2)
    room = s.rooms()[0]
    # this is the real edge case -- hospital has ONE MORE container level
    # than hotel/mall (Wing, then Floor), so the joined string must carry
    # BOTH, not silently drop the wing the way a flat "floor" field would.
    assert _location_for(room, s) == "Wing A · Floor 1"
    assert dpr.LOCATION_LABEL[s.root["kind"]] == "WING / FLOOR"


def test_full_pipeline_and_export_header_for_a_hospital():
    s = structure.hospital("City Hospital", wings=["Wing A"], floors=["Floor 3"], rooms_per_floor=3)
    rooms = {r["name"]: _location_for(r, s) for r in s.rooms()}
    assert rooms["Room 1"] == "Wing A · Floor 3"

    log = []
    today = "2026-08-21"
    dpr.record_change(log, today, "Electrical", rooms["Room 1"], "Wire Pulling", "Room 1")
    dpr.record_change(log, today, "Electrical", rooms["Room 2"], "Wire Pulling", "Room 2")
    grouped = dpr.group_for_export(
        dpr.entries_for_range(log, today)[today], all_services=["Electrical"])
    assert grouped["Electrical"][0][:2] == ("Wing A · Floor 3", "WIRE PULLING IN ROOM NO Room 1,Room 2")

    wb = dpr.build_workbook([(today, grouped)], location_label=dpr.LOCATION_LABEL[s.root["kind"]])
    ws = wb.active
    assert ws.cell(4, 1).value == "WING / FLOOR"   # header follows the project's real shape
    assert ws.cell(5, 1).value == "Wing A · Floor 3"


def test_full_pipeline_for_a_mall_reuses_thoth_mall_shape():
    s = structure.mall("Thoth Mall", levels=["B1", "B2", "B3"], zone_labels=[f"Zone {i}" for i in range(1, 8)])
    assert s.count_rooms() == 21   # matches the real "21 zones" from earlier screenshots this session

    log = []
    today = "2026-08-21"
    dpr.record_change(log, today, "Electrical", "B1", "Cable Tray", "Zone 2")
    dpr.record_change(log, today, "Electrical", "B1", "Cable Tray", "Zone 3")
    grouped = dpr.group_for_export(
        dpr.entries_for_range(log, today)[today], all_services=["Electrical", "HVAC"])
    wb = dpr.build_workbook([(today, grouped)], location_label=dpr.LOCATION_LABEL[s.root["kind"]])
    ws = wb.active
    assert ws.cell(4, 1).value == "LEVEL"
    assert ws.cell(5, 1).value == "B1"
    assert ws.cell(5, 2).value == "CABLE TRAY IN ROOM NO Zone 2,Zone 3"
    wb.save("/home/claude/dpr/DPR_mall_shape_check.xlsx")


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-v", __file__]))
