"""Builds frontend/billing_stockinbox_fixture.json from the REAL backend, for test_billing_stockinbox_ui.mjs: every payload the
jsdom test replays is what the live API returned. Scenario = Thoth Mall Electrical on delivery challans, whose stock register has
dated INs of ordinary material plus the real wire deliveries of the site's WIRE CHALLAN sheet (28 Mar, 2 Jul, 16 Jul).
usage: python make_stockinbox_fixture.py <repo root> <out .json>   (BOQ descriptions / rates come from backend/test_billing_wire.py,
which carries the real Thoth BOQ texts)"""
import sys, json, pathlib, tempfile
ROOT = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(ROOT))
import numpy as np, pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import siteprogress as sp, billing, structure
from backend.test_billing_wire import wb_path
from backend.test_billing_stock_inbox import BASE_ROWS, wire_rows, register, links
tmp = pathlib.Path(tempfile.mkdtemp())
sp.PROJECTS = tmp / "p"; sp.RUNS = tmp / "r"; sp.PROJECTS.mkdir(); sp.RUNS.mkdir()
app = FastAPI(); app.include_router(sp.router); app.include_router(billing.router); c = TestClient(app)
S, B, U = "test-wire", "/api/billing/test-wire", "Electrical"
wb_path(tmp / "boq.xlsx")
assert c.post(f"/api/siteprogress/{S}/boq", files={"file": ("b.xlsx", open(tmp / "boq.xlsx", "rb"))}).status_code == 200
d = sp._dir(S)
(d / "structure.json").write_text(structure.hotel("T", floors=["1"], room_labels=["a"]).to_json())
assert c.post(f"{B}/setup", json={"service": U, "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": "challan"}).status_code == 200
import backend.test_billing_stock_inbox as T
T.sp = sp
register(BASE_ROWS + wire_rows()); links(d)
out = {"unit": U}
out["setup"] = c.get(f"{B}/setup").json()
out["lines"] = c.get(f"{B}/lines/{U}").json()["lines"]
out["ov0"] = c.get(B).json()
out["ch0"] = c.get(f"{B}/challans/{U}").json()
out["ib0"] = c.get(f"{B}/stock-inbox/{U}").json()
out["wire0"] = c.get(f"{B}/wire/{U}").json()
ents = {e["material"] + e["date"]: e for e in out["ib0"]["entries"]}
sw = next(e for e in out["ib0"]["entries"] if e["material"] == "SWITCH 6A MODULE BOX")
out["keys"] = {"switch": sw["key"], "pvc": next(e["key"] for e in out["ib0"]["entries"] if e["material"] == "PVC CLAMP 20MM"),
               "mcb": next(e["key"] for e in out["ib0"]["entries"] if e["material"] == "MCB 32A FP"),
               "cable": next(e["key"] for e in out["ib0"]["entries"] if e["material"] == "MAINS CABLE 6SQMM" and e["date"] == "2026-07-12")}
r = c.post(f"{B}/stock-inbox/{U}/accept", json={"items": [{"key": sw["key"]}]}); assert r.status_code == 200, r.text
out["ch1"] = c.get(f"{B}/challans/{U}").json(); out["ib1"] = c.get(f"{B}/stock-inbox/{U}").json()
# the register changes after the delivery was added
register([x if x[1] != "SWITCH 6A MODULE BOX" else (x[0], x[1], x[2], x[3], 4, x[5]) for x in BASE_ROWS] + wire_rows(), run="r2", created="2026-10-02T10:00:00")
out["ch_changed"] = c.get(f"{B}/challans/{U}").json()
# the same delivery already typed by hand
c2 = c.post(f"{B}/challans/{U}", json={"no": "0911", "date": "2026-07-12", "lines": [{"item_code": "1.2.1", "qty": 300}]}); assert c2.status_code == 200
out["ib_dup"] = c.get(f"{B}/stock-inbox/{U}").json()
# wire: confirm the materials, sync (the real RA 1 deliveries)
mats = [m["material"] for m in c.get(f"{B}/wire/{U}").json()["stock"]["materials"]]
assert c.put(f"{B}/wire/{U}/stock-map", json={"confirm": mats}).status_code == 200
out["wire_mats"] = mats
s = c.post(f"{B}/wire/{U}/stock-sync"); assert s.status_code == 200, s.text
out["sync"] = s.json()
out["wire1"] = c.get(f"{B}/wire/{U}").json()
json.dump(out, open(sys.argv[2], "w"))
print("fixture ok", len(out["ib0"]["entries"]), out["ib0"]["counts"], out["sync"], [x["points"] if "points" in x else None for x in out["wire1"]["lines"]])
