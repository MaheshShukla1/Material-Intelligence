"""Builds frontend/billing_packages_fixture.json from the REAL backend and the real Thoth BOQ, for
test_billing_packages_ui.mjs: every payload the jsdom test replays is what the live API returned.
Electrical is split into three Work Orders the way the real project is billed:
  'Only point wiring'  = the POINT WIRING group (supply invoiced: RA 1 locked, from wire challans)
  'Excl. point wiring' = Cable Tray + Earthing (installation draft RA 1, from Site Progress)
  'Other Electrical'   = the rest (no RA yet)
HVAC stays one Work Order.
usage: python make_packages_fixture.py <repo root> <Thoth BOQ .xlsx> <out .json>"""
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
out = {}
out["packages_plain"] = c.get(f"{B}/packages/Electrical").json()
g = out["packages_plain"]["groups"]
grp = lambda x: {"sheet": x["sheet"], "from": x["from"], "to": x["to"]}
by = {x["name"].strip(): x for x in g}
pw = next(x for x in g if x["name"].startswith("POINT WIRING"))
ct = next(x for x in g if x["name"] == "Cable Tray"); ea = next(x for x in g if x["name"] == "Earthing")
items = [{"name": "Excl. point wiring", "groups": [grp(ct), grp(ea)]}, {"name": "Only point wiring", "groups": [grp(pw)]},
         {"name": "Other Electrical", "rest": True}]
r = c.post(f"{B}/packages/Electrical", json={"items": items}); assert r.status_code == 200, r.text
out["save_reply"] = r.json()
EX, PW, OT = "Electrical › Excl. point wiring", "Electrical › Only point wiring", "Electrical › Other Electrical"
for u, src in ((EX, "stock"), (PW, "challan"), (OT, "stock")):
    assert c.post(f"{B}/setup", json={"service": u, "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": src}).status_code == 200
assert c.post(f"{B}/setup", json={"service": "HVAC", "mode": "separate", "supply_pct": 75, "install_pct": 25, "supply_source": "stock"}).status_code == 200
pl = billing._lines(d, PW)
assert len(pl) == 30
# wire challans on three point lines, billed in RA 1, then RA 1 invoiced
for no, ln, q in (("1207", pl[0], 2000), ("615", pl[1], 150), ("617", pl[2], 80)):
    rr = c.post(f"{B}/challans/{PW}", json={"no": no, "date": "2026-07-04", "lines": [{"item_code": ln["item_code"], "qty": min(q, ln["boq_qty"])}]}); assert rr.status_code == 200, rr.text
ra_pw = c.post(f"{B}/ra", json={"service": PW, "series": "supply"}).json()["id"]
assert c.post(f"{B}/ra/{ra_pw}/lock").status_code == 200
# installation progress on cable tray + earthing lines -> the app opens the draft by itself
el = billing._lines(d, EX)
for ln in el[:3]:
    assert c.post(f"/api/siteprogress/{S}/progress/item", json={"service": "Electrical", "item_code": ln["item_code"], "frac": 0.5}).status_code == 200
out["auto"] = c.post(f"{B}/auto").json()
out["ov"] = c.get(B).json()
out["overall"] = c.get(f"{B}/overall/Electrical").json()
out["overall_hvac"] = c.get(f"{B}/overall/HVAC").json()
out["setup"] = c.get(f"{B}/setup").json()
out["packages_split"] = c.get(f"{B}/packages/Electrical").json()
out["packages_hvac"] = c.get(f"{B}/packages/HVAC").json()
out["ids"] = {"pw": ra_pw, "ex": next(r["id"] for r in out["ov"]["ras"] if r["service"] == EX)}
out["units"] = {"EX": EX, "PW": PW, "OT": OT}
out["bills"] = {i: c.get(f"{B}/ra/{i}").json() for i in out["ids"].values()}
json.dump(out, open(sys.argv[3], "w"))
print("fixture ok:", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in out.items()})
print([ (u["unit"], round(u["wo_value"]), round(u["invoiced"]), round(u["draft"])) for u in out["overall"]["units"]])
