"""
稳定性测试 — 并发任务压力测试。

覆盖：
- TaskPool 高并发提交与执行
- TaskPool 并发 resize
- BrowserPool 并发 acquire/release（信号量模式）
- 跨组件联动：TaskPool + BrowserPool 协同
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.task_pool import TaskPool
from agent.browser_pool import BrowserPool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_pool(max_size=3):
    pool = BrowserPool(max_size=max_size)
    pool.start_sync()
    return pool


# ══════════════════════════════════════════════════════════════════════════════
# TaskPool 并发压力
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskPoolConcurrentStress:
    """TaskPool 在高并发下的正确性和稳定性。"""

    @pytest.mark.asyncio
    async def test_burst_submit_20_tasks(self):
        """瞬间提交 20 个任务，max_workers=3，全部应正确完成。"""
        pool = TaskPool(max_workers=3)
        results = []

        async def work(task_id):
            await asyncio.sleep(0.01)
            results.append(task_id)

        tasks = [pool.submit(f"t-{i}", work) for i in range(20)]
        await asyncio.gather(*tasks)
        for _ in range(200):
            stats = pool.stats()
            if stats.completed + stats.failed >= 20:
                break
            await asyncio.sleep(0.05)

        assert pool.stats().completed == 20
        assert pool.stats().failed == 0
        assert pool.stats().running == 0
        assert pool.stats().queued == 0
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_burst_with_failures(self):
        """20 个任务中一半抛异常，统计应正确。"""
        pool = TaskPool(max_workers=5)

        async def work(task_id):
            await asyncio.sleep(0.01)
            idx = int(task_id.split("-")[1])
            if idx % 2 == 0:
                raise RuntimeError(f"fail-{task_id}")

        for i in range(20):
            await pool.submit(f"t-{i}", work)

        for _ in range(200):
            stats = pool.stats()
            if stats.completed + stats.failed >= 20:
                break
            await asyncio.sleep(0.05)

        stats = pool.stats()
        assert stats.completed == 10
        assert stats.failed == 10
        assert stats.running == 0

    @pytest.mark.asyncio
    async def test_concurrent_resize_during_execution(self):
        """任务执行中动态 resize，不应崩溃或死锁。"""
        pool = TaskPool(max_workers=2)
        completed = []

        async def slow_work(task_id):
            await asyncio.sleep(0.05)
            completed.append(task_id)

        for i in range(10):
            await pool.submit(f"t-{i}", slow_work)

        await asyncio.sleep(0.02)
        pool.resize(5)
        assert pool.max_workers == 5

        await asyncio.sleep(0.02)
        pool.resize(1)
        assert pool.max_workers == 1

        for _ in range(200):
            stats = pool.stats()
            if stats.completed + stats.failed >= 10:
                break
            await asyncio.sleep(0.05)

        assert len(completed) == 10

    @pytest.mark.asyncio
    async def test_stats_consistency_under_load(self):
        """高并发下 stats 的 running + queued + completed + failed 应等于 total_submitted。"""
        pool = TaskPool(max_workers=3)

        async def work(task_id):
            await asyncio.sleep(0.02)

        for i in range(15):
            await pool.submit(f"t-{i}", work)

        for _ in range(50):
            s = pool.stats()
            assert s.running + s.queued + s.completed + s.failed == s.total_submitted
            if s.completed + s.failed >= 15:
                break
            await asyncio.sleep(0.02)

    @pytest.mark.asyncio
    async def test_zero_duration_tasks(self):
        """大量瞬间完成的任务不应导致竞态。"""
        pool = TaskPool(max_workers=10)

        async def instant_work(task_id):
            pass

        for i in range(100):
            await pool.submit(f"t-{i}", instant_work)

        for _ in range(200):
            stats = pool.stats()
            if stats.completed + stats.failed >= 100:
                break
            await asyncio.sleep(0.02)

        stats = pool.stats()
        assert stats.completed == 100
        assert stats.failed == 0

    @pytest.mark.asyncio
    async def test_duplicate_task_ids(self):
        """重复 task_id 提交不应导致崩溃。"""
        pool = TaskPool(max_workers=3)
        results = []

        async def work(task_id):
            await asyncio.sleep(0.01)
            results.append(task_id)

        for _ in range(5):
            await pool.submit("same-id", work)

        for _ in range(100):
            stats = pool.stats()
            if stats.completed + stats.failed >= 5:
                break
            await asyncio.sleep(0.05)

        assert stats.total_submitted == 5


# ══════════════════════════════════════════════════════════════════════════════
# BrowserPool 并发压力（信号量模式）
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserPoolConcurrentStress:
    """BrowserPool 在并发 acquire/release 下的正确性。"""

    def test_concurrent_acquire_release_threads(self):
        """多线程同时 acquire/release，不应死锁或泄漏。"""
        pool = _make_pool(max_size=3)
        errors = []

        def use_slot(task_id):
            try:
                pool.acquire_sync(task_id, timeout=5)
                time.sleep(0.01)
                pool.release_sync(task_id)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=use_slot, args=(f"t-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert pool.stats()["in_use"] == 0
        assert pool.stats()["idle"] == 3

    def test_acquire_blocks_when_full(self):
        """池满时 acquire 应阻塞，释放后应解除阻塞。"""
        pool = _make_pool(max_size=2)
        pool.acquire_sync("t-1")
        pool.acquire_sync("t-2")

        acquired = threading.Event()

        def waiter():
            pool.acquire_sync("t-3", timeout=5)
            acquired.set()
            pool.release_sync("t-3")

        t = threading.Thread(target=waiter)
        t.start()

        time.sleep(0.1)
        assert not acquired.is_set()

        pool.release_sync("t-1")
        t.join(timeout=3)
        assert acquired.is_set()

        pool.release_sync("t-2")

    def test_resize_up_during_contention(self):
        """池满时 resize 增大应立即解除阻塞。"""
        pool = _make_pool(max_size=1)
        pool.acquire_sync("t-1")

        acquired = threading.Event()

        def waiter():
            pool.acquire_sync("t-2", timeout=5)
            acquired.set()
            pool.release_sync("t-2")

        t = threading.Thread(target=waiter)
        t.start()

        time.sleep(0.1)
        assert not acquired.is_set()

        pool.resize_sync(2)
        t.join(timeout=3)
        assert acquired.is_set()

        pool.release_sync("t-1")

    def test_resize_down(self):
        """resize 减小后 max_size 应更新。"""
        pool = _make_pool(max_size=4)
        pool.resize_sync(2)
        assert pool.max_size == 2

    def test_shutdown_with_active_slots(self):
        """有槽位在使用时 shutdown 不应崩溃。"""
        pool = _make_pool(max_size=2)
        pool.acquire_sync("t-1")
        assert pool.stats()["in_use"] == 1
        pool.shutdown_sync()
        assert pool.started is False

    def test_rapid_acquire_release_100_cycles(self):
        """快速 acquire/release 100 次，不应泄漏。"""
        pool = _make_pool(max_size=2)

        for i in range(100):
            pool.acquire_sync(f"t-{i}")
            pool.release_sync(f"t-{i}")

        assert pool.stats()["in_use"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# TaskPool + BrowserPool 联动
# ══════════════════════════════════════════════════════════════════════════════

class TestPoolIntegration:
    """TaskPool 和 BrowserPool 协同工作的稳定性。"""

    @pytest.mark.asyncio
    async def test_task_pool_with_browser_pool_lifecycle(self):
        """模拟真实场景：TaskPool 调度任务，每个任务从 BrowserPool 获取槽位。"""
        task_pool = TaskPool(max_workers=3)
        browser_pool = _make_pool(max_size=3)
        completed_tasks = []

        async def agent_work(task_id):
            # 在 asyncio 线程里用 run_in_executor 调用同步 acquire
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, browser_pool.acquire_sync, task_id)
            try:
                await asyncio.sleep(0.02)
                completed_tasks.append(task_id)
            finally:
                browser_pool.release_sync(task_id)

        for i in range(10):
            await task_pool.submit(f"t-{i}", agent_work)

        for _ in range(200):
            stats = task_pool.stats()
            if stats.completed + stats.failed >= 10:
                break
            await asyncio.sleep(0.05)

        assert task_pool.stats().completed == 10
        assert browser_pool.stats()["in_use"] == 0
        assert len(completed_tasks) == 10

    @pytest.mark.asyncio
    async def test_task_failure_releases_browser_slot(self):
        """任务失败时浏览器槽位应正确归还。"""
        browser_pool = _make_pool(max_size=2)

        async def failing_work(task_id):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, browser_pool.acquire_sync, task_id)
            try:
                raise RuntimeError("task crashed")
            finally:
                browser_pool.release_sync(task_id)

        task_pool = TaskPool(max_workers=2)
        for i in range(5):
            await task_pool.submit(f"t-{i}", failing_work)

        for _ in range(100):
            stats = task_pool.stats()
            if stats.completed + stats.failed >= 5:
                break
            await asyncio.sleep(0.05)

        assert task_pool.stats().failed == 5
        assert browser_pool.stats()["in_use"] == 0
