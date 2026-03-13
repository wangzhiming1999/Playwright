"""
Shared pytest fixtures for test isolation.
- Each test session gets its own SQLite DB
- TASKS and EXPLORE_TASKS are cleared between tests
"""

import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Point db.DB_PATH to a temp file so tests never touch the real DB."""
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_tasks.db")
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def clear_stores():
    """Clear in-memory TASKS and EXPLORE_TASKS between tests."""
    import app as app_module
    app_module.TASKS.clear()
    app_module.EXPLORE_TASKS.clear()
    yield
    app_module.TASKS.clear()
    app_module.EXPLORE_TASKS.clear()
