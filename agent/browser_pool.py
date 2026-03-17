"""
浏览器实例池：池化复用 Playwright 浏览器实例，避免每个任务冷启动。

设计：
- 池运行在自己的专用线程中（ProactorEventLoop），解决 Playwright 跨线程问题
- 预创建 N 个浏览器实例，任务从池中借用，用完归还
- 每个任务用独立 BrowserContext 隔离（cookies/storage 互不干扰）
- 归还时关闭 context，浏览器进程保持运行
- 空闲超时自动回收（默认 5 分钟）
- 健康检查：借出前检测浏览器是否存活，崩溃的自动替换
- 只有 builtin 模式走池，cdp/user_chrome 不走池
"""

import asyncio
import sys
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)


@dataclass
class BrowserSlot:
    """浏览器池中的一个槽位。"""
    browser: Browser
    in_use: bool = False
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    task_id: Optional[str] = None
    context: Optional[BrowserContext] = None


class BrowserPool:
    """
    浏览器实例池，运行在专用线程中。

    池内部有自己的事件循环和 Playwright 实例，
    通过 acquire_sync/release_sync 提供线程安全的同步接口。
    """

    def __init__(
        self,
        max_size: int = 3,
        idle_timeout: float = 300.0,
        headless: bool = False,
        proxy: Optional[str] = None,
    ):
        self._max_size = max_size
        self._idle_timeout = idle_timeout
        self._headless = headless
        self._proxy = proxy
        self._pw: Optional[Playwright] = None
        self._slots: list[BrowserSlot] = []
        self._lock = asyncio.Lock()
        self._started = False
        self._cleanup_task: Optional[asyncio.Task] = None
        # 专用线程和事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def started(self) -> bool:
        return self._started

    def start_sync(self):
        """同步启动：创建专用线程，启动 Playwright 和浏览器池。"""
        if self._started:
            return
        ready = threading.Event()
        error_holder = [None]

        def _run():
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._start_async())
                ready.set()
                self._loop.run_forever()
            except Exception as e:
                error_holder[0] = e
                ready.set()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=_run, name="browser-pool", daemon=True)
        self._thread.start()
        ready.wait(timeout=60)
        if error_holder[0]:
            raise error_holder[0]

    async def _start_async(self):
        """在池线程的事件循环中启动 Playwright 和浏览器。"""
        self._pw = await async_playwright().start()
        for _ in range(self._max_size):
            browser = await self._launch_browser()
            self._slots.append(BrowserSlot(browser=browser))
        self._started = True
        self._cleanup_task = asyncio.ensure_future(self._idle_cleanup_loop())
        logger.info(f"BrowserPool started: {self._max_size} browsers, headless={self._headless}")

    async def _launch_browser(self) -> Browser:
        """启动一个新的浏览器实例。"""
        launch_args = {"headless": self._headless}
        if self._proxy:
            launch_args["proxy"] = {"server": self._proxy}
        return await self._pw.chromium.launch(**launch_args)

    # ── 异步核心方法（在池线程的事件循环中执行） ──────────────────────────

    async def _acquire_async(self, task_id: str) -> tuple[Browser, BrowserContext]:
        """从池中借用一个浏览器实例，创建独立的 BrowserContext。"""
        while True:
            async with self._lock:
                for slot in self._slots:
                    if not slot.in_use:
                        # 健康检查
                        if not slot.browser.is_connected():
                            logger.warning("Browser in slot is dead, replacing...")
                            try:
                                await slot.browser.close()
                            except Exception:
                                pass
                            slot.browser = await self._launch_browser()
                            slot.created_at = time.time()

                        slot.in_use = True
                        slot.task_id = task_id
                        slot.last_used_at = time.time()
                        context = await slot.browser.new_context(
                            viewport={"width": 1920, "height": 1080},
                            locale="zh-CN",
                        )
                        slot.context = context
                        logger.info(f"Acquired browser for task {task_id}")
                        return slot.browser, context
            await asyncio.sleep(0.5)

    async def _release_async(self, task_id: str):
        """归还浏览器实例：关闭 context，保留浏览器进程。"""
        async with self._lock:
            for slot in self._slots:
                if slot.task_id == task_id and slot.in_use:
                    if slot.context:
                        try:
                            await slot.context.close()
                        except Exception as e:
                            logger.warning(f"Failed to close context for task {task_id}: {e}")
                        slot.context = None
                    slot.in_use = False
                    slot.task_id = None
                    slot.last_used_at = time.time()
                    logger.info(f"Released browser for task {task_id}")
                    return
        logger.warning(f"No slot found for task {task_id} during release")

    # ── 同步接口（从任意线程调用） ──────────────────────────────────────

    def acquire_sync(self, task_id: str, timeout: float = 60) -> tuple[Browser, BrowserContext]:
        """线程安全的同步 acquire。从任意线程调用，阻塞直到获取到浏览器。"""
        fut = asyncio.run_coroutine_threadsafe(self._acquire_async(task_id), self._loop)
        return fut.result(timeout=timeout)

    def release_sync(self, task_id: str, timeout: float = 30):
        """线程安全的同步 release。从任意线程调用。"""
        fut = asyncio.run_coroutine_threadsafe(self._release_async(task_id), self._loop)
        fut.result(timeout=timeout)

    # ── 管理方法 ──────────────────────────────────────────────────────────

    def shutdown_sync(self, timeout: float = 30):
        """同步关闭：停止所有浏览器和 Playwright，终止专用线程。"""
        if not self._started or not self._loop:
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
        try:
            fut.result(timeout=timeout)
        except Exception as e:
            logger.warning(f"Shutdown error: {e}")
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)

    async def _shutdown_async(self):
        """在池线程中执行关闭。"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        for slot in self._slots:
            try:
                if slot.context:
                    await slot.context.close()
                await slot.browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
        self._slots.clear()
        if self._pw:
            await self._pw.stop()
            self._pw = None
        self._started = False
        logger.info("BrowserPool shutdown complete")

    def resize_sync(self, new_size: int, timeout: float = 30):
        """同步调整池大小。"""
        fut = asyncio.run_coroutine_threadsafe(self._resize_async(new_size), self._loop)
        fut.result(timeout=timeout)

    async def _resize_async(self, new_size: int):
        """在池线程中执行 resize。"""
        if new_size < 1:
            new_size = 1
        async with self._lock:
            old_size = self._max_size
            self._max_size = new_size
            if new_size > old_size:
                for _ in range(new_size - old_size):
                    browser = await self._launch_browser()
                    self._slots.append(BrowserSlot(browser=browser))
            elif new_size < old_size:
                to_remove = old_size - new_size
                removed = 0
                for i in range(len(self._slots) - 1, -1, -1):
                    if removed >= to_remove:
                        break
                    if not self._slots[i].in_use:
                        slot = self._slots.pop(i)
                        try:
                            await slot.browser.close()
                        except Exception:
                            pass
                        removed += 1
        logger.info(f"BrowserPool resized: {old_size} → {new_size}")

    def warmup_sync(self, timeout: float = 60):
        """同步预热。"""
        fut = asyncio.run_coroutine_threadsafe(self._warmup_async(), self._loop)
        fut.result(timeout=timeout)

    async def _warmup_async(self):
        """在池线程中执行预热。"""
        async with self._lock:
            for slot in self._slots:
                if not slot.browser.is_connected():
                    try:
                        await slot.browser.close()
                    except Exception:
                        pass
                    slot.browser = await self._launch_browser()
                    slot.created_at = time.time()

    async def _idle_cleanup_loop(self):
        """定期检查空闲超时的浏览器，关闭并替换。"""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            async with self._lock:
                for slot in self._slots:
                    if (
                        not slot.in_use
                        and (now - slot.last_used_at) > self._idle_timeout
                        and slot.browser.is_connected()
                    ):
                        logger.info(f"Closing idle browser (idle {now - slot.last_used_at:.0f}s)")
                        try:
                            await slot.browser.close()
                        except Exception:
                            pass
                        slot.browser = await self._launch_browser()
                        slot.created_at = time.time()
                        slot.last_used_at = time.time()

    def stats(self) -> dict:
        """返回池状态（线程安全，只读快照）。"""
        now = time.time()
        slots_info = []
        for i, slot in enumerate(self._slots):
            slots_info.append({
                "index": i,
                "in_use": slot.in_use,
                "task_id": slot.task_id,
                "connected": slot.browser.is_connected() if slot.browser else False,
                "created_at": slot.created_at,
                "last_used_at": slot.last_used_at,
                "idle_seconds": round(now - slot.last_used_at, 1) if not slot.in_use else 0,
            })
        return {
            "max_size": self._max_size,
            "total": len(self._slots),
            "in_use": sum(1 for s in self._slots if s.in_use),
            "idle": sum(1 for s in self._slots if not s.in_use),
            "headless": self._headless,
            "idle_timeout": self._idle_timeout,
            "slots": slots_info,
        }
