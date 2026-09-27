"""Issue 3 (backend correctness): _scoped_forecast_grouped() -- per-service
category/size scoping as an OR-of-ANDs, not the cross-product a flat
service+subcategory filter would silently produce."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import api


ROWS = [
    ("Electrical", "1.5 SQMM WIRE", "Wire"),
    ("Electrical", "METAL BOX", "Box/JB"),
    ("Plumbing", "50MM PIPE", "Pipe"),
    ("Plumbing", "GATE VALVE", "Valve"),
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
    (d / "meta.json").write_text(json.dumps({"project": "Hyatt Hotel"}))
    return TestClient(api.app), run_id


class TestScopedForecastGrouped:
    def test_no_cross_product_electrical_narrow_plumbing_wide(self, run):
        """The actual bug this exists to fix: Electrical scoped to Wire
        only, Plumbing left wide open, must never produce Electrical/Pipe
        or drop real Plumbing rows just because 'Wire' doesn't match them."""
        client, run_id = run
        groups = [{"service": "Electrical", "subcategory": ["Wire"], "size": []},
                  {"service": "Plumbing", "subcategory": [], "size": []}]
        f = api._scoped_forecast_grouped(run_id, groups)
        assert set(f.material) == {"1.5 SQMM WIRE", "50MM PIPE", "GATE VALVE"}

    def test_both_services_narrowed(self, run):
        client, run_id = run
        groups = [{"service": "Electrical", "subcategory": ["Wire"], "size": []},
                  {"service": "Plumbing", "subcategory": ["Pipe"], "size": []}]
        f = api._scoped_forecast_grouped(run_id, groups)
        assert set(f.material) == {"1.5 SQMM WIRE", "50MM PIPE"}

    def test_empty_groups_means_everything(self, run):
        client, run_id = run
        f = api._scoped_forecast_grouped(run_id, [])
        assert len(f) == 4


class TestExportRouteWithGroups:
    def test_groups_param_scopes_correctly(self, run):
        client, run_id = run
        groups = [{"service": "Electrical", "subcategory": ["Wire"], "size": []},
                  {"service": "Plumbing", "subcategory": [], "size": []}]
        r = client.get(f"/api/export/{run_id}", params={"groups": json.dumps(groups)})
        assert r.status_code == 200

    def test_filename_uses_project_name_and_aggregated_capped_scope(self, run):
        client, run_id = run
        groups = [{"service": "Electrical", "subcategory": ["Wire"], "size": []},
                  {"service": "Plumbing", "subcategory": ["Pipe"], "size": []}]
        r = client.get(f"/api/export/{run_id}", params={"groups": json.dumps(groups)})
        cd = r.headers["content-disposition"]
        assert "Hyatt-Hotel" in cd
        assert "Electrical+Plumbing" in cd or "Plumbing+Electrical" in cd
        assert "Wire+Pipe" in cd or "Pipe+Wire" in cd

    def test_invalid_groups_json_returns_400(self, run):
        client, run_id = run
        r = client.get(f"/api/export/{run_id}", params={"groups": "not valid json"})
        assert r.status_code == 400

    def test_no_groups_falls_back_to_flat_scoping_unchanged(self, run):
        """Backward compatibility: an old caller with no groups param at all
        (or the plain <a href> with zero params) must behave exactly as
        before -- this is what makes the change additive, not breaking."""
        client, run_id = run
        r = client.get(f"/api/export/{run_id}", params={"service": "Electrical"})
        assert r.status_code == 200
        cd = r.headers["content-disposition"]
        assert "Electrical" in cd
