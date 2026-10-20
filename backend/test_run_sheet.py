"""From-To Detail sheet (Annexure D) -- the client's sign-off sheet for Mtr / RMT items, built from the runs entered at site.

Reported need (site engineer, Hyatt): the client signs a measurement sheet that says WHERE the cable / pipe was laid (From, To,
drum reading start / end, length), not only how much. The runs are entered in the Zones dialog (test_runs.py); this file pins what
the bill does with them. Rules under test:
  * the sheet is the LAST sheet, right after Annexure C, and exists only when a billed Mtr / RMT item has runs (no runs = no change);
  * per zone the lines are [recorded without From-To detail] + the active runs, in the order they were entered;
  * Previous / This bill of every line comes from what the last LOCKED RA billed, so the sheet ties to Annexure B-BOQ and C;
  * whatever the runs do not explain is ONE visible "Rest of the item" line -- the item total is always the bill's quantity;
  * a locked RA keeps its run lines for ever: runs added or cancelled later never move an invoice already submitted;
  * a bill locked before locations were kept shows lengths only, previous / this left blank, never guessed.
Harness: test_billing.py's Fire BOQ (GI pipe 100 Rmt over 4 zones = 25 each; floors "1" and "2", zones a/b; 20 % overall = 5 each)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from backend import billing
from backend.test_billing import env, prepared, SLUG                      # noqa: F401
from backend.test_billing_challan import new_ra, B
from backend.test_measurement_sheet import meas_book, recalc, lock, boq_this, set_rooms, item_block, meas_ws, vals

P = f"/api/siteprogress/{SLUG}"
PIPE = "GI pipe 100mm"
SHEET_C, SHEET_D = "Annexure C-Measurement", "Annexure D-From-To Detail"


def add_run(client, room, frm, to, start=None, end=None, qty=None, code="3", **kw):
    body = {"service": "Fire", "item_code": code, "room": room, "from": frm, "to": to}
    if start is not None:
        body.update(start=start, end=end)
    if qty is not None:
        body["qty"] = qty
    body.update(kw)
    r = client.post(f"{P}/progress/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def runs_of(client, room, code="3"):
    r = client.get(f"{P}/progress/runs", params={"service": "Fire", "item_code": code}).json()
    return r["rooms"][room]["runs"]


def cancel(client, room, run_id, reason="laid in the wrong riser", code="3"):
    r = client.post(f"{P}/progress/runs/cancel", json={"service": "Fire", "item_code": code, "room": room, "run_id": run_id, "reason": reason})
    assert r.status_code == 200, r.text


def dws(wb):
    assert SHEET_D in wb.sheetnames, wb.sheetnames
    return wb[SHEET_D]


def block(ws, desc):
    """(header_row, [line rows], total_row) of one item of the Run Detail sheet."""
    h = next(r for r in range(9, ws.max_row + 1) if ws.cell(r, 2).value == desc)
    r, rows = h + 1, []
    while not str(ws.cell(r, 2).value or "").startswith("Total qty in bill"):
        rows.append(r)
        r += 1
    return h, rows, r


def row_of(ws, r):
    """label, from, to, start, end, length, previous, this, remarks"""
    return [ws.cell(r, c).value for c in (2, 4, 5, 6, 7, 8, 9, 10, 11)]


def standard_runs(client):
    """Zone a of floor 2 (roo5): 5 recorded before (the overall 20 %), then 8 + 7 by drum; zone b (roo6): 10 typed. Floor 1 stays overall (10)."""
    add_run(client, "roo5", "FACP-3", "Riser R1", start=1000, end=992)
    add_run(client, "roo5", "Riser R1", "Corridor 2A", start=992, end=985, remarks="in conduit")
    add_run(client, "roo6", "FACP-3", "Corridor 2B", qty=10)


# =================================================================== the sheet in the workbook
def test_a_draft_bill_gets_the_run_sheet_after_annexure_c_and_it_ties(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra = new_ra(client, "install")
    wb = recalc(client, ra)
    assert wb.sheetnames[-2:] == [SHEET_C, SHEET_D], wb.sheetnames
    ws = dws(wb)
    assert ws["B5"].value.startswith("DRAFT") and ws["A6"].value == "Annexure D"
    assert [ws.cell(8, c).value for c in range(1, 12)] == ["Sr. No", "Description / Zone", "Unit", "From", "To", "Drum reading START",
                                                          "Drum reading END", "LENGTH", "PREVIOUS BILL QTY", "THIS BILL QTY", "Remarks"]
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    labels = [g[0].strip() for g in got]
    assert labels[0] == labels[1] == labels[2] and labels[0].endswith("a"), labels      # zone a: its three lines, together
    assert "without From-To detail" in got[0][8], got[0]
    assert got[0][5:8] == [5, 0, 5] and got[0][1:3] == [None, None]             # the part nobody wrote a run for
    assert got[1] == [got[1][0], "FACP-3", "Riser R1", 1000, 992, 8, 0, 8, None]
    assert got[2][1:] == ["Riser R1", "Corridor 2A", 992, 985, 7, 0, 7, "in conduit"]
    assert labels[3].endswith("b") and "without From-To detail" in got[3][8] and got[3][5] == 5
    assert got[4][1:8] == ["FACP-3", "Corridor 2B", None, None, 10, 0, 10]       # typed length: no drum readings printed
    assert "Rest of the item" in got[5][0] and got[5][5:8] == [10, 0, 10]       # floor 1, recorded overall
    # the item total is the bill's own quantity, calculated from the lines
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [45, 0, 45]
    assert sum(ws.cell(r, 8).value for r in rows) == 45
    assert boq_this(wb["Annexure B-BOQ"], PIPE)[0] == 45
    # ... and it is the same quantity Annexure C prints
    wc = wb[SHEET_C]
    assert vals(wc, item_block(wc, PIPE)[3]) == [0, 45, 45]


def test_no_runs_means_no_run_sheet_and_nothing_else_changes(env):
    client, d, _ = prepared(env)
    set_rooms(client, [{"room": "roo5", "qty": 10}])                          # a plain typed zone quantity, no run
    ra = new_ra(client, "install")
    wb = meas_book(client, ra)
    assert wb.sheetnames[-1] == SHEET_C and SHEET_D not in wb.sheetnames
    lock(client, ra)
    assert SHEET_D not in meas_book(client, ra).sheetnames


def test_cancelled_runs_are_not_on_the_sheet(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    wrong = add_run(client, "roo6", "Rack-1", "Rack-2", qty=4)["zone"]["runs"][-1]["id"]
    cancel(client, "roo6", wrong)
    ra = new_ra(client, "install")
    ws = dws(recalc(client, ra))
    h, rows, tot = block(ws, PIPE)
    assert not any(ws.cell(r, 4).value == "Rack-1" for r in rows)
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [45, 0, 45]


def test_a_locked_bill_keeps_its_run_lines_whatever_is_entered_later(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    before = [row_of(dws(meas_book(client, ra1)), r) for r in range(9, 30)]
    add_run(client, "roo5", "Corridor 2A", "Room 201", qty=5)                   # more cable after the invoice was locked
    first_run = runs_of(client, "roo6")[0]["id"]
    cancel(client, "roo6", first_run)                                          # and a run struck off
    ws = dws(meas_book(client, ra1))
    assert [row_of(ws, r) for r in range(9, 30)] == before
    h, rows, tot = block(ws, PIPE)
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [45, 0, 45]
    assert not any(ws.cell(r, 5).value == "Room 201" for r in rows)


def test_the_next_bill_splits_each_run_into_previous_and_this(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    add_run(client, "roo5", "Corridor 2A", "Room 201", start=985, end=980)      # 5 more in zone a (20 -> 25 = its plan)
    ra2 = new_ra(client, "install")
    wb = recalc(client, ra2)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    assert [g[5:8] for g in got[:4]] == [[5, 5, 0], [8, 8, 0], [7, 7, 0], [5, 0, 5]]       # zone a: all billed before except the new run
    assert got[3][1:5] == ["Corridor 2A", "Room 201", 985, 980]
    assert [g[5:8] for g in got[4:6]] == [[5, 5, 0], [10, 10, 0]]                          # zone b: nothing new
    assert "Rest of the item" in got[6][0] and got[6][5:8] == [10, 10, 0]
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [50, 45, 5]
    assert boq_this(wb["Annexure B-BOQ"], PIPE)[0] == 5
    # the same totals Annexure C shows
    wc = wb[SHEET_C]
    assert vals(wc, item_block(wc, PIPE)[3]) == [45, 5, 50]


def test_a_run_struck_off_after_it_was_billed_is_one_visible_reduction(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    seven = [r for r in runs_of(client, "roo5") if r["qty"] == 7][0]["id"]
    cancel(client, "roo5", seven)                                              # 7 m that RA-1 already billed
    ra2 = new_ra(client, "install")
    wb = recalc(client, ra2)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    red = [g for g in got if "no longer in the records" in str(g[8])]
    assert len(red) == 1 and red[0][5:8] == [0, 7, -7]
    assert not any(g[2] == "Corridor 2A" for g in got)                         # the struck-off run is not listed
    # the sheet still adds up to what the bill says
    t = [ws.cell(tot, c).value for c in (8, 9, 10)]
    assert t[0] == sum(ws.cell(r, 8).value for r in rows) and t[1] == sum(ws.cell(r, 9).value for r in rows) and t[2] == sum(ws.cell(r, 10).value for r in rows)
    assert t[2] == boq_this(wb["Annexure B-BOQ"], PIPE)[0]


def test_a_bill_locked_without_locations_shows_lengths_only_and_still_ties(env):
    client, d, _ = prepared(env)
    ra1 = new_ra(client, "install")                                            # 20 billed, locked before locations were kept
    lock(client, ra1)
    st = billing._load(d)
    for r in st["ras"]:
        for row in r["snap"]["lines"]:
            (row.get("install") or {}).pop("loc", None)
    billing._save(d, st)
    add_run(client, "roo5", "FACP-3", "Riser R1", start=1000, end=990)         # 10 more at zone a
    ra2 = new_ra(client, "install")
    wb = recalc(client, ra2)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    assert all(g[6] is None and g[7] is None for g in got), got                 # previous / this are blank, never guessed
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [30, 20, 10]          # the bill's own figures
    assert sum(ws.cell(r, 8).value for r in rows) == 30


def test_two_bills_in_one_workbook_both_close_the_book(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    add_run(client, "roo5", "Corridor 2A", "Room 201", qty=5)
    ra2 = new_ra(client, "install")
    r = client.get(f"{B}/download", params={"ras": f"{ra1},{ra2}", "mode": "one", "sheets": "boq,meas"})
    assert r.status_code == 200, r.text
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    names = wb.sheetnames
    assert len(names) == len(set(names))
    runs_sheets = [n for n in names if "From-To Detail" in n or n.endswith("FROM-TO")]
    meas_sheets = [n for n in names if "Measurement" in n or n.endswith("MEASUREMENT")]
    assert len(runs_sheets) == 2 and len(meas_sheets) == 2, names
    assert set(names[-4:]) == set(runs_sheets) | set(meas_sheets), names        # the supporting backups close the workbook


def test_the_run_sheet_is_only_in_the_download_that_asks_for_the_measurement_backup(env):
    client, d, _ = prepared(env)
    standard_runs(client)
    ra = new_ra(client, "install")
    import io, openpyxl
    r = client.get(f"{B}/download", params={"ras": ra, "sheets": "boq"})
    assert SHEET_D not in openpyxl.load_workbook(io.BytesIO(r.content)).sheetnames


def test_the_screens_never_carry_the_run_lines(env):
    """loc is for the sheets only: the RA screens (draft or locked) must not grow."""
    client, d, _ = prepared(env)
    standard_runs(client)
    ra = new_ra(client, "install")
    lock(client, ra)
    body = client.get(f"{B}/ra/{ra}").json()
    assert "runs" not in json.dumps(body) and '"loc"' not in json.dumps(body)
