"""Issue 3 (backend piece): /api/sizes now accepts comma-joined multi-values
for service/subcategory (matching _scoped_forecast's own convention), so the
Export modal's multi-select Category picker can ask for sizes across several
categories/services in one call."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import api


ROWS = [
    ("Electrical", "1.5 SQMM WIRE GREEN", "Wire"),
    ("Electrical", "2.5 SQMM WIRE BLUE", "Wire"),
    ("Electrical", "300X50MM LADDER TRAY", "Cable tray"),
    ("Plumbing", "50MM UPVC PIPE", "Pipe"),
]
COLS = ["service", "material", "subcategory"]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RUNS", tmp_path)
    run_id = "run1"
    d = tmp_path / run_id
    d.mkdir()
    df = pd.DataFrame(ROWS, columns=COLS)
    df.to_parquet(d / "forecast.parquet")
    (d / "meta.json").write_text(json.dumps({"project": "Fixture"}))
    return TestClient(api.app), run_id


def test_single_subcategory_unchanged(run):
    client, run_id = run
    r = client.get(f"/api/sizes/{run_id}", params={"service": "Electrical", "subcategory": "Wire"})
    names = {x["name"] for x in r.json()}
    assert names == {"1.5SQMM", "2.5SQMM"}


def test_comma_joined_subcategory_unions_both(run):
    client, run_id = run
    r = client.get(f"/api/sizes/{run_id}", params={"service": "Electrical", "subcategory": "Wire,Cable tray"})
    names = {x["name"] for x in r.json()}
    assert "1.5SQMM" in names and "2.5SQMM" in names
    # 300X50MM ladder tray's own size token should show too, from the second category
    assert any("300" in n or "50" in n for n in names), f"expected the Cable tray size token too, got {names}"


def test_comma_joined_service_unions_both(run):
    client, run_id = run
    r = client.get(f"/api/sizes/{run_id}", params={"service": "Electrical,Plumbing"})
    names = {x["name"] for x in r.json()}
    assert "1.5SQMM" in names   # Electrical
    assert "50MM" in names      # Plumbing
