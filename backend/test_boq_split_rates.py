"""A BOQ whose rate is split into 'Supply Rate' and 'Installation Rate' columns (no single 'Rate' column).

Reported (Rise Mumbai, Electrical sheet of a real ProjectBase export): after the upload every rate showed "set rate" and Work done / Remaining were Rs 0,
because boq.py read a rate only from a column named exactly "Rate". The same workbook's ELV sheet (which has a "Rate" column) read fine.
Rules under test, through the real upload route and the real service view, on rows copied from that file:
  * with both columns, the item's rate is Supply + Installation (the combined rate every other BOQ carries), and its installation share is
    Installation / (Supply + Installation) -- so Site Progress's done / remaining Rs count only the installation part, exactly as the sheet prices it;
  * a line priced on one side only (LED fixtures: installation only; first aid box: supply only) takes that side as its rate and 100 % / 0 % installation:
    nothing is invented, the sheet's own number is used;
  * a "Rate Only" line (quantity "RO") still gets its rate;
  * the same code in two sections ("1.1" appears many times) keeps its OWN rate after the codes are made unique;
  * a sheet that has a plain "Rate" column behaves exactly as before and sets no installation share;
  * a rate or installation % the engineer already edited is never overwritten by a re-upload;
  * a sheet with only ONE of the two columns uses it as the rate and sets no installation share (nothing to split).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import boq, siteprogress as sp

SLUG = "rise-mumbai"
HEAD = ["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Order Quantity", "Design Quantity", "Supply Rate", "Supply Discount %",
        "Installation Rate", "Installation Discount %", "HSN Code", "SAC Code", "Supply Tax", "Installation Tax", "Progress Method", "Billing Term"]
# (internal ref, client ref, BOQ, unit, order qty, design qty, supply rate, installation rate) -- copied from the real Electrical sheet
ROWS = [
    ("1", "A", "CIRCUIT CUM POINT WIRING", None, None, None, None, None),
    ("2", None, "Notes :", None, None, None, None, None),
    ("28", "1.1", "MCB controlled Primary point: From DB to switch board - cost", "Pt", 20, 20, 875, 350),
    ("29", "1.2", "MCB controlled Primary point: From DB to switch board - cost", "Pt", 24, 24, 1775, 700),
    ("33", "1.6", "Additional one meter cost after 25m will be measured under t", "Rmt", 1316, 1316, 230, 70),
    ("166", "B", "UNDERFLOOR RACEWAY & JUNCTION BOXES", None, None, None, None, None),
    ("204", "1.1", "3.1/2 core 300 sq. mm", "Rmt", 4376, 4376, 1600, 115),                      # the code 1.1 again, in another section
    ("208", "1.4", "3.1/2 core 185 sq. mm.", "Rmt", "RO", "RO", 1135, 100),                     # Rate Only: no quantity
    ("781", "1", "40W LED Light similar to Havells Dura LED Batten f", "Nos", 677, 677, None, 275),   # installation only
    ("964", "11", "Supply of first aid box of approved make as per specification.", "Nos", 2, 2, 950, None),   # supply only
]
PLAIN = [("1", "1.1", "MCB point", "Pt", 20, 20, 1225), ("2", "1.2", "LED fitting", "Nos", 10, 10, None)]


def workbook(path, split=True, only=None, both=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Electrical"
    if split:
        ws.append(HEAD)
        for r in ROWS:
            ws.append([r[0], r[1], r[2], r[3], r[4], r[5], r[6], None, r[7], None, None, None, "GST @ 18%", "GST @ 18%", "Cumulative", None])
    elif both:                       # a "Rate" column AND the two split columns: the plain Rate is what it always was
        ws.append(["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Order Quantity", "Design Quantity", "Rate", "Supply Rate", "Installation Rate"])
        ws.append(["1", "1.1", "MCB point", "Pt", 20, 20, 1000, 875, 350])
    elif only:
        ws.append(["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Order Quantity", "Design Quantity", only, "HSN Code"])
        ws.append(["1", "1.1", "MCB point", "Pt", 20, 20, 350, None])
    else:
        ws.append(["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Order Quantity", "Design Quantity", "Rate", "HSN Code"])
        for r in PLAIN:
            ws.append([r[0], r[1], r[2], r[3], r[4], r[5], r[6], None])
    wb.save(path)


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "projects")
    sp.PROJECTS.mkdir(parents=True)
    app = FastAPI()
    app.include_router(sp.router)
    return TestClient(app), tmp_path


def upload(c, tmp, **kw):
    x = tmp / "boq.xlsx"
    workbook(x, **kw)
    with open(x, "rb") as fh:
        r = c.post(f"/api/siteprogress/{SLUG}/boq", files={"file": ("boq.xlsx", fh)})
    assert r.status_code == 200, r.text
    return sp._dir(SLUG)


def rows_by_desc(c):
    v = c.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    out = {}
    for g in v.get("activities", []) + v.get("unmapped", []) if isinstance(v, dict) else []:
        for it in g.get("items", []):
            out[it["desc"]] = it
    return v, out


def test_split_rates_become_the_rate_and_the_installation_share(site):
    c, tmp = site
    d = upload(c, tmp)
    rates = json.loads((d / "rates.json").read_text())["Electrical"]
    ip = json.loads((d / "install_pct.json").read_text())["Electrical"]
    assert rates["1.1"] == 1225.0 and abs(ip["1.1"] - 350 / 1225 * 100) < 1e-6
    assert rates["1.2"] == 2475.0 and abs(ip["1.2"] - 700 / 2475 * 100) < 1e-6
    assert rates["1.6"] == 300.0 and abs(ip["1.6"] - 70 / 300 * 100) < 1e-6
    assert abs(rates["1.1"] * ip["1.1"] / 100 - 350) < 1e-6                         # the installation Rs per unit is the sheet's own 350 again


def test_the_same_code_in_two_sections_keeps_its_own_rate(site):
    c, tmp = site
    d = upload(c, tmp)
    rates = json.loads((d / "rates.json").read_text())["Electrical"]
    ip = json.loads((d / "install_pct.json").read_text())["Electrical"]
    dup = [k for k in rates if k.startswith("1.1")]
    assert sorted(rates[k] for k in dup) == [1225.0, 1715.0]                         # 875 + 350 in the first section, 1600 + 115 in the second
    assert {round(rates[k] * ip[k] / 100) for k in dup} == {350, 115}


def test_one_sided_and_rate_only_lines_use_the_sheets_own_number(site):
    c, tmp = site
    d = upload(c, tmp)
    rates = json.loads((d / "rates.json").read_text())["Electrical"]
    ip = json.loads((d / "install_pct.json").read_text())["Electrical"]
    assert rates["1.4"] == 1235.0                                                    # "RO" quantity, rate still read
    assert rates["1"] == 275.0 and ip["1"] == 100.0                                  # LED: installation only
    assert rates["11"] == 950.0 and ip["11"] == 0.0                                  # first aid box: supply only


def test_the_service_view_shows_the_rate_and_the_installation_rate(site):
    """What the engineer saw: 'set rate' and Rs 0. Now the row carries the combined rate and the installation part of it."""
    c, tmp = site
    upload(c, tmp)
    v = c.get(f"/api/siteprogress/{SLUG}/service/Electrical").json()
    flat = json.dumps(v)
    assert '"rate": 1225.0' in flat and '"install_rate": 350.0' in flat, flat[:600]


def test_a_plain_rate_column_behaves_as_before(site):
    c, tmp = site
    d = upload(c, tmp, split=False)
    assert json.loads((d / "rates.json").read_text())["Electrical"] == {"1.1": 1225.0}
    assert not (d / "install_pct.json").exists() or not json.loads((d / "install_pct.json").read_text()).get("Electrical")


@pytest.mark.parametrize("col", ["Supply Rate", "Installation Rate"])
def test_a_sheet_with_only_one_of_the_two_columns_has_nothing_to_split(site, col):
    c, tmp = site
    d = upload(c, tmp, split=False, only=col)
    assert json.loads((d / "rates.json").read_text())["Electrical"] == {"1.1": 350.0}
    assert not (d / "install_pct.json").exists() or not json.loads((d / "install_pct.json").read_text()).get("Electrical")


def test_a_reupload_never_overwrites_an_edited_rate_or_share(site):
    c, tmp = site
    d = upload(c, tmp)
    assert c.post(f"/api/siteprogress/{SLUG}/rates", json={"service": "Electrical", "rates": {"1.1": 999}, "install_pct": {"1.1": 40}}).status_code == 200
    upload(c, tmp)
    assert json.loads((d / "rates.json").read_text())["Electrical"]["1.1"] == 999
    assert json.loads((d / "install_pct.json").read_text())["Electrical"]["1.1"] == 40


def test_the_parser_hands_back_no_split_columns_to_the_parquet(site):
    c, tmp = site
    d = upload(c, tmp)
    import pandas as pd
    cols = pd.read_parquet(d / "boq.parquet").columns.tolist()
    assert "rate" not in cols and "install_share" not in cols


def test_parse_workbook_reports_the_split_for_the_final_codes(tmp_path):
    x = tmp_path / "b.xlsx"
    workbook(x)
    parsed, skipped = boq.parse_workbook(str(x))
    r = parsed["Electrical"]
    assert r["source_format"] == "projectbase" and len(r["rates"]) == 7              # every priced line, none dropped
    assert set(r["install_pcts"]) == set(r["rates"])


def test_a_plain_rate_column_wins_over_split_columns_next_to_it(site):
    c, tmp = site
    d = upload(c, tmp, split=False, both=True)
    assert json.loads((d / "rates.json").read_text())["Electrical"] == {"1.1": 1000.0}      # not 875 + 350
    assert not (d / "install_pct.json").exists() or not json.loads((d / "install_pct.json").read_text()).get("Electrical")
