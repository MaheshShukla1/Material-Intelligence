"""Service list order must match the order sheets appeared in the uploaded
BOQ workbook -- plain Python sorted() is case-sensitive, so an all-caps
service like "ELV" sorted before Title-case ones ("Electrical", "Fire"),
landing first in the Site Progress pill row for no reason the engineer
would expect (reported against the real Thoth Mall multi-sheet file)."""
import json

import pandas as pd
import pytest

from backend import siteprogress as sp
from backend import boq, structure as structure_mod


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d = tmp_path / "thoth-mall"
    d.mkdir()
    import shutil
    shutil.copy("/mnt/user-data/uploads/Thoth_Malll_Multi_Sheet.xlsx", d / "src.xlsx")
    parsed, _ = boq.parse_workbook(str(d / "src.xlsx"))
    frames = []
    for r in parsed.values():
        items = r["items"]
        if "rate" in items.columns:
            items = items.drop(columns=["rate"])
        frames.append(items)
    pd.concat(frames, ignore_index=True).to_parquet(d / "boq.parquet")
    s = structure_mod.mall("Thoth Mall", levels=["B1"], zones_per_level=1)
    (d / "structure.json").write_text(s.to_json())
    (d / "activities.json").write_text(json.dumps({}))
    (d / "project.json").write_text(json.dumps({"name": "Thoth Mall", "slug": "thoth-mall"}))
    return "thoth-mall"


def test_real_file_services_come_back_in_sheet_order_not_alphabetical(project):
    info = sp.get_state(project)
    # the real workbook's own sheet order is Electrical, Fire, HVAC, PHE(Plumbing), ELV
    assert info["services"] == ["Electrical", "Fire", "HVAC", "Plumbing", "ELV"], \
        f"FAIL: expected upload order with ELV last, got {info['services']}"
    assert info["services"][-1] == "ELV", \
        "FAIL: ELV was the LAST sheet in the real file -- a case-sensitive alphabetical sort wrongly puts it first"


def test_synthetic_case_sensitivity_regression_guard(tmp_path, monkeypatch):
    """A direct, minimal repro of the actual bug mechanism (plain sorted()
    is case-sensitive) -- independent of the real file, so this keeps
    failing loudly if a future edit reintroduces sorted() anywhere in the
    services-list path."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d = tmp_path / "fixture"
    d.mkdir()
    # upload order: Electrical first, ELV (all-caps) last -- a plain
    # sorted() would wrongly put ELV first (uppercase sorts before lowercase)
    df = pd.DataFrame([
        {"service": "Electrical", "item_code": "E1", "description": "Wire", "unit": "MTR", "qty": 10},
        {"service": "ELV", "item_code": "V1", "description": "Camera", "unit": "NOS", "qty": 5},
    ])
    df.to_parquet(d / "boq.parquet")
    s = structure_mod.hotel("Fixture", floors=["F1"], room_labels=["R1"])
    (d / "structure.json").write_text(s.to_json())
    (d / "activities.json").write_text(json.dumps({}))
    (d / "project.json").write_text(json.dumps({"name": "Fixture", "slug": "fixture"}))

    info = sp.get_state("fixture")
    assert info["services"] == ["Electrical", "ELV"], \
        f"FAIL: upload order broken, got {info['services']}"
