"""
Tests for agent/browser_pool.py — BrowserPool 预热对象池。

新架构：预先 launch N 个 Browser，任务来了直接 new_context()，跳过冷启动。
测试策略：mock Playwright 对象，不启动真实浏览器，保持测试快速。
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from agent.browser_pool import BrowserPool, _Slot


# ── 辅助：创建 mock browser ──────────────────────────────────────────

def _make_mock_browser():
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=MagicMock())
    browser.close = AsyncMock()
    return browser


def _make_pool_with_mock_browsers(max_size: int = 2, headless: bool = True) -> BrowserPool:
    """
    创建一个已启动的 BrowserPool，内部 browser 全部是 mock。
    绕过真实 Playwright 启动，测试纯逻辑。
    """
    pool = BrowserPool(max_size=max_size, headless=headless)

    # 手动初始化：创建专用事件循环线程
    pool._loop = asyncio.new_event_loop()
    pool._loop_thread = threading.Thread(
        target=pool._run_loop, args=(pool._loop,), daemon=True
    )
    pool._loop_thread.start()

    # 注入 mock 槽位
    for i in range(max_size):
        slot = _Slot(index=i, browser=_make_mock_browser())
        pool._slots.append(slot)
        pool._semaphore.release()

    pool._started = True
    return pool


# ── 初始化测试 ──────────────────────────────────────────

class TestPoolInit:
    def test_default_values(self):
        pool = BrowserPool()
        assert pool.max_size == 3
        assert pool.started is False

    def test_custom_values(self):
        pool = BrowserPool(max_size=5, headless=True, proxy="http://proxy:8080")
        assert pool.max_size == 5
        assert pool._headless is True
        assert pool._proxy == "http://proxy:8080"

    def test_not_started_initially(self):
        pool = BrowserPool(max_size=2)
        assert pool.started is False

    def test_start_idempotent(self):
        """start_sync 重复调用不应抛异常。"""
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.start_sync()  # 已 started，应直接返回
        assert pool.started is True
        pool.shutdown_sync()


# ── acquire / release 测试 ──────────────────────────────────────────

class TestAcquireRelease:
    def test_acquire_returns_browser(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        browser = pool.acquire_sync("task-1")
        assert browser is not None  # 返回 mock browser
        pool.release_sync("task-1")
        pool.shutdown_sync()

    def test_acquire_tracks_slot(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.acquire_sync("task-1")
        # 有一个槽位被占用
        occupied = [s for s in pool._slots if s.task_id == "task-1"]
        assert len(occupied) == 1
        pool.release_sync("task-1")
        pool.shutdown_sync()

    def test_release_frees_slot(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.acquire_sync("task-1")
        pool.release_sync("task-1")
        occupied = [s for s in pool._slots if s.task_id == "task-1"]
        assert len(occupied) == 0
        pool.shutdown_sync()

    def test_acquire_release_cycle(self):
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.acquire_sync("task-1")
        pool.release_sync("task-1")
        # 释放后可以再次获取
        browser = pool.acquire_sync("task-2")
        assert browser is not None
        pool.release_sync("task-2")
        pool.shutdown_sync()

    def test_max_concurrent_respected(self):
        """超过 max_size 的 acquire 应该阻塞。"""
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.acquire_sync("task-1")
        pool.acquire_sync("task-2")

        timed_out = threading.Event()

        def try_acquire():
            result = pool._semaphore.acquire(timeout=0.1)
            if result:
                pool._semaphore.release()
            else:
                timed_out.set()

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=1)

        assert timed_out.is_set()
        pool.release_sync("task-1")
        pool.release_sync("task-2")
        pool.shutdown_sync()

    def test_acquire_timeout_raises(self):
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.acquire_sync("task-1")

        with pytest.raises(TimeoutError):
            pool.acquire_sync("task-2", timeout=0.1)

        pool.release_sync("task-1")
        pool.shutdown_sync()

    def test_release_unblocks_waiter(self):
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.acquire_sync("task-1")

        results = []

        def waiter():
            pool.acquire_sync("task-2", timeout=3)
            results.append("acquired")
            pool.release_sync("task-2")

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        pool.release_sync("task-1")
        t.join(timeout=4)
        assert results == ["acquired"]
        pool.shutdown_sync()

    def test_release_with_unhealthy_browser_triggers_replace(self):
        """release_sync(browser_healthy=False) 应触发异步替换。"""
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.acquire_sync("task-1")

        # mock _replace_browser
        replaced = threading.Event()
        original_replace = pool._replace_browser

        async def mock_replace(slot, old_browser):
            replaced.set()

        pool._replace_browser = mock_replace
        pool.release_sync("task-1", browser_healthy=False)

        # 等待异步替换触发
        replaced.wait(timeout=2)
        assert replaced.is_set()
        pool.shutdown_sync()


# ── resize 测试 ──────────────────────────────────────────

class TestResize:
    def test_resize_up(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.resize_sync(4)
        assert pool.max_size == 4
        pool.shutdown_sync()

    def test_resize_down(self):
        pool = _make_pool_with_mock_browsers(max_size=4)
        pool.resize_sync(2)
        assert pool.max_size == 2
        pool.shutdown_sync()

    def test_resize_min_1(self):
        pool = _make_pool_with_mock_browsers(max_size=3)
        pool.resize_sync(0)
        assert pool.max_size == 1
        pool.shutdown_sync()

    def test_resize_up_allows_more_concurrent(self):
        pool = _make_pool_with_mock_browsers(max_size=1)
        pool.acquire_sync("task-1")
        pool.resize_sync(2)
        # 增大后应能再获取一个（新槽位由 _add_slots 异步添加，这里只验证信号量）
        acquired = pool._semaphore.acquire(timeout=2)
        assert acquired
        pool._semaphore.release()
        pool.release_sync("task-1")
        pool.shutdown_sync()


# ── shutdown 测试 ──────────────────────────────────────────

class TestShutdown:
    def test_shutdown_marks_not_started(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.shutdown_sync()
        assert pool.started is False

    def test_shutdown_idempotent(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.shutdown_sync()
        pool.shutdown_sync()  # 不应抛异常


# ── stats 测试 ──────────────────────────────────────────

class TestStats:
    def test_stats_empty(self):
        pool = _make_pool_with_mock_browsers(max_size=3)
        stats = pool.stats()
        assert stats["max_size"] == 3
        assert stats["in_use"] == 0
        assert stats["idle"] == 3
        assert stats["total"] == 3
        pool.shutdown_sync()

    def test_stats_with_active(self):
        pool = _make_pool_with_mock_browsers(max_size=3)
        pool.acquire_sync("task-1")
        pool.acquire_sync("task-2")
        stats = pool.stats()
        assert stats["in_use"] == 2
        assert stats["idle"] == 1
        pool.release_sync("task-1")
        pool.release_sync("task-2")
        pool.shutdown_sync()

    def test_stats_slots_length(self):
        pool = _make_pool_with_mock_browsers(max_size=3)
        stats = pool.stats()
        assert len(stats["slots"]) == 3
        pool.shutdown_sync()

    def test_stats_slot_connected(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        stats = pool.stats()
        for slot in stats["slots"]:
            assert slot["connected"] is True  # mock browser 存在
        pool.shutdown_sync()

    def test_stats_before_start(self):
        """start 前调用 stats 不应崩溃。"""
        pool = BrowserPool(max_size=3)
        stats = pool.stats()
        assert stats["max_size"] == 3
        assert stats["in_use"] == 0
        assert len(stats["slots"]) == 3

    def test_warmup_noop(self):
        pool = _make_pool_with_mock_browsers(max_size=2)
        pool.warmup_sync()  # 兼容接口，不应抛异常
        pool.shutdown_sync()


# ── _Slot 数据类测试 ──────────────────────────────────────────

class TestSlot:
    def test_slot_default(self):
        slot = _Slot(index=0)
        assert slot.in_use is False
        assert slot.connected is False

    def test_slot_with_browser(self):
        slot = _Slot(index=0, browser=MagicMock())
        assert slot.connected is True

    def test_slot_in_use(self):
        slot = _Slot(index=0, browser=MagicMock(), task_id="task-1")
        assert slot.in_use is True
