"""Draft RAs open by themselves as soon as installation is logged in Site Progress, and follow it live.
Real scenario: the engineer ticks work in Site Progress; nobody clicks 'New RA bill'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.test_billing import env, progress, SLUG  # noqa: F401  (env is a fixture)
from backend.test_billing_challan import cs, ch, new_ra, B


def auto(client):
    r = client.post(f"{B}/auto")
    assert r.status_code == 200, r.text
    return r.json()["created"]


def ras(client):
    return client.get(B).json()["ras"]


def test_nothing_opens_until_installation_is_logged(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    assert auto(client) == [] and ras(client) == []


def test_installation_progress_opens_an_installation_draft_for_a_separate_service_and_it_follows_progress(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    progress(client, "2", 0.25)
    made = auto(client)
    assert len(made) == 1 and made[0]["service"] == "Fire" and made[0]["label"].startswith("Fire · Installation RA 1")
    r = ras(client)
    assert len(r) == 1 and r[0]["series"] == "install" and r[0]["status"] == "draft" and r[0]["this_total"] > 0
    first = r[0]["this_total"]
    assert auto(client) == [] and len(ras(client)) == 1          # asking again changes nothing
    progress(client, "2", 0.75)                                  # more work logged -> the same draft grows, no typing
    r2 = ras(client)
    assert len(r2) == 1 and r2[0]["id"] == r[0]["id"] and r2[0]["this_total"] > first


def test_a_service_billed_together_gets_a_together_draft(env):
    client, _, _ = env
    cs(client, mode="together", sup=70, ins=30)
    ch(client, "614", "2026-07-04", [("2", 2)])
    progress(client, "2", 0.5)
    made = auto(client)
    assert len(made) == 1
    r = ras(client)[0]
    assert r["series"] == "together" and r["stages"] == ["supply", "install"]
    assert r["stage_this"]["supply"] > 0 and r["stage_this"]["install"] > 0


def test_an_open_draft_on_the_same_stage_is_never_duplicated_and_a_typed_one_is_left_alone(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    ch(client, "614", "2026-07-04", [("2", 2)])
    mine = new_ra(client, "together")                            # the engineer started a Together draft by hand
    progress(client, "2", 0.5)
    assert auto(client) == []
    assert [x["id"] for x in ras(client)] == [mine]


def test_deleting_the_draft_is_remembered_until_more_installation_is_logged(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    progress(client, "2", 0.25)
    rid = auto(client)[0]["id"]
    assert client.delete(f"{B}/ra/{rid}").status_code == 200
    assert auto(client) == [] and ras(client) == []              # 'not now' is respected
    progress(client, "2", 0.5)                                   # new work -> it is reasonable to open again
    again = auto(client)
    assert len(again) == 1 and again[0]["label"].startswith("Fire · Installation RA 1")


def test_after_invoicing_only_new_installation_opens_the_next_draft(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    progress(client, "2", 0.25)
    rid = auto(client)[0]["id"]
    assert client.post(f"{B}/ra/{rid}/lock").status_code == 200
    assert auto(client) == []                                    # everything logged so far is billed
    progress(client, "2", 0.5)
    made = auto(client)
    assert len(made) == 1 and made[0]["label"].startswith("Fire · Installation RA 2")


def test_the_switch_per_service_turns_it_off_and_on_and_survives_other_edits(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    st = lambda: next(s for s in client.get(f"{B}/setup").json()["services"] if s["service"] == "Fire")
    assert st()["auto_draft"] is True                            # default ON
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30,
                                    "supply_source": "challan", "auto_draft": False})
    assert st()["auto_draft"] is False
    progress(client, "2", 0.25)
    assert auto(client) == []                                    # switched off: nothing opens
    cs(client, mode="separate", sup=70, ins=30)                  # a later save that does not mention the switch keeps it
    assert st()["auto_draft"] is False
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30,
                                    "supply_source": "challan", "auto_draft": True})
    assert len(auto(client)) == 1
    assert client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "auto_draft": "yes"}).status_code == 400


def test_a_service_without_a_complete_setup_is_skipped_not_guessed(env):
    client, _, _ = env
    client.post(f"{B}/setup", json={"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": None,
                                    "supply_source": "challan"})
    progress(client, "2", 0.5)
    assert auto(client) == [] and ras(client) == []              # installation % unknown -> nothing is invented
