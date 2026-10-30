"""memtrim: hand finished memory back to the OS (the host bills RAM per GB-minute).

Run from the repo root:  pytest backend/test_memtrim.py -v
"""
import sys

import pytest

from backend import memtrim


def test_release_never_raises_even_without_pyarrow_or_glibc(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)                      # import pyarrow -> ImportError
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("no libc.so.6")))   # Windows / musl
    memtrim.release()


def test_janitor_does_nothing_where_memory_cannot_be_read(monkeypatch):
    monkeypatch.setattr(memtrim, "rss_mb", lambda: None)
    monkeypatch.setitem(memtrim._STATE, "thread", None)
    assert memtrim.start_janitor() is None                                  # Windows start.bat: no thread, no error
    assert memtrim._tick(40) is False


def test_tick_releases_only_after_memory_has_grown(monkeypatch):
    seen, calls = [100.0], []
    monkeypatch.setattr(memtrim, "rss_mb", lambda: seen[0])
    monkeypatch.setattr(memtrim, "release", lambda: calls.append(1))
    monkeypatch.setitem(memtrim._STATE, "after", 100.0)
    assert memtrim._tick(40) is False and not calls                         # idle: nothing to do
    seen[0] = 120.0
    assert memtrim._tick(40) is False and not calls                         # small growth: leave it
    seen[0] = 400.0
    assert memtrim._tick(40) is True and len(calls) == 1                    # a big run was built: release
    assert memtrim._STATE["after"] == 400.0
    assert memtrim._tick(40) is False and len(calls) == 1                   # and not again until it grows again


def test_janitor_starts_once(monkeypatch):
    if memtrim.rss_mb() is None:
        pytest.skip("needs /proc")
    monkeypatch.setitem(memtrim._STATE, "thread", None)
    t = memtrim.start_janitor(interval=3600)
    try:
        assert t is not None and t.daemon and t.is_alive()
        assert memtrim.start_janitor(interval=3600) is t
    finally:
        t._stop_event.set()
        t.join(2)


@pytest.mark.skipif(memtrim.rss_mb() is None, reason="needs /proc (Linux)")
def test_release_really_returns_arrow_memory_to_the_os():
    """The mechanism itself: pyarrow's allocator keeps what it grew to after the data is gone."""
    pa = pytest.importorskip("pyarrow")
    big = [pa.array(range(4_000_000)) for _ in range(8)]                    # ~256 MB in Arrow's pool
    held = memtrim.rss_mb()
    del big
    memtrim.release()
    assert memtrim.rss_mb() < held - 120, (held, memtrim.rss_mb())


def test_the_entry_point_starts_the_janitor():
    """`python main.py` (the Procfile / start.bat command) is what starts it; the API module itself is untouched."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    assert "memtrim.start_janitor()" in src and src.index("memtrim.start_janitor()") < src.index("uvicorn.run(")
