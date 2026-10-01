"""ELV BOQ sheets now resolve to their own "ELV" service, matching
schema.py's existing register-side convention, instead of silently folding
into "Electrical" -- which previously caused real item-code collisions
(verified against the real Thoth Mall multi-sheet workbook: 19 colliding
codes between the two trades' independent numbering)."""
import sys
sys.path.insert(0, ".")
from backend import boq


def test_sheet_named_elv_resolves_to_its_own_service():
    assert boq._boq_service("ELV") == "ELV"
    assert boq._boq_service("ELV - Data & CCTV") == "ELV"


def test_sheet_named_bms_also_resolves_to_elv():
    assert boq._boq_service("BMS") == "ELV"


def test_plain_electrical_sheet_still_resolves_to_electrical():
    assert boq._boq_service("ELE") == "Electrical"
    assert boq._boq_service("Electrical") == "Electrical"
    assert boq._boq_service("ELECTRICAL LV") == "Electrical"


def test_real_file_no_longer_merges_elv_into_electrical(tmp_path):
    import shutil
    src = "/mnt/user-data/uploads/Thoth_Malll_Multi_Sheet.xlsx"
    dest = tmp_path / "f.xlsx"
    shutil.copy(src, dest)
    parsed, skipped = boq.parse_workbook(str(dest))

    assert "ELV" in parsed, "FAIL: ELV should now appear as its own service"
    elec = parsed["Electrical"]["items"]
    elv = parsed["ELV"]["items"]

    assert set(elec["source_sheet"].unique()) == {"Electrical"}, \
        "FAIL: Electrical must no longer include any ELV-sourced rows"
    assert set(elv["source_sheet"].unique()) == {"ELV"}

    # the two trades can share raw numbering now without conflict -- they
    # live in separate service namespaces throughout the rest of the app
    assert len(elec) == 339, f"FAIL: Electrical item count changed unexpectedly: {len(elec)}"
    assert len(elv) == 156, f"FAIL: ELV item count changed unexpectedly: {len(elv)}"
