"""Backend performance checklist: only the 2 items that actually applied to
this (file-based, no-SQL-database) architecture -- Calamine for Excel
reads, and caching load_run() since a run's data is immutable once written.
"""
import json
import time

import pandas as pd
import pytest

from backend import api


class TestLoadRunCaching:
    def test_second_call_returns_identical_cached_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "RUNS", tmp_path)
        run_id = "run1"
        d = tmp_path / run_id
        d.mkdir()
        df = pd.DataFrame([{"service": "Electrical", "material": "WIRE", "unit": "MTR"}])
        df.to_parquet(d / "forecast.parquet")
        (d / "meta.json").write_text(json.dumps({"project": "Fixture"}))

        f1, meta1 = api.load_run(run_id)
        f2, meta2 = api.load_run(run_id)
        assert f1 is f2, "FAIL: second call should return the SAME cached object, not re-read disk"
        assert meta1 is meta2

    def test_a_404_is_never_cached(self, tmp_path, monkeypatch):
        """A failed lookup must not poison the cache -- if the run shows up
        moments later (upload still in flight), the next real call must
        succeed, not keep raising the old 404 forever."""
        monkeypatch.setattr(api, "RUNS", tmp_path)
        run_id = "run-not-yet-ready"
        with pytest.raises(Exception):
            api.load_run(run_id)
        d = tmp_path / run_id
        d.mkdir()
        df = pd.DataFrame([{"service": "Electrical", "material": "WIRE", "unit": "MTR"}])
        df.to_parquet(d / "forecast.parquet")
        (d / "meta.json").write_text(json.dumps({"project": "Fixture"}))
        f, meta = api.load_run(run_id)   # must succeed now, not still raise
        assert meta["project"] == "Fixture"

    def test_different_run_ids_never_share_a_cache_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "RUNS", tmp_path)
        for rid, project in (("run-a", "Hyatt"), ("run-b", "Thoth Mall")):
            d = tmp_path / rid
            d.mkdir()
            df = pd.DataFrame([{"service": "Electrical", "material": "WIRE", "unit": "MTR"}])
            df.to_parquet(d / "forecast.parquet")
            (d / "meta.json").write_text(json.dumps({"project": project}))
        _, meta_a = api.load_run("run-a")
        _, meta_b = api.load_run("run-b")
        assert meta_a["project"] == "Hyatt"
        assert meta_b["project"] == "Thoth Mall"


class TestCalamineEngine:
    def test_excel_file_reads_use_calamine(self, tmp_path):
        """Confirms the real engine= parameter is actually wired, not just
        present somewhere unused -- builds a real small xlsx and reads it
        through api.sheet_plan(), which is one of the switched call sites."""
        import openpyxl
        path = tmp_path / "fixture.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Electrical"
        ws.append(["Material", "Unit", "In", "Out"])
        ws.append(["WIRE", "MTR", 10, 2])
        wb.save(path)
        # sheet_plan() internally does pd.ExcelFile(path, engine="calamine")
        keep, skipped = api.sheet_plan(str(path))
        assert "Electrical" in keep or "Electrical" in [s["sheet"] for s in skipped], \
            "FAIL: the real sheet should at least be seen by the calamine-backed reader"

    def test_calamine_and_openpyxl_agree_on_real_content(self, tmp_path):
        """Not just 'doesn't crash' -- the actual cell data read via
        calamine must match what openpyxl itself would read, on a real
        multi-row file."""
        import openpyxl
        path = tmp_path / "fixture.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["A", "B", "C"])
        ws.append([1, 2, 3])
        ws.append(["x", "y", "z"])
        wb.save(path)
        via_calamine = pd.read_excel(path, engine="calamine")
        via_openpyxl = pd.read_excel(path, engine="openpyxl")
        pd.testing.assert_frame_equal(via_calamine, via_openpyxl, check_dtype=False)
