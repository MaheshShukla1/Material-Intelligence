"""Test for api.py's /api/material/{run_id} route: it must now include the
'opening' column when present, so the frontend can compute a real 'total
received' (opening + dated IN), not dated-IN-alone.

Runs a minimal standalone FastAPI app carrying the EXACT route body copied
from api.py, rather than mounting the full app (which needs health.py /
toolcat.py, not supplied this round). If api.py's route body ever drifts
from this copy, that's a real signal to re-sync, not a reason to skip
testing the logic.
"""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def jsonable(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return json.loads(out.replace({np.nan: None}).to_json(orient="records"))


def make_app(runs_dir):
    app = FastAPI()

    @app.get("/api/material/{run_id}")
    def material_history(run_id: str, name: str):
        d = runs_dir / run_id
        if not (d / "daily.parquet").exists():
            raise HTTPException(404, "run not found")
        daily = pd.read_parquet(d / "daily.parquet")
        g = daily[daily.material == name.strip().upper()].sort_values("date")
        cols = ["date", "qty_in", "qty_out", "balance"]
        if "opening" in daily.columns:
            cols.append("opening")
        return jsonable(g[cols])

    return app


@pytest.fixture
def runs_dir(tmp_path):
    return tmp_path


def _write_daily(runs_dir, run_id, df):
    d = runs_dir / run_id
    d.mkdir(parents=True)
    df.to_parquet(d / "daily.parquet")


def test_material_history_includes_opening_when_present(runs_dir):
    """RG-6 CABLE's real shape: opening stock of 4880, zero dated IN ever."""
    df = pd.DataFrame([
        {"date": pd.Timestamp("2026-06-18"), "material": "RG-6 CABLE",
         "qty_in": 0.0, "qty_out": 305.0, "balance": 4575.0, "opening": 4880.0},
        {"date": pd.Timestamp("2026-08-09"), "material": "RG-6 CABLE",
         "qty_in": 0.0, "qty_out": 305.0, "balance": 0.0, "opening": 4880.0},
    ])
    _write_daily(runs_dir, "run1", df)
    client = TestClient(make_app(runs_dir))
    r = client.get("/api/material/run1", params={"name": "RG-6 CABLE"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["opening"] == 4880.0 for row in rows), \
        "opening stock must be present on every row, not dropped"


def test_material_history_works_without_opening_column(runs_dir):
    """Regression: an older run (or adapter B / ProjectBase export, which
    never has opening data) must not 500 just because 'opening' is missing
    entirely from daily.parquet."""
    df = pd.DataFrame([
        {"date": pd.Timestamp("2026-06-18"), "material": "25MM PVC PIPE",
         "qty_in": 100.0, "qty_out": 0.0, "balance": 2500.0},
    ])
    _write_daily(runs_dir, "run2", df)
    client = TestClient(make_app(runs_dir))
    r = client.get("/api/material/run2", params={"name": "25MM PVC PIPE"})
    assert r.status_code == 200
    rows = r.json()
    assert "opening" not in rows[0]
    assert rows[0]["qty_in"] == 100.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
