"""Billing -- ONE upload for the downloaded RA workbook, filled in: the quantities typed into its THIS BILL QTY column
(Supply and/or Installation) come back together with the challans of its CHALLAN sheet. Before this, the only upload was
"Import challans", so a quantity typed against a BOQ line in the downloaded sheet was lost.
Same harness as test_billing_challan.py: real routers, the synthetic ProjectBase workbook, data in tmp_path."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import pytest

from backend.test_billing import env, prepared, make_run, link, progress, line, STOCK1, SLUG   # noqa: F401
from backend.test_billing_challan import cs, ch, new_ra, bill, B
from backend.test_billing_invoice_cover import invoice_of


def boq_cols(ws):
    """(header row, [column numbers of THIS BILL QTY]) on the B-BOQ sheet."""
    for r in range(1, 20):
        vals = [ws.cell(r, c).value for c in range(1, 25)]
        if "THIS BILL QTY" in vals:
            return r, [i + 1 for i, v in enumerate(vals) if v == "THIS BILL QTY"]
    raise AssertionError("no THIS BILL QTY header")


def row_of(ws, desc):
    return next(r for r in range(8, ws.max_row + 1) if ws.cell(r, 2).value == desc)


def filled(client, ra, tmp, typed=None, challan=None, name="f.xlsx"):
    """Download the RA workbook exactly as Billing gives it, type numbers into it like a person would, save it.
    typed = [(description, block index, number)], challan = (no, date, RA tag, [(description, qty)])."""
    p = invoice_of(client, ra, tmp, name)
    wb = openpyxl.load_workbook(p)                      # formulas kept, like opening it in Excel
    ws = next(wb[n] for n in wb.sheetnames if "BOQ" in n and "Summary" not in n)
    _, cols = boq_cols(ws)
    for desc, bi, v in typed or []:
        ws.cell(row_of(ws, desc), cols[bi]).value = v
    if challan:
        if "CHALLAN" not in wb.sheetnames:               # a stock-register service's download has none: add the person's own challan sheet
            cw = wb.create_sheet("CHALLAN")
            for c, v in ((2, "Description"), (3, "Unit"), (4, "Qty")):
                cw.cell(7, c).value = v
            for k, (desc, unit, q) in enumerate([("Diesel engine pump set", "Nos", 2), ("Butterfly valve 150mm", "Nos", 4), ("GI pipe 100mm", "Rmt", 100)]):
                cw.cell(8 + k, 2).value, cw.cell(8 + k, 3).value, cw.cell(8 + k, 4).value = desc, unit, q
        cw = wb["CHALLAN"]
        no, date, tag, items = challan
        col = 11                                         # a free column after the file's own challan / total columns
        cw.cell(2, col).value = "Challan Date"
        cw.cell(3, col).value = date
        cw.cell(4, col).value = "Challan No"
        cw.cell(5, col).value = no
        cw.cell(6, col).value = tag
        cw.cell(7, col).value = "Qty"
        for desc, q in items:
            cw.cell(row_of(cw, desc), col).value = q
    wb.save(p)
    return p


def up(client, p, ra=None, commit=False, svc="Fire", **kw):
    with open(p, "rb") as fh:
        return client.post(f"{B}/import-sheet/{svc}", files={"file": ("f.xlsx", fh)},
                           data={"commit": "true" if commit else "false", "ra_id": ra or "", **kw})


# ---------------------------------------------------- installation typed in the sheet
def test_installation_qty_typed_in_the_sheet_comes_back(env):
    client, d, tmp = prepared(env)                      # item 3 (GI pipe): Site Progress says 20 of 100 done
    ra = new_ra(client, "install")
    assert line(bill(client, ra), "3")["install"]["this"] == 20
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, 35)])
    j = up(client, p, ra).json()
    assert j["committed"] is False and j["challans"] is None
    assert [(c["desc"], c["now"], c["sheet"], c["stage"]) for c in j["typed"]["changes"]] == [("GI pipe 100mm", 20, 35, "install")]
    assert line(bill(client, ra), "3")["install"]["this"] == 20                # the preview saved nothing
    j = up(client, p, ra, commit=True).json()
    assert j["committed"] is True and j["applied"] == 1
    l3 = line(bill(client, ra), "3")["install"]
    assert l3["this"] == 35 and l3["overridden"] is True and l3["reason"] == "Typed in the uploaded Excel"
    assert line(bill(client, ra), "2")["install"]["this"] == 2                  # untouched lines keep following Site Progress


def test_cells_left_alone_change_nothing(env):
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp)                                                 # downloaded and uploaded again, nothing typed
    j = up(client, p, ra).json()
    assert j["typed"]["changes"] == [] and j["typed"]["problems"] == []
    r = up(client, p, ra, commit=True)
    assert r.status_code == 409 and "Nothing new" in r.json()["detail"]


def test_uploading_the_same_filled_sheet_twice_adds_nothing_twice(env):
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, 35)])
    assert up(client, p, ra, commit=True).status_code == 200
    assert up(client, p, ra).json()["typed"]["changes"] == []
    assert up(client, p, ra, commit=True).status_code == 409


# ---------------------------------------------------- supply typed + challans in ONE upload
def test_supply_typed_and_a_challan_arrive_in_one_upload(env):
    client, d, tmp = env
    cs(client)                                                                  # Fire is billed from challans
    ch(client, "614", "2026-07-04", [("1", 2)])
    ra = new_ra(client, "supply")
    assert line(bill(client, ra), "1")["supply"]["this"] == 2
    # the person types 3 pipes' worth against the GI pipe line (no challan for it) AND fills a new challan column for the valves
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, 40)],
               challan=("700", "2026-08-01", "RA-01", [("Butterfly valve 150mm", 3)]))
    j = up(client, p, ra).json()
    assert j["challans"]["new"] == 1 and [c["no"] for c in j["challans"]["challans"]][-1] == "700"
    assert [(c["desc"], c["now"], c["sheet"]) for c in j["typed"]["changes"]] == [("GI pipe 100mm", 0, 40)]
    assert client.get(f"{B}/challans/Fire").json()["challans"][0]["no"] == "614" and len(client.get(f"{B}/challans/Fire").json()["challans"]) == 1
    j = up(client, p, ra, commit=True).json()
    assert j["committed"] is True and j["applied"] == 1 and j["challans"]["new"] == 1
    b = bill(client, ra)
    assert line(b, "3")["supply"]["this"] == 40 and line(b, "3")["supply"]["overridden"] is True     # typed, no challan needed
    assert line(b, "2")["supply"]["this"] == 3 and line(b, "2")["supply"]["source"] == "challan"      # challan, as before
    assert line(b, "1")["supply"]["this"] == 2                                                         # the formula cell was left alone
    assert up(client, p, ra, commit=True).status_code == 409                                           # second time: nothing new


def test_only_challans_in_the_file_still_works_like_the_old_import(env):
    client, d, tmp = env
    cs(client)
    ra = new_ra(client, "supply")
    p = filled(client, ra, tmp, challan=("700", "2026-08-01", "RA-01", [("Butterfly valve 150mm", 3)]))
    j = up(client, p, ra, commit=True).json()
    assert j["applied"] == 0 and j["challans"]["new"] == 1 and j["typed"]["changes"] == []
    assert line(bill(client, ra), "2")["supply"]["this"] == 3


def test_stock_register_service_ignores_the_challan_sheet_but_takes_typed_quantities(env):
    client, d, tmp = prepared(env)                                              # supply comes from the stock register
    ra = new_ra(client, "supply")
    p = filled(client, ra, tmp, typed=[("Diesel engine pump set", 0, 1)],
               challan=("700", "2026-08-01", "RA-01", [("Butterfly valve 150mm", 3)]))
    j = up(client, p, ra).json()
    assert j["challans"] is None and "not read" in j["challan_note"] and "stock register" in j["challan_note"]
    assert up(client, p, ra, commit=True).json()["applied"] == 1
    assert line(bill(client, ra), "1")["supply"]["this"] == 1
    assert client.get(f"{B}/challans/Fire").json()["challans"] == []


# ---------------------------------------------------- Supply + Installation RA: both stages in one file
def test_together_ra_takes_both_stages(env):
    client, d, tmp = prepared(env)
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "together", "supply_pct": 70, "install_pct": 30})
    ra = new_ra(client, "together")
    p = filled(client, ra, tmp, typed=[("Diesel engine pump set", 0, 1), ("GI pipe 100mm", 1, 45)])
    j = up(client, p, ra).json()
    assert sorted((c["stage"], c["desc"], c["sheet"]) for c in j["typed"]["changes"]) == [("install", "GI pipe 100mm", 45), ("supply", "Diesel engine pump set", 1)]
    up(client, p, ra, commit=True)
    b = bill(client, ra)
    assert line(b, "1")["supply"]["this"] == 1 and line(b, "3")["install"]["this"] == 45


# ---------------------------------------------------- never the wrong RA, never above the BOQ
def test_a_sheet_for_another_ra_is_not_applied_to_this_one(env):
    client, d, tmp = prepared(env)
    ra1 = new_ra(client, "install")
    p = filled(client, ra1, tmp, typed=[("GI pipe 100mm", 0, 35)], name="ra1.xlsx")
    client.post(f"{B}/ra/{ra1}/lock")
    ra2 = new_ra(client, "install")
    j = up(client, p, ra2).json()
    assert j["typed"]["changes"] == [] and any("RA-01" in x and "RA-02" not in x.split("you are in")[0] for x in j["typed"]["problems"])
    assert up(client, p, ra2, commit=True).status_code == 409
    assert all(x["item_code"] != "3" or x["install"]["this"] != 35 for x in bill(client, ra2)["lines"])


def test_an_invoiced_ra_is_never_changed(env):
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, 35)])
    client.post(f"{B}/ra/{ra}/lock")
    j = up(client, p).json()
    assert j["typed"]["changes"] == [] and any("already invoiced" in x for x in j["typed"]["problems"])


def test_a_quantity_above_the_boq_is_listed_as_extra_and_saved_only_when_accepted(env):
    import json
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, 130), ("Butterfly valve 150mm", 0, 3)])
    j = up(client, p, ra).json()                                         # preview: both are listed, the big one flagged as extra
    by = {c["desc"]: c for c in j["typed"]["changes"]}
    assert set(by) == {"Butterfly valve 150mm", "GI pipe 100mm"}
    assert by["GI pipe 100mm"]["over"] is True and by["GI pipe 100mm"]["extra"] == 30 and "over" not in by["Butterfly valve 150mm"]
    j = up(client, p, ra, commit=True).json()                            # nothing ticked: the extra is NOT taken, the rest is
    assert any("GI pipe 100mm" in x and "not accepted" in x for x in j["typed"]["problems"])
    assert line(bill(client, ra), "3")["install"]["this"] == 20
    acc = json.dumps([{"ra": ra, "item_code": "3", "stage": "install"}])
    j = up(client, p, ra, commit=True, accept_over=acc).json()           # ticked: taken, and the line is marked Extra
    assert j["committed"] is True
    b = bill(client, ra)
    assert line(b, "3")["install"]["this"] == 130 and line(b, "3")["install"]["extra"] == 30 and line(b, "3")["install"]["accepted"] is True
    assert b["excess"] == []


def test_a_negative_number_is_refused(env):
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp, typed=[("GI pipe 100mm", 0, -5)])
    j = up(client, p, ra).json()
    assert j["typed"]["changes"] == [] and any("below 0" in x for x in j["typed"]["problems"])


def test_an_edited_description_is_listed_not_guessed(env):
    client, d, tmp = prepared(env)
    ra = new_ra(client, "install")
    p = filled(client, ra, tmp)
    wb = openpyxl.load_workbook(p)
    ws = next(wb[n] for n in wb.sheetnames if "BOQ" in n and "Summary" not in n)
    r = row_of(ws, "GI pipe 100mm")
    ws.cell(r, 2).value = "Some other pipe"
    _, cols = boq_cols(ws)
    ws.cell(r, cols[0]).value = 33
    wb.save(p)
    j = up(client, p, ra).json()
    assert j["typed"]["changes"] == [] and [(u["desc"], u["qty"]) for u in j["typed"]["unpaired"]] == [("Some other pipe", 33)]


def test_a_file_with_nothing_to_read_is_refused(env):
    client, d, tmp = prepared(env)
    wb = openpyxl.Workbook()
    wb.active["A1"] = "hello"
    p = tmp / "x.xlsx"
    wb.save(p)
    r = up(client, p)
    assert r.status_code == 400 and "Nothing to read" in r.json()["detail"]
