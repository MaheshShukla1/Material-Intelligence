"""Your own company's details are typed once and offered on the next project (never applied silently);
each project can still edit them or start without them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

from backend import siteprogress as sp
from backend.test_billing import env, SLUG, make_workbook  # noqa: F401  (env is a fixture)

B = f"/api/billing/{SLUG}"
OTHER = "hyatt-hotel"
BO = f"/api/billing/{OTHER}"
CO = {"contractor": "Vedant Engitech And Infrastructure Pvt. Ltd.", "supplier_gstin": "24aadcv2799m1zp",
      "supplier_state": "GUJARAT", "supplier_state_code": "24", "supplier_pan": "aadcv2799m", "supplier_category": "MEPF"}


def second_project(client, tmp):
    x = tmp / "boq2.xlsx"
    make_workbook(x)
    with open(x, "rb") as fh:
        r = client.post(f"/api/siteprogress/{OTHER}/boq", files={"file": ("boq.xlsx", fh)})
    assert r.status_code == 200, r.text


def setup(c, base):
    r = c.get(f"{base}/setup")
    assert r.status_code == 200, r.text
    return r.json()


def test_nothing_is_offered_before_any_project_has_company_details(env):
    client, _, tmp = env
    second_project(client, tmp)
    assert setup(client, B)["profile"] is None and setup(client, BO)["profile"] is None


def test_company_details_typed_on_one_project_are_offered_on_a_fresh_one_but_not_applied(env):
    client, _, tmp = env
    second_project(client, tmp)
    r = client.post(f"{B}/meta", json={**CO, "client": "Thoth Mall Pvt Ltd", "sgst": 9, "cgst": 9, "wo_no": "WO/12"})
    assert r.status_code == 200, r.text
    # the project itself already has them: nothing to offer
    assert setup(client, B)["profile"] is None
    fresh = setup(client, BO)
    assert fresh["meta"].get("contractor") in (None, "")                      # NOT applied
    assert fresh["profile"]["fields"] == {**CO, "supplier_gstin": "24AADCV2799M1ZP", "supplier_pan": "AADCV2799M"}
    assert fresh["profile"]["from"] == "test-thoth"
    # only company identity travels: client, W/O, GST % stay with their project
    assert set(fresh["profile"]["fields"]) <= {"contractor", "supplier_gstin", "supplier_state", "supplier_state_code",
                                                "supplier_pan", "supplier_category"}
    assert "Your company name" in fresh["invoice_missing"] and "Client company" in fresh["invoice_missing"]


def test_using_the_offer_fills_the_project_and_it_is_still_editable(env):
    client, _, tmp = env
    second_project(client, tmp)
    client.post(f"{B}/meta", json=CO)
    offer = setup(client, BO)["profile"]["fields"]
    assert client.post(f"{BO}/meta", json=offer).status_code == 200
    after = setup(client, BO)
    assert after["profile"] is None and after["meta"]["contractor"] == CO["contractor"]
    assert "Your company name" not in after["invoice_missing"] and "Your GSTIN" not in after["invoice_missing"]
    client.post(f"{BO}/meta", json={"supplier_state_code": "27", "supplier_state": "MAHARASHTRA"})   # an edit on this project only
    assert setup(client, B)["meta"]["supplier_state_code"] == "24"
    assert setup(client, BO)["meta"]["supplier_state_code"] == "27"


def test_a_project_can_start_without_them_and_it_sticks(env):
    client, _, tmp = env
    second_project(client, tmp)
    client.post(f"{B}/meta", json=CO)
    assert setup(client, BO)["profile"] is not None
    blank = {k: "" for k in CO}
    assert client.post(f"{BO}/meta", json=blank).status_code == 200            # "Clear" then Save
    assert setup(client, BO)["profile"] is None                                # not offered again for this project
    # a blank save never erases what is kept for other projects
    assert setup(client, B)["meta"]["contractor"] == CO["contractor"]
    third = setup(client, B)
    assert third["profile"] is None
    # typing something later switches the project back to normal
    client.post(f"{BO}/meta", json={"contractor": "Another Co"})
    assert setup(client, BO)["meta"]["contractor"] == "Another Co"


def test_a_partly_filled_project_is_offered_only_what_it_lacks(env):
    client, _, tmp = env
    second_project(client, tmp)
    client.post(f"{B}/meta", json=CO)
    client.post(f"{BO}/meta", json={"contractor": "Different Co"})
    offer = setup(client, BO)["profile"]
    assert offer is not None and "supplier_gstin" in offer["fields"]
    client.post(f"{BO}/meta", json={k: v for k, v in offer["fields"].items() if k != "contractor"})
    assert setup(client, BO)["meta"]["contractor"] == "Different Co"          # the project's own name is never overwritten by the offer


def test_the_profile_file_sits_next_to_the_projects_folder_and_holds_only_company_fields(env):
    client, _, tmp = env
    client.post(f"{B}/meta", json={**CO, "client": "X", "client_gstin": "Y", "sgst": 9})
    import json
    f = sp.PROJECTS.parent / "billing_profile.json"
    assert f.exists()
    raw = json.loads(f.read_text())
    assert set(raw["fields"]) == set(CO)
