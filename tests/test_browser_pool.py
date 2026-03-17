"""
Tests for agent/browser_pool.py — BrowserPool 浏览器实例池。
使用 mock 替代真实 Playwright，测试池的核心逻辑。
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.browser_pool import BrowserPool, BrowserSlot


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_mock_browser(connected=True):
    """创建一个 mock Browser 对象。"""
    browser = MagicMock()
    browser.is_connected.return_value = connected
    browser.close = AsyncMock()
    context = MagicMock()
    context.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    return browser, context


def _make_pool(max_size=3, headless=True):
    """创建一个未启动的 BrowserPool（用于直接测试异步方法）。"""
    pool = BrowserPool(max_size=max_size, headless=headless, idle_timeout=300)
    return pool


# ── BrowserSlot ──────────────────────────────────────────────────────────────

class TestBrowserSlot:
    def test_defaults(self):
        browser = MagicMock()
        slot = BrowserSlot(browser=browser)
        assert slot.in_use is False
        assert slot.task_id is None
        assert slot.context is None
        assert slot.created_at > 0
        assert slot.last_used_at > 0

    def test_fields(self):
        browser = MagicMock()
        slot = BrowserSlot(browser=browser, in_use=True, task_id="t1")
        assert slot.in_use is True
        assert slot.task_id == "t1"


# ── BrowserPool init ────────────────────────────────────────────────────────

class TestPoolInit:
    def test_default_values(self):
        pool = BrowserPool()
        assert pool.max_size == 3
        assert pool.started is False

    def test_custom_values(self):
        pool = BrowserPool(max_size=5, idle_timeout=600, headless=True, proxy="http://proxy:8080")
        assert pool.max_size == 5
        assert pool._headless is True
        assert pool._proxy == "http://proxy:8080"
        assert pool._idle_timeout == 600


# ── Async core methods (tested directly, bypassing thread) ──────────────────

class TestAcquireRelease:
    @pytest.mark.asyncio
    async def test_acquire_returns_browser_and_context(self):
        pool = _make_pool(max_size=2)
        b1, c1 = _make_mock_browser()
        b2, c2 = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=b1), BrowserSlot(browser=b2)]
        pool._started = True
        pool._lock = asyncio.Lock()

        browser, context = await pool._acquire_async("task-1")
        assert browser is b1
        assert pool._slots[0].in_use is True
        assert pool._slots[0].task_id == "task-1"

    @pytest.mark.asyncio
    async def test_acquire_skips_in_use_slots(self):
        pool = _make_pool(max_size=2)
        b1, c1 = _make_mock_browser()
        b2, c2 = _make_mock_browser()
        pool._slots = [
            BrowserSlot(browser=b1, in_use=True, task_id="task-1"),
            BrowserSlot(browser=b2),
        ]
        pool._started = True
        pool._lock = asyncio.Lock()

        browser, context = await pool._acquire_async("task-2")
        assert browser is b2
        assert pool._slots[1].in_use is True

    @pytest.mark.asyncio
    async def test_acquire_replaces_dead_browser(self):
        pool = _make_pool(max_size=1)
        dead_browser, _ = _make_mock_browser(connected=False)
        new_browser, new_ctx = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=dead_browser)]
        pool._started = True
        pool._lock = asyncio.Lock()
        pool._pw = MagicMock()
        pool._pw.chromium = MagicMock()
        pool._pw.chromium.launch = AsyncMock(return_value=new_browser)

        browser, context = await pool._acquire_async("task-1")
        assert browser is new_browser
        dead_browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_clears_slot(self):
        pool = _make_pool(max_size=1)
        b1, c1 = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=b1, in_use=True, task_id="task-1", context=c1)]
        pool._started = True
        pool._lock = asyncio.Lock()

        await pool._release_async("task-1")
        assert pool._slots[0].in_use is False
        assert pool._slots[0].task_id is None
        assert pool._slots[0].context is None
        c1.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_nonexistent_task(self):
        pool = _make_pool(max_size=1)
        b1, _ = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=b1)]
        pool._started = True
        pool._lock = asyncio.Lock()
        # Should not raise
        await pool._release_async("nonexistent")

    @pytest.mark.asyncio
    async def test_acquire_release_cycle(self):
        pool = _make_pool(max_size=1)
        b1, c1 = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=b1)]
        pool._started = True
        pool._lock = asyncio.Lock()

        # Acquire
        await pool._acquire_async("task-1")
        assert pool._slots[0].in_use is True

        # Release
        await pool._release_async("task-1")
        assert pool._slots[0].in_use is False

        # Acquire again (same slot reused)
        await pool._acquire_async("task-2")
        assert pool._slots[0].in_use is True
        assert pool._slots[0].task_id == "task-2"


# ── Resize ───────────────────────────────────────────────────────────────────

class TestResize:
    @pytest.mark.asyncio
    async def test_resize_up(self):
        pool = _make_pool(max_size=2)
        b1, _ = _make_mock_browser()
        b2, _ = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=b1), BrowserSlot(browser=b2)]
        pool._started = True
        pool._lock = asyncio.Lock()
        pool._pw = MagicMock()
        new_b, _ = _make_mock_browser()
        pool._pw.chromium = MagicMock()
        pool._pw.chromium.launch = AsyncMock(return_value=new_b)

        await pool._resize_async(3)
        assert pool.max_size == 3
        assert len(pool._slots) == 3

    @pytest.mark.asyncio
    async def test_resize_down_closes_idle(self):
        pool = _make_pool(max_size=3)
        b1, _ = _make_mock_browser()
        b2, _ = _make_mock_browser()
        b3, _ = _make_mock_browser()
        pool._slots = [
            BrowserSlot(browser=b1, in_use=True, task_id="t1"),
            BrowserSlot(browser=b2),
            BrowserSlot(browser=b3),
        ]
        pool._started = True
        pool._lock = asyncio.Lock()

        await pool._resize_async(1)
        assert pool.max_size == 1
        # Should have closed idle slots (b2, b3), kept b1 (in use)
        assert len(pool._slots) == 1
        assert pool._slots[0].browser is b1
        b2.close.assert_awaited_once()
        b3.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resize_min_1(self):
        pool = _make_pool(max_size=3)
        pool._slots = []
        pool._started = True
        pool._lock = asyncio.Lock()

        await pool._resize_async(0)
        assert pool.max_size == 1


# ── Shutdown ─────────────────────────────────────────────────────────────────

class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_closes_all(self):
        pool = _make_pool(max_size=2)
        b1, _ = _make_mock_browser()
        b2, _ = _make_mock_browser()
        c1 = MagicMock()
        c1.close = AsyncMock()
        mock_pw = MagicMock()
        mock_pw.stop = AsyncMock()
        pool._slots = [
            BrowserSlot(browser=b1, context=c1),
            BrowserSlot(browser=b2),
        ]
        pool._started = True
        pool._pw = mock_pw
        pool._cleanup_task = None

        await pool._shutdown_async()
        assert pool._started is False
        assert len(pool._slots) == 0
        b1.close.assert_awaited_once()
        b2.close.assert_awaited_once()
        c1.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        pool = _make_pool()
        pool._started = False
        await pool._shutdown_async()  # Should not raise


# ── Stats ───────────────────────────────────────────────────────────────────��

class TestStats:
    def test_stats_empty(self):
        pool = _make_pool(max_size=2)
        pool._slots = []
        stats = pool.stats()
        assert stats["max_size"] == 2
        assert stats["total"] == 0
        assert stats["in_use"] == 0
        assert stats["idle"] == 0

    def test_stats_with_slots(self):
        pool = _make_pool(max_size=3)
        b1, _ = _make_mock_browser()
        b2, _ = _make_mock_browser()
        b3, _ = _make_mock_browser(connected=False)
        pool._slots = [
            BrowserSlot(browser=b1, in_use=True, task_id="t1"),
            BrowserSlot(browser=b2),
            BrowserSlot(browser=b3),
        ]
        stats = pool.stats()
        assert stats["max_size"] == 3
        assert stats["total"] == 3
        assert stats["in_use"] == 1
        assert stats["idle"] == 2
        assert len(stats["slots"]) == 3
        assert stats["slots"][0]["in_use"] is True
        assert stats["slots"][0]["task_id"] == "t1"
        assert stats["slots"][2]["connected"] is False


# ── Warmup ───────────────────────────────────────────────────────────────────

class TestWarmup:
    @pytest.mark.asyncio
    async def test_warmup_replaces_dead_browsers(self):
        pool = _make_pool(max_size=2)
        alive_b, _ = _make_mock_browser(connected=True)
        dead_b, _ = _make_mock_browser(connected=False)
        new_b, _ = _make_mock_browser()
        pool._slots = [BrowserSlot(browser=alive_b), BrowserSlot(browser=dead_b)]
        pool._started = True
        pool._lock = asyncio.Lock()
        pool._pw = MagicMock()
        pool._pw.chromium = MagicMock()
        pool._pw.chromium.launch = AsyncMock(return_value=new_b)

        await pool._warmup_async()
        # alive_b should not be replaced
        assert pool._slots[0].browser is alive_b
        # dead_b should be replaced
        assert pool._slots[1].browser is new_b
        dead_b.close.assert_awaited_once()
