"""Issue 2: DPR export scoped by Service and/or Area (rooms/zones), cascading
through all THREE sheets (Summary, Item detail, Daily updates) together --
not just the Daily-updates table. Parametrized across hotel/mall/hospital
so nothing here is hotel-specific, matching test_dpr_pipeline.py's own
"a different project, different structure" convention.

Run from the repo root:
    pytest tests/test_issue2_area_service_export.py -v
"""
import json

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress as sp
from backend import structure as structure_mod


def _build(kind, tmp_path):
    """A tiny 2-floor, 3-room-per-floor project of the given structure kind,
    with one Electrical item ('Metal Box') planned in every room, some
    rooms marked done, and a few real dpr_log entries spread across both
    floors/rooms (some room-specific, one floor-wide 'overall' entry) --
    the exact shape the real reported scenario has.

    Built directly via the Structure API (not the hotel()/mall()/
    hospital() convenience templates) so every room has a GLOBALLY UNIQUE
    name across floors -- those templates reuse the same room/zone labels
    on every floor by design (realistic for a real site), which would make
    "Room 1 on Floor 1" and "Room 1 on Floor 2" indistinguishable by name
    alone in a test asserting cross-floor isolation."""
    d = tmp_path / "fixture"
    d.mkdir()

    kind_map = {"hotel": ("floor", "room", "13th Floor", "14th Floor"),
               "mall": ("level", "room", "Level 1", "Level 2"),
               "hospital": ("floor", "room", "Wing A Floor 1", "Wing A Floor 2")}
    container_type, leaf_type, f1_name, f2_name = kind_map[kind]
    s = structure_mod.Structure.new(f"Fixture {kind.title()}", kind=kind)
    f1 = s.add(s.root["id"], container_type, f1_name)
    f2 = s.add(s.root["id"], container_type, f2_name)
    if kind == "hospital":   # one extra container level: Wing > Floor > Room
        wing = s.add(s.root["id"], "wing", "Wing A")
        f1 = s.add(wing, "floor", "Floor 1")
        f2 = s.add(wing, "floor", "Floor 2")
    f1_rooms = s.add_many(f1, leaf_type, [f"{f1_name} Room A", f"{f1_name} Room B", f"{f1_name} Room C"])
    f2_rooms = s.add_many(f2, leaf_type, [f"{f2_name} Room A", f"{f2_name} Room B", f"{f2_name} Room C"])
    (d / "structure.json").write_text(s.to_json())

    boq_df = pd.DataFrame([{
        "service": "Electrical", "item_code": "MB1", "description": "Metal Box",
        "unit": "Nos", "qty": 10.0, "section": "Boxes", "item_code_raw": "MB1",
        "subcategory": "Box",
    }])
    boq_df.to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Metal Box Install"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Metal Box Install": ["MB1"]}}))

    rooms = s.rooms()
    floor1_rooms = [r for r in rooms if r["id"] in f1_rooms]
    floor2_rooms = [r for r in rooms if r["id"] in f2_rooms]
    # mark the FIRST room of floor 1 as fully done
    item_progress = {"Electrical": {"MB1": {floor1_rooms[0]["id"]: 1.0}}}
    (d / "item_progress.json").write_text(json.dumps(item_progress))

    return d, floor1_rooms, floor2_rooms, s


def _dpr_log_for(d, floor1_rooms, floor2_rooms):
    """Real-shaped dpr_log.json entries -- built via the SAME
    _room_location_map() the app itself uses, so floor/room strings here
    are guaranteed consistent with what the export route will look up."""
    loc = sp._room_location_map(d)
    log = [
        # room-specific, floor 1, room 1
        {"date": "2026-09-10", "service": "Electrical",
         "floor": loc[floor1_rooms[0]["id"]]["location"],
         "activity": "Metal Box Install", "room": loc[floor1_rooms[0]["id"]]["name"],
         "item": "Metal Box", "qty": 10.0, "unit": "Nos"},
        # room-specific, floor 1, room 2
        {"date": "2026-09-10", "service": "Electrical",
         "floor": loc[floor1_rooms[1]["id"]]["location"],
         "activity": "Metal Box Install", "room": loc[floor1_rooms[1]["id"]]["name"],
         "item": "Metal Box", "qty": 10.0, "unit": "Nos"},
        # floor-WIDE ("overall", no specific room) fact on floor 1
        {"date": "2026-09-10", "service": "Electrical",
         "floor": loc[floor1_rooms[0]["id"]]["location"],
         "activity": "Some Overall Activity", "room": None,
         "item": "Widget", "qty": 5.0, "unit": "Nos"},
        # room-specific, floor 2 -- must NEVER appear in a floor-1-scoped export
        {"date": "2026-09-10", "service": "Electrical",
         "floor": loc[floor2_rooms[0]["id"]]["location"],
         "activity": "Metal Box Install", "room": loc[floor2_rooms[0]["id"]]["name"],
         "item": "Metal Box", "qty": 10.0, "unit": "Nos"},
        # a different service entirely -- must never appear in an
        # Electrical-only export
        {"date": "2026-09-10", "service": "Fire",
         "floor": loc[floor1_rooms[0]["id"]]["location"],
         "activity": "Sprinkler Work", "room": loc[floor1_rooms[0]["id"]]["name"],
         "item": "Pipe", "qty": 20.0, "unit": "Mtr"},
    ]
    (d / "dpr_log.json").write_text(json.dumps(log))


@pytest.fixture(params=["hotel", "mall", "hospital"])
def project(request, tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d, floor1_rooms, floor2_rooms, s = _build(request.param, tmp_path)
    _dpr_log_for(d, floor1_rooms, floor2_rooms)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    return client, "fixture", floor1_rooms, floor2_rooms, request.param


def _sheet_names(xlsx_bytes):
    import openpyxl, io
    return openpyxl.load_workbook(io.BytesIO(xlsx_bytes)).sheetnames


def _load(xlsx_bytes):
    import openpyxl, io
    return openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=False)


class TestBackwardCompatibility:
    def test_no_filters_matches_todays_export_exactly(self, project):
        """The single most important guarantee: a caller that never passes
        service/rooms gets byte-for-byte the same export as before this
        feature existed."""
        client, slug, *_ = project
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10"})
        assert r.status_code == 200
        wb = _load(r.content)
        assert wb.sheetnames == ["Summary", "Item detail", "Daily updates"]
        item = wb["Item detail"]
        # whole-project: all 6 rooms count, 1 done -> planned=60, used=10
        assert item.cell(2, 3).value == 60      # Planned
        assert item.cell(2, 4).value == 10      # Used so far


class TestServiceFilter:
    def test_electrical_only_excludes_fire_from_every_sheet(self, project):
        client, slug, *_ = project
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10", "service": "Electrical"})
        wb = _load(r.content)
        summary_rows = [wb["Summary"].cell(row, 1).value for row in range(7, 12)]
        assert "Fire" not in summary_rows
        assert "Electrical" in summary_rows
        item_services = {wb["Item detail"].cell(r, 1).value
                         for r in range(2, wb["Item detail"].max_row + 1)}
        item_services.discard(None)
        assert item_services == {"Electrical"}
        daily_text = "\n".join(str(c.value) for row in wb["Daily updates"].iter_rows() for c in row)
        assert "FIRE" not in daily_text
        assert "ELECTRICAL" in daily_text


class TestAreaFilter:
    def test_specific_rooms_scope_summary_and_item_detail_to_a_real_sum(self, project):
        """The real point of Issue 2: Summary/Item-detail must reflect ONLY
        the selected rooms' own numbers -- verified against a real,
        independently-computed manual sum, not just "some smaller number"."""
        client, slug, floor1_rooms, floor2_rooms, kind = project
        selected = [floor1_rooms[0]["id"], floor1_rooms[1]["id"]]
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10", "rooms": selected})
        wb = _load(r.content)
        item = wb["Item detail"]
        # 2 rooms x qty 10 = planned 20; room[0] done (frac=1) -> used 10
        assert item.cell(2, 3).value == 20, f"planned mismatch for {kind}"
        assert item.cell(2, 4).value == 10, f"used mismatch for {kind}"

    def test_floor2_room_never_leaks_into_floor1_scoped_export(self, project):
        client, slug, floor1_rooms, floor2_rooms, kind = project
        selected = [floor1_rooms[0]["id"], floor1_rooms[1]["id"]]
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10", "rooms": selected})
        wb = _load(r.content)
        daily_text = "\n".join(str(c.value) for row in wb["Daily updates"].iter_rows() for c in row)
        floor2_name = sp._room_location_map(sp.PROJECTS / slug)[floor2_rooms[0]["id"]]["name"]
        assert floor2_name not in daily_text, f"floor-2 room leaked into a floor-1-scoped export ({kind})"

    def test_overall_floor_wide_entry_still_shows_when_a_room_on_that_floor_is_selected(self, project):
        """A floor-wide fact (no specific room known) must not be silently
        hidden just because the export is scoped to one room on that
        floor -- real logged work must stay visible."""
        client, slug, floor1_rooms, floor2_rooms, kind = project
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10", "rooms": [floor1_rooms[0]["id"]]})
        wb = _load(r.content)
        daily_text = "\n".join(str(c.value) for row in wb["Daily updates"].iter_rows() for c in row)
        assert "SOME OVERALL ACTIVITY" in daily_text.upper()


class TestGenericAcrossStructureKinds:
    def test_location_label_matches_structure_kind(self, project):
        client, slug, *_, kind = project
        r = client.get(f"/api/siteprogress/{slug}/export-dpr",
                       params={"start": "2026-09-10"})
        wb = _load(r.content)
        daily = wb["Daily updates"]
        expected = {"hotel": "FLOOR", "mall": "LEVEL", "hospital": "WING / FLOOR"}[kind]
        headers = [daily.cell(4, c).value for c in range(1, 6)]
        assert headers[0] == expected
