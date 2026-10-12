"""Deleting a Site Progress activity vs. the installation on the bill.

Real report: an engineer deleted an activity in Site Progress, but the Billing draft still showed that installation.
Reason (code): deleting an activity removes the GROUPING (and the old room-level rows), while the recorded work of each BOQ
item (item_progress.json) -- which Billing reads -- stays. That stays the default. The new, explicit second choice also
erases the recorded work of the items that belong to that activity ALONE; an item shared with another activity is never
wiped. The preview endpoint asks the real bill code what each open draft would become, and must agree with what really happens.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

from backend.test_billing import env, progress, setup_fire, SLUG, make_run, link, STOCK1  # noqa: F401  (env is a fixture)

SP = f"/api/siteprogress/{SLUG}"
B = f"/api/billing/{SLUG}"


def activity(client, op, name, **kw):
    r = client.post(f"{SP}/activity", json={"service": "Fire", "op": op, "name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def mapto(client, name, codes):
    r = client.post(f"{SP}/mapping", json={"service": "Fire", "activity": name, "codes": codes})
    assert r.status_code == 200, r.text


@pytest.fixture
def scene(env):
    """Fire: 'Pumps' = items 2 (pump set) and 3 (pipe); 'Pipes' = items 3 and 5. So 2 belongs to Pumps alone, 3 is shared,
    5 belongs to Pipes alone. Work is logged on all three and the installation draft opened by itself."""
    client, d, tmp = env
    make_run(tmp, "run1", "2026-09-01T10:00:00", STOCK1)
    for code, mat in (("2", "BUTTERFLY VALVE 150MM"), ("3", "GI PIPE 100MM"), ("5", "HYDRANT VALVE")):
        link(client, code, mat)
    setup_fire(client)
    activity(client, "create", "Pumps"); activity(client, "create", "Pipes")
    mapto(client, "Pumps", ["2", "3"]); mapto(client, "Pipes", ["3", "5"])
    progress(client, "2", 0.5); progress(client, "3", 0.2); progress(client, "5", 0.5)
    r = client.post(f"{B}/auto")
    assert r.status_code == 200 and len(r.json()["created"]) == 1, r.text
    return client, d


def draft(client):
    return next(x for x in client.get(B).json()["ras"] if x["status"] == "draft")


def item_prog(d):
    return json.loads((d / "item_progress.json").read_text())["Fire"]


def inst(client, ra_id, code):
    bill = client.get(f"{B}/ra/{ra_id}").json()
    ln = next((x for x in bill["lines"] if x["item_code"] == code), None)
    return 0.0 if ln is None else ln["install"]["amount"]


def test_the_reported_case_deleting_the_activity_leaves_the_draft_installation_untouched(scene):
    client, d = scene
    before = draft(client)["this_total"]
    assert before > 0
    view = activity(client, "delete", "Pumps")                       # the plain delete, exactly as before
    assert "Pumps" not in view["activities"] and "Pumps" not in view["mapping"]
    assert draft(client)["this_total"] == pytest.approx(before)      # the recorded work is on the items, so the bill stays
    assert set(item_prog(d)) == {"2", "3", "5"}


def test_with_progress_wipes_only_the_items_that_belong_to_that_activity_alone(scene):
    client, d = scene
    ra = draft(client)
    before = ra["this_total"]
    two = inst(client, ra["id"], "2")
    assert two > 0
    activity(client, "delete", "Pumps", with_progress=True)
    assert set(item_prog(d)) == {"3", "5"}                           # item 2 (Pumps only) gone; item 3 is shared with Pipes: kept
    assert draft(client)["this_total"] == pytest.approx(before - two)
    assert inst(client, ra["id"], "3") > 0 and inst(client, ra["id"], "5") > 0
    # …and deleting the other one with progress now wipes 3 and 5 (3 is no longer shared)
    activity(client, "delete", "Pipes", with_progress=True)
    assert item_prog(d) == {} and draft(client)["this_total"] == pytest.approx(0)


def test_the_preview_says_exactly_what_the_delete_then_does(scene):
    client, d = scene
    ra = draft(client)
    snapshot = (d / "item_progress.json").read_text()
    r = client.get(f"{B}/activity-impact", params={"service": "Fire", "activity": "Pumps"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["items"] == 1 and p["locked"] == 0 and len(p["drafts"]) == 1
    dr = p["drafts"][0]
    assert dr["id"] == ra["id"] and dr["now"] == pytest.approx(ra["this_total"]) and dr["then"] < dr["now"]
    assert (d / "item_progress.json").read_text() == snapshot         # a preview writes nothing
    assert draft(client)["this_total"] == pytest.approx(ra["this_total"])
    activity(client, "delete", "Pumps", with_progress=True)
    assert draft(client)["this_total"] == pytest.approx(dr["then"])    # the preview was right, to the rupee


def test_an_invoiced_ra_never_changes_and_the_draft_can_go_below_zero_so_the_preview_must_say_so(scene):
    client, d = scene
    first = draft(client)
    assert client.post(f"{B}/ra/{first['id']}/lock").status_code == 200   # invoiced: a snapshot
    locked_total = next(x for x in client.get(B).json()["ras"] if x["id"] == first["id"])["this_total"]
    progress(client, "2", 0.75)                                            # more work after the invoice -> a new draft
    assert len(client.post(f"{B}/auto").json()["created"]) == 1
    new = draft(client)
    p = client.get(f"{B}/activity-impact", params={"service": "Fire", "activity": "Pumps"}).json()
    assert p["locked"] == 1 and [x["id"] for x in p["drafts"]] == [new["id"]]
    assert p["drafts"][0]["then"] < 0                                      # item 2 was billed before, so removing its work = a credit
    activity(client, "delete", "Pumps", with_progress=True)
    assert draft(client)["this_total"] == pytest.approx(p["drafts"][0]["then"])
    assert next(x for x in client.get(B).json()["ras"] if x["id"] == first["id"])["this_total"] == pytest.approx(locked_total)


def test_an_activity_with_no_recorded_work_has_nothing_to_wipe(scene):
    client, d = scene
    activity(client, "create", "Empty")
    p = client.get(f"{B}/activity-impact", params={"service": "Fire", "activity": "Empty"}).json()
    assert p["items"] == 0 and p["drafts"][0]["now"] == pytest.approx(p["drafts"][0]["then"])
    before = (d / "item_progress.json").read_text()
    activity(client, "delete", "Empty", with_progress=True)
    assert (d / "item_progress.json").read_text() == before
