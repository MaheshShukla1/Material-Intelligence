"""Billing -- challan-based supply, challan import, and the per-RA invoice workbook
(the user's own 4-sheet invoice layout). Same harness as test_billing.py:
synthetic ProjectBase workbook, real FastAPI routers, data in tmp_path."""
import io
import os
import sys
import datetime as dt
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress as sp, billing, structure
from backend.test_billing import (env, make_run, link, progress, line, STOCK1, SLUG,  # noqa: F401  (env is a fixture)
                                  _recalc)

B = f"/api/billing/{SLUG}"


def cs(client, sup=70, ins=30, svc="Fire", mode="separate"):
    r = client.post(f"{B}/setup", json={"service": svc, "mode": mode, "supply_pct": sup, "install_pct": ins,
                                          "supply_source": "challan"})
    assert r.status_code == 200, r.text


def ch(client, no, date, lines, svc="Fire", **kw):
    return client.post(f"{B}/challans/{svc}", json={"no": no, "date": date,
                                                   "lines": [{"item_code": c, "qty": q} for c, q in lines], **kw})


def new_ra(client, series="supply", svc="Fire"):
    r = client.post(f"{B}/ra", json={"service": svc, "series": series})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def bill(client, ra):
    return client.get(f"{B}/ra/{ra}").json()


# ------------------------------------------------------- challan -> RA
def test_challans_become_the_supply_quantity_and_amount_of_the_open_ra(env):
    client, _, _ = env
    cs(client)
    assert ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)]).status_code == 200
    b = bill(client, new_ra(client))
    pump, valve = line(b, "1")["supply"], line(b, "2")["supply"]
    assert (pump["this"], pump["source"]) == (2, "challan")
    assert pump["amount"] == pytest.approx(100000 * 2 * .70)
    assert valve["this"] == 1 and valve["amount"] == pytest.approx(5000 * 1 * .70)
    assert b["this_total"] == pytest.approx(143500)
    assert "614" in pump["note"]
    assert {x["item_code"] for x in b["lines"]} == {"1", "2"}         # nothing else has a challan


def test_ra2_carries_only_new_challans_and_ra1_never_moves(env):
    client, _, _ = env
    cs(client)
    ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)])
    ra1 = new_ra(client)
    client.post(f"{B}/ra/{ra1}/lock")
    snap1 = bill(client, ra1)
    r = ch(client, "616", "2026-08-13", [("2", 2), ("3", 40)])         # default tag = the next RA (2)
    assert r.status_code == 200
    ra2 = new_ra(client)
    b = bill(client, ra2)
    assert b["ra"]["no"] == 2
    valve = line(b, "2")["supply"]
    assert (valve["prev"], valve["utd"], valve["this"]) == (1, 3, 2)
    assert line(b, "3")["supply"]["amount"] == pytest.approx(800 * 40 * .70)
    assert all(x["item_code"] != "1" for x in b["lines"])               # pump fully billed in RA 1
    assert b["this_total"] == pytest.approx(5000 * 2 * .7 + 800 * 40 * .7)
    assert bill(client, ra1)["this_total"] == snap1["this_total"] == pytest.approx(143500)


def test_a_challan_in_a_locked_ra_is_frozen_until_that_ra_is_reopened(env):
    client, _, _ = env
    cs(client)
    ch(client, "614", "2026-07-04", [("1", 2)])
    ra1 = new_ra(client)
    client.post(f"{B}/ra/{ra1}/lock")
    cid = client.get(f"{B}/challans/Fire").json()["challans"][0]["id"]
    assert client.get(f"{B}/challans/Fire").json()["challans"][0]["frozen"] is True
    r = client.post(f"{B}/challans/Fire/{cid}", json={"no": "614", "date": "2026-07-04", "lines": [{"item_code": "1", "qty": 1}]})
    assert r.status_code == 409 and "RA-01" in r.json()["detail"]
    assert client.delete(f"{B}/challans/Fire/{cid}").status_code == 409
    assert client.post(f"{B}/ra/{ra1}/reopen").status_code == 200
    r = client.post(f"{B}/challans/Fire/{cid}", json={"no": "614", "date": "2026-07-04", "lines": [{"item_code": "1", "qty": 1}]})
    assert r.status_code == 200
    assert line(bill(client, ra1), "1")["supply"]["this"] == 1


def test_challan_validation(env):
    client, _, _ = env
    cs(client)
    assert ch(client, "614", "2026-07-04", [("1", 2)]).status_code == 200
    assert ch(client, " 614 ", "2026-07-05", [("1", 1)]).status_code == 409           # same number twice
    assert ch(client, "615", "2026-07-05", [("99", 1)]).status_code == 400            # not a billable line
    assert ch(client, "615", "2026-07-05", [("4", 1)]).status_code == 400             # the RO line is not billable
    assert ch(client, "615", "2026-07-05", [("1", 0)]).status_code == 400
    assert ch(client, "615", "2026-07-05", [("1", 1), ("1", 1)]).status_code == 400   # same line twice
    assert ch(client, "615", "2026-07-05", []).status_code == 400
    assert ch(client, "615", "05/07/2026x", [("1", 1)]).status_code == 400
    assert ch(client, "", "2026-07-05", [("1", 1)]).status_code == 400
    assert ch(client, "615", "2026-07-05", [("1", 1)], ra=0).status_code == 409


def test_more_challan_qty_than_the_boq_is_billed_only_up_to_the_boq_and_flagged(env):
    client, _, _ = env
    cs(client)
    ch(client, "700", "2026-07-04", [("2", 3)])
    ch(client, "701", "2026-07-06", [("2", 3)])                                        # 6 delivered, BOQ is 4
    b = bill(client, new_ra(client))
    c = line(b, "2")["supply"]
    assert (c["utd"], c["this"]) == (4, 4)
    assert any("BOQ qty" in w["msg"] for w in b["warnings"])
    assert client.get(f"{B}/challans/Fire").json()["over"][0]["challan"] == 6


def test_held_and_future_challans_are_not_in_the_open_ra(env):
    client, _, _ = env
    cs(client)
    ch(client, "1", "2026-07-04", [("1", 1)])
    ch(client, "2", "2026-07-04", [("2", 1)], ra=None)           # on hold
    ch(client, "3", "2026-07-04", [("3", 10)], ra=3)             # pre-tagged for RA 3
    b = bill(client, new_ra(client))
    assert [x["item_code"] for x in b["lines"]] == ["1"]
    states = {c["no"]: c["state"] for c in client.get(f"{B}/challans/Fire").json()["challans"]}
    assert states == {"1": "open", "2": "held", "3": "later"}
    assert ch(client, "4", "2026-07-04", [("1", 1)], ra=0).status_code == 409


def test_manual_typed_qty_still_works_on_a_challan_service(env):
    client, _, _ = env
    cs(client)
    ra = new_ra(client)
    r = client.post(f"{B}/ra/{ra}/line", json={"item_code": "5", "stage": "supply", "qty": 3, "reason": "bill of lading"})
    assert r.status_code == 200
    c = line(bill(client, ra), "5")["supply"]
    assert c["this"] == 3 and c["overridden"]


def test_stock_register_is_only_a_cross_check_for_challan_services(env):
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    link(client, "2", "BUTTERFLY VALVE 150MM")      # stock says 3 received
    link(client, "3", "GI PIPE 100MM")              # stock says 50 received
    cs(client)
    ch(client, "614", "2026-07-04", [("2", 1)])
    j = client.get(f"{B}/challans/Fire").json()
    chk = {x["item_code"]: x for x in j["stock_check"]}
    assert chk["2"]["challan"] == 1 and chk["2"]["stock"] == 3 and chk["2"]["diff"] == -2
    assert chk["3"]["challan"] == 0 and chk["3"]["stock"] == 50
    b = bill(client, new_ra(client))
    assert line(b, "2")["supply"]["this"] == 1               # the bill follows the challans, not the stock
    assert line(b, "2")["supply"]["stock_qty"] == 3
    assert all(x["item_code"] != "3" for x in b["lines"])


def test_challans_need_a_challan_service(env):
    client, _, _ = env
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30})
    r = ch(client, "1", "2026-07-04", [("1", 1)])
    assert r.status_code == 409 and "Challans" in r.json()["detail"]
    r = client.post(f"{B}/challans/HVAC", json={"no": "1", "date": "2026-07-04", "lines": [{"item_code": "1", "qty": 1}]})
    assert r.status_code == 409 and "setup" in r.json()["detail"].lower()


def test_supply_source_cannot_change_once_a_supply_ra_is_locked(env):
    client, _, _ = env
    cs(client)
    ch(client, "1", "2026-07-04", [("1", 1)])
    client.post(f"{B}/ra/{new_ra(client)}/lock")
    r = client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30, "supply_source": "stock"})
    assert r.status_code == 409
    # saving the % again without naming a source keeps the challan source
    r = client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 75, "install_pct": 25})
    assert r.status_code == 200
    assert next(x for x in client.get(f"{B}/setup").json()["services"] if x["service"] == "Fire")["supply_source"] == "challan"


def test_together_service_tags_challans_to_the_combined_series(env):
    client, _, _ = env
    cs(client, 75, 25, svc="HVAC", mode="together")
    assert ch(client, "9", "2026-07-04", [("1", 1)], svc="HVAC").status_code == 200
    client.post(f"/api/siteprogress/{SLUG}/progress/item", json={"service": "HVAC", "item_code": "1", "frac": 0.5})
    ra = new_ra(client, "together", "HVAC")
    b = bill(client, ra)
    row = line(b, "1")
    assert row["supply"]["this"] == 1 and row["install"]["this"] == pytest.approx(1)
    assert b["this_total"] == pytest.approx(5000000 * 1 * .75 + 5000000 * 1 * .25)
    client.post(f"{B}/ra/{ra}/lock")
    assert client.get(f"{B}/challans/HVAC").json()["open_no"] == 2


# ------------------------------------------------------------- import
def his_sheet(path, rows=None, tags=("RA-01", "RA-01"), dates=("04.07.26", dt.datetime(2026, 8, 13)), nos=(614, 616)):
    """A CHALLAN sheet laid out like the user's own HVAC invoice workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CHALLAN"
    ws["A1"] = "Thoth Mall and Commercial Real Estate Pvt. Ltd"
    for j, (dte, no, tag) in enumerate(zip(dates, nos, tags)):
        col = 6 + j
        ws.cell(3, col, "Challan Date"); ws.cell(4, col, dte)
        ws.cell(5, col, "Challan No"); ws.cell(6, col, no)
        ws.cell(7, col, tag); ws.cell(8, col, "Qty")
    for i, h in enumerate(["Sr. No", "Description", "Unit", "Qty"], start=1):
        ws.cell(8, i, h)
    rows = rows or [
        (None, "FIRE PUMP & ACCESSORIES", None, None, {}),
        (1, "Diesel engine pump set", "Nos", 2, {0: 2}),
        (2, "Butterfly valve 150mm", "Nos", 4, {1: 1}),
        (3, "GI pipe 100mm", "Rmt", 100, {1: 40}),
        (4, "Rate only fitting", "Nos", "RO", {}),
        (5, "Something MI does not have", "Nos", 3, {0: 1}),
        (6, "Hydrant valve", "Nos", 10, {}),
    ]
    for k, (sr, desc, unit, qty, per) in enumerate(rows):
        r = 9 + k
        ws.cell(r, 1, sr); ws.cell(r, 2, desc); ws.cell(r, 3, unit); ws.cell(r, 4, qty)
        for j, q in per.items():
            ws.cell(r, 6 + j, q)
    # a decoy abstract sheet with no challan labels
    ab = wb.create_sheet("CHALLAN ABTRACT")
    ab["E7"], ab["E8"] = "CERTIFIED QTY", "RA 01"
    wb.save(path)
    return path


def imp(client, path, commit=False, lock_upto=0, svc="Fire"):
    with open(path, "rb") as fh:
        return client.post(f"{B}/challans/{svc}/import", files={"file": ("c.xlsx", fh)},
                           data={"commit": "true" if commit else "false", "lock_upto": str(lock_upto)})


def test_import_preview_changes_nothing_and_commit_is_idempotent(env, tmp_path):
    client, _, _ = env
    cs(client)
    p = his_sheet(tmp_path / "c.xlsx")
    j = imp(client, p).json()
    assert j["sheet"] == "CHALLAN" and j["committed"] is False
    assert [(c["no"], c["date"], c["ra"], c["lines"]) for c in j["challans"]] == [("614", "2026-07-04", 1, 1), ("616", "2026-08-13", 1, 2)]
    assert [(u["challan"], u["desc"], u["qty"]) for u in j["unmatched"]] == [("614", "Something MI does not have", 1)]
    assert client.get(f"{B}/challans/Fire").json()["challans"] == []
    j = imp(client, p, commit=True).json()
    assert j["committed"] is True and j["new"] == 2
    assert j["by_ra"] == [{"ra": 1, "lines": 3, "value": pytest.approx((200000 + 5000 + 32000) * .70)}]
    b = bill(client, new_ra(client))
    assert b["this_total"] == pytest.approx((200000 + 5000 + 32000) * .70)
    again = imp(client, p, commit=True)
    assert again.status_code == 409 and "Nothing new" in again.json()["detail"]


def test_import_brings_an_already_submitted_ra_in_as_locked(env, tmp_path):
    client, _, _ = env
    cs(client)
    p = his_sheet(tmp_path / "c.xlsx")
    j = imp(client, p, lock_upto=1).json()                 # preview with opening RA
    assert j["opening_totals"] == {"1": pytest.approx(237000 * .70)}
    assert client.get(f"{B}").json()["ras"] == []          # preview saved nothing
    j = imp(client, p, commit=True, lock_upto=1).json()
    ov = client.get(f"{B}").json()
    assert [(r["no"], r["status"]) for r in ov["ras"]] == [(1, "locked")]
    assert client.get(f"{B}/challans/Fire").json()["open_no"] == 2
    ch(client, "620", "2026-09-01", [("5", 4)])
    b = bill(client, new_ra(client))
    assert b["ra"]["no"] == 2 and [x["item_code"] for x in b["lines"]] == ["5"]
    assert line(b, "5")["supply"]["amount"] == pytest.approx(3000 * 4 * .7)


def test_import_refuses_tags_of_locked_ras_and_opening_for_non_separate_services(env, tmp_path):
    client, _, _ = env
    cs(client)
    ch(client, "1", "2026-07-01", [("1", 1)])
    client.post(f"{B}/ra/{new_ra(client)}/lock")
    j = imp(client, his_sheet(tmp_path / "c.xlsx")).json()
    assert j["new"] == 0 and len(j["problems"]) == 2 and "already locked" in j["problems"][0]
    cs(client, 75, 25, svc="HVAC", mode="together")
    r = imp(client, his_sheet(tmp_path / "c2.xlsx"), lock_upto=1, svc="HVAC")
    assert r.status_code == 409


def test_import_pairs_repeated_descriptions_by_quantity_and_never_guesses(env, tmp_path):
    client, d, _ = env
    # two BOQ lines with the same words but different quantities
    wb = openpyxl.load_workbook(d / "boq.xlsx")
    ws = wb["Fire"]
    ws.append(["20", "9", "Hydrant valve", "Nos", 6, 6, 3000, None, None, "GST @ 18%", None, None, None])
    wb.save(d / "boq.xlsx")
    from backend import siteprogress as _sp
    with open(d / "boq.xlsx", "rb") as fh:
        assert client.post(f"/api/siteprogress/{SLUG}/boq", files={"file": ("boq.xlsx", fh)}).status_code == 200
    cs(client)
    rows = [(1, "Hydrant valve", "Nos", 6, {0: 2}), (2, "Hydrant valve", "Nos", 10, {1: 5})]
    j = imp(client, his_sheet(tmp_path / "c.xlsx", rows=rows)).json()
    assert j["unmatched"] == [] and [c["lines"] for c in j["challans"]] == [1, 1]
    # same words, same quantity on both sides but only one on the BOQ side -> ambiguous -> not guessed
    rows = [(1, "Hydrant valve", "Nos", 6, {0: 2}), (2, "Hydrant valve", "Nos", 6, {1: 5})]
    j = imp(client, his_sheet(tmp_path / "c3.xlsx", rows=rows)).json()
    assert len(j["unmatched"]) == 2


def test_import_rejects_a_workbook_without_a_challan_sheet(env, tmp_path):
    client, _, _ = env
    cs(client)
    wb = openpyxl.Workbook()
    wb.active["A1"] = "nothing here"
    wb.save(tmp_path / "x.xlsx")
    r = imp(client, tmp_path / "x.xlsx")
    assert r.status_code == 400 and "challan" in r.json()["detail"].lower()
    (tmp_path / "bad.xlsx").write_bytes(b"not excel")
    assert imp(client, tmp_path / "bad.xlsx").status_code == 400


# ------------------------------------------------------- invoice workbook
def _inv(client, ra, tmp, name):
    r = client.get(f"{B}/invoice/{ra}")
    assert r.status_code == 200, r.text
    p = tmp / name
    p.write_bytes(r.content)
    return p


def _find(ws, text, col=2):
    return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, col).value == text)


def _row_of(ws, desc):
    return next(r for r in range(9, ws.max_row + 1) if ws.cell(r, 2).value == desc)


def test_invoice_workbook_has_the_five_sheets_and_the_numbers_of_the_ra(env):
    client, _, tmp = env
    cs(client)
    client.post(f"{B}/meta", json={"client": "Thoth Mall", "contractor": "Vedant Engitech", "wo_no": "WO/12"})
    ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)])
    ch(client, "616", "2026-08-13", [("2", 2), ("3", 40)])
    ra = new_ra(client)
    b = bill(client, ra)
    p = _inv(client, ra, tmp, "inv.xlsx")
    wb = openpyxl.load_workbook(p)
    assert wb.sheetnames == ["RA-01 INVOICE", "Annexure B-Summary BOQ", "Annexure B-BOQ", "CHALLAN ABTRACT", "CHALLAN"]
    rc = _recalc(p, tmp / "rc")
    bq, ab, cl, sm = rc["Annexure B-BOQ"], rc["CHALLAN ABTRACT"], rc["CHALLAN"], rc["Annexure B-Summary BOQ"]
    assert bq["A1"].value == "Thoth Mall" and bq["B2"].value == "WO/12" and bq["B4"].value == "Vedant Engitech"
    assert bq["G7"].value == "RA-01" and bq["H8"].value == "PREVIOUS BILL QTY" and bq["I8"].value == "THIS BILL QTY"
    r = _row_of(bq, "Diesel engine pump set")
    assert [bq.cell(r, c).value for c in (3, 4, 5, 6)] == ["Nos", 2, 100000, 200000]
    assert bq.cell(r, 7).value == pytest.approx(70000)                  # rate x 70%
    assert (bq.cell(r, 8).value or 0, bq.cell(r, 9).value, bq.cell(r, 10).value) == (0, 2, 2)
    assert bq.cell(r, 12).value == pytest.approx(140000) and bq.cell(r, 13).value == pytest.approx(140000)
    rv = _row_of(bq, "Butterfly valve 150mm")
    assert bq.cell(rv, 9).value == 3 and bq.cell(rv, 12).value == pytest.approx(5000 * 3 * .7)
    # the abstract carries the certified qty per RA, the challan sheet the challans
    assert ab.cell(8, 5).value == "RA 01" and ab.cell(rv, 5).value == 3 and ab.cell(rv, 6).value == 3
    assert [cl.cell(r_, 6).value for r_ in (4, 6, 7)] == [dt.datetime(2026, 7, 4), 614, "RA-01"]
    assert cl.cell(rv, 6).value == 1 and cl.cell(rv, 7).value == 2
    # annexure summary links to the sub-total rows: grand total = this bill
    g = _find(sm, "Grand Total")
    assert sm.cell(g, 5).value == pytest.approx(b["this_total"], abs=.01)
    assert sm.cell(g, 3).value == pytest.approx(b["contract_value"], abs=1) or sm.cell(g, 3).value > 0
    assert sm["D4"].value == "RA-01" and sm["D5"].value == "SUPPLY @ 70%"
    names = [sm.cell(r_, 2).value for r_ in range(8, g)]
    assert names == ["Fire Pump & Accessories", "Hydrant System"]
    assert "DRAFT" in str(wb["Annexure B-BOQ"]["B5"].value)


def test_invoice_for_ra2_has_previous_qty_value_and_all_ras_in_the_abstract(env):
    client, _, tmp = env
    cs(client)
    ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)])
    ra1 = new_ra(client)
    client.post(f"{B}/ra/{ra1}/lock")
    ch(client, "616", "2026-08-13", [("2", 2), ("3", 40)])
    ra2 = new_ra(client)
    b = bill(client, ra2)
    p = _inv(client, ra2, tmp, "inv2.xlsx")
    rc = _recalc(p, tmp / "rc")
    bq, ab, cl, sm = rc["Annexure B-BOQ"], rc["CHALLAN ABTRACT"], rc["CHALLAN"], rc["Annexure B-Summary BOQ"]
    rv = _row_of(bq, "Butterfly valve 150mm")
    assert (bq.cell(rv, 8).value, bq.cell(rv, 9).value, bq.cell(rv, 10).value) == (1, 2, 3)
    assert bq.cell(rv, 11).value == pytest.approx(3500) and bq.cell(rv, 12).value == pytest.approx(7000)
    assert bq.cell(rv, 13).value == pytest.approx(10500)
    rp = _row_of(bq, "Diesel engine pump set")                   # fully billed in RA 1: still listed, nothing new
    assert (bq.cell(rp, 8).value, bq.cell(rp, 9).value or 0, bq.cell(rp, 11).value) == (2, 0, pytest.approx(140000))
    assert ab.cell(8, 5).value == "RA 01" and ab.cell(8, 6).value == "RA 02"
    assert (ab.cell(rv, 5).value, ab.cell(rv, 6).value, ab.cell(rv, 7).value) == (1, 2, 3)
    assert [cl.cell(7, c).value for c in (6, 7)] == ["RA-01", "RA-02"]
    assert cl.cell(rv, 8).value == 3 and cl.cell(rv, 9).value == 1 and cl.cell(rv, 10).value == 2    # total / previous / this
    g = _find(sm, "Grand Total")
    assert [round(sm.cell(g, c).value, 2) for c in (4, 5, 6)] == [143500.0, pytest.approx(b["this_total"]), pytest.approx(143500 + b["this_total"])]


def test_locked_invoice_is_values_only_and_equals_the_snapshot(env):
    client, _, tmp = env
    cs(client)
    ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)])
    ra = new_ra(client)
    client.post(f"{B}/ra/{ra}/lock")
    p = _inv(client, ra, tmp, "locked.xlsx")
    wb = openpyxl.load_workbook(p)
    for ws in wb.worksheets:
        assert not any(isinstance(c.value, str) and c.value.startswith("=") for row in ws.iter_rows() for c in row), ws.title
    bq = wb["Annexure B-BOQ"]
    r = _row_of(bq, "Diesel engine pump set")
    assert bq.cell(r, 12).value == 140000 and "DRAFT" not in str(bq["B5"].value or "")
    sm = wb["Annexure B-Summary BOQ"]
    g = _find(sm, "Grand Total")
    assert sm.cell(g, 5).value == pytest.approx(143500)


def test_invoice_of_an_installation_ra_has_invoice_summary_and_boq_only(env):
    client, _, tmp = env
    progress(client, "2", 0.5)
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30})
    ra = new_ra(client, "install")
    p = _inv(client, ra, tmp, "ins.xlsx")
    assert openpyxl.load_workbook(p).sheetnames == ["RA-01 INVOICE", "Annexure B-Summary BOQ", "Annexure B-BOQ"]
    rc = _recalc(p, tmp / "rc")
    bq = rc["Annexure B-BOQ"]
    rv = _row_of(bq, "Butterfly valve 150mm")
    assert bq.cell(rv, 9).value == 2 and bq.cell(rv, 12).value == pytest.approx(5000 * 2 * .3)
    assert "INSTALLATION" in bq["G8"].value or "INSTALLATION" in rc["Annexure B-Summary BOQ"]["D5"].value


def test_invoice_of_a_together_ra_has_both_stage_blocks(env):
    client, _, tmp = env
    cs(client, 75, 25, svc="HVAC", mode="together")
    ch(client, "9", "2026-07-04", [("1", 1)], svc="HVAC")
    client.post(f"/api/siteprogress/{SLUG}/progress/item", json={"service": "HVAC", "item_code": "1", "frac": 0.5})
    ra = new_ra(client, "together", "HVAC")
    p = _inv(client, ra, tmp, "tog.xlsx")
    rc = _recalc(p, tmp / "rc")
    bq, sm = rc["Annexure B-BOQ"], rc["Annexure B-Summary BOQ"]
    r = _row_of(bq, "Chiller")
    assert bq.cell(r, 9).value == 1 and bq.cell(r, 16).value == pytest.approx(1)            # supply I, install P
    assert bq.cell(r, 12).value == pytest.approx(3750000) and bq.cell(r, 19).value == pytest.approx(1250000)
    g = _find(sm, "Grand Total")
    assert sm.cell(g, 5).value == pytest.approx(3750000) and sm.cell(g, 8).value == pytest.approx(1250000)
    assert sm.cell(g, 11).value == pytest.approx(5000000)


def test_the_invoice_challan_sheet_imports_back_to_the_same_challans(env, tmp_path):
    client, _, tmp = env
    cs(client)
    ch(client, "614", "2026-07-04", [("1", 2), ("2", 1)])
    ch(client, "616", "2026-08-13", [("2", 2), ("3", 40)], ra=2)
    ra = new_ra(client)
    p = _inv(client, ra, tmp, "rt.xlsx")        # RA 1 invoice: only challan 614 is in it
    parsed = billing.parse_challan_workbook(p.read_bytes())
    assert [(c["no"], c["date"], c["ra"]) for c in parsed["challans"]] == [("614", "2026-07-04", 1)]
    j = imp(client, p).json()
    assert j["new"] == 0 and j["skipped_duplicate"] == 1 and j["unmatched"] == []


def test_invoice_for_an_unknown_ra_is_404(env):
    client, _, _ = env
    assert client.get(f"{B}/invoice/ra999").status_code == 404


# ----------------------- the user's real HVAC invoice workbook, against the real Thoth BOQ
from backend.test_billing import REAL  # noqa: E402
INVOICE = os.environ.get("HVAC_INVOICE", "/root/.claude/uploads/cc641dec-fdd2-5679-9e93-268c1cf0e51e/037f25b2-HVAC_MAIN_INVOICE_SHEET.xlsx")


@pytest.mark.skipif(not (os.path.exists(REAL) and os.path.exists(INVOICE)),
                    reason="real Thoth BOQ / HVAC invoice workbook not available here (set THOTH_BOQ, HVAC_INVOICE)")
def test_real_hvac_challan_sheet_imports_and_the_invoice_matches_the_users_own_rs1(tmp_path, monkeypatch):
    import re
    monkeypatch.setattr(sp, "PROJECTS", tmp_path / "p")
    monkeypatch.setattr(sp, "RUNS", tmp_path / "r")
    sp.PROJECTS.mkdir()
    sp.RUNS.mkdir()
    app = FastAPI()
    app.include_router(sp.router)
    app.include_router(billing.router)
    c = TestClient(app)
    with open(REAL, "rb") as fh:
        assert c.post(f"/api/siteprogress/{SLUG}/boq", files={"file": ("b.xlsx", fh)}).status_code == 200
    d = sp._dir(SLUG)
    (d / "structure.json").write_text(structure.hotel("Thoth", floors=["1", "2"], room_labels=["a"]).to_json())
    cs(c, 75, 25, svc="HVAC")
    with open(INVOICE, "rb") as fh:
        j = c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", fh)}, data={"commit": "false"}).json()
    # every challan of the user's sheet, every quantity row paired with a BOQ line
    assert j["sheet"] == "CHALLAN" and j["unmatched"] == [] and j["problems"] == []
    assert len(j["challans"]) == 12 and {x["ra"] for x in j["challans"]} == {1}
    assert [x["no"] for x in j["challans"]][:3] == ["614", "616", "624"]
    # the two STP lines the user billed 2 of against a BOQ qty of 1 are reported, not silently changed
    assert sorted(x["desc"][:16] for x in j["over_boq"]) == ["38000 CFM, 20mm ", "45000 CFM, 25mm "]
    assert j["by_ra"][0]["lines"] == 45 and j["by_ra"][0]["value"] == pytest.approx(19314508.875 - (346870 + 310590) * .75)
    with open(INVOICE, "rb") as fh:
        c.post(f"{B}/challans/HVAC/import", files={"file": ("i.xlsx", fh)}, data={"commit": "true"})
    ra = new_ra(c, "supply", "HVAC")
    b = bill(c, ra)
    assert b["this_total"] == pytest.approx(j["by_ra"][0]["value"]) and len(b["warnings"]) == 2
    p = tmp_path / "hvac.xlsx"
    r = c.get(f"{B}/invoice/{ra}")
    p.write_bytes(r.content)
    rc = _recalc(p, tmp_path / "rc")
    sm = rc["Annexure B-Summary BOQ"]
    g = _find(sm, "Grand Total")
    assert sm.cell(g, 5).value == pytest.approx(b["this_total"], abs=.01)
    # line by line against the user's own B-BOQ: same rate x 75%, same qty, same value (except the 2 capped lines)
    fold = lambda t: re.sub(r"[^0-9a-z]+", " ", str(t or "").lower()).strip()
    his = openpyxl.load_workbook(INVOICE, data_only=True, read_only=True)["Annexure B-BOQ"]
    theirs = {}
    for r_ in his.iter_rows(min_row=9, max_row=967, max_col=13, values_only=True):
        if isinstance(r_[11], (int, float)) and r_[11] > 0 and r_[1] and r_[2]:      # item rows only, not his sub-totals
            theirs.setdefault(fold(r_[1]), []).append((r_[8], r_[11]))
    ours = {}
    bq = rc["Annexure B-BOQ"]
    for r_ in range(9, bq.max_row + 1):
        if isinstance(bq.cell(r_, 12).value, (int, float)) and bq.cell(r_, 12).value > 0 and bq.cell(r_, 2).value \
                and not str(bq.cell(r_, 2).value).startswith("TOTAL FOR"):
            ours.setdefault(fold(bq.cell(r_, 2).value), []).append((bq.cell(r_, 9).value, bq.cell(r_, 12).value))
    assert set(ours) == set(theirs) and sum(len(v) for v in ours.values()) == 45
    capped = 0
    for k in theirs:
        for (tq, tv), (oq, ov) in zip(theirs[k], ours[k]):
            if oq == pytest.approx(tq) and ov == pytest.approx(tv):
                continue
            capped += 1
            assert (tq, oq) == (2, 1) and ov == pytest.approx(tv / 2)
    assert capped == 2
    # challan sheet of the generated workbook reads back to the very same 12 challans
    again = billing.parse_challan_workbook(p.read_bytes())
    assert [x["no"] for x in again["challans"]] == [x["no"] for x in j["challans"]]
