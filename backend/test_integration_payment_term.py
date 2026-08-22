"""End-to-end test of the payment-term routes through the real FastAPI
router (settings + rates + the service GET view), against a minimal
on-disk project fixture -- not just the pnl.py unit math."""
import sys, json, shutil
from pathlib import Path
sys.path.insert(0, "/home/claude")

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure

SLUG = "test-thoth"
PROJECT_DIR = siteprogress.PROJECTS / SLUG


def _reset_fixture():
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True)

    # one floor, one room -- so qty_per_room x 1 room == planned_total, and
    # the numbers are easy to hand-check.
    s = structure.hotel("Thoth Mall", floors=["B1"], room_labels=["Zone 1"])
    (PROJECT_DIR / "structure.json").write_text(s.to_json())

    boq_df = pd.DataFrame([{
        "service": "Electrical", "item_code": "Q12",
        "description": "150X50X2MM CABLE TRAY", "unit": "MTR",
        "qty": 1700.0, "section": None, "item_code_raw": "Q12",
        "subcategory": "Cable Tray",
    }])
    boq_df.to_parquet(PROJECT_DIR / "boq.parquet")

    # 100/1700 of the single room done -> used == 100 MTR, matching the
    # real screenshot numbers.
    (PROJECT_DIR / "item_progress.json").write_text(json.dumps(
        {"Electrical": {"Q12": {"*": 100.0 / 1700.0}}}))
    (PROJECT_DIR / "rates.json").write_text(json.dumps(
        {"Electrical": {"Q12": 97.0}}))
    # a real project always has the item mapped to an activity -- rollup_pnl's
    # service/project totals (unlike the per-item row list) only count MAPPED
    # items by design, so /overall needs this to see any value at all.
    (PROJECT_DIR / "activities.json").write_text(json.dumps(
        {"Electrical": ["Cable Tray"]}))
    (PROJECT_DIR / "mapping.json").write_text(json.dumps(
        {"Electrical": {"Cable Tray": ["Q12"]}}))


app = FastAPI()
app.include_router(siteprogress.router)
client = TestClient(app)


def test_no_settings_saved_yet_matches_old_full_rate_behaviour():
    _reset_fixture()
    r = client.get(f"/api/siteprogress/{SLUG}/service/Electrical")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["code"] == "Q12"
    # no payment term configured anywhere -> done_val should be the FULL
    # combined-rate figure, exactly like before this feature existed.
    assert round(item["done_val"], 2) == round(100.0 * 97.0, 2)
    assert round(item["full_val"], 2) == round(1700.0 * 97.0, 2)


def test_settings_route_sets_project_default_and_view_reflects_it():
    _reset_fixture()
    r = client.post(f"/api/siteprogress/{SLUG}/settings",
                    json={"default_install_pct": 15})
    assert r.status_code == 200
    assert r.json()["default_install_pct"] == 15.0

    v = client.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    item = v["items"][0]
    assert round(item["done_val"], 2) == 1455.0        # 100 * 97 * 0.15
    assert round(item["rem_val"], 2) == 23280.0         # 1600 * 97 * 0.15
    assert round(item["full_val"], 2) == 164900.0       # unchanged reference
    assert item["install_pct"] == 15.0
    # done + remaining == planned exactly, the "clean installation P&L" ask
    planned_total_val = round(item["done_val"] + item["rem_val"], 2)
    assert planned_total_val == round(1700.0 * 97.0 * 0.15, 2)


def test_rates_route_saves_per_item_override_alongside_rate():
    _reset_fixture()
    client.post(f"/api/siteprogress/{SLUG}/settings", json={"default_install_pct": 15})
    r = client.post(f"/api/siteprogress/{SLUG}/rates",
                    json={"service": "Electrical",
                          "rates": {"Q12": 97},
                          "install_pct": {"Q12": 20}})
    assert r.status_code == 200
    on_disk = json.loads((PROJECT_DIR / "install_pct.json").read_text())
    assert on_disk["Electrical"]["Q12"] == 20

    v = client.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    item = v["items"][0]
    assert item["install_pct"] == 20.0                  # override wins over the 15% default
    assert round(item["done_val"], 2) == 100.0 * 97.0 * 0.20


def test_clearing_override_falls_back_to_project_default():
    _reset_fixture()
    client.post(f"/api/siteprogress/{SLUG}/settings", json={"default_install_pct": 15})
    client.post(f"/api/siteprogress/{SLUG}/rates",
               json={"service": "Electrical", "rates": {"Q12": 97}, "install_pct": {"Q12": 20}})
    client.post(f"/api/siteprogress/{SLUG}/rates",
               json={"service": "Electrical", "rates": {"Q12": 97}, "install_pct": {"Q12": None}})
    v = client.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    item = v["items"][0]
    assert item["install_pct"] == 15.0                  # back to project default


def test_overall_route_carries_full_value_and_scaled_done_value():
    _reset_fixture()
    client.post(f"/api/siteprogress/{SLUG}/settings", json={"default_install_pct": 15})
    r = client.get(f"/api/siteprogress/{SLUG}/overall")
    assert r.status_code == 200
    body = r.json()
    assert round(body["full_value"], 2) == 164900.0
    assert round(body["done_value"], 2) == 1455.0
    assert round(body["by_service"]["Electrical"]["full_value"], 2) == 164900.0


def test_get_state_exposes_settings_for_modal_prefill():
    _reset_fixture()
    client.post(f"/api/siteprogress/{SLUG}/settings", json={"default_install_pct": 15})
    st = client.get(f"/api/siteprogress/{SLUG}").json()
    assert st["settings"]["default_install_pct"] == 15.0


def test_install_pct_own_only_set_when_item_has_explicit_override():
    """The 'Set rates' modal must be able to tell 'this item has its own
    override' apart from 'inheriting the project default' -- otherwise it
    would pre-fill every row with the resolved percent and silently turn
    every item into an explicit override on the next save."""
    _reset_fixture()
    client.post(f"/api/siteprogress/{SLUG}/settings", json={"default_install_pct": 15})
    v = client.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    item = v["items"][0]
    assert item["install_pct"] == 15.0        # effective (inherited)
    assert item["install_pct_own"] is None    # but NOT its own override

    client.post(f"/api/siteprogress/{SLUG}/rates",
               json={"service": "Electrical", "rates": {"Q12": 97}, "install_pct": {"Q12": 20}})
    v2 = client.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    item2 = v2["items"][0]
    assert item2["install_pct"] == 20.0
    assert item2["install_pct_own"] == 20.0   # now it IS its own override


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-v", __file__]))
