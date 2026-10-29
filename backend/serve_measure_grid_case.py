"""Test helper (not deployed): serves the REAL Site Progress router over the Thoth Fire case of test_measure_grid.py so a browser-side test
(jsdom or Chromium) can type into the grid against the real backend. `python -m backend.serve_measure_grid_case PORT`, prints READY.
Seeded like the reported case: 39b = 37 + 9.4 m measured in B3 > Zone 2 and 58 m typed in B3 > Zone 5."""
import json, sys, tempfile, pathlib, time, threading
import pandas as pd
import uvicorn
from fastapi import FastAPI
from backend import siteprogress, structure
from backend import test_measure_grid as T


def build(tmp):
    siteprogress.PROJECTS = tmp
    d = tmp / T.SLUG; d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B3", "B2", "B1"], zone_labels=[f"Zone {i}" for i in range(1, 8)])
    (d / "structure.json").write_text(s.to_json())
    ids = [r["id"] for r in s.rooms()]
    rows = [T._row(T.SVC, k, v[0], "Rmt", v[1]) for k, v in T.PIPES.items()] + [T._row(T.SVC, "N.1", "Smoke detector", "Nos", 210.0)]
    rows += [T._row("HVAC", f"H.{i}", f"Duct {i}", "Sqm", 10.0) for i in range(1, 7)]
    pd.DataFrame(rows).to_parquet(d / "boq.parquet")
    planned = {k: v[2] for k, v in T.PIPES.items()}; planned["N.1"] = 210.0
    (d / "planned.json").write_text(json.dumps({T.SVC: planned}))
    (d / "activities.json").write_text(json.dumps({T.SVC: ["Piping"], "HVAC": ["Ducting"]}))
    (d / "mapping.json").write_text(json.dumps({T.SVC: {"Piping": list(T.PIPES) + ["N.1"]}, "HVAC": {"Ducting": [f"H.{i}" for i in range(1, 7)]}}))
    return d, ids


def seed(c, ids):
    T.measured(c, ids[1], 37.0, "FACP-1", "Riser 2")
    T.measured(c, ids[1], 9.4, "Riser 2", "Zone 2 end")
    T.typed(c, ids[4], 58.0)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    port = int(sys.argv[1])
    tmp = pathlib.Path(tempfile.mkdtemp())
    d, ids = build(tmp)
    app = FastAPI(); app.include_router(siteprogress.router)
    seed(TestClient(app), ids)
    if len(sys.argv) > 2:                             # optional: a folder served at /static (for a real-browser run of the grid)
        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory=sys.argv[2]), name="static")
    @app.get("/__ids")
    def _ids(): return {"slug": T.SLUG, "ids": ids}
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started: time.sleep(0.05)
    print("READY", flush=True)
    sys.stdin.read()                                  # lives until the test closes stdin
