"""Site Progress -- "Rebuild" wipes ALL recorded progress of the project (every service, every room). The button is hidden in the page; as an
OPTIONAL extra lock the server's MI_ADMIN_PASSWORD, when set, is required for the reset (and for replacing an existing structure through the
template route). Not set = no password is asked (nothing breaks, the owner needs no setup)."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress as sp
from backend import structure as structure_mod

PW = "Owner@123 ₹"          # a password with spaces and a non-latin character: the header carries it URL-encoded
from urllib.parse import quote
HDR = {"X-Mi-Admin": quote(PW)}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A project with a structure and recorded progress in two services, like a live pilot."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    c = TestClient(app)
    d = tmp_path / "thoth"
    d.mkdir()
    s = structure_mod.hotel("Thoth", floors=["Floor 1"], room_labels=["Room 1", "Room 2"])
    (d / "structure.json").write_text(s.to_json())
    prog = {"Electrical": {"2.1": {"*": 0.5}}, "Fire": {"9.1": {"*": 0.3}}}
    (d / "item_progress.json").write_text(json.dumps(prog))
    return c, d, prog


def reset(c, **kw):
    return c.post("/api/siteprogress/thoth/structure/reset", **kw)


def untouched(d, prog):
    return (d / "structure.json").exists() and json.loads((d / "item_progress.json").read_text()) == prog


def test_no_password_on_the_server_means_no_password_is_asked(site, monkeypatch):
    """The owner does not have to set anything up: Rebuild just works (the page keeps the button hidden)."""
    c, d, prog = site
    monkeypatch.delenv("MI_ADMIN_PASSWORD", raising=False)
    r = reset(c)
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert not (d / "structure.json").exists()


def test_an_empty_password_value_counts_as_not_set_never_as_an_empty_password(site, monkeypatch):
    c, d, prog = site
    monkeypatch.setenv("MI_ADMIN_PASSWORD", "")
    assert reset(c, headers={"X-Mi-Admin": ""}).status_code == 200


def test_reset_without_or_with_a_wrong_password_changes_nothing(site, monkeypatch):
    c, d, prog = site
    monkeypatch.setenv("MI_ADMIN_PASSWORD", PW)
    for h in (None, {"X-Mi-Admin": ""}, {"X-Mi-Admin": quote("owner@123 ₹")}, {"X-Mi-Admin": quote(PW + "x")}, {"X-Mi-Admin": quote(PW[:-1])}):
        r = reset(c, headers=h) if h else reset(c)
        assert r.status_code == 403 and "Wrong admin password" in r.json()["detail"], h
    assert untouched(d, prog)


def test_the_right_password_resets_everything_as_before(site, monkeypatch):
    c, d, prog = site
    monkeypatch.setenv("MI_ADMIN_PASSWORD", PW)
    r = reset(c, headers=HDR)
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert not (d / "structure.json").exists() and not (d / "item_progress.json").exists()


def test_admin_check_tells_the_ui_if_the_password_opens_the_lock(site, monkeypatch):
    c, d, prog = site
    url = "/api/siteprogress/thoth/admin/check"
    monkeypatch.delenv("MI_ADMIN_PASSWORD", raising=False)
    r = c.post(url)
    assert r.status_code == 200 and r.json() == {"ok": True, "protected": False}      # nothing to type: the page unlocks straight away
    monkeypatch.setenv("MI_ADMIN_PASSWORD", PW)
    assert c.post(url).status_code == 403
    assert c.post(url, headers={"X-Mi-Admin": "nope"}).status_code == 403
    r = c.post(url, headers=HDR)
    assert r.status_code == 200 and r.json() == {"ok": True, "protected": True}
    assert untouched(d, prog)                                  # checking the password never changes data
    assert c.post("/api/siteprogress/no-such-project/admin/check", headers=HDR).status_code == 404


def test_replacing_an_existing_structure_through_the_template_route_needs_it_too(site, monkeypatch):
    c, d, prog = site
    monkeypatch.setenv("MI_ADMIN_PASSWORD", PW)
    body = {"kind": "mall", "name": "X", "levels": ["Ground"], "zones_per_level": 2}
    r = c.post("/api/siteprogress/thoth/structure/template", json=body)
    assert r.status_code == 403
    assert "Floor 1" in (d / "structure.json").read_text()      # the old tree is still there
    assert c.post("/api/siteprogress/thoth/structure/template", json=body, headers=HDR).status_code == 200


def test_first_time_setup_of_a_project_with_no_structure_needs_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    monkeypatch.delenv("MI_ADMIN_PASSWORD", raising=False)
    app = FastAPI()
    app.include_router(sp.router)
    c = TestClient(app)
    r = c.post("/api/siteprogress/new-mall/structure/template", json={"kind": "mall", "name": "New Mall", "levels": ["L1"], "zones_per_level": 2})
    assert r.status_code == 200 and r.json()["rooms"] == 2


def test_normal_editing_of_the_structure_is_not_locked(site, monkeypatch):
    """Engineers still add / rename / delete floors and rooms on site: only Rebuild is locked."""
    c, d, prog = site
    monkeypatch.delenv("MI_ADMIN_PASSWORD", raising=False)
    tree = json.loads((d / "structure.json").read_text())
    r = c.post("/api/siteprogress/thoth/structure", json={"structure": tree})
    assert r.status_code == 200
