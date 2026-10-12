"""Builds frontend/billing_challan_fixture.json from the REAL backend, the real Thoth
multi-sheet BOQ and the user's real HVAC invoice workbook: every payload the jsdom
test replays is what the live API returned, not a hand-written guess.
usage: python make_challan_fixture.py <repo root> <Thoth BOQ .xlsx> <HVAC invoice .xlsx> <out .json>"""
import sys, json, pathlib, tempfile
ROOT = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import siteprogress as sp, billing, structure
tmp = pathlib.Path(tempfile.mkdtemp())
sp.PROJECTS = tmp / "p"; sp.RUNS = tmp / "r"; sp.PROJECTS.mkdir(); sp.RUNS.mkdir()
app = FastAPI(); app.include_router(sp.router); app.include_router(billing.router); c = TestClient(app)
S, B = "thoth-mall", "/api/billing/thoth-mall"
assert c.post(f"/api/siteprogress/{S}/boq", files={"file": ("b.xlsx", open(sys.argv[2], "rb"))}).status_code == 200
d = sp._dir(S)
(d / "structure.json").write_text(structure.mall("Thoth Mall", levels=["B3", "B2"], zone_labels=["Zone 1", "Zone 2"]).to_json())
import pandas as pd
rd = sp.RUNS / "r_thoth"; rd.mkdir()
(rd / "meta.json").write_text(json.dumps({"run_id": "r_thoth", "project": "Thoth Mall", "project_slug": S, "created": "2026-10-01T10:00:00", "filename": "s.xlsx", "source": "upload", "stats": {}}))
hv = billing._lines(d, "HVAC")
mats = [{"material": f"HVAC MATERIAL {i}", "service": "HVAC", "unit": l["unit"], "stock": float(l["boq_qty"]) * 0.5, "total_consumed": float(l["boq_qty"]) * 0.1,
         "rate_per_day": 0.0, "days_left": 99.0, "status": "GREEN"} for i, l in enumerate(hv[40:44])]
pd.DataFrame(mats).to_parquet(rd / "forecast.parquet")
for i, l in enumerate(hv[40:44]):
    c.post(f"/api/siteprogress/{S}/links", json={"service": "HVAC", "item_code": l["item_code"], "materials": [{"material": f"HVAC MATERIAL {i}", "factor": None}]})
assert c.post(f"{B}/setup", json={"service": "HVAC", "mode": "separate", "supply_pct": 75, "install_pct": 25, "supply_source": "challan"}).status_code == 200
assert c.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30}).status_code == 200
out = {"hvac_lines": [{k: l[k] for k in ("item_code", "desc", "unit", "boq_qty", "rate", "internal_ref")} for l in hv]}
out["setup0"] = c.get(f"{B}/setup").json()
out["ch_empty"] = c.get(f"{B}/challans/HVAC").json()
out["preview"] = c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", open(sys.argv[3], "rb"))}, data={"commit": "false", "lock_upto": "0"}).json()
out["preview_lock"] = c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", open(sys.argv[3], "rb"))}, data={"commit": "false", "lock_upto": "1"}).json()
assert c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", open(sys.argv[3], "rb"))}, data={"commit": "true", "lock_upto": "0"}).status_code == 200
out["ch_open"] = c.get(f"{B}/challans/HVAC").json()
ra = c.post(f"{B}/ra", json={"service": "HVAC", "series": "supply"}).json()["id"]
out["raid"] = ra
out["bill"] = c.get(f"{B}/ra/{ra}").json()
out["ov_open"] = c.get(f"{B}").json()
assert c.post(f"{B}/ra/{ra}/lock").status_code == 200
nxt = hv[0]
assert c.post(f"{B}/challans/HVAC", json={"no": "640", "date": "2026-09-10", "lines": [{"item_code": hv[41]["item_code"], "qty": 1}]}).status_code == 200
assert c.post(f"{B}/challans/HVAC", json={"no": "641", "date": "2026-09-11", "ra": None, "lines": [{"item_code": hv[42]["item_code"], "qty": 1}]}).status_code == 200
out["ch_locked"] = c.get(f"{B}/challans/HVAC").json()
out["ov_locked"] = c.get(f"{B}").json()
json.dump(out, open(sys.argv[4], "w"))
print("fixture:", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in out.items()})
