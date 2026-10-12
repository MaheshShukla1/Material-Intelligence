"""Taking a line out of a draft RA.

Real report: a line (Medium Protection pipe) whose item is no longer in any Site Progress activity still sits in the Billing
draft. Reason (code): Billing reads each BOQ item's recorded work (item_progress.json), not the activity grouping, so an item
taken out of every activity keeps being billed. The only way to get rid of the line was to type 0, and a typed line stays on
screen. The new route takes the line out of THIS draft only; Site Progress, other RAs and invoiced RAs are not touched.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

from backend.test_billing import env  # noqa: F401  (fixture used by scene)
from backend.test_activity_delete import scene, draft, inst, mapto, activity, SP, B  # noqa: F401  (scene is a fixture)


def lines(client, ra_id):
    return {x["item_code"]: x for x in client.get(f"{B}/ra/{ra_id}").json()["lines"]}


def remove(client, ra_id, code, remove=True):
    return client.post(f"{B}/ra/{ra_id}/line/remove", json={"item_code": code, "remove": remove})


@pytest.fixture
def orphan(scene):
    """The reported state: item 5 was taken out of its activity (Pipes keeps item 3). Its recorded work stays."""
    client, d = scene
    mapto(client, "Pipes", ["3"])
    ra = draft(client)
    assert "5" in lines(client, ra["id"]), "an item in no activity is still billed (the cause of the report)"
    return client, d, ra


def test_typing_zero_does_not_remove_the_line_the_reported_workaround(orphan):
    client, d, ra = orphan
    r = client.post(f"{B}/ra/{ra['id']}/line", json={"item_code": "5", "stage": "install", "qty": 0, "reason": ""})
    assert r.status_code == 200
    row = lines(client, ra["id"])["5"]
    assert row["install"]["overridden"] and row["install"]["this"] == 0       # still listed, "typed by engineer"


def test_remove_takes_the_line_out_and_only_its_amount_leaves_the_total(orphan):
    client, d, ra = orphan
    before_lines = lines(client, ra["id"])
    total_before = draft(client)["this_total"]
    gone = before_lines["5"]["install"]["amount"]
    assert gone > 0
    r = remove(client, ra["id"], "5")
    assert r.status_code == 200 and r.json()["removed"] == 1
    bill = client.get(f"{B}/ra/{ra['id']}").json()
    assert "5" not in {x["item_code"] for x in bill["lines"]}
    assert [x["item_code"] for x in bill["removed"]] == ["5"]
    assert bill["this_total"] == pytest.approx(total_before - gone)
    assert draft(client)["this_total"] == pytest.approx(total_before - gone)         # the home list agrees with the bill
    for code, row in before_lines.items():                                           # every other line is exactly as it was
        if code != "5":
            assert lines(client, ra["id"])[code]["install"]["amount"] == pytest.approx(row["install"]["amount"])


def test_site_progress_is_not_touched(orphan):
    client, d, ra = orphan
    before = (d / "item_progress.json").read_bytes()
    maps = (d / "mapping.json").read_bytes()
    remove(client, ra["id"], "5")
    assert (d / "item_progress.json").read_bytes() == before and (d / "mapping.json").read_bytes() == maps


def test_restore_puts_it_back_with_the_same_amount(orphan):
    client, d, ra = orphan
    amt = lines(client, ra["id"])["5"]["install"]["amount"]
    remove(client, ra["id"], "5")
    r = remove(client, ra["id"], "5", remove=False)
    assert r.status_code == 200 and r.json()["removed"] == 0
    bill = client.get(f"{B}/ra/{ra['id']}").json()
    assert bill["removed"] == [] and lines(client, ra["id"])["5"]["install"]["amount"] == pytest.approx(amt)


def test_a_typed_quantity_is_dropped_on_remove_so_restore_starts_clean(orphan):
    client, d, ra = orphan
    base = lines(client, ra["id"])["5"]["install"]["this"]
    client.post(f"{B}/ra/{ra['id']}/line", json={"item_code": "5", "stage": "install", "qty": 1, "reason": "x"})
    assert lines(client, ra["id"])["5"]["install"]["this"] == 1
    remove(client, ra["id"], "5"); remove(client, ra["id"], "5", remove=False)
    row = lines(client, ra["id"])["5"]["install"]
    assert row["this"] == base and not row["overridden"]


def test_add_line_on_a_removed_line_brings_it_back(orphan):
    client, d, ra = orphan
    remove(client, ra["id"], "5")
    r = client.post(f"{B}/ra/{ra['id']}/line", json={"item_code": "5", "stage": "install", "qty": 2, "reason": "needed"})
    assert r.status_code == 200
    bill = client.get(f"{B}/ra/{ra['id']}").json()
    assert bill["removed"] == [] and lines(client, ra["id"])["5"]["install"]["this"] == 2


def test_removing_twice_is_harmless(orphan):
    client, d, ra = orphan
    remove(client, ra["id"], "5"); r = remove(client, ra["id"], "5")
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert [x["item_code"] for x in client.get(f"{B}/ra/{ra['id']}").json()["removed"]] == ["5"]


def test_not_a_line_of_this_service_and_unknown_draft_are_refused(orphan):
    client, d, ra = orphan
    assert remove(client, ra["id"], "no-such-item").status_code == 400
    assert remove(client, "ra999", "5").status_code in (400, 404)


def test_invoiced_bill_cannot_be_changed_and_keeps_the_line_out(orphan):
    client, d, ra = orphan
    remove(client, ra["id"], "5")
    total = client.get(f"{B}/ra/{ra['id']}").json()["this_total"]
    assert client.post(f"{B}/ra/{ra['id']}/lock").status_code == 200
    locked = client.get(f"{B}/ra/{ra['id']}").json()
    assert "5" not in {x["item_code"] for x in locked["lines"]} and locked["this_total"] == pytest.approx(total)
    r = remove(client, ra["id"], "5", remove=False)
    assert r.status_code == 409                                                       # locked: nothing can change
    assert client.post(f"{B}/ra/{ra['id']}/reopen").status_code == 200
    again = client.get(f"{B}/ra/{ra['id']}").json()
    assert [x["item_code"] for x in again["removed"]] == ["5"]                        # reopened: still removed


def test_it_only_changes_this_draft_the_next_bill_starts_from_site_progress_again(orphan):
    """Honest limit: the removal belongs to this draft. The next RA is built from Site Progress again."""
    client, d, ra = orphan
    remove(client, ra["id"], "5")
    client.post(f"{B}/ra/{ra['id']}/lock")
    nxt = client.post(f"{B}/ra", json={"service": "Fire", "series": "install"})
    assert nxt.status_code == 200, nxt.text
    assert "5" in lines(client, nxt.json()["id"])
