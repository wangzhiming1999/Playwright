"""
稳定性测试 — 并发任务压力测试。

覆盖：
- TaskPool 高并发提交与执行
- TaskPool 并发 resize
- BrowserPool 并发 acquire/release
- BrowserPool 死浏览器替换竞争
- 跨组件联动：TaskPool + BrowserPool 协同
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.task_pool import TaskPool
from agent.browser_pool import BrowserPool, BrowserSlot


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_mock_browser(connected=True):
    browser = MagicMock()
    browser.is_connected.return_value = connected
    browser.close = AsyncMock()
    context = MagicMock()
    context.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    return browser, context


def _make_pool_with_slots(max_size=3):
    pool = BrowserPool(max_size=max_size, headless=True, idle_timeout=300)
    pool._lock = asyncio.Lock()
    pool._started = True
    pool._pw = MagicMock()
    new_b, _ = _make_mock_browser()
    pool._pw.chromium = MagicMock()
    pool._pw.chromium.launch = AsyncMock(return_value=new_b)
    for _ in range(max_size):
        b, _ = _make_mock_browser()
        pool._slots.append(BrowserSlot(browser=b))
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
        # 等待所有任务完成
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

        # 提交 10 个任务
        for i in range(10):
            await pool.submit(f"t-{i}", slow_work)

        # 执行中 resize
        await asyncio.sleep(0.02)
        pool.resize(5)
        assert pool.max_workers == 5

        await asyncio.sleep(0.02)
        pool.resize(1)
        assert pool.max_workers == 1

        # 等待全部完成
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

        # 多次采样检查一致性
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
            pass  # 瞬间完成

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

        # 提交相同 ID 的任务
        for _ in range(5):
            await pool.submit("same-id", work)

        for _ in range(100):
            stats = pool.stats()
            if stats.completed + stats.failed >= 5:
                break
            await asyncio.sleep(0.05)

        assert stats.total_submitted == 5


# ══════════════════════════════════════════════════════════════════════════════
# BrowserPool 并发压力
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserPoolConcurrentStress:
    """BrowserPool 在并发 acquire/release 下的正确性。"""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release_cycles(self):
        """多个协程同时 acquire/release，不应死锁或泄漏。"""
        pool = _make_pool_with_slots(max_size=3)

        async def use_browser(task_id):
            browser, ctx = await pool._acquire_async(task_id)
            await asyncio.sleep(0.01)
            await pool._release_async(task_id)

        # 10 个协程竞争 3 个浏览器槽位
        await asyncio.gather(*[use_browser(f"t-{i}") for i in range(10)])

        # 所有槽位应归还
        stats = pool.stats()
        assert stats["in_use"] == 0
        assert stats["idle"] == 3

    @pytest.mark.asyncio
    async def test_acquire_all_slots_then_release(self):
        """占满所有槽位后释放，后续 acquire 应成功。"""
        pool = _make_pool_with_slots(max_size=2)

        # 占满
        b1, c1 = await pool._acquire_async("t-1")
        b2, c2 = await pool._acquire_async("t-2")
        assert pool.stats()["in_use"] == 2

        # 后台 acquire 应阻塞
        acquired = asyncio.Event()

        async def delayed_acquire():
            b, c = await pool._acquire_async("t-3")
            acquired.set()
            await pool._release_async("t-3")

        task = asyncio.create_task(delayed_acquire())

        # 短暂等待确认阻塞
        await asyncio.sleep(0.1)
        assert not acquired.is_set()

        # 释放一个槽位
        await pool._release_async("t-1")
        # 等待 acquire 轮询拿到空闲 slot（轮询间隔 0.5s）
        for _ in range(20):
            if acquired.is_set():
                break
            await asyncio.sleep(0.1)
        assert acquired.is_set()

        await pool._release_async("t-2")
        await task

    @pytest.mark.asyncio
    async def test_dead_browser_replacement_under_contention(self):
        """多个协程同时遇到死浏览器，替换逻辑不应竞态。"""
        pool = BrowserPool(max_size=2, headless=True, idle_timeout=300)
        pool._lock = asyncio.Lock()
        pool._started = True

        # 两个都是死浏览器
        dead1, _ = _make_mock_browser(connected=False)
        dead2, _ = _make_mock_browser(connected=False)
        pool._slots = [BrowserSlot(browser=dead1), BrowserSlot(browser=dead2)]

        new_browsers = [_make_mock_browser()[0] for _ in range(4)]
        call_count = 0

        async def mock_launch(**kwargs):
            nonlocal call_count
            b = new_browsers[min(call_count, len(new_browsers) - 1)]
            call_count += 1
            return b

        pool._pw = MagicMock()
        pool._pw.chromium = MagicMock()
        pool._pw.chromium.launch = AsyncMock(side_effect=mock_launch)

        # 两个协程同时 acquire
        results = await asyncio.gather(
            pool._acquire_async("t-1"),
            pool._acquire_async("t-2"),
        )
        assert len(results) == 2
        assert pool.stats()["in_use"] == 2

        await pool._release_async("t-1")
        await pool._release_async("t-2")

    @pytest.mark.asyncio
    async def test_resize_during_acquire(self):
        """acquire 进行中 resize 不应崩溃。"""
        pool = _make_pool_with_slots(max_size=3)

        b, c = await pool._acquire_async("t-1")
        assert pool.stats()["in_use"] == 1

        # resize down
        await pool._resize_async(2)
        assert pool.max_size == 2

        # 已借出的不受影响
        assert pool.stats()["in_use"] == 1

        await pool._release_async("t-1")

    @pytest.mark.asyncio
    async def test_release_with_context_close_failure(self):
        """context.close() 失败时 release 不应崩溃。"""
        pool = _make_pool_with_slots(max_size=1)
        b, c = await pool._acquire_async("t-1")

        # 让 context.close 抛异常
        pool._slots[0].context = MagicMock()
        pool._slots[0].context.close = AsyncMock(side_effect=RuntimeError("close failed"))

        # 不应抛异常
        await pool._release_async("t-1")
        assert pool._slots[0].in_use is False

    @pytest.mark.asyncio
    async def test_shutdown_with_active_tasks(self):
        """有任务在使用时 shutdown 不应挂起。"""
        pool = _make_pool_with_slots(max_size=2)
        pool._cleanup_task = None
        pool._pw = MagicMock()
        pool._pw.stop = AsyncMock()

        await pool._acquire_async("t-1")
        assert pool.stats()["in_use"] == 1

        # shutdown 应清理所有
        await pool._shutdown_async()
        assert pool._started is False
        assert len(pool._slots) == 0


# ══════════════════════════════════════════════════════════════════════════════
# TaskPool + BrowserPool 联动
# ══════════════════════════════════════════════════════════════════════════════

class TestPoolIntegration:
    """TaskPool 和 BrowserPool 协同工作的稳定性。"""

    @pytest.mark.asyncio
    async def test_task_pool_with_browser_pool_lifecycle(self):
        """模拟真实场景：TaskPool 调度任务，每个任务从 BrowserPool 借用浏览器。"""
        task_pool = TaskPool(max_workers=3)
        browser_pool = _make_pool_with_slots(max_size=3)
        completed_tasks = []

        async def agent_work(task_id):
            browser, ctx = await browser_pool._acquire_async(task_id)
            try:
                await asyncio.sleep(0.02)  # 模拟工作
                completed_tasks.append(task_id)
            finally:
                await browser_pool._release_async(task_id)

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
    async def test_task_failure_releases_browser(self):
        """任务失败时浏览器应正确归还。"""
        browser_pool = _make_pool_with_slots(max_size=2)

        async def failing_work(task_id):
            browser, ctx = await browser_pool._acquire_async(task_id)
            try:
                raise RuntimeError("task crashed")
            finally:
                await browser_pool._release_async(task_id)

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
