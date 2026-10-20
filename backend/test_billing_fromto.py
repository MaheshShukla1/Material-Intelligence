"""From-To status of a bill: how much of its LENGTH quantity (Mtr / RMT items) has measurement entries behind it.

Reported need (site engineers, Hyatt): the bill should come with the client's measurement sheet (From, To, drum readings) and the person making
the bill must SEE, while it is still a draft, how much of it has no such backup -- in rupees -- without being forced to enter anything.
Rules under test (through the real routers on test_billing.py's Fire BOQ: GI pipe 100 Rmt at Rs 800, Installation 30 %, 20 Rmt recorded overall):
  * an Installation RA with a length line carries "fromto": per line the entries, what of THIS bill they back, what they do not, and the ₹ of it;
  * no entry at all = the whole line is without backup (nothing is hidden because nothing was entered);
  * only Mtr / RMT lines count; a Nos line never does; a Supply-only RA or a bill without a length line carries no "fromto" at all;
  * the overview adds the ₹ of the open drafts that has no backup (and nothing when there is no draft with a length line);
  * previous bills are not asked twice: after RA-1 was locked, only what RA-2 bills newly needs backup;
  * a locked RA keeps what it said at lock whatever is entered afterwards;
  * the download dialog's coverage says how many length items are backed and which are not.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.test_billing import env, prepared, SLUG                      # noqa: F401
from backend.test_billing_challan import new_ra, B
from backend.test_measurement_sheet import lock

P = f"/api/siteprogress/{SLUG}"
RATE, PCT = 800.0, 0.30


def entry(client, qty, scope="project", frm="Transformer", to="MDB", **kw):
    body = {"service": "Fire", "item_code": "3", "scope": scope, "from": frm, "to": to, "qty": qty}
    body.update(kw)
    r = client.post(f"{P}/progress/runs", json=body)
    assert r.status_code == 200, r.text


def ra_json(client, ra):
    r = client.get(f"{B}/ra/{ra}")
    assert r.status_code == 200, r.text
    return r.json()


def amount(q):
    return round(RATE * q * PCT, 2)


def test_no_entry_means_the_whole_length_line_has_no_backup(env):
    client, d, _ = prepared(env)
    ra = new_ra(client, "install")
    ft = ra_json(client, ra)["fromto"]
    assert ft["items"] == 1 and ft["unbacked_items"] == 1 and ft["unknown"] == 0                       # the pipe; the butterfly valve is Nos
    assert ft["lines"]["3"] == {"n": 0, "this": 20.0, "backed": 0.0, "unbacked": 20.0, "unbacked_amount": amount(20)}
    assert ft["value"] == amount(20) and ft["unbacked_value"] == amount(20)
    assert "2" not in ft["lines"]                                                                         # a Nos line never counts


def test_entries_back_part_of_the_line_and_the_rest_is_still_shown_in_rupees(env):
    client, d, _ = prepared(env)
    entry(client, 15)                                                                                    # 20 recorded overall + 15 = 35 installed
    ra = new_ra(client, "install")
    ft = ra_json(client, ra)["fromto"]
    assert ft["lines"]["3"]["this"] == 35.0 and ft["lines"]["3"]["n"] == 1
    assert ft["lines"]["3"]["backed"] == 15.0 and ft["lines"]["3"]["unbacked"] == 20.0
    assert ft["lines"]["3"]["unbacked_amount"] == amount(20) and ft["value"] == amount(35) and ft["unbacked_value"] == amount(20)


def test_a_line_fully_backed_says_nothing_is_missing(env):
    """Everything on the line was entered with From-To (nothing recorded overall before): nothing is missing, 0 rupees without backup."""
    client, d, _ = prepared(env)
    assert client.post(f"{P}/progress/item", json={"service": "Fire", "item_code": "3", "frac": 0}).status_code == 200
    entry(client, 25)
    entry(client, 15, to="DB-1")
    ra = new_ra(client, "install")
    ft = ra_json(client, ra)["fromto"]
    assert ft["lines"]["3"] == {"n": 2, "this": 40.0, "backed": 40.0, "unbacked": 0.0, "unbacked_amount": 0.0}
    assert ft["unbacked_items"] == 0 and ft["unbacked_value"] == 0.0 and ft["value"] == amount(40)
    assert client.get(B).json()["no_backup"] == {"value": 0.0, "items": 0, "drafts": 1}


def test_supply_only_bill_and_bills_without_length_lines_carry_nothing(env):
    client, d, _ = prepared(env)
    sup = new_ra(client, "supply")
    assert "fromto" not in ra_json(client, sup)
    r = client.post(f"{B}/ra", json={"service": "HVAC", "series": "install"})
    if r.status_code == 200:                                                                             # HVAC has no setup in this harness; if it did, it has no length line
        assert "fromto" not in ra_json(client, r.json()["id"])


def test_the_overview_adds_up_the_rupees_of_the_open_drafts(env):
    client, d, _ = prepared(env)
    assert "no_backup" not in client.get(B).json()                                                      # no draft yet
    entry(client, 15)
    new_ra(client, "install")
    ov = client.get(B).json()
    assert ov["no_backup"] == {"value": amount(20), "items": 1, "drafts": 1}
    ra = [r for r in ov["ras"] if r["status"] == "draft"][0]
    assert ra["fromto"] == {"items": 1, "unbacked_items": 1, "with_entries": 1, "value": amount(35), "unbacked_value": amount(20), "unknown": 0}


def test_an_invoiced_ra_is_in_the_overview_with_what_it_had_at_lock_but_not_in_the_open_total(env):
    client, d, _ = prepared(env)
    entry(client, 15)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    ov = client.get(B).json()
    locked = [r for r in ov["ras"] if r["status"] == "locked"][0]
    assert locked["fromto"]["unbacked_value"] == amount(20) and locked["fromto"]["with_entries"] == 1
    assert "no_backup" not in ov                                                                         # the open total counts drafts only


def test_the_next_bill_needs_backup_only_for_what_it_adds(env):
    client, d, _ = prepared(env)
    entry(client, 15)
    ra1 = new_ra(client, "install")
    assert ra_json(client, ra1)["fromto"]["lines"]["3"]["unbacked"] == 20.0                               # RA-1 itself did have 20 without backup
    lock(client, ra1)                                                                                    # 35 billed (20 + 15 entered)
    entry(client, 10, to="DB-1")
    ra2 = new_ra(client, "install")
    ft = ra_json(client, ra2)["fromto"]
    assert ft["lines"]["3"] == {"n": 2, "this": 10.0, "backed": 10.0, "unbacked": 0.0, "unbacked_amount": 0.0}      # the 20 + 15 billed before are not asked again
    assert ft["unbacked_value"] == 0.0 and ft["unbacked_items"] == 0


def test_a_locked_bill_keeps_what_it_said_whatever_is_entered_later(env):
    client, d, _ = prepared(env)
    entry(client, 15)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    before = ra_json(client, ra1)["fromto"]
    entry(client, 10, to="DB-1")
    assert ra_json(client, ra1)["fromto"] == before
    assert before["lines"]["3"]["unbacked"] == 20.0


def test_the_screens_still_never_carry_the_run_lines(env):
    client, d, _ = prepared(env)
    entry(client, 15)
    ra = new_ra(client, "install")
    txt = client.get(f"{B}/ra/{ra}").text
    assert '"loc"' not in txt and '"runs"' not in txt and '"fromto"' in txt


def test_the_download_dialog_is_told_which_length_items_have_no_backup(env):
    client, d, _ = prepared(env)
    entry(client, 15)
    ra = new_ra(client, "install")
    c = client.get(f"{B}/measurement/coverage", params={"ras": ra}).json()["ras"][0]
    f = c["fromto"]
    assert f["items"] == 1 and f["unbacked_items"] == 1 and f["with_entries"] == 1
    assert f["gaps"] == [{"sr": f["gaps"][0]["sr"], "desc": "GI pipe 100mm", "unit": "Rmt", "unbacked": 20.0, "this": 35.0}]
    sup = new_ra(client, "supply")
    assert "fromto" not in client.get(f"{B}/measurement/coverage", params={"ras": sup}).json()["ras"][0]
