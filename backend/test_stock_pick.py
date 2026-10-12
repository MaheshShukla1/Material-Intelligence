"""Site Progress picks WHICH stock it reads (an upload or a Google Sheet made on the Forecast tab), instead of
needing the stock to carry the project's exact name. Reproduces the reported scenario: Thoth Mall's stock was deleted,
a new Google Sheet was synced under another name ("Live Sheet 4F2A"), and Site Progress / Billing could not use it."""
import sys, json, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from backend import siteprogress as sp
from backend.test_billing import env, make_run, link, progress, setup_fire, STOCK1, SLUG   # noqa: F401

SP = "/api/siteprogress"


def make_stock(name_slug, run, created, rows, **meta):
    """A run belonging to ANOTHER stock (meta.project_slug != the project's own slug)."""
    import pandas as pd
    rd = sp.RUNS / run
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "meta.json").write_text(json.dumps({"project_slug": name_slug, "project": meta.pop("project", name_slug),
                                              "run_id": run, "created": created, **meta}))
    pd.DataFrame([{"service": "Fire & HVAC", "rate_per_day": 0.0, "days_left": 99.0, "status": "OK", **r} for r in rows]
                 ).to_parquet(rd / "forecast.parquet")
    for c in (sp._read_forecast_parquet_cached, sp._forecast_pool_cached, sp._full_run_rows_cached, sp._linkage_match_cached):
        c.cache_clear()


def snap(d):
    """Hash of everything the stock choice must NOT touch."""
    out = {}
    for f in sorted(d.iterdir()):
        if f.name == "stock.json":
            continue
        out[f.name] = hashlib.md5(f.read_bytes()).hexdigest()
    return out


@pytest.fixture
def thoth_without_stock(env):
    """Thoth Mall as the user has it: BOQ + progress + links saved, stock run deleted."""
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    for code, mat in (("2", "BUTTERFLY VALVE 150MM"), ("3", "GI PIPE 100MM"), ("5", "HYDRANT VALVE")):
        link(client, code, mat)
    progress(client, "2", 0.5)
    setup_fire(client)
    import shutil
    shutil.rmtree(sp.RUNS / "run1")                      # "maine thoth mall ka stock delete kar diya tha"
    for c in (sp._read_forecast_parquet_cached, sp._forecast_pool_cached, sp._full_run_rows_cached, sp._linkage_match_cached):
        c.cache_clear()
    return client, d, tmp


def test_stockless_project_is_listed_with_its_real_name(thoth_without_stock):
    client, d, _ = thoth_without_stock
    r = client.get(f"{SP}/projects-without-stock")           # not swallowed by the /{slug} route
    assert r.status_code == 200
    got = {p["slug"]: p for p in r.json()}
    assert SLUG in got and got[SLUG]["project"] == "Thoth" and got[SLUG]["runs"] == 0


def test_project_with_its_own_stock_is_not_listed_as_stockless(env):
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    assert SLUG not in [p["slug"] for p in client.get(f"{SP}/projects-without-stock").json()]


def test_no_stock_means_not_connected_and_options_list_the_synced_sheet(thoth_without_stock):
    client, d, tmp = thoth_without_stock
    assert client.get(f"{SP}/{SLUG}/links/Fire").json()["has_run"] is False
    make_stock("live-sheet-4f2a", "g1", "2026-10-12T10:00:00", STOCK1, project="Live Sheet 4F2A",
               source_link="https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?output=xlsx")
    v = client.get(f"{SP}/{SLUG}/stock").json()
    assert v["current"] is None and v["chosen"] is False and v["stock"] == SLUG
    (o,) = v["options"]
    assert o["stock"] == "live-sheet-4f2a" and o["name"] == "Live Sheet 4F2A" and o["kind"] == "Google Sheet"
    assert o["materials"] == 3 and o["links"] == {"saved": 3, "found": 3}


def test_picking_the_sheet_connects_site_progress_without_touching_anything_else(thoth_without_stock):
    client, d, tmp = thoth_without_stock
    make_stock("live-sheet-4f2a", "g1", "2026-10-12T10:00:00", STOCK1, project="Live Sheet 4F2A", source_link="https://x/pub?output=xlsx")
    before = snap(d)
    r = client.post(f"{SP}/{SLUG}/stock", json={"stock": "live-sheet-4f2a"})
    assert r.status_code == 200 and r.json()["chosen"] is True and r.json()["current"]["stock"] == "live-sheet-4f2a"
    assert snap(d) == before                               # BOQ, progress, links, billing: byte-identical
    lk = client.get(f"{SP}/{SLUG}/links/Fire").json()
    assert lk["has_run"] is True and "BUTTERFLY VALVE 150MM" in lk["stock_names"]
    assert [i["linked"][0]["material"] for i in lk["items"] if i["linked"]] == ["BUTTERFLY VALVE 150MM", "GI PIPE 100MM", "HYDRANT VALVE"]
    assert client.get(f"{SP}/{SLUG}/stock").json()["current"]["name"] == "Live Sheet 4F2A"


def test_billing_reads_the_picked_stock(thoth_without_stock):
    """Billing's _stock_rows (challan / supply source) resolves the run through the same lookup: none before the pick,
    the picked sheet's newest run after it."""
    client, d, tmp = thoth_without_stock
    import backend.billing as billing
    assert billing._stock_rows(SLUG) == ((), None)
    make_stock("live-sheet-4f2a", "g1", "2026-10-12T10:00:00", STOCK1, project="Live Sheet 4F2A", source_link="https://x/pub?output=xlsx")
    assert billing._stock_rows(SLUG) == ((), None)          # a stock existing is not enough: it must be picked
    client.post(f"{SP}/{SLUG}/stock", json={"stock": "live-sheet-4f2a"})
    assert billing._stock_rows(SLUG)[1] == "g1"


def test_default_is_unchanged_same_name_stock_is_used_with_no_binding(env):
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    assert not (d / "stock.json").exists()
    v = client.get(f"{SP}/{SLUG}/stock").json()
    assert v["chosen"] is False and v["current"]["stock"] == SLUG
    assert sp._latest_run_for(SLUG) == sp.RUNS / "run1"


def test_newest_run_of_the_picked_stock_is_used(thoth_without_stock):
    client, d, tmp = thoth_without_stock
    make_stock("sheet-b", "b1", "2026-10-01T10:00:00", STOCK1[:1], project="Sheet B")
    make_stock("sheet-b", "b2", "2026-10-12T10:00:00", STOCK1, project="Sheet B")
    make_stock("other", "o1", "2026-10-20T10:00:00", STOCK1[:2], project="Other")   # newer, but a different stock
    client.post(f"{SP}/{SLUG}/stock", json={"stock": "sheet-b"})
    assert sp._latest_run_for(SLUG).name == "b2"


def test_links_missing_from_the_chosen_stock_are_counted_and_never_repointed(thoth_without_stock):
    client, d, tmp = thoth_without_stock
    make_stock("poor", "p1", "2026-10-12T10:00:00", [{"material": "GI PIPE 100MM", "unit": "Rmt", "stock": 1.0, "total_consumed": 0.0},
                                                       {"material": "BUTTERFLY VALVE 150 MM NEW", "unit": "Nos", "stock": 1.0, "total_consumed": 0.0}],
               project="Poor")
    o = client.get(f"{SP}/{SLUG}/stock").json()["options"][0]
    assert o["links"] == {"saved": 3, "found": 1}
    before = (d / "links.json").read_text()
    client.post(f"{SP}/{SLUG}/stock", json={"stock": "poor"})
    assert (d / "links.json").read_text() == before        # nothing re-pointed to the similar-looking name


def test_unknown_stock_is_refused_and_null_goes_back_to_default(thoth_without_stock):
    client, d, tmp = thoth_without_stock
    r = client.post(f"{SP}/{SLUG}/stock", json={"stock": "no-such-stock"})
    assert r.status_code == 404 and not (d / "stock.json").exists()
    make_stock("sheet-b", "b1", "2026-10-12T10:00:00", STOCK1, project="Sheet B")
    client.post(f"{SP}/{SLUG}/stock", json={"stock": "sheet-b"})
    assert (d / "stock.json").exists()
    v = client.post(f"{SP}/{SLUG}/stock", json={"stock": None}).json()
    assert not (d / "stock.json").exists() and v["chosen"] is False and v["current"] is None


def test_unknown_project_is_404(env):
    client, d, tmp = env
    assert client.post(f"{SP}/nope/stock", json={"stock": None}).status_code in (200, 404)
    assert client.post(f"{SP}/nope/stock", json={"stock": "x"}).status_code == 404


def test_broken_stock_json_falls_back_to_default(env):
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    (d / "stock.json").write_text("{not json")
    assert sp._latest_run_for(SLUG) == sp.RUNS / "run1"


def test_project_can_be_built_from_nothing_without_any_stock_then_stock_connects_by_name(tmp_path, monkeypatch):
    """Site Progress first, stock later: no stock run exists anywhere. Structure is created, the project is usable, the stock
    line says not connected; a stock made later with the project's own name is picked up with no binding at all."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "projects"); monkeypatch.setattr(sp, "RUNS", tmp_path / "runs")
    sp.PROJECTS.mkdir(); sp.RUNS.mkdir()
    for c in (sp._read_forecast_parquet_cached, sp._forecast_pool_cached, sp._full_run_rows_cached, sp._linkage_match_cached):
        c.cache_clear()
    app = FastAPI(); app.include_router(sp.router); client = TestClient(app)
    assert client.get(f"{SP}/new-mall/stock").json()["options"] == []          # nothing exists, and asking does not error
    r = client.post(f"{SP}/new-mall/structure/template", json={"kind": "mall", "name": "New Mall", "levels": ["Level 1"], "zones_per_level": 3})
    assert r.status_code == 200 and r.json()["rooms"] == 3
    assert [p["slug"] for p in client.get(f"{SP}/projects-without-stock").json()] == ["new-mall"]
    v = client.get(f"{SP}/new-mall/stock").json()
    assert v["current"] is None and v["chosen"] is False
    make_stock("new-mall", "n1", "2026-10-12T10:00:00", STOCK1, project="New Mall", source_link="https://x/pub?output=xlsx")
    v = client.get(f"{SP}/new-mall/stock").json()
    assert v["current"]["stock"] == "new-mall" and v["current"]["kind"] == "Google Sheet" and v["current"]["source_link"] == "https://x/pub?output=xlsx"
    assert not (sp.PROJECTS / "new-mall" / "stock.json").exists()               # same name: nothing to bind
    assert "new-mall" not in [p["slug"] for p in client.get(f"{SP}/projects-without-stock").json()]


def test_upload_stock_has_no_source_link(env):
    client, d, tmp = env
    make_stock("up1", "u1", "2026-10-12T10:00:00", STOCK1, project="Up 1", filename="stock.xlsx")
    o = client.get(f"{SP}/{SLUG}/stock").json()["options"][0]
    assert o["kind"] == "Upload" and o["filename"] == "stock.xlsx" and o["source_link"] is None
