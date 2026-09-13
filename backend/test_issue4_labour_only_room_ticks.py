"""Issue 4: labour-only activities (Zari Work, core-cutting, chasing,
testing...) are now tracked per-room (tick + Mark done/Undo), the same
mechanism items already use via /mark-rooms-done -- not a single overall
%-slider that silently wiped every per-room tick the moment it was
touched (itemprog.set_progress()'s own documented "*" behaviour).

Run from the repo root (same level as the backend/ package):
    pytest tests/test_issue4_labour_only_room_ticks.py -v
"""
import json

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress as sp
from backend import structure as structure_mod


@pytest.fixture
def zari_project(tmp_path, monkeypatch):
    """A tiny 4-room hotel, one labour-only activity ('Zari Work') in
    Electrical, no BOQ items mapped to it at all -- exactly the reported
    shape."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d = tmp_path / "fixture"
    d.mkdir()

    s = structure_mod.hotel("Fixture", floors=["Floor 1"],
                            room_labels=["Room 1", "Room 2", "Room 3", "Room 4"])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]

    # a real BOQ item must exist for _load_boq/items_df not to 404, even
    # though Zari Work itself has none mapped to it
    boq_df = pd.DataFrame([{
        "service": "Electrical", "item_code": "2.1", "description": "conduit",
        "unit": "Mtr", "qty": 10.0, "section": "Wiring", "item_code_raw": "2.1",
        "subcategory": "Pipe",
    }])
    boq_df.to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Zari Work"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Zari Work": []}}))
    (d / "labour_only.json").write_text(json.dumps({"Electrical": ["Zari Work"]}))

    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    return client, "fixture", room_ids


def test_ticking_some_rooms_done_computes_a_real_mean_percent(zari_project):
    """The exact reported need: room/zone-tickable, not one flat number.
    2 of 4 rooms marked done -> overall % must be 50, not some flat
    manually-set number."""
    client, slug, room_ids = zari_project
    r = client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
                    json={"service": "Electrical", "activity": "Zari Work",
                          "rooms": room_ids[:2], "done": True})
    assert r.status_code == 200
    assert r.json()["act_pct"]["Zari Work"] == 50.0


def test_marking_more_rooms_done_never_wipes_earlier_ticks(zari_project):
    """The actual bug the old slider had: itemprog.set_progress(room=None)
    wipes every per-room override. Marking a SECOND batch of rooms done
    must never erase the first batch's recorded progress."""
    client, slug, room_ids = zari_project
    client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
               json={"service": "Electrical", "activity": "Zari Work",
                     "rooms": [room_ids[0]], "done": True})
    r = client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
                    json={"service": "Electrical", "activity": "Zari Work",
                          "rooms": [room_ids[1]], "done": True})
    assert r.json()["act_pct"]["Zari Work"] == 50.0, \
        "FAIL: room_ids[0]'s earlier done-mark must still count"
    # confirm on disk too, not just the response
    stored = json.loads((sp.PROJECTS / slug / "activity_progress.json").read_text())
    assert stored["Electrical"]["Zari Work"][room_ids[0]] == 1.0
    assert stored["Electrical"]["Zari Work"][room_ids[1]] == 1.0


def test_undo_reverts_just_the_ticked_rooms(zari_project):
    client, slug, room_ids = zari_project
    client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
               json={"service": "Electrical", "activity": "Zari Work",
                     "rooms": room_ids, "done": True})   # all 4 done -> 100%
    r = client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
                    json={"service": "Electrical", "activity": "Zari Work",
                          "rooms": [room_ids[0]], "done": False})
    assert r.json()["act_pct"]["Zari Work"] == 75.0


def test_room_scoped_view_shows_just_that_rooms_own_fraction(zari_project):
    client, slug, room_ids = zari_project
    client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
               json={"service": "Electrical", "activity": "Zari Work",
                     "rooms": [room_ids[0]], "done": True})
    r = client.get(f"/api/siteprogress/{slug}/service/Electrical",
                   params={"room": room_ids[0]}).json()
    assert r["act_pct"]["Zari Work"] == 100.0
    r2 = client.get(f"/api/siteprogress/{slug}/service/Electrical",
                    params={"room": room_ids[1]}).json()
    assert r2["act_pct"]["Zari Work"] == 0.0


def test_backward_compatible_with_old_flat_star_only_data(zari_project):
    """An activity that only ever had the OLD overall slider touched (a
    flat '*' fraction, no per-room ticks at all) must still read exactly
    the same overall % as before this change -- frac_for() falls back to
    '*' for every room, so the mean collapses back to that one number."""
    client, slug, room_ids = zari_project
    (sp.PROJECTS / slug / "activity_progress.json").write_text(
        json.dumps({"Electrical": {"Zari Work": {"*": 0.69}}}))
    r = client.get(f"/api/siteprogress/{slug}/service/Electrical").json()
    assert r["act_pct"]["Zari Work"] == 69.0, \
        "FAIL: legacy flat '*' data must read unchanged (backward compatibility)"


def test_activity_progress_exposed_for_the_rooms_modal(zari_project):
    """The frontend's Rooms modal needs the raw per-room data to show
    which rooms are already ticked (the small checkmark) -- it must be in
    the service-view response, the same way item_progress already is."""
    client, slug, room_ids = zari_project
    client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
               json={"service": "Electrical", "activity": "Zari Work",
                     "rooms": [room_ids[0]], "done": True})
    r = client.get(f"/api/siteprogress/{slug}/service/Electrical").json()
    assert "activity_progress" in r
    assert r["activity_progress"]["Zari Work"][room_ids[0]] == 1.0


def test_labour_buckets_exposed_for_the_rooms_chip_label(zari_project):
    client, slug, room_ids = zari_project
    client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
               json={"service": "Electrical", "activity": "Zari Work",
                     "rooms": room_ids[:3], "done": True})
    r = client.get(f"/api/siteprogress/{slug}/service/Electrical").json()
    b = r["labour_buckets"]["Zari Work"]
    assert b == {"done": 3, "in_progress": 0, "not_started": 1, "total": 4}


def test_mark_activity_rooms_done_requires_at_least_one_room(zari_project):
    client, slug, room_ids = zari_project
    r = client.post(f"/api/siteprogress/{slug}/mark-activity-rooms-done",
                    json={"service": "Electrical", "activity": "Zari Work", "rooms": []})
    assert r.status_code == 400
