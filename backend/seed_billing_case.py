import sys, json, random, pathlib
"""Seeds a SCRATCH copy of the app (never production) for test_billing_chromium.py:
real Thoth multi-sheet BOQ uploaded through the real route, some Site Progress,
a stock run linked to six Fire lines, and payment terms for Fire/HVAC/Electrical.
usage: python seed_billing_case.py <scratch app root> <path to Thoth BOQ .xlsx>"""
ROOT = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(ROOT))
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import siteprogress as sp, billing, structure
sp.PROJECTS = ROOT/"data"/"projects"; sp.RUNS = ROOT/"data"/"runs"
app = FastAPI(); app.include_router(sp.router); app.include_router(billing.router); c = TestClient(app)
SRC = sys.argv[2]
assert c.post("/api/siteprogress/thoth-mall/boq", files={"file": ("b.xlsx", open(SRC, "rb"))}).status_code == 200
d = sp._dir("thoth-mall")
(d/"structure.json").write_text(structure.mall("Thoth Mall", levels=["B3","B2","B1"], zone_labels=[f"Zone {i}" for i in range(1,8)]).to_json())
random.seed(5)
fire = billing._lines(d, "Fire")
for l in fire[:14]:
    c.post("/api/siteprogress/thoth-mall/progress/item", json={"service":"Fire","item_code":l["item_code"],"frac":random.choice([.25,.5,.75,1.0])})
mats = []
for i, l in enumerate(fire[2:8]):
    nm = f"FIRE MATERIAL {i+1} {l['desc'][:20].upper()}"
    mats.append({"material": nm, "service": "Fire & HVAC", "unit": l["unit"], "stock": float(l["boq_qty"])*0.3, "total_consumed": float(l["boq_qty"])*0.4,
                 "rate_per_day": 0.0, "days_left": 99.0, "status": "GREEN"})
    c.post("/api/siteprogress/thoth-mall/links", json={"service":"Fire","item_code":l["item_code"],"materials":[{"material":nm,"factor":None}]})
rd = ROOT/"data"/"runs"/"r_thoth"; rd.mkdir(parents=True, exist_ok=True)
(rd/"meta.json").write_text(json.dumps({"run_id":"r_thoth","project":"Thoth Mall","project_slug":"thoth-mall","created":"2026-10-01T10:00:00","filename":"stock.xlsx","source":"upload","stats":{}}))
pd.DataFrame(mats).to_parquet(rd/"forecast.parquet")
for svc, mode, s_, i_ in [("Fire","separate",70,30),("HVAC","separate",75,None),("Electrical","together",60,40)]:
    assert c.post("/api/billing/thoth-mall/setup", json={"service":svc,"mode":mode,"supply_pct":s_,"install_pct":i_}).status_code == 200
print("seeded", len(fire), "fire lines")
