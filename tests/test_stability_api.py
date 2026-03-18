"""
稳定性测试 — API 端点压力与异常测试。

覆盖：
- 高频并发请求（GET /tasks, GET /health）
- 大量任务提交与队列满拒绝
- 畸形输入 / 边界值 / 注入攻击
- SSE 连接并发
- 跨端点竞态（提交+取消+重试+删除 同时进行）
"""

import asyncio
import json
import threading
import time
import concurrent.futures
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


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


# ══════════════════════════════════════════════════════════════════════════════
# 高频并发请求
# ══════════════════════════════════════════════════════════════════════════════

class TestHighFrequencyRequests:
    """模拟多线程并发请求，验证 API 不崩溃、不死锁。"""

    def test_concurrent_health_checks(self, client):
        """50 个线程同时请求 /health。"""
        errors = []

        def hit_health():
            try:
                r = client.get("/health")
                assert r.status_code == 200
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(hit_health) for _ in range(50)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Errors: {errors[:5]}"

    def test_concurrent_list_tasks(self, client):
        """填充 100 个任务后，20 线程并发 GET /tasks。"""
        from app import TASKS
        for i in range(100):
            TASKS[f"stress-{i:04d}"] = _make_task(f"stress-{i:04d}", task_text=f"stress task {i}")

        errors = []

        def hit_list():
            try:
                r = client.get("/tasks?limit=20&offset=0")
                assert r.status_code == 200
                data = r.json()
                assert data["total"] == 100
                assert len(data["tasks"]) == 20
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(hit_list) for _ in range(20)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0

    def test_concurrent_mixed_reads(self, client):
        """混合 GET /health + GET /tasks + GET /pool 并发。"""
        from app import TASKS
        for i in range(10):
            TASKS[f"mix-{i}"] = _make_task(f"mix-{i}")

        errors = []

        def hit_random(idx):
            try:
                endpoints = ["/health", "/tasks", "/pool"]
                r = client.get(endpoints[idx % 3])
                assert r.status_code == 200
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
            futures = [pool.submit(hit_random, i) for i in range(30)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 畸形输入与边界值
# ══════════════════════════════════════════════════════════════════════════════

class TestMalformedInput:
    """畸形请求不应导致 500 或崩溃。"""

    def test_run_empty_tasks_list(self, client):
        """空任务列表。"""
        r = client.post("/run", json={"tasks": []})
        assert r.status_code == 200
        assert r.json()["task_ids"] == []

    def test_run_whitespace_only_tasks(self, client):
        """纯空白任务应被过滤。"""
        with patch("app._task_pool.submit", new_callable=AsyncMock):
            r = client.post("/run", json={"tasks": ["   ", "\t", "\n"]})
        assert r.status_code == 200
        assert r.json()["task_ids"] == []

    def test_run_invalid_browser_mode(self, client):
        """无效 browser_mode 应被 Pydantic 拒绝。"""
        r = client.post("/run", json={"tasks": ["test"], "browser_mode": "invalid_mode"})
        assert r.status_code == 422

    def test_run_negative_timeout(self, client):
        """负数 timeout 应被拒绝。"""
        r = client.post("/run", json={"tasks": ["test"], "timeout": -1})
        assert r.status_code == 422

    def test_run_huge_timeout(self, client):
        """超大 timeout 应被拒绝（>3600）。"""
        r = client.post("/run", json={"tasks": ["test"], "timeout": 99999})
        assert r.status_code == 422

    def test_run_oversized_task_text(self, client):
        """超长任务文本应被拒绝。"""
        huge_text = "x" * 10001
        r = client.post("/run", json={"tasks": [huge_text]})
        assert r.status_code == 422

    def test_batch_delete_non_json(self, client):
        """非 JSON body 应返回 422。"""
        r = client.post("/tasks/batch-delete", content="not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 422

    def test_pool_resize_non_integer(self, client):
        """非整数 max_workers 应返回 422。"""
        r = client.put("/pool", json={"max_workers": "abc"})
        assert r.status_code == 422

    def test_pool_resize_negative(self, client):
        """负数 max_workers 应返回 400。"""
        r = client.put("/pool", json={"max_workers": -1})
        assert r.status_code == 400

    def test_screenshot_path_traversal_dotdot(self, client):
        """路径遍历攻击 ../ — FastAPI 路由可能拆分路径段，验证不会返回敏感文件。"""
        # FastAPI 会将 ../../../ 作为路径段处理，可能返回 200（index.html）或 404
        # 关键是不应返回系统文件内容
        r = client.get("/screenshots/../../../etc/passwd/file.png")
        # 只要不返回真实系统文件即可
        if r.status_code == 200:
            assert "root:" not in r.text  # 不应泄露 /etc/passwd

    def test_screenshot_null_bytes(self, client):
        """null 字节注入。"""
        r = client.get("/screenshots/task%00id/file.png")
        assert r.status_code in (400, 404, 422)

    def test_tasks_pagination_extreme_values(self, client):
        """极端分页参数。"""
        r = client.get("/tasks?limit=999999&offset=0")
        assert r.status_code == 200
        data = r.json()
        assert data["limit"] <= 200  # 应被 clamp

        r = client.get("/tasks?limit=1&offset=999999")
        assert r.status_code == 200
        assert len(r.json()["tasks"]) == 0

    def test_tasks_negative_pagination(self, client):
        """负数分页参数应被 clamp 到 0。"""
        r = client.get("/tasks?limit=-5&offset=-10")
        assert r.status_code == 200
        data = r.json()
        assert data["limit"] >= 1
        assert data["offset"] >= 0


# ══════════════════════════════════════════════════════════════════════════════
# 队列满与限流
# ══════════════════════════════════════════════════════════════════════════════

class TestQueueLimits:
    """队列满时的行为。"""

    def test_queue_full_returns_429(self, client):
        from app import TASKS, MAX_QUEUE_SIZE
        for i in range(MAX_QUEUE_SIZE):
            TASKS[f"q{i:04d}"] = _make_task(f"q{i:04d}", status="pending")
        r = client.post("/run", json={"tasks": ["new task"]})
        assert r.status_code == 429
        assert "queue full" in r.json()["detail"]
        # cleanup
        for i in range(MAX_QUEUE_SIZE):
            TASKS.pop(f"q{i:04d}", None)

    def test_queue_counts_running_tasks(self, client):
        """running 状态的任务也计入队列。"""
        from app import TASKS, MAX_QUEUE_SIZE
        for i in range(MAX_QUEUE_SIZE):
            status = "pending" if i % 2 == 0 else "running"
            TASKS[f"qr{i:04d}"] = _make_task(f"qr{i:04d}", status=status)
        r = client.post("/run", json={"tasks": ["new task"]})
        assert r.status_code == 429
        for i in range(MAX_QUEUE_SIZE):
            TASKS.pop(f"qr{i:04d}", None)

    def test_queue_ignores_done_tasks(self, client):
        """done/failed 任务不计入队列。"""
        from app import TASKS
        for i in range(50):
            TASKS[f"done{i}"] = _make_task(f"done{i}", status="done")
        with patch("app._task_pool.submit", new_callable=AsyncMock):
            r = client.post("/run", json={"tasks": ["new task"]})
        assert r.status_code == 200
        for i in range(50):
            TASKS.pop(f"done{i}", None)


# ══════════════════════════════════════════════════════════════════════════════
# 跨端点竞态
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossEndpointRace:
    """多个端点同时操作同一任务。"""

    def test_batch_delete_during_list(self, client):
        """列表查询和批量删除同时进行。"""
        from app import TASKS
        for i in range(20):
            TASKS[f"bd-{i}"] = _make_task(f"bd-{i}")

        errors = []

        def do_list():
            try:
                r = client.get("/tasks")
                assert r.status_code == 200
            except Exception as e:
                errors.append(str(e))

        def do_delete():
            try:
                ids = [f"bd-{i}" for i in range(0, 20, 2)]
                r = client.post("/tasks/batch-delete", json={"task_ids": ids})
                assert r.status_code == 200
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_list) for _ in range(5)]
        threads.append(threading.Thread(target=do_delete))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_concurrent_cancel_same_task(self, client):
        """多个线程同时取消同一个任务。"""
        from app import TASKS
        TASKS["cc1"] = _make_task("cc1", status="pending")

        results = []

        def do_cancel():
            with patch("app.save_task"):
                r = client.post("/tasks/cc1/cancel")
                results.append(r.status_code)

        threads = [threading.Thread(target=do_cancel) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # 第一个应成功(200)，后续应返回 400（already cancelled）
        assert 200 in results
        assert all(code in (200, 400) for code in results)


# ══════════════════════════════════════════════════════════════════════════════
# SSE 连接稳定性
# NOTE: SSE 是无限流，TestClient.stream() 会阻塞，无法在单元测试中可靠测试。
# SSE 端点的正确性已在 test_app_endpoints.py 中通过 snapshot 事件验证。
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# 数据库并发写入
# ══════════════════════════════════════════════════════════════════════════════

class TestDBConcurrency:
    """SQLite 并发写入稳定性。"""

    def test_concurrent_save_tasks(self, client):
        """多线程同时写入不同任务到 DB。"""
        from db import save_task, load_all_tasks

        errors = []

        def save_one(idx):
            try:
                t = {
                    "id": f"dbstress-{idx}", "task": f"task {idx}",
                    "status": "done", "logs": [f"log {idx}"],
                    "screenshots": [], "curation": None, "generated": None,
                    "started_at": None, "finished_at": None,
                }
                save_task(t)
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(save_one, i) for i in range(30)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0
        # 验证数据完整性
        all_tasks = load_all_tasks()
        saved_ids = {tid for tid in all_tasks if tid.startswith("dbstress-")}
        assert len(saved_ids) == 30

    def test_concurrent_read_write(self, client):
        """读写同时进行不应死锁。"""
        from db import save_task, load_all_tasks

        errors = []

        def writer(idx):
            try:
                save_task({
                    "id": f"rw-{idx}", "task": f"task {idx}",
                    "status": "done", "logs": [], "screenshots": [],
                    "curation": None, "generated": None,
                    "started_at": None, "finished_at": None,
                })
            except Exception as e:
                errors.append(f"write: {e}")

        def reader():
            try:
                load_all_tasks()
            except Exception as e:
                errors.append(f"read: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(20):
                futures.append(pool.submit(writer, i))
                futures.append(pool.submit(reader))
            concurrent.futures.wait(futures)

        assert len(errors) == 0
