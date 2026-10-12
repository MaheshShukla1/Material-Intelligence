"""Builds frontend/billing_wire_fixture.json from the REAL backend and the real Thoth BOQ, for test_billing_wire_ui.mjs:
every payload the jsdom test replays is what the live API returned. Scenario = the real RA 1 of Electrical "Only point wiring":
the three wire challans of the site's WIRE CHALLAN sheet (1207, 615, 617), the metres given to 1.1.5 / 1.1.3 / 1.1.4.
usage: python make_wire_fixture.py <repo root> <Thoth BOQ .xlsx> <out .json>"""
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
g = c.get(f"{B}/packages/Electrical").json()["groups"]
grp = lambda x: {"sheet": x["sheet"], "from": x["from"], "to": x["to"]}
pw = next(x for x in g if x["name"].startswith("POINT WIRING"))
ct = next(x for x in g if x["name"] == "Cable Tray"); ea = next(x for x in g if x["name"] == "Earthing")
r = c.post(f"{B}/packages/Electrical", json={"items": [{"name": "Excl. point wiring", "groups": [grp(ct), grp(ea)]},
                                                      {"name": "Only point wiring", "groups": [grp(pw)]}, {"name": "Other Electrical", "rest": True}]})
assert r.status_code == 200, r.text
EX, PW, OT = "Electrical › Excl. point wiring", "Electrical › Only point wiring", "Electrical › Other Electrical"
for u, src in ((EX, "stock"), (PW, "challan"), (OT, "stock")):
    assert c.post(f"{B}/setup", json={"service": u, "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": src}).status_code == 200
W = f"{B}/wire/{PW}"
out = {"units": {"EX": EX, "PW": PW, "OT": OT}}
out["ov0"] = c.get(B).json()
out["wire0"] = c.get(W).json()
out["ch0"] = c.get(f"{B}/challans/{PW}").json()
sz = lambda s, **k: {"size": s, "colours": {a.upper(): v for a, v in k.items()}}
for no, date, a15, a25 in (
        ("1207", "2026-03-28", dict(red=300, yellow=600, blue=300, black=900, green=1500), dict(red=300, yellow=300, blue=300, black=600)),
        ("615", "2026-07-02", dict(red=3300, yellow=3000, blue=2700, black=9600, green=13800), dict(red=1500, yellow=1500, blue=1200, black=3900)),
        ("617", "2026-07-16", dict(red=3900, yellow=3600, blue=2400, black=10500, green=14700), dict(red=1500, yellow=1500, blue=1800, black=4800))):
    rr = c.post(f"{W}/challans", json={"no": no, "date": date, "sizes": [sz("1.5", **a15), sz("2.5", **a25)]}); assert rr.status_code == 200, rr.text
out["wire1"] = c.get(W).json()
rows = [{"item_code": "1.1.5", "size": "1.5", "metres": 20100}, {"item_code": "1.1.3", "size": "2.5", "metres": 6000}, {"item_code": "1.1.4", "size": "2.5", "metres": 3900}]
out["alloc_rows"] = rows
rr = c.put(f"{W}/alloc", json={"rows": rows}); assert rr.status_code == 200, rr.text
out["wire2"] = c.get(W).json()
out["ch2"] = c.get(f"{B}/challans/{PW}").json()
ra = c.post(f"{B}/ra", json={"service": PW, "series": "supply"}).json()["id"]
out["ra"] = ra
out["bill"] = c.get(f"{B}/ra/{ra}").json()
out["ov2"] = c.get(B).json()
out["setup"] = c.get(f"{B}/setup").json()
json.dump(out, open(sys.argv[3], "w"))
l = {x["item_code"]: x["points"] for x in out["wire2"]["lines"]}
print("fixture ok", l, round(out["wire2"]["totals"]["supply_this"], 2), round(out["bill"]["this_total"], 2))
print("PW wire flag:", [(s["service"], s["wire"]) for s in out["ov2"]["services"]])
