"""Billing screens were slow because boq.xlsx was re-opened for every RA / preview on every refresh
(~85% of the Thoth overview's time). The rows are now cached per file version -- same numbers, fewer reads."""
import os
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openpyxl

from backend import billing
from backend.test_billing import env, SLUG  # noqa: F401  (env is a fixture)
from backend.test_billing_challan import cs, new_ra, B


def _count_opens(monkeypatch):
    calls = []
    real = openpyxl.load_workbook

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(openpyxl, "load_workbook", spy)
    return calls


def _busy(client):
    cs(client, mode="together", sup=70, ins=30)
    new_ra(client, "together")


def test_repeated_overviews_read_the_boq_workbook_once(env, monkeypatch):
    client, _, _ = env
    _busy(client)
    billing._SHEET_CACHE.clear()
    calls = _count_opens(monkeypatch)
    first = client.get(B).json()
    n_first = len(calls)
    for _ in range(3):
        assert client.get(B).json() == first            # identical answer every time
    assert n_first == 1 and len(calls) == 1             # one read for the whole service set, then none


def test_cached_and_cold_overview_give_identical_numbers(env):
    client, _, _ = env
    _busy(client)
    billing._SHEET_CACHE.clear()
    cold = client.get(B).json()
    warm = client.get(B).json()
    assert cold == warm and cold["ras"]


def test_a_changed_boq_file_is_read_afresh(env, monkeypatch):
    client, _, _ = env
    _busy(client)
    d = billing.sp._need(SLUG)
    billing._SHEET_CACHE.clear()
    client.get(B)
    calls = _count_opens(monkeypatch)
    client.get(B)
    assert calls == []                                   # cached
    x = d / "boq.xlsx"
    st = x.stat()
    os.utime(x, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # BOQ re-uploaded -> new version
    client.get(B)
    assert len(calls) == 1                               # re-read exactly once


def test_home_ready_figures_match_what_each_series_computes_alone(env):
    client, _, _ = env
    cs(client, mode="separate", sup=70, ins=30)
    svc = next(s for s in client.get(B).json()["services"] if s["service"] == "Fire")
    d = billing.sp._need(SLUG)
    store = billing._load(d)
    assert svc["ready"] == billing._ready(SLUG, d, store, "Fire")
    assert svc["can"] == billing._ready(SLUG, d, store, "Fire", all_types=True)
    assert list(svc["ready"]) == ["supply", "install"]
    cs(client, mode="together", sup=70, ins=30)
    svc = next(s for s in client.get(B).json()["services"] if s["service"] == "Fire")
    store = billing._load(d)
    assert svc["ready"] == billing._ready(SLUG, d, store, "Fire") and list(svc["ready"]) == ["together"]


# ---------------------------------------------------------------- BOQ lines + installation quantities are remembered per data version
from backend.test_billing import progress, line  # noqa: E402


def _age(d, seconds=30):
    """Make every data file look older than the 'just written' window (real files are, a moment after an edit)."""
    t = os.stat(d / "boq.xlsx").st_mtime_ns - seconds * 1_000_000_000
    for f in d.iterdir():
        if f.is_file():
            os.utime(f, ns=(t, t))


def _spy(monkeypatch):
    n = {"lines": 0, "inst": 0}
    rl, ri = billing._base_lines_raw, billing._install_qty_raw
    monkeypatch.setattr(billing, "_base_lines_raw", lambda *a, **k: (n.__setitem__("lines", n["lines"] + 1), rl(*a, **k))[1])
    monkeypatch.setattr(billing, "_install_qty_raw", lambda *a, **k: (n.__setitem__("inst", n["inst"] + 1), ri(*a, **k))[1])
    return n


def test_lines_and_installation_are_computed_once_per_data_version_not_per_series_and_ra(env, monkeypatch):
    client, d, _ = env
    _busy(client)
    progress(client, "1", 0.5)
    billing._DATA_CACHE.clear()
    _age(d)
    n = _spy(monkeypatch)
    first = client.get(B).json()
    assert n["lines"] <= 2 and n["inst"] <= 2             # two services in the test BOQ: once each, however many bills / series are asked
    seen = dict(n)
    again = client.get(B).json()
    assert again == first and n == seen                  # nothing recomputed, identical answer
    # the cached answer is the same as an uncached one
    billing._DATA_CACHE.clear()
    assert client.get(B).json() == first


def test_a_file_written_a_moment_ago_is_not_trusted_so_an_edit_shows_at_once(env, monkeypatch):
    client, d, _ = env
    _busy(client)
    billing._DATA_CACHE.clear()
    progress(client, "1", 0.25)
    first = client.get(B).json()
    n = _spy(monkeypatch)
    client.get(B)
    assert n["lines"] > 0 and n["inst"] > 0              # freshly written files: recomputed, never served from memory
    progress(client, "1", 0.75)                          # same size on disk as 0.25, written inside one timestamp tick
    after = client.get(B).json()
    ra = lambda o: next(r for r in o["ras"] if r["status"] == "draft")["this_total"]
    assert ra(after) == pytest.approx(ra(first) * 3)


def test_a_data_file_that_changes_after_it_was_remembered_is_read_again(env, monkeypatch):
    client, d, _ = env
    _busy(client)
    progress(client, "1", 0.25)
    _age(d)
    billing._DATA_CACHE.clear()
    first = client.get(B).json()
    client.get(B)                                        # now remembered
    n = _spy(monkeypatch)
    progress(client, "1", 0.75)                          # item_progress.json is rewritten
    after = client.get(B).json()
    assert n["inst"] > 0                                 # the new version was read
    ra = lambda o: next(r for r in o["ras"] if r["status"] == "draft")["this_total"]
    assert ra(after) == pytest.approx(ra(first) * 3)


def test_a_work_order_split_made_after_the_lines_were_remembered_applies_at_once(env):
    client, d, _ = env
    cs(client)
    _age(d)
    billing._DATA_CACHE.clear()
    assert [s["service"] for s in client.get(B).json()["services"]] == ["Fire", "HVAC"]
    g = client.get(f"{B}/packages/Fire").json()["groups"]
    r = client.post(f"{B}/packages/Fire", json={"items": [
        {"name": "Pumps", "groups": [{"sheet": g[0]["sheet"], "from": g[0]["from"], "to": g[0]["to"]}]}, {"name": "Hydrants", "rest": True}]})
    assert r.status_code == 200, r.text                  # only billing.json changed: the remembered lines are filtered per Work Order
    svcs = {s["service"]: s for s in client.get(B).json()["services"]}
    assert set(svcs) == {"Fire › Pumps", "Fire › Hydrants", "HVAC"}
    assert svcs["Fire › Hydrants"]["contract_value"] == 10 * 3000


def test_callers_may_change_the_lines_they_get_without_touching_the_remembered_ones(env):
    client, d, _ = env
    _age(d)
    billing._DATA_CACHE.clear()
    a = billing._lines(d, "Fire")
    a[0]["rate"] = -1
    a.pop()
    b = billing._lines(d, "Fire")
    assert b[0]["rate"] != -1 and len(b) == len(a) + 1
