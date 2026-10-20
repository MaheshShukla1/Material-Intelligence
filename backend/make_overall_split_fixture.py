"""Builds frontend/billing_overall_split_fixture.json from the REAL backend (the Fire BOQ of test_billing.py), for
test_billing_overall_split_ui.mjs: every payload the jsdom test replays is what the live API returned.
Three real situations of one service:
  split    Supply RA 1 invoiced + Supply RA 2 draft + Installation RA 1 draft in the same Work Order (the reported scenario)
  together one Together RA (a single bill that carries both stages)
  unset    only Supply is set up, Installation has no % (the app never guesses a number)
usage: python backend/make_overall_split_fixture.py <repo root> <out .json>"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress as sp, billing, structure
from backend.test_billing import make_workbook, SLUG

B = f"/api/billing/{SLUG}"


def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp())
    sp.PROJECTS = tmp / "projects"
    sp.RUNS = tmp / "runs"
    sp.PROJECTS.mkdir()
    sp.RUNS.mkdir()
    for c in (sp._read_forecast_parquet_cached, sp._forecast_pool_cached, sp._full_run_rows_cached, sp._linkage_match_cached):
        c.cache_clear()
    app = FastAPI()
    app.include_router(sp.router)
    app.include_router(billing.router)
    client = TestClient(app)
    xlsx = tmp / "boq.xlsx"
    make_workbook(xlsx)
    with open(xlsx, "rb") as fh:
        assert client.post(f"/api/siteprogress/{SLUG}/boq", files={"file": ("boq.xlsx", fh)}).status_code == 200
    (sp._dir(SLUG) / "structure.json").write_text(structure.hotel("Thoth", floors=["1", "2"], room_labels=["a", "b"]).to_json())
    return client


def post(c, path, body=None):
    r = c.post(B + path, json=body or {})
    assert r.status_code == 200, (path, r.text)
    return r.json()


def challan(c, no, lines):
    post(c, "/challans/Fire", {"no": no, "date": "2026-07-04", "lines": [{"item_code": k, "qty": q} for k, q in lines]})


def progress(c, code, frac):
    r = c.post(f"/api/siteprogress/{SLUG}/progress/item", json={"service": "Fire", "item_code": code, "frac": frac})
    assert r.status_code == 200, r.text


def snapshot(c, ra_ids):
    return {"ov": c.get(B).json(), "overall": c.get(f"{B}/overall/Fire").json(),
            "bills": {i: c.get(f"{B}/ra/{i}").json() for i in ra_ids}}


out = {}

c = fresh()                                                         # ---- split: the reported scenario
post(c, "/setup", {"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30, "supply_source": "challan"})
challan(c, "614", [("1", 1)])
s1 = post(c, "/ra", {"service": "Fire", "series": "supply"})["id"]
post(c, f"/ra/{s1}/lock")
challan(c, "615", [("1", 1), ("2", 2)])
s2 = post(c, "/ra", {"service": "Fire", "series": "supply"})["id"]
progress(c, "1", 0.5)
progress(c, "2", 0.5)
i1 = post(c, "/ra", {"service": "Fire", "series": "install"})["id"]
out["split"] = snapshot(c, [s1, s2, i1])
out["split"]["ids"] = {"s1": s1, "s2": s2, "i1": i1}

c = fresh()                                                         # ---- together: one bill, both stages
post(c, "/setup", {"service": "Fire", "mode": "together", "supply_pct": 70, "install_pct": 30, "supply_source": "challan"})
challan(c, "614", [("1", 1), ("2", 2)])
progress(c, "1", 0.5)
progress(c, "2", 0.25)
t1 = post(c, "/ra", {"service": "Fire", "series": "together"})["id"]
out["together"] = snapshot(c, [t1])
out["together"]["ids"] = {"t1": t1}

c = fresh()                                                         # ---- unset: Installation % never set
post(c, "/setup", {"service": "Fire", "mode": "separate", "supply_pct": 70, "supply_source": "challan"})
challan(c, "614", [("1", 1)])
u1 = post(c, "/ra", {"service": "Fire", "series": "supply"})["id"]
out["unset"] = snapshot(c, [u1])
out["unset"]["ids"] = {"u1": u1}

json.dump(out, open(sys.argv[2], "w"))
print("fixture ok:", {k: (round(v["overall"]["units"][0]["invoiced"]), round(v["overall"]["units"][0]["draft"])) for k, v in out.items()})
