"""What the BOQ reader reports about HOW it read a sheet, and the rate columns of ordinary (non-ProjectBase) BOQs.

Need (any firm's BOQ must upload without the engineer guessing): after an upload the app says which column it used for Code / Description / Unit /
Qty / Rate, how many lines it read and priced, and -- when the sheet has an Amount column -- whether Amount / Qty really equals the rate it read.
Rules under test, through boq.parse_workbook and the real upload route:
  * a normal BOQ with a "Rate (Rs.)" / "Unit Rate" / "Rate in Rs" column now has its rates read; before, rate was never read from such a sheet;
  * "Supply Rate" + "Installation Rate" work in the normal parser exactly as in the ProjectBase one (rate = sum, installation share per line);
  * a "Rate" column is never mistaken for the Unit column, and "Amount" is NEVER used as a rate;
  * two rate-like columns and an Amount column: the one whose Qty x Rate equals the Amount wins; with no Amount to decide, NO rate is taken and the
    report lists the columns to choose from (nothing is guessed);
  * the check says ok / mismatch / none (no Amount column), with the first lines that do not tie when it does not;
  * a column the engineer chose is used, remembered for that sheet, and replaces only rates the engineer never edited.
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

SLUG = "any-firm"


def book(path, header, rows, title="Electrical"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(["PROJECT: Some Tower", None, None])
    ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def parse(tmp_path, header, rows, **kw):
    x = tmp_path / "b.xlsx"
    book(x, header, rows)
    parsed, skipped = boq.parse_workbook(str(x), **kw)
    return parsed["Electrical"]


H = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate (Rs.)", "Amount (Rs.)"]
GOOD = [[1, "Wire 2.5 sqmm", "Rmt", 100, 12.5, 1250],
        [2, "MCB 16A single pole", "Nos", 10, 250, 2500],
        [3, "Cable tray 300mm", "Mtr", 40, 480, 19200],
        [4, "Light fitting", "Nos", 7, 1000, 7000]]


def test_a_normal_boq_with_a_rate_column_now_has_its_rates(tmp_path):
    r = parse(tmp_path, H, GOOD)
    assert r["rates"] == {"1": 12.5, "2": 250.0, "3": 480.0, "4": 1000.0}
    assert r["install_pcts"] == {}
    assert [(c["role"], c["header"]) for c in r["report"]["sheets"][0]["columns"]] == [
        ("Code", "Sr. No."), ("Description", "Description of Work"), ("Unit", "Unit"), ("Qty", "Qty"), ("Rate", "Rate (Rs.)")]


@pytest.mark.parametrize("name", ["Rate", "Unit Rate", "Rate in Rs", "RATE (INR)", "Rate per Unit"])
def test_the_usual_ways_of_writing_a_rate_header(tmp_path, name):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", name]
    r = parse(tmp_path, h, [[x[0], x[1], x[2], x[3], x[4]] for x in GOOD])
    assert r["rates"]["2"] == 250.0


def test_a_rate_column_is_not_taken_for_the_unit_column(tmp_path):
    """'Unit Rate' sits LEFT of 'Unit': the unit words must still come from 'Unit'."""
    h = ["Sr. No.", "Description of Work", "Unit Rate", "Unit", "Qty"]
    r = parse(tmp_path, h, [[x[0], x[1], x[4], x[2], x[3]] for x in GOOD])
    assert list(r["items"]["unit"]) == ["Rmt", "Nos", "Mtr", "Nos"] and r["rates"]["3"] == 480.0


def test_amount_is_never_a_rate(tmp_path):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Amount (Rs.)"]
    r = parse(tmp_path, h, [[x[0], x[1], x[2], x[3], x[5]] for x in GOOD])
    assert r["rates"] == {}
    sh = r["report"]["sheets"][0]
    assert not [c for c in sh["columns"] if c["role"] == "Rate"] and sh["check"]["status"] == "none"


def test_supply_and_installation_rate_in_a_normal_boq(tmp_path):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Supply Rate (INR)", "Installation Rate (INR)"]
    rows = [[1, "MCB point", "Pt", 20, 875, 350], [2, "LED fitting", "Nos", 9, None, 275], [3, "First aid box", "Nos", 2, 950, None]]
    r = parse(tmp_path, h, rows)
    assert r["rates"] == {"1": 1225.0, "2": 275.0, "3": 950.0}
    assert abs(r["install_pcts"]["1"] - 350 / 1225 * 100) < 1e-6 and r["install_pcts"]["2"] == 100.0 and r["install_pcts"]["3"] == 0.0
    assert [c["header"] for c in r["report"]["sheets"][0]["columns"] if c["role"] == "Rate"] == ["Supply Rate (INR) + Installation Rate (INR)"]


def test_the_check_says_ok_when_amount_ties_to_qty_times_rate(tmp_path):
    ck = parse(tmp_path, H, GOOD)["report"]["sheets"][0]["check"]
    assert ck["status"] == "ok" and ck["checked"] == 4 and ck["matched"] == 4


def test_the_check_lists_the_lines_that_do_not_tie(tmp_path):
    rows = [list(x) for x in GOOD]
    rows[1][5] = 2000                                                                    # 10 x 250 = 2500, the sheet says 2000
    rows[3][5] = 7500
    ck = parse(tmp_path, H, rows)["report"]["sheets"][0]["check"]
    assert ck["status"] == "mismatch" and ck["checked"] == 4 and ck["matched"] == 2
    assert [b["code"] for b in ck["bad"]] == ["2", "4"] and ck["bad"][0]["amount"] == 2000 and ck["bad"][0]["expected"] == 2500


def test_no_amount_column_means_not_checkable_not_failed(tmp_path):
    h = H[:5]
    ck = parse(tmp_path, h, [x[:5] for x in GOOD])["report"]["sheets"][0]["check"]
    assert ck["status"] == "none"


def test_two_rate_columns_are_told_apart_by_the_amount(tmp_path):
    """'Rate' is the old rate, 'Unit Rate' the one the Amount was built from."""
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate", "Unit Rate", "Amount"]
    rows = [[x[0], x[1], x[2], x[3], x[4] + 5, x[4], x[5]] for x in GOOD]
    r = parse(tmp_path, h, rows)
    assert r["rates"]["2"] == 250.0
    assert r["report"]["sheets"][0]["check"]["status"] == "ok"


def test_two_rate_columns_and_nothing_to_decide_take_no_rate_and_ask(tmp_path):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate", "Unit Rate"]
    rows = [[x[0], x[1], x[2], x[3], x[4] + 5, x[4]] for x in GOOD]
    r = parse(tmp_path, h, rows)
    assert r["rates"] == {}                                                              # not guessed
    sh = r["report"]["sheets"][0]
    assert sh["choices"] == [{"role": "rate", "headers": ["Rate", "Unit Rate"]}] and sh["rated"] == 0
    assert any("Rate" in i for i in sh["issues"])


def test_a_chosen_rate_column_is_used(tmp_path):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate", "Unit Rate"]
    rows = [[x[0], x[1], x[2], x[3], x[4] + 5, x[4]] for x in GOOD]
    r = parse(tmp_path, h, rows, overrides={"Electrical": {"rate": "Unit Rate"}})
    assert r["rates"]["2"] == 250.0 and r["report"]["sheets"][0]["choices"] == []


def test_the_report_counts_lines_priced_lines_and_value(tmp_path):
    rows = [list(x) for x in GOOD]
    rows[3][4] = None                                                                    # one line without a rate
    rep = parse(tmp_path, H, rows)["report"]
    assert (rep["lines"], rep["rated"]) == (4, 3)
    assert rep["value"] == 100 * 12.5 + 10 * 250 + 40 * 480                              # only lines that have both a qty and a rate


def test_a_sheet_without_any_rate_column_reads_exactly_as_before(tmp_path):
    h = ["Sr. No.", "Description of Work", "Unit", "Qty"]
    r = parse(tmp_path, h, [x[:4] for x in GOOD])
    assert r["rates"] == {} and "rate" not in r["items"].columns
    sh = r["report"]["sheets"][0]
    assert sh["rated"] == 0 and sh["check"]["status"] == "none"


# ----------------------------------------------------------------- through the real upload route
@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "projects")
    sp.PROJECTS.mkdir(parents=True)
    app = FastAPI()
    app.include_router(sp.router)
    return TestClient(app), tmp_path


def post_boq(c, tmp, header, rows):
    x = tmp / "boq.xlsx"
    book(x, header, rows)
    with open(x, "rb") as fh:
        r = c.post(f"/api/siteprogress/{SLUG}/boq", files={"file": ("boq.xlsx", fh)})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_upload_answer_carries_the_read_report(site):
    c, tmp = site
    j = post_boq(c, tmp, H, GOOD)
    rep = j["read"]["Electrical"]
    assert rep["lines"] == 4 and rep["rated"] == 4 and rep["sheets"][0]["check"]["status"] == "ok"
    assert json.loads((sp._dir(SLUG) / "rates.json").read_text())["Electrical"]["2"] == 250.0


def test_choosing_the_column_reads_again_remembers_it_and_keeps_edited_rates(site):
    c, tmp = site
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate", "Unit Rate"]
    rows = [[x[0], x[1], x[2], x[3], x[4] + 5, x[4]] for x in GOOD]
    j = post_boq(c, tmp, h, rows)
    assert j["read"]["Electrical"]["sheets"][0]["choices"] and not (sp._dir(SLUG) / "rates.json").read_text().count('"2"')
    assert c.post(f"/api/siteprogress/{SLUG}/rates", json={"service": "Electrical", "rates": {"3": 999}}).status_code == 200   # engineer edits line 3
    r = c.post(f"/api/siteprogress/{SLUG}/boq/column", json={"sheet": "Electrical", "role": "rate", "header": "Unit Rate"})
    assert r.status_code == 200, r.text
    rates = json.loads((sp._dir(SLUG) / "rates.json").read_text())["Electrical"]
    assert rates["2"] == 250.0 and rates["3"] == 999                                      # the edited one stays
    assert r.json()["read"]["Electrical"]["sheets"][0]["choices"] == []
    j2 = post_boq(c, tmp, h, rows)                                                        # a re-upload remembers the choice: no question again
    assert j2["read"]["Electrical"]["sheets"][0]["choices"] == []


def test_choosing_again_replaces_only_what_the_previous_choice_put_there(site):
    c, tmp = site
    h = ["Sr. No.", "Description of Work", "Unit", "Qty", "Rate", "Unit Rate"]
    rows = [[x[0], x[1], x[2], x[3], x[4] + 5, x[4]] for x in GOOD]
    post_boq(c, tmp, h, rows)
    c.post(f"/api/siteprogress/{SLUG}/boq/column", json={"sheet": "Electrical", "role": "rate", "header": "Rate"})
    c.post(f"/api/siteprogress/{SLUG}/rates", json={"service": "Electrical", "rates": {"4": 5}})
    c.post(f"/api/siteprogress/{SLUG}/boq/column", json={"sheet": "Electrical", "role": "rate", "header": "Unit Rate"})
    rates = json.loads((sp._dir(SLUG) / "rates.json").read_text())["Electrical"]
    assert rates["1"] == 12.5 and rates["2"] == 250.0 and rates["4"] == 5                # 1, 2 follow the new choice; 4 was edited by hand


def test_a_column_that_is_not_in_the_sheet_is_refused(site):
    c, tmp = site
    post_boq(c, tmp, H, GOOD)
    r = c.post(f"/api/siteprogress/{SLUG}/boq/column", json={"sheet": "Electrical", "role": "rate", "header": "Nope"})
    assert r.status_code == 400


def test_a_line_that_looks_like_a_heading_but_has_a_unit_and_quantity_is_reported(tmp_path):
    """The old heading rule (short number code + capital-letter text) still applies -- but it is no longer silent."""
    rows = [list(x) for x in GOOD]
    rows[1][1] = "MCB 16A"
    r = parse(tmp_path, H, rows)
    assert "2" not in r["rates"]                                                          # existing behaviour, unchanged
    sh = r["report"]["sheets"][0]
    assert any("MCB 16A" in i and "section headings" in i for i in sh["issues"]), sh["issues"]
