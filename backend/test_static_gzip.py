"""Static JS/CSS go out gzipped (643 KB -> ~179 KB on the wire); API responses
are left exactly as they were (exports are already-compressed files).

Run from the repo root:
    pytest backend/test_static_gzip.py -v
"""
import gzip

import pytest
from fastapi.testclient import TestClient

from backend import api

client = TestClient(api.app)
BIG = ["app.js", "siteprogress.js", "billing.js", "style.css", "siteprogress.css", "billing.css"]


@pytest.mark.parametrize("name", BIG)
def test_static_assets_are_gzipped_and_decode_to_the_same_bytes(name):
    raw = (api.FRONTEND / name).read_bytes()
    r = client.get("/" + name, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert r.content == raw                       # the client library already decoded it
    # the bytes actually sent are much smaller
    sent = gzip.compress(raw, 6)
    assert len(sent) < 0.5 * len(raw)


def test_total_size_on_the_wire_for_the_whole_frontend_is_about_a_quarter():
    raw = sum((api.FRONTEND / n).stat().st_size for n in BIG)
    wire = sum(len(gzip.compress((api.FRONTEND / n).read_bytes(), 6)) for n in BIG)
    assert wire < 0.35 * raw, (raw, wire)


def test_clients_that_do_not_ask_for_gzip_get_plain_files():
    r = client.get("/app.js", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200 and "content-encoding" not in r.headers
    assert r.content == (api.FRONTEND / "app.js").read_bytes()


def test_static_files_still_carry_etag_and_last_modified():
    r = client.get("/app.js")
    assert r.headers.get("etag") and r.headers.get("last-modified")
    again = client.get("/app.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_api_responses_are_not_touched_even_when_large(monkeypatch):
    metas = [{"run_id": f"r{i}", "project": f"P{i}", "project_slug": f"p{i}", "created": "2026-10-01T00:00:00"}
             for i in range(400)]
    monkeypatch.setattr(api, "all_meta", lambda: metas)
    r = client.get("/api/projects", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and len(r.content) > 1024
    assert "content-encoding" not in r.headers
