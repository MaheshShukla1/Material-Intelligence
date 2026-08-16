"""Performance regression guard for the /overall route's two heaviest pure-
Python pieces: itemprog.compute() (runs once per service inside every
_service_view() call) and itemprog.project_room_status() (runs once, across
every service, for the "Rooms - whole site" rollup).

Backstory: the first version of project_room_status() re-scanned every
item in every service with DataFrame.iterrows() for EACH ROOM (nested the
wrong way round) -- at Hyatt's real scale (109 rooms x 5 services x ~80
items) that measured ~1.7 SECONDS on its own. compute() had the same
iterrows() + O(n) list-membership pattern and ran once per service inside
/overall's loop. Together they were the actual reason "Overall" felt slow
to load. Both were rewritten (itertuples() instead of iterrows(), a room
SET instead of list-membership scans, item-first instead of room-first
looping in project_room_status) -- verified to produce byte-for-byte
identical output to the old versions (see the benchmark this test distills),
133x and 4.2x faster respectively.

This test doesn't re-litigate correctness (test_room_buckets.py already
covers that exhaustively) -- it only guards against the SPEED regressing
back in, by asserting real-scale execution stays well under a generous time
budget. Run: cd tests && python -m pytest test_overall_performance.py -v
"""
import random
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd
import pytest

import itemprog

# Real Hyatt Hotel scale: 109 rooms, 5 services, ~80 mapped items each.
N_ROOMS = 109
N_SERVICES = 5
ITEMS_PER_SERVICE = 80


def _make_service(seed, n_items):
    rnd = random.Random(seed)
    items = pd.DataFrame([
        {"item_code": f"{seed}.{j}", "description": f"item {seed}.{j}",
         "unit": "MTR", "qty": rnd.uniform(1, 50)}
        for j in range(n_items)
    ])
    # the common real pattern: an engineer sets one overall "*" fraction per
    # item most of the time, rather than every room individually
    prog = {f"{seed}.{j}": {"*": rnd.choice([0.0, 0.3, 0.6, 1.0, 1.0])}
            for j in range(n_items)}
    rooms_svc = {}   # most items apply to every room -- the common case
    mapped = {f"{seed}.{j}" for j in range(n_items)}
    return items, prog, rooms_svc, mapped


@pytest.fixture(scope="module")
def real_scale_data():
    all_rooms = [f"roo{i}" for i in range(N_ROOMS)]
    services_data = [_make_service(s, ITEMS_PER_SERVICE) for s in range(N_SERVICES)]
    return all_rooms, services_data


def test_project_room_status_stays_fast_at_real_scale(real_scale_data):
    all_rooms, services_data = real_scale_data
    t0 = time.perf_counter()
    result = itemprog.project_room_status(services_data, all_rooms)
    elapsed = time.perf_counter() - t0
    assert result["total"] == N_ROOMS
    # old (room-first, iterrows()) version measured ~1.7s at this exact
    # scale; new version measured ~13ms. Budget generously at 300ms so this
    # doesn't flake on a slow CI box, while still catching a real regression
    # back to the old O(rooms x services x items) iterrows() pattern (which
    # would blow past this by 5-6x).
    assert elapsed < 0.3, (
        f"project_room_status took {elapsed*1000:.0f}ms at real project "
        "scale (109 rooms x 5 services x 80 items) -- budget is 300ms. "
        "This likely means the room-first iterrows() pattern crept back in; "
        "see this file's module docstring.")


def test_compute_stays_fast_at_real_scale(real_scale_data):
    _all_rooms, services_data = real_scale_data
    all_rooms = _all_rooms
    t0 = time.perf_counter()
    for items, prog, rooms_svc, _mapped in services_data:
        itemprog.compute(items, prog, rooms_svc, all_rooms, {})
    elapsed = time.perf_counter() - t0
    # old version measured ~60ms for 5 services at this scale; new ~14ms.
    # /overall calls this once per service via _service_view(), so this
    # directly drives how long the whole-site page takes to load.
    assert elapsed < 0.3, (
        f"compute() took {elapsed*1000:.0f}ms across {N_SERVICES} services "
        "at real project scale -- budget is 300ms. This likely means the "
        "iterrows() + list-membership pattern crept back in; see this "
        "file's module docstring.")


def test_room_buckets_stays_fast_for_every_item_in_a_service(real_scale_data):
    """_service_view() calls room_buckets() once per item (for the drawer's
    Rooms panel) -- same O(n²) list-membership risk as compute() had, at
    smaller scale (one service's worth of items instead of all five)."""
    all_rooms, services_data = real_scale_data
    items, prog, _rooms_svc, _mapped = services_data[0]
    t0 = time.perf_counter()
    for it in items.itertuples():
        itemprog.room_buckets(it.item_code, prog, {}, all_rooms)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"room_buckets() over {ITEMS_PER_SERVICE} items took "
        f"{elapsed*1000:.0f}ms -- budget is 300ms.")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
