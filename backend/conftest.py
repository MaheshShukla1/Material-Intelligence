"""Shared pytest fixtures for the backend test suite."""
import pytest


@pytest.fixture(autouse=True)
def _clear_load_run_cache():
    """api.load_run is @lru_cache'd by run_id alone (safe in production --
    a real run_id's data never changes after it's written). Tests are the
    one place that breaks: many fixtures across different test files reuse
    the literal id "run1" with a fresh monkeypatched RUNS/tmp_path each
    time, and without this, a later test's "run1" could silently read an
    earlier test's cached data. Runs once before every test automatically,
    so individual test files never need to remember to do this themselves."""
    try:
        from backend import api
        api.load_run.cache_clear()
    except ImportError:
        pass
    yield
