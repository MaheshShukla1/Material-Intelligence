"""The frontend folder is served to the browser, so only the files the app itself
loads may be reachable. Test fixtures, real PPE records (worker names), the dev
preview page and stray Python copies lived in the same folder and were
downloadable by anyone with the link.

Run from the repo root:
    pytest backend/test_static_lockdown.py -v
"""
import pytest
from fastapi.testclient import TestClient

from backend import api

client = TestClient(api.app)

# Exactly what index.html loads, plus the page itself.
APP_FILES = ["/", "/index.html", "/app.js", "/siteprogress.js", "/billing.js",
             "/style.css", "/siteprogress.css", "/billing.css", "/logo.svg"]

# Files that really sit in frontend/ and must NOT be downloadable.
PRIVATE_FILES = [
    "/hyatt_ppe_records.json",      # real worker names, contractors, shoe sizes
    "/thoth_ppe_records.json",
    "/api.py",                      # stale copy of backend source
    "/seed_fire_case.py",
    "/billing_fixture.json",
    "/projectbase_fixture.json",
    "/test_billing_ui.mjs",
    "/test_app_js.mjs",
    "/site_progress_preview.html",  # dev-only mock page
    "/start.bat",
]


@pytest.mark.parametrize("path", APP_FILES)
def test_the_files_the_app_loads_are_still_served(path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert len(r.content) > 0


@pytest.mark.parametrize("path", PRIVATE_FILES)
def test_private_files_in_the_frontend_folder_are_not_downloadable(path):
    assert (api.FRONTEND / path.lstrip("/")).exists(), (
        f"{path} no longer exists in frontend/ - update this list, do not drop the check")
    assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("path", [
    "/../backend/api.py", "/%2e%2e/backend/api.py", "/..%2fbackend%2fapi.py",
    "/.env", "/.git/config", "/test_x.js", "/sub/test_x.js",
])
def test_traversal_hidden_and_test_names_never_serve(path):
    assert client.get(path).status_code in (400, 404), path


def test_every_script_and_stylesheet_index_html_references_is_served():
    import re
    html = (api.FRONTEND / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"#?]+\.(?:js|css|svg))', html)
    assert refs, "index.html references no assets? the regex is wrong"
    for ref in refs:
        if ref.startswith(("http:", "https:", "data:")):
            continue
        assert client.get("/" + ref).status_code == 200, ref


def test_the_api_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RUNS", tmp_path)
    assert client.get("/api/projects").status_code == 200
