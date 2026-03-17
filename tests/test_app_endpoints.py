"""
Tests for new API endpoints in app.py that are NOT covered by test_app.py.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def client():
    from app import app, TASKS, EXPLORE_TASKS
    TASKS.clear()
    EXPLORE_TASKS.clear()
    return TestClient(app)


def _make_task(tid, status="done", task_text="test task", **extra):
    t = {
        "id": tid, "task": task_text, "status": status,
        "logs": [], "screenshots": [],
        "browser_mode": "builtin", "cdp_url": "http://localhost:9222",
        "chrome_profile": "Default", "webhook_url": "", "timeout": 0,
        "created_at": time.time(),
    }
    t.update(extra)
    return t


# ── GET /health ──────────────────────────────────────────────────────────────


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_includes_counts(self, client):
        data = client.get("/health").json()
        assert "tasks" in data
        assert "explore_tasks" in data
        assert "pool" in data


# ── GET /tasks/{task_id} ─────────────────────────────────────────────────────


class TestGetTask:
    def test_found(self, client):
        from app import TASKS
        TASKS["t001"] = _make_task("t001")
        r = client.get("/tasks/t001")
        assert r.status_code == 200
        assert r.json()["id"] == "t001"

    def test_not_found(self, client):
        r = client.get("/tasks/nonexistent")
        assert r.status_code == 404

    def test_includes_all_fields(self, client):
        from app import TASKS
        TASKS["t002"] = _make_task("t002", retry_of="t001")
        data = client.get("/tasks/t002").json()
        assert data["browser_mode"] == "builtin"
        assert data["retry_of"] == "t001"


# ── POST /tasks/{task_id}/retry ──────────────────────────────────────────────


class TestRetryTask:
    def test_retry_failed_task(self, client):
        from app import TASKS
        TASKS["fail1"] = _make_task("fail1", status="failed", task_text="open example.com")
        with patch("app._run_task"), patch("app._task_pool.submit", new_callable=AsyncMock):
            r = client.post("/tasks/fail1/retry")
        assert r.status_code == 200
        data = r.json()
        assert data["retry_of"] == "fail1"
        assert data["task_id"] != "fail1"
        # New task should exist in TASKS
        new_id = data["task_id"]
        assert TASKS[new_id]["status"] == "pending"
        assert TASKS[new_id]["task"] == "open example.com"

    def test_retry_not_found(self, client):
        r = client.post("/tasks/missing/retry")
        assert r.status_code == 404

    def test_retry_running_task_rejected(self, client):
        from app import TASKS
        TASKS["run1"] = _make_task("run1", status="running")
        r = client.post("/tasks/run1/retry")
        assert r.status_code == 400
        assert "running" in r.json()["detail"]

    def test_retry_pending_task_rejected(self, client):
        from app import TASKS
        TASKS["pend1"] = _make_task("pend1", status="pending")
        r = client.post("/tasks/pend1/retry")
        assert r.status_code == 400

    def test_retry_inherits_params(self, client):
        from app import TASKS
        TASKS["orig1"] = _make_task("orig1", status="failed",
                                     browser_mode="cdp", cdp_url="http://remote:9222",
                                     timeout=120, webhook_url="https://hook.example.com")
        with patch("app._run_task"), patch("app._task_pool.submit", new_callable=AsyncMock):
            r = client.post("/tasks/orig1/retry")
        new_id = r.json()["task_id"]
        new_task = TASKS[new_id]
        assert new_task["browser_mode"] == "cdp"
        assert new_task["cdp_url"] == "http://remote:9222"
        assert new_task["timeout"] == 120
        assert new_task["webhook_url"] == "https://hook.example.com"


# ── POST /tasks/batch-delete ─────────────────────────────────────────────────


class TestBatchDelete:
    def test_deletes_multiple(self, client):
        from app import TASKS
        TASKS["d1"] = _make_task("d1")
        TASKS["d2"] = _make_task("d2")
        TASKS["d3"] = _make_task("d3")
        r = client.post("/tasks/batch-delete", json={"task_ids": ["d1", "d2"]})
        assert r.status_code == 200
        data = r.json()
        assert data["deleted"] == 2
        assert set(data["deleted_ids"]) == {"d1", "d2"}
        assert "d1" not in TASKS
        assert "d3" in TASKS

    def test_skips_missing(self, client):
        from app import TASKS
        TASKS["e1"] = _make_task("e1")
        r = client.post("/tasks/batch-delete", json={"task_ids": ["e1", "missing1", "missing2"]})
        data = r.json()
        assert data["deleted"] == 1
        assert data["deleted_ids"] == ["e1"]

    def test_empty_list(self, client):
        r = client.post("/tasks/batch-delete", json={"task_ids": []})
        assert r.json()["deleted"] == 0

    def test_cleans_screenshot_dir(self, client, tmp_path):
        from app import TASKS
        tid = "shotdel1"
        shot_dir = Path(f"screenshots/{tid}")
        shot_dir.mkdir(parents=True, exist_ok=True)
        (shot_dir / "test.png").write_bytes(b"fake")
        TASKS[tid] = _make_task(tid)
        client.post("/tasks/batch-delete", json={"task_ids": [tid]})
        assert not shot_dir.exists()


# ── GET /pool + PUT /pool ────────────────────────────────────────────────────


class TestPool:
    def test_get_pool_status(self, client):
        r = client.get("/pool")
        assert r.status_code == 200
        data = r.json()
        assert "max_workers" in data

    def test_resize_valid(self, client):
        r = client.put("/pool", json={"max_workers": 5})
        assert r.status_code == 200
        data = r.json()
        assert data["new_max_workers"] == 5
        assert "pool" in data

    def test_resize_too_low(self, client):
        r = client.put("/pool", json={"max_workers": 0})
        assert r.status_code == 400

    def test_resize_too_high(self, client):
        r = client.put("/pool", json={"max_workers": 11})
        assert r.status_code == 400


# ── POST /tasks/{task_id}/cancel ─────────────────────────────────────────────


class TestCancelTask:
    def test_cancel_pending(self, client):
        from app import TASKS
        TASKS["cp1"] = _make_task("cp1", status="pending")
        with patch("app.save_task"):
            r = client.post("/tasks/cp1/cancel")
        assert r.status_code == 200
        assert TASKS["cp1"]["status"] == "cancelled"

    def test_cancel_not_found(self, client):
        r = client.post("/tasks/missing/cancel")
        assert r.status_code == 404

    def test_cancel_already_done(self, client):
        from app import TASKS
        TASKS["cd1"] = _make_task("cd1", status="done")
        r = client.post("/tasks/cd1/cancel")
        assert r.status_code == 400
        assert "already" in r.json()["detail"]

    def test_cancel_already_failed(self, client):
        from app import TASKS
        TASKS["cf1"] = _make_task("cf1", status="failed")
        r = client.post("/tasks/cf1/cancel")
        assert r.status_code == 400

    def test_cancel_running_sets_signal(self, client):
        from app import TASKS, _CANCEL_EVENTS
        TASKS["cr1"] = _make_task("cr1", status="running")
        ev = threading.Event()
        _CANCEL_EVENTS["cr1"] = ev
        r = client.post("/tasks/cr1/cancel")
        assert r.status_code == 200
        assert ev.is_set()
        _CANCEL_EVENTS.pop("cr1", None)


# ── POST /tasks/{task_id}/reply ──────────────────────────────────────────────


class TestReplyTask:
    def test_no_pending_question(self, client):
        r = client.post("/tasks/nopend/reply?answer=hello")
        assert r.status_code == 404
        assert "no pending question" in r.json()["detail"]

    def test_reply_success(self, client):
        from app import _PENDING_QUESTIONS, _PENDING_LOCK
        ev = threading.Event()
        with _PENDING_LOCK:
            _PENDING_QUESTIONS["rp1"] = {"question": "code?", "reason": "captcha", "event": ev, "answer": None}
        r = client.post("/tasks/rp1/reply?answer=1234")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert ev.is_set()
        _PENDING_QUESTIONS.pop("rp1", None)


# ── GET /screenshots/{task_id}/{filename} ────────────────────────────────────


class TestScreenshots:
    @pytest.fixture
    def _screenshot_file(self):
        tid = "shot001"
        d = Path(f"screenshots/{tid}")
        d.mkdir(parents=True, exist_ok=True)
        img_path = d / "result.png"
        Image.new("RGB", (10, 10)).save(img_path)
        yield tid, "result.png"
        import shutil
        if d.exists():
            shutil.rmtree(d)

    def test_valid_path(self, client, _screenshot_file):
        tid, fname = _screenshot_file
        r = client.get(f"/screenshots/{tid}/{fname}")
        assert r.status_code == 200

    def test_path_traversal_task_id(self, client):
        # The regex [a-zA-Z0-9_-]+ rejects dots, so "task..id" should fail
        r = client.get("/screenshots/task..id/file.png")
        assert r.status_code == 400

    def test_path_traversal_filename(self, client):
        # Filename with space (not matching [a-zA-Z0-9_.-]+) should be rejected
        r = client.get("/screenshots/validid/file name.png")
        assert r.status_code in (400, 404, 422)

    def test_special_chars_task_id(self, client):
        r = client.get("/screenshots/id%20with%20spaces/file.png")
        assert r.status_code in (400, 404, 422)

    def test_not_found(self, client):
        r = client.get("/screenshots/validid/nonexistent.png")
        assert r.status_code == 404


# ── GET /tasks (pagination + filtering) ──────────────────────────────────────


class TestListTasksPagination:
    @pytest.fixture
    def _many_tasks(self, client):
        from app import TASKS
        for i in range(10):
            status = "done" if i < 7 else "failed"
            TASKS[f"pg{i:02d}"] = _make_task(f"pg{i:02d}", status=status,
                                              task_text=f"task number {i}")
            TASKS[f"pg{i:02d}"]["created_at"] = time.time() + i

    def test_default_returns_all(self, client, _many_tasks):
        r = client.get("/tasks")
        data = r.json()
        assert data["total"] == 10
        assert len(data["tasks"]) == 10

    def test_status_filter(self, client, _many_tasks):
        r = client.get("/tasks?status=failed")
        data = r.json()
        assert data["total"] == 3
        for t in data["tasks"]:
            assert t["status"] == "failed"

    def test_search_filter(self, client, _many_tasks):
        r = client.get("/tasks?q=number 5")
        data = r.json()
        assert data["total"] == 1
        assert "5" in data["tasks"][0]["task"]

    def test_pagination(self, client, _many_tasks):
        r = client.get("/tasks?limit=3&offset=0")
        data = r.json()
        assert len(data["tasks"]) == 3
        assert data["total"] == 10
        assert data["limit"] == 3
        assert data["offset"] == 0

    def test_pagination_offset(self, client, _many_tasks):
        r1 = client.get("/tasks?limit=5&offset=0")
        r2 = client.get("/tasks?limit=5&offset=5")
        ids1 = {t["id"] for t in r1.json()["tasks"]}
        ids2 = {t["id"] for t in r2.json()["tasks"]}
        assert ids1.isdisjoint(ids2)


# ── POST /run input validation ───────────────────────────────────────────────


class TestRunValidation:
    def test_queue_full(self, client):
        from app import TASKS, MAX_QUEUE_SIZE
        # Fill up the queue
        for i in range(MAX_QUEUE_SIZE):
            TASKS[f"q{i:04d}"] = _make_task(f"q{i:04d}", status="pending")
        r = client.post("/run", json={"tasks": ["new task"]})
        assert r.status_code == 429
        assert "queue full" in r.json()["detail"]
        # Cleanup
        for i in range(MAX_QUEUE_SIZE):
            TASKS.pop(f"q{i:04d}", None)
