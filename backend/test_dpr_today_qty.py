"""DPR "daily updates" must show only the work done THAT day, not the cumulative quantity (the reported bug: a site engineer
who had put 70 Rmt of a 100 Rmt tray run on Monday saw 100 -- not the 30 done on Tuesday -- in Tuesday's daily update).

Through the real FastAPI router and real fixtures (structure + boq.parquet + mapping), saving on different days (the clock is moved)."""
import datetime as _dt
import json
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import dpr, siteprogress as sp, structure

SVC = "Electrical"
URL = "/api/siteprogress/thoth"


class Clock:
    day = _dt.date(2026, 10, 12)


class FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return Clock.day


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    ns = types.SimpleNamespace(**{k: getattr(_dt, k) for k in dir(_dt) if not k.startswith("__")})
    ns.date = FakeDate
    monkeypatch.setattr(sp, "dt", ns)
    Clock.day = _dt.date(2026, 10, 12)
    d = tmp_path / "thoth"
    d.mkdir()
    # a mall: 2 levels x 2 zones = 4 zones; one tray item of 100 Rmt over all 4 zones (25 Rmt a zone), a second item of 40 Nos
    s = structure.mall("Thoth Mall", levels=["Ground", "First"], zones_per_level=2)
    (d / "structure.json").write_text(s.to_json())
    pd.DataFrame([
        {"service": SVC, "item_code": "T1", "description": "Cable tray 300 mm", "unit": "Rmt", "qty": 100.0 / 4,
         "section": None, "item_code_raw": "T1", "subcategory": "Tray"},
        {"service": SVC, "item_code": "T2", "description": "Light fitting", "unit": "Nos", "qty": 10.0,
         "section": None, "item_code_raw": "T2", "subcategory": "Light"},
    ]).to_parquet(d / "boq.parquet")
    (d / "rates.json").write_text(json.dumps({SVC: {"T1": 100.0, "T2": 50.0}}))
    (d / "activities.json").write_text(json.dumps({SVC: ["Tray laying", "Lighting"]}))
    (d / "mapping.json").write_text(json.dumps({SVC: {"Tray laying": ["T1"], "Lighting": ["T2"]}}))
    app = FastAPI()
    app.include_router(sp.router)
    c = TestClient(app)
    zones = [r["id"] for r in s.rooms()]
    return c, d, zones, s


def log_of(d):
    return json.loads((d / "dpr_log.json").read_text())


def today_of(d, day, **match):
    """qty_today of the single log line of `day` matching `match`."""
    rows = [e for e in log_of(d) if e["date"] == day and all(e.get(k) == v for k, v in match.items())]
    assert len(rows) == 1, rows
    return dpr.qty_today(rows[0])


def save(c, code, frac, room=None):
    body = {"service": SVC, "item_code": code, "frac": frac}
    if room:
        body["room"] = room
    r = c.post(f"{URL}/progress/item", json=body)
    assert r.status_code == 200, r.text


def test_the_reported_case_tuesdays_update_shows_only_tuesdays_quantity(site):
    """Monday a zone goes to 70%, Tuesday to 100%: Monday = 17.5 Rmt, Tuesday = 7.5 Rmt (cumulative 25 is NOT what 'today' shows)."""
    c, d, z, s = site
    save(c, "T1", 0.7, z[0])
    Clock.day = _dt.date(2026, 10, 13)
    save(c, "T1", 1.0, z[0])
    assert today_of(d, "2026-10-12", room=s.rooms()[0]["name"]) == 17.5
    assert today_of(d, "2026-10-13", room=s.rooms()[0]["name"]) == 7.5
    # the legacy cumulative field is still there and unchanged
    assert [e["qty"] for e in log_of(d)] == [17.5, 25.0]


def test_several_saves_the_same_day_add_up_to_that_days_work_not_double_counted(site):
    c, d, z, s = site
    save(c, "T1", 0.3, z[0])
    save(c, "T1", 0.7, z[0])
    save(c, "T1", 0.6, z[0])        # the engineer corrected himself the same day
    assert today_of(d, "2026-10-12") == 15.0          # 60% of 25, from 0%
    assert len(log_of(d)) == 1


def test_reducing_a_figure_gives_a_negative_correction_not_a_hidden_zero(site):
    c, d, z, s = site
    save(c, "T1", 0.8, z[0])
    Clock.day = _dt.date(2026, 10, 13)
    save(c, "T1", 0.6, z[0])
    assert today_of(d, "2026-10-13") == -5.0


def test_the_overall_slider_logs_each_floor_with_only_the_increase(site):
    """Overall '*' 0 -> 40% on Monday, 40 -> 100% on Tuesday: each level has 2 zones x 25 Rmt = 50 Rmt."""
    c, d, z, s = site
    save(c, "T1", 0.4)
    Clock.day = _dt.date(2026, 10, 13)
    save(c, "T1", 1.0)
    mon = [e for e in log_of(d) if e["date"] == "2026-10-12"]
    tue = [e for e in log_of(d) if e["date"] == "2026-10-13"]
    assert sorted(dpr.qty_today(e) for e in mon) == [20.0, 20.0]
    assert sorted(dpr.qty_today(e) for e in tue) == [30.0, 30.0]
    assert sorted(e["qty"] for e in tue) == [50.0, 50.0]       # cumulative still recorded as before
    assert sum(dpr.qty_today(e) for e in mon + tue) == 100.0   # the days add up to the whole item


def test_overall_slider_after_zone_updates_keeps_the_days_adding_up_to_the_truth(site):
    """Zone A goes to 50% on its own, then the overall slider is set to 30% (it wipes the per-zone value, itemprog's documented behaviour).
    The sum of the day's lines must be the real net move: 30% of all four zones = 30 Rmt."""
    c, d, z, s = site
    save(c, "T1", 0.5, z[0])
    save(c, "T1", 0.3)
    assert sum(dpr.qty_today(e) for e in log_of(d) if e["date"] == "2026-10-12") == pytest.approx(30.0)


def test_mark_rooms_done_logs_only_the_zones_that_actually_moved(site):
    c, d, z, s = site
    save(c, "T1", 1.0, z[0])                                    # zone 1 already done on Monday
    Clock.day = _dt.date(2026, 10, 13)
    r = c.post(f"{URL}/mark-rooms-done", json={"service": SVC, "item_code": "T1", "rooms": [z[0], z[1]], "done": True})
    assert r.status_code == 200
    names = {x["id"]: x["name"] for x in s.rooms()}
    assert today_of(d, "2026-10-13", room=names[z[0]]) == 0.0           # re-ticking a finished zone is no new work
    assert today_of(d, "2026-10-13", room=names[z[1]]) == 25.0


def test_planned_quantity_override_is_used_like_everywhere_else(site):
    c, d, z, s = site
    (d / "planned.json").write_text(json.dumps({SVC: {"T1": 80.0}}))        # the pencil edit: 80 Rmt in total -> 20 a zone
    save(c, "T1", 0.5, z[0])
    assert today_of(d, "2026-10-12") == 10.0


def test_zone_wise_quantities_are_respected(site):
    c, d, z, s = site
    (d / "item_room_qty.json").write_text(json.dumps({SVC: {"T1": [{"rooms": [z[0]], "qty": 60.0}, {"rooms": [z[1], z[2], z[3]], "qty": 10.0}]}}))
    save(c, "T1", 0.5, z[0])
    assert today_of(d, "2026-10-12") == 30.0
    Clock.day = _dt.date(2026, 10, 13)
    save(c, "T1", 1.0, z[0])
    assert today_of(d, "2026-10-13") == 30.0


def test_quantity_above_the_boq_counts_the_extra_as_that_days_work(site):
    """Item of 100 Rmt reaches 100% on Monday; on Tuesday the engineer types 120 Rmt installed. Tuesday's work is the extra 20."""
    c, d, z, s = site
    c.post(f"{URL}/progress/item-qty", json={"service": SVC, "item_code": "T1", "qty": 100})
    Clock.day = _dt.date(2026, 10, 13)
    r = c.post(f"{URL}/progress/item-qty", json={"service": SVC, "item_code": "T1", "qty": 120})
    assert r.status_code == 200
    tue = [e for e in log_of(d) if e["date"] == "2026-10-13"]
    assert sum(dpr.qty_today(e) for e in tue) == pytest.approx(20.0)
    # and 'Back to BOQ qty' the day after is a -20 correction, not a fresh 100
    Clock.day = _dt.date(2026, 10, 14)
    assert c.post(f"{URL}/progress/item-qty/undo", json={"service": SVC, "item_code": "T1"}).status_code == 200
    wed = [e for e in log_of(d) if e["date"] == "2026-10-14"]
    assert sum(dpr.qty_today(e) for e in wed) == pytest.approx(-20.0)


def test_an_entry_from_before_this_existed_has_an_unknown_day_quantity_never_a_guess(site):
    c, d, z, s = site
    (d / "dpr_log.json").write_text(json.dumps([{"date": "2026-10-12", "service": SVC, "floor": "Ground", "activity": "Tray laying",
                                                 "room": "Zone 1", "item": "Cable tray 300 mm", "qty": 25.0, "unit": "Rmt", "item_code": "T1"}]))
    e = log_of(d)[0]
    assert dpr.qty_today(e) is None
    # a save later that same day updates the old line in place and still does not invent a baseline
    save(c, "T1", 0.5, z[0])
    rows = log_of(d)
    assert len(rows) == 1 and "qty_before" not in rows[0] and dpr.qty_today(rows[0]) is None


def test_unknown_item_quantity_stays_unknown(site):
    c, d, z, s = site
    pd.DataFrame([{"service": SVC, "item_code": "T9", "description": "No qty item", "unit": "Nos", "qty": None,
                   "section": None, "item_code_raw": "T9", "subcategory": "x"}]).to_parquet(d / "boq.parquet")
    (d / "mapping.json").write_text(json.dumps({SVC: {"Tray laying": ["T9"]}}))
    save(c, "T9", 0.5, z[0])
    assert dpr.qty_today(log_of(d)[0]) is None


def test_record_change_keeps_the_first_baseline_of_the_day_and_old_callers_still_work():
    log = []
    dpr.record_change(log, "2026-10-12", "E", "L1", "A", "R1", item="x", qty=10.0, unit="Nos", item_code="1", qty_before=2.0)
    dpr.record_change(log, "2026-10-12", "E", "L1", "A", "R1", item="x", qty=15.0, unit="Nos", item_code="1", qty_before=10.0)
    assert log[0]["qty"] == 15.0 and log[0]["qty_before"] == 2.0 and dpr.qty_today(log[0]) == 13.0
    dpr.record_change(log, "2026-10-12", "E", "L1", "A", "R2", item="y", qty=3.0, unit="Nos")          # an old-style call
    assert "qty_before" not in log[1] and dpr.qty_today(log[1]) is None


def test_ticking_a_zone_by_mistake_and_undoing_it_the_same_day_leaves_no_work_behind(site):
    """Mark zone done, then 'Undo done' on the same day: the zone is back to 0, so that day shows no work for it (not the 25 Rmt of the wrong tick)."""
    c, d, z, s = site
    body = {"service": SVC, "item_code": "T1", "rooms": [z[0]]}
    assert c.post(f"{URL}/mark-rooms-done", json={**body, "done": True}).status_code == 200
    assert today_of(d, "2026-10-12") == 25.0
    assert c.post(f"{URL}/mark-rooms-done", json={**body, "done": False}).status_code == 200
    assert today_of(d, "2026-10-12") == 0.0
    assert len(log_of(d)) == 1


def test_undoing_a_zone_that_was_finished_on_an_earlier_day_adds_no_line_for_today(site):
    """An undo is not work done today: it never creates a line of its own (the earlier day's report is history and stays as it was)."""
    c, d, z, s = site
    c.post(f"{URL}/mark-rooms-done", json={"service": SVC, "item_code": "T1", "rooms": [z[0]], "done": True})
    Clock.day = _dt.date(2026, 10, 13)
    c.post(f"{URL}/mark-rooms-done", json={"service": SVC, "item_code": "T1", "rooms": [z[0]], "done": False})
    assert [e["date"] for e in log_of(d)] == ["2026-10-12"]
    assert today_of(d, "2026-10-12") == 25.0
