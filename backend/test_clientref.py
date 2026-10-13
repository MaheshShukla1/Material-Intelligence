"""The client's BOQ reference of a line, read from the client's own sheet layout (ProjectBase: Client Ref No / BOQ / BOQ Unit / Design Quantity)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import pandas as pd

from backend import clientref

COLS = {"code": 1, "desc": 2, "unit": 3, "qty": 4}


def sheet(*rows):
    """{row_number: [Internal, Client Ref, BOQ, Unit, Qty]} from (ref, desc, unit, qty) tuples; row 1 is the header."""
    out = {1: ["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Design Quantity"]}
    for i, (ref, desc, unit, qty) in enumerate(rows, start=2):
        out[i] = [str(i), ref or "", desc, unit or "", "" if qty is None else str(qty)]
    return out


def ref(rows, n):
    return clientref.derive(rows, COLS, n)[0]


def test_number_like_references_are_used_as_they_are():
    s = sheet(("1.1.4", "One light point", "Nos", 211), ("13.2.1", "25 x 6 mm GI Strip", "Rmt", 7500), ("3.03.5", "110 mm Dia", "Rmt", 770), ("1.23", "FRLS cable", "Mtr", 12))
    assert [ref(s, n) for n in (2, 3, 4, 5)] == ["1.1.4", "13.2.1", "3.03.5", "1.23"]


def test_roman_lines_take_their_heading_parent():
    s = sheet(("10.2", "Cable tray, perforated", None, None), ("i", "14 G.G.I - 150 X 50", "Rmt", 10), ("ii", "14 G.G.I - 100 X 50", "Rmt", 5),
              ("iii", "14 G.G.I - 75 X 50", "Rmt", 7))
    assert [ref(s, n) for n in (3, 4, 5)] == ["10.2 (i)", "10.2 (ii)", "10.2 (iii)"]
    assert clientref.derive(s, COLS, 5)[1] == "Cable tray, perforated"


def test_first_lettered_line_carries_the_parent_text_and_the_next_ones_follow():
    s = sheet(("39", "Supply of M.S. pipes of Class C complete. a) 150 mm dia", "Rmt", 5120), (None, "b) 100 mm dia", "Rmt", 1259), (None, "c) 80 mm dia", "Rmt", 10))
    assert clientref.derive(s, COLS, 2) == ("39 (a)", "Supply of M.S. pipes of Class C complete.", "a) 150 mm dia")
    assert clientref.derive(s, COLS, 3) == ("39 (b)", "Supply of M.S. pipes of Class C complete.", None)
    assert ref(s, 4) == "39 (c)"


def test_a_line_with_no_clear_parent_gets_no_reference_never_a_guess():
    s = sheet(("5", "A normal item with its own quantity", "Nos", 4), ("a", "a lettered line under an ordinary item", "Nos", 1), (None, "plain text, no label", "Nos", 1))
    assert ref(s, 3) is None and ref(s, 4) is None


def test_refs_for_reads_the_saved_boq_xlsx_and_falls_back_to_the_boqs_own_code_for_other_formats(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Electrical"
    for r in [["Internal Ref No", "Client Ref No", "BOQ", "BOQ Unit", "Design Quantity", "Rate"], ["1", "1.1.4", "A light point", "Nos", 5, 10]]:
        ws.append(r)
    wb.save(tmp_path / "boq.xlsx")
    items = pd.DataFrame([{"service": "Electrical", "item_code": "1.1.4", "item_code_raw": "1.1.4", "src_row": 2, "source_sheet": "Electrical"},
                          {"service": "HVAC", "item_code": "7#2", "item_code_raw": "7", "src_row": None, "source_sheet": None},
                          {"service": "HVAC", "item_code": "nan", "item_code_raw": None, "src_row": None, "source_sheet": None}])
    out = clientref.refs_for(tmp_path, items)
    assert out[("Electrical", "1.1.4")]["ref"] == "1.1.4"
    assert out[("HVAC", "7#2")]["ref"] == "7"                       # another kind of BOQ: its own item number
    assert ("HVAC", "nan") not in out
    assert clientref.refs_for(tmp_path / "nowhere", items)[("HVAC", "7#2")]["ref"] == "7"      # no boq.xlsx: no error
