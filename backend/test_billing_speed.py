"""Billing screens were slow because boq.xlsx was re-opened for every RA / preview on every refresh
(~85% of the Thoth overview's time). The rows are now cached per file version -- same numbers, fewer reads."""
import os
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
