"""
Tests for db.py persistence layer
- init_db
- save_task / load_all_tasks
- save_explore_task / load_all_explore_tasks
- round-trip with curation and generated data
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from db import init_db, save_task, load_all_tasks, save_explore_task, load_all_explore_tasks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite DB in a temp dir."""
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr("db.DB_PATH", db_path)
    init_db()
    yield db_path


# ── init_db ───────────────────────────────────────────────────────────────────

def test_init_creates_tables(isolated_db):
    conn = sqlite3.connect(isolated_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "tasks" in tables
    assert "explore_tasks" in tables


def test_init_idempotent(isolated_db):
    # Calling init_db again should not raise
    init_db()
    conn = sqlite3.connect(isolated_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "tasks" in tables


# ── save_task / load_all_tasks ────────────────────────────────────────────────

def _make_task(tid="abc12345", status="pending", **kwargs):
    return {
        "id": tid,
        "task": "open example.com",
        "status": status,
        "logs": ["log1", "log2"],
        "screenshots": ["a.png", "b.png"],
        **kwargs,
    }


class TestTaskPersistence:
    def test_save_and_load(self):
        t = _make_task()
        save_task(t)
        loaded = load_all_tasks()
        assert t["id"] in loaded
        assert loaded[t["id"]]["task"] == "open example.com"
        assert loaded[t["id"]]["status"] == "pending"

    def test_logs_round_trip(self):
        t = _make_task(logs=["step 1", "step 2", "✓ done"])
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["logs"] == ["step 1", "step 2", "✓ done"]

    def test_screenshots_round_trip(self):
        t = _make_task(screenshots=["shot_01.png", "shot_02.png"])
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["screenshots"] == ["shot_01.png", "shot_02.png"]

    def test_curation_round_trip(self):
        curation = {
            "cards": [{"title": "看板", "marketing_score": 8.5, "filename": "a.png"}],
            "stats": {"total": 3, "after_dedup": 3, "kept": 1},
        }
        t = _make_task(status="done")
        t["curation"] = curation
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["curation"]["stats"]["kept"] == 1
        assert loaded[t["id"]]["curation"]["cards"][0]["title"] == "看板"

    def test_generated_round_trip(self):
        generated = {
            "ai_page": {"hero": {"headline": "测试标题", "subheadline": "副标题", "cta_text": "开始"}},
            "tweets": {"single_tweet": "这是推文"},
        }
        t = _make_task(status="done")
        t["generated"] = generated
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["generated"]["ai_page"]["hero"]["headline"] == "测试标题"

    def test_upsert_updates_existing(self):
        t = _make_task()
        save_task(t)
        t["status"] = "done"
        t["screenshots"] = ["result.png"]
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["status"] == "done"
        assert loaded[t["id"]]["screenshots"] == ["result.png"]

    def test_multiple_tasks(self):
        for i in range(5):
            save_task(_make_task(tid=f"task{i:04d}"))
        loaded = load_all_tasks()
        assert len(loaded) == 5

    def test_none_curation_loads_as_none(self):
        t = _make_task()
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["curation"] is None

    def test_none_generated_loads_as_none(self):
        t = _make_task()
        save_task(t)
        loaded = load_all_tasks()
        assert loaded[t["id"]]["generated"] is None

    def test_empty_db_returns_empty_dict(self):
        assert load_all_tasks() == {}

    def test_unicode_content_preserved(self):
        t = _make_task(task="打开 https://example.com 并截图 📸")
        save_task(t)
        loaded = load_all_tasks()
        assert "📸" in loaded[t["id"]]["task"]


# ── save_explore_task / load_all_explore_tasks ────────────────────────────────

def _make_explore(eid="exp12345", status="pending", **kwargs):
    return {
        "id": eid,
        "url": "https://example.com",
        "product_context": "test product",
        "status": status,
        "logs": [],
        "screenshots": [],
        "result": None,
        **kwargs,
    }


class TestExplorePersistence:
    def test_save_and_load(self):
        et = _make_explore()
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert et["id"] in loaded
        assert loaded[et["id"]]["url"] == "https://example.com"

    def test_result_round_trip(self):
        result = {
            "site_understanding": {
                "site_category": "B2B SaaS",
                "site_name": "Acme",
                "candidate_feature_pages": [{"path": "/dashboard", "marketing_score": 9.0}],
            },
            "visited_pages": [{"url": "https://example.com/dashboard", "score": 8.0}],
            "screenshots": [{"filename": "01_dashboard.png", "score": 8.0}],
        }
        et = _make_explore(status="done", result=result)
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        si = loaded[et["id"]]["result"]["site_understanding"]
        assert si["site_name"] == "Acme"
        assert si["candidate_feature_pages"][0]["marketing_score"] == 9.0

    def test_screenshots_list_round_trip(self):
        shots = [
            {"filename": "00_homepage.png", "score": 7.0, "page_type": "landing"},
            {"filename": "01_dashboard.png", "score": 8.5, "page_type": "dashboard"},
        ]
        et = _make_explore(status="done", screenshots=shots)
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert len(loaded[et["id"]]["screenshots"]) == 2
        assert loaded[et["id"]]["screenshots"][1]["page_type"] == "dashboard"

    def test_curation_round_trip(self):
        curation = {
            "cards": [{"title": "实时看板", "marketing_score": 9.0}],
            "stats": {"total": 5, "after_dedup": 4, "kept": 1},
        }
        et = _make_explore(status="done")
        et["curation"] = curation
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert loaded[et["id"]]["curation"]["cards"][0]["title"] == "实时看板"

    def test_upsert_updates_status(self):
        et = _make_explore()
        save_explore_task(et)
        et["status"] = "done"
        et["screenshots"] = [{"filename": "a.png"}]
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert loaded[et["id"]]["status"] == "done"

    def test_empty_db_returns_empty_dict(self):
        assert load_all_explore_tasks() == {}

    def test_multiple_explore_tasks(self):
        for i in range(4):
            save_explore_task(_make_explore(eid=f"exp{i:04d}"))
        loaded = load_all_explore_tasks()
        assert len(loaded) == 4

    def test_none_result_loads_as_none(self):
        et = _make_explore()
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert loaded[et["id"]]["result"] is None

    def test_product_context_preserved(self):
        et = _make_explore(product_context="AI analytics for e-commerce 电商分析")
        save_explore_task(et)
        loaded = load_all_explore_tasks()
        assert "电商分析" in loaded[et["id"]]["product_context"]
