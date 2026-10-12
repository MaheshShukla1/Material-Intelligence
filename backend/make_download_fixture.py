"""Builds frontend/billing_download_fixture.json from the REAL backend, the real Thoth multi-sheet
BOQ and the user's real HVAC invoice workbook, for test_billing_download_ui.mjs: every payload the
jsdom test replays is what the live API returned. Two services: HVAC on delivery challans
(RA-01 supply locked, RA-02 supply draft, RA-01 installation draft) and Fire on the stock register
(RA-01 supply draft).
usage: python make_download_fixture.py <repo root> <Thoth BOQ .xlsx> <HVAC invoice .xlsx> <out .json>"""
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
assert c.post(f"{B}/setup", json={"service": "HVAC", "mode": "separate", "supply_pct": 75, "install_pct": 25, "supply_source": "challan"}).status_code == 200
assert c.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30}).status_code == 200
assert c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", open(sys.argv[3], "rb"))}, data={"commit": "true", "lock_upto": "0"}).status_code == 200
out = {}
hv = billing._lines(d, "HVAC")
ra1 = c.post(f"{B}/ra", json={"service": "HVAC", "series": "supply"}).json()["id"]
assert c.post(f"{B}/ra/{ra1}/lock").status_code == 200
assert c.post(f"{B}/challans/HVAC", json={"no": "700", "date": "2026-09-10", "lines": [{"item_code": hv[1]["item_code"], "qty": 1}]}).status_code == 200
ra2 = c.post(f"{B}/ra", json={"service": "HVAC", "series": "supply"}).json()["id"]
rai = c.post(f"{B}/ra", json={"service": "HVAC", "series": "install"}).json()["id"]
raf = c.post(f"{B}/ra", json={"service": "Fire", "series": "supply"}).json()["id"]
out["ids"] = {"hvac1": ra1, "hvac2": ra2, "hvac_i": rai, "fire": raf}
assert c.post(f"{B}/meta", json={"client": "Thoth Mall", "contractor": "Vedant Engitech And Infrastructure Pvt. Ltd.", "wo_no": "WO/12"}).status_code == 200
assert c.post(f"{B}/ra/{ra1}/invoice", json={"no": "VE/25-26/014", "date": "2026-07-05"}).status_code == 200
out["ov0"] = c.get(B).json()
out["setup0"] = c.get(f"{B}/setup").json()
out["bill2"] = c.get(f"{B}/ra/{ra2}").json()
out["bill1"] = c.get(f"{B}/ra/{ra1}").json()
# after the person fills the details
meta = {"client": "Thoth Mall", "contractor": "Vedant Engitech And Infrastructure Pvt. Ltd.", "wo_no": "WO/12", "client_address": "Plot 5, SG Highway, Ahmedabad",
        "place_of_supply": "Gujarat", "client_gstin": "24ABCDE1234F1Z5", "client_state": "Gujarat", "client_state_code": "24", "project_name": "Thoth Mall",
        "supplier_gstin": "24AADCV2799M1ZP", "supplier_state": "GUJARAT", "supplier_state_code": "24", "supplier_pan": "AADCV2799M", "supplier_category": "MEPF",
        "sgst": 9, "cgst": 9, "sac": {"HVAC": "995461", "Fire": "995461"}}
out["meta_full"] = meta
assert c.post(f"{B}/meta", json=meta).status_code == 200
for r, n in ((ra2, "VE/25-26/021"), (rai, "VE/25-26/022"), (raf, "VE/25-26/023")):
    assert c.post(f"{B}/ra/{r}/invoice", json={"no": n, "date": "2026-10-08"}).status_code == 200
out["ov1"] = c.get(B).json()
out["setup1"] = c.get(f"{B}/setup").json()
out["bill2_full"] = c.get(f"{B}/ra/{ra2}").json()
out["inv_reply"] = c.post(f"{B}/ra/{ra2}/invoice", json={"no": "VE/25-26/021"}).json()
json.dump(out, open(sys.argv[4], "w"))
print("fixture:", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in out.items()})
