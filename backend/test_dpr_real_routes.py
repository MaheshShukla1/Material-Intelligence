"""End-to-end test of the real DPR wiring: the two Layer-1 capture hooks
(/progress/item, /mark-rooms-done), the today-count route, and the full
export route -- through the real FastAPI router, against a real project
fixture (structure.json + boq.parquet), not the standalone dpr.py unit
tests. This is what actually ships.
"""
import sys, json, shutil
from pathlib import Path
sys.path.insert(0, "/home/claude")

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure

SLUG = "test-hyatt"
PROJECT_DIR = siteprogress.PROJECTS / SLUG


def _reset_fixture():
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True)

    # Hyatt-shaped hotel: two floors, two rooms each -- matches this
    # session's real screenshots (13TH/14TH floors, numbered rooms).
    s = structure.hotel("Hyatt Hotel", floors=["13TH", "14TH"], room_labels=["5", "14"])
    (PROJECT_DIR / "structure.json").write_text(s.to_json())

    boq_df = pd.DataFrame([
        {"service": "Electrical", "item_code": "E1", "description": "POINT WIRING",
         "unit": "Nos", "qty": 1.0, "section": None, "item_code_raw": "E1", "subcategory": "Wiring"},
        {"service": "Electrical", "item_code": "E2", "description": "UNMAPPED SPARE ITEM",
         "unit": "Nos", "qty": 1.0, "section": None, "item_code_raw": "E2", "subcategory": "Wiring"},
    ])
    boq_df.to_parquet(PROJECT_DIR / "boq.parquet")
    (PROJECT_DIR / "rates.json").write_text(json.dumps({"Electrical": {"E1": 500.0, "E2": 200.0}}))
    (PROJECT_DIR / "activities.json").write_text(json.dumps({"Electrical": ["Point Wiring"]}))
    (PROJECT_DIR / "mapping.json").write_text(json.dumps({"Electrical": {"Point Wiring": ["E1"]}}))
    # E2 deliberately left OUT of mapping.json -- this is the "39 items not
    # in any activity" case from the real screenshots, and must not show up
    # in the Item detail export.

    # real Room-Detail tick data -- Zari Work, 4 rooms: 3 done, 1 pending,
    # matching the shape (not the exact numbers) of the real Activity
    # Completion Summary screenshot.
    prog_rows = [
        {"service": "Electrical", "floor": "13TH", "activity": "Zari Work", "room": "5", "tick": "✓", "frac": 1.0},
        {"service": "Electrical", "floor": "13TH", "activity": "Zari Work", "room": "14", "tick": "✓", "frac": 1.0},
        {"service": "Electrical", "floor": "13TH", "activity": "Zari Work", "room": "6", "tick": "✗", "frac": 0.0},
        {"service": "Electrical", "floor": "14TH", "activity": "Zari Work", "room": "5", "tick": "✓", "frac": 1.0},
    ]
    pd.DataFrame(prog_rows).to_parquet(PROJECT_DIR / "progress.parquet")
    return s


app = FastAPI()
app.include_router(siteprogress.router)
client = TestClient(app)


def _room_id(s, floor_name, room_name):
    for r in s.rooms():
        if r["path"][-1] == floor_name and r["name"] == room_name:
            return r["id"]
    raise KeyError((floor_name, room_name))


def test_progress_item_route_logs_a_dpr_change_for_a_specific_room():
    s = _reset_fixture()
    rid = _room_id(s, "13TH", "5")
    r = client.post(f"/api/siteprogress/{SLUG}/progress/item",
                    json={"service": "Electrical", "item_code": "E1", "frac": 1.0, "room": rid})
    assert r.status_code == 200

    log = json.loads((PROJECT_DIR / "dpr_log.json").read_text())
    assert len(log) == 1
    assert log[0]["service"] == "Electrical"
    assert log[0]["floor"] == "13TH"
    assert log[0]["activity"] == "Point Wiring"
    assert log[0]["room"] == "5"


def test_progress_item_route_does_not_log_the_star_catch_all():
    """room omitted -> the '*' overall slider. Never attributable to one
    room, so never logged -- this is the honest behaviour, not a bug."""
    s = _reset_fixture()
    r = client.post(f"/api/siteprogress/{SLUG}/progress/item",
                    json={"service": "Electrical", "item_code": "E1", "frac": 0.5})
    assert r.status_code == 200
    log_path = PROJECT_DIR / "dpr_log.json"
    assert not log_path.exists() or json.loads(log_path.read_text()) == []


def test_mark_rooms_done_logs_each_room_only_when_done_true():
    s = _reset_fixture()
    r1 = _room_id(s, "13TH", "5")
    r2 = _room_id(s, "13TH", "14")
    client.post(f"/api/siteprogress/{SLUG}/mark-rooms-done",
               json={"service": "Electrical", "item_code": "E1", "rooms": [r1, r2], "done": True})
    log = json.loads((PROJECT_DIR / "dpr_log.json").read_text())
    assert {e["room"] for e in log} == {"5", "14"}

    # now undo -- must NOT add a second "work done" entry for the undo itself
    client.post(f"/api/siteprogress/{SLUG}/mark-rooms-done",
               json={"service": "Electrical", "item_code": "E1", "rooms": [r1], "done": False})
    log2 = json.loads((PROJECT_DIR / "dpr_log.json").read_text())
    assert len(log2) == 2   # unchanged -- the undo logged nothing new


def test_dpr_today_count_matches_export_row_count():
    s = _reset_fixture()
    r1 = _room_id(s, "13TH", "5")
    r2 = _room_id(s, "13TH", "14")
    client.post(f"/api/siteprogress/{SLUG}/mark-rooms-done",
               json={"service": "Electrical", "item_code": "E1", "rooms": [r1, r2], "done": True})
    today = client.get(f"/api/siteprogress/{SLUG}/dpr/today").json()
    assert today["count"] == 1   # same floor + same activity -> ONE row (rooms comma-joined)


def test_export_dpr_single_day_downloads_a_real_workbook():
    s = _reset_fixture()
    rid = _room_id(s, "13TH", "5")
    client.post(f"/api/siteprogress/{SLUG}/progress/item",
               json={"service": "Electrical", "item_code": "E1", "frac": 1.0, "room": rid})
    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "attachment" in r.headers["content-disposition"]

    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Summary", "Activity completion", "Item detail", "Daily updates"]
    daily = wb["Daily updates"]
    assert daily.cell(4, 1).value == "FLOOR"          # hotel -> FLOOR, real structure.kind
    assert daily.cell(5, 1).value == "13TH"
    assert "POINT WIRING" in daily.cell(5, 2).value
    detail = wb["Item detail"]
    assert detail.cell(2, 1).value == "Electrical"
    assert detail.cell(2, 6).value == 1               # 1 room done, real itemprog.room_buckets


def test_export_dpr_range_stacks_multiple_day_blocks():
    _reset_fixture()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr",
                   params={"start": "2026-08-19", "end": "2026-08-21"})
    assert r.status_code == 200
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    daily = wb["Daily updates"]
    dates_found = [c.value for row in daily.iter_rows() for c in row
                  if c.value in ("2026-08-19", "2026-08-20", "2026-08-21")]
    assert dates_found == ["2026-08-19", "2026-08-20", "2026-08-21"]


def test_export_dpr_uses_location_label_from_structure_kind():
    """A mall project (Level/Zone, no 'floor' at all) gets a LEVEL header,
    not a hardcoded FLOOR -- the same real bug this session already caught
    and fixed in dpr.py, now proven through the actual route."""
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True)
    s = structure.mall("Thoth Mall", levels=["B1"], zone_labels=["Zone 1"])
    (PROJECT_DIR / "structure.json").write_text(s.to_json())
    boq_df = pd.DataFrame([{"service": "Electrical", "item_code": "Q1", "description": "CABLE TRAY",
                           "unit": "MTR", "qty": 10.0, "section": None,
                           "item_code_raw": "Q1", "subcategory": "Cable Tray"}])
    boq_df.to_parquet(PROJECT_DIR / "boq.parquet")

    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb["Daily updates"].cell(4, 1).value == "LEVEL"


def test_export_dpr_excludes_unmapped_items_from_item_detail():
    _reset_fixture()
    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    detail = wb["Item detail"]
    items_listed = [detail.cell(row, 2).value for row in range(2, detail.max_row + 1)]
    assert "POINT WIRING" in items_listed
    assert "UNMAPPED SPARE ITEM" not in items_listed   # E2 has no activity -> excluded


def test_export_dpr_includes_activity_completion_from_real_tick_data():
    _reset_fixture()
    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert "Activity completion" in wb.sheetnames
    ac = wb["Activity completion"]
    # find the Zari Work row and confirm real counts (3 done, 1 pending of 4)
    found = None
    for row in range(1, ac.max_row + 1):
        if ac.cell(row, 1).value == "Zari Work":
            found = row
            break
    assert found is not None, "Zari Work row present"
    assert ac.cell(found, 2).value == 4    # total
    assert ac.cell(found, 3).value == 3    # done
    assert ac.cell(found, 5).value == 1    # pending
    assert round(ac.cell(found, 6).value * 100, 2) == 75.0   # % done


def test_export_dpr_floor_rows_present_and_collapsed():
    _reset_fixture()
    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ac = wb["Activity completion"]
    floor_rows = [row for row in range(1, ac.max_row + 1)
                 if str(ac.cell(row, 1).value or "").strip() in ("13TH", "14TH")]
    assert len(floor_rows) == 2
    for row in floor_rows:
        assert ac.row_dimensions[row].outlineLevel == 1
        assert ac.row_dimensions[row].hidden is True


def test_export_dpr_summary_sheet_has_a_generated_timestamp():
    _reset_fixture()
    today = __import__("datetime").date.today().isoformat()
    r = client.get(f"/api/siteprogress/{SLUG}/export-dpr", params={"start": today})
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert "Generated" in wb["Summary"].cell(2, 1).value


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-v", __file__]))
