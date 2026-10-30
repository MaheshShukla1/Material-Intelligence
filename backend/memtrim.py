"""Give memory the app has finished with back to the operating system.

Why this exists: the host (Railway) bills RAM per GB-minute. Building a run from a big stock register
(or reading a parquet file) allocates a few hundred MB for a few seconds, but two allocators keep it
afterwards instead of returning it:

  * pyarrow's own pool (mimalloc) holds on to what it grew to (measured: ~120 MB after one 12 MB register),
  * glibc malloc keeps freed arenas and fragmented pages of the main heap.

So resident memory only ever stepped up, after every sync, until the process was restarted. Nothing is
leaked -- the objects are gone -- the memory just was not handed back. `release()` hands it back.

`start_janitor()` runs that in the background: every `interval` seconds it looks at the process's
resident memory and, only when it has grown by `grow_mb` since the last release, calls `release()`.
It touches no request and no data; it works on Linux (Railway) and does nothing elsewhere (Windows
start.bat), where every call below is simply skipped.
"""
import gc
import threading

_STATE = {"thread": None, "after": 0.0}


def rss_mb():
    """Resident memory of this process in MB, or None where /proc is not available (Windows, macOS)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def release():
    """Collect garbage, then ask pyarrow and glibc to return their free memory to the OS. Never raises."""
    try:
        gc.collect()
    except Exception:
        pass
    try:
        import pyarrow as pa
        pa.default_memory_pool().release_unused()
    except Exception:
        pass
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _tick(grow_mb):
    """One look: release only if resident memory grew by `grow_mb` since the last release. Returns True if it released."""
    now = rss_mb()
    if now is None:
        return False
    if now - _STATE["after"] < grow_mb:
        return False
    release()
    after = rss_mb()
    _STATE["after"] = after if after is not None else now
    return True


def start_janitor(interval=30, grow_mb=40):
    """Start the background check once (safe to call again). Does nothing where memory cannot be read."""
    t = _STATE["thread"]
    if t is not None and t.is_alive():
        return t
    if rss_mb() is None:
        return None
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                _tick(grow_mb)
            except Exception:
                pass

    t = threading.Thread(target=loop, name="memtrim-janitor", daemon=True)
    t._stop_event = stop
    _STATE["thread"] = t
    t.start()
    return t
