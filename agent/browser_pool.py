"""
浏览器对象池（预热模式）

设计原则：
- 预先启动 N 个 Playwright Browser 实例，任务来了直接 new_context() 跳过 launch()
- 节省 ~400ms/任务的冷启动时间（launch 125ms + pw.start 266ms）
- Browser 对象绑定到专用事件循环线程，通过 run_coroutine_threadsafe 跨线程调用
- acquire_sync / release_sync 是线程安全的同步接口，供 TaskPool 工作线程调用
- 降级兜底：池未启动或获取失败时，调用方自行 launch（与旧行为一致）
"""

import asyncio
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class _Slot:
    """一个浏览器槽位。"""
    index: int
    browser: object = None          # playwright Browser
    task_id: Optional[str] = None   # 当前占用的任务 ID
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)

    @property
    def in_use(self) -> bool:
        return self.task_id is not None

    @property
    def connected(self) -> bool:
        return self.browser is not None


class BrowserPool:
    """
    浏览器对象池（预热模式）。

    启动时预先 launch N 个 Browser，任务来了直接 new_context()，
    跳过 launch() 冷启动，节省 ~400ms/任务。

    线程安全：内部维护一个专用 asyncio 事件循环线程，
    所有 Playwright 操作都在该线程执行。
    acquire_sync / release_sync 是同步接口，可从任意线程调用。
    """

    def __init__(
        self,
        max_size: int = 3,
        idle_timeout: float = 300.0,  # 保留参数，兼容旧调用
        headless: bool = False,
        proxy: Optional[str] = None,
    ):
        self._max_size = max_size
        self._headless = headless
        self._proxy = proxy
        self._idle_timeout = idle_timeout

        # 专用事件循环线程
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # 槽位管理
        self._slots: list[_Slot] = []
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(0)  # 初始为 0，start 后释放
        self._started = False

        # Playwright 实例（在专用线程中创建）
        self._pw = None

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def started(self) -> bool:
        return self._started

    # ── 专用事件循环线程 ──────────────────────────────────────────

    def _run_loop(self, loop: asyncio.AbstractEventLoop):
        """在专用线程中运行事件循环。"""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _call_async(self, coro, timeout: float = 60):
        """在专用事件循环中执行协程，阻塞等待结果。"""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("BrowserPool event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── 生命周期 ──────────────────────────────────────────

    def start_sync(self, timeout: float = 60):
        """
        启动池：创建专用事件循环线程，预热所有浏览器槽位。
        """
        if self._started:
            return

        # 创建专用事件循环
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            args=(self._loop,),
            daemon=True,
            name="BrowserPool-Loop",
        )
        self._loop_thread.start()

        # 预热浏览器
        try:
            self._call_async(self._warmup_all(), timeout=timeout)
        except Exception as e:
            logger.error(f"BrowserPool warmup failed: {e}")
            # 降级：即使预热失败也标记为 started，acquire 时会返回 None
            self._started = True
            # 释放信号量（允许任务继续，但会走冷启动路径）
            for _ in range(self._max_size):
                self._semaphore.release()
            return

        self._started = True
        logger.info(f"BrowserPool started: {len(self._slots)} browsers ready (headless={self._headless})")

    async def _warmup_all(self):
        """预热所有浏览器槽位（在专用事件循环中执行）。"""
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()

        proxy_config = None
        if self._proxy:
            proxy_config = {"server": self._proxy}

        for i in range(self._max_size):
            try:
                browser = await self._pw.chromium.launch(
                    headless=self._headless,
                    proxy=proxy_config,
                )
                slot = _Slot(index=i, browser=browser)
                with self._lock:
                    self._slots.append(slot)
                self._semaphore.release()  # 每个槽位就绪后释放一个信号量
                logger.info(f"BrowserPool: slot {i} ready")
            except Exception as e:
                logger.error(f"BrowserPool: failed to launch browser for slot {i}: {e}")
                # 创建空槽位，acquire 时会走冷启动路径
                slot = _Slot(index=i, browser=None)
                with self._lock:
                    self._slots.append(slot)
                self._semaphore.release()

    def shutdown_sync(self, timeout: float = 30):
        """关闭池，释放所有浏览器资源。"""
        if not self._started:
            return
        self._started = False
        try:
            self._call_async(self._shutdown_all(), timeout=timeout)
        except Exception as e:
            logger.error(f"BrowserPool shutdown error: {e}")
        finally:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=5)
        logger.info("BrowserPool shutdown complete")

    async def _shutdown_all(self):
        """关闭所有浏览器（在专用事件循环中执行）。"""
        with self._lock:
            slots = list(self._slots)
        for slot in slots:
            if slot.browser:
                try:
                    await slot.browser.close()
                except Exception:
                    pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

    # ── 槽位获取/释放 ──────────────────────────────────────────

    def acquire_sync(self, task_id: str, timeout: float = 60) -> Optional[object]:
        """
        阻塞直到获取到一个空闲浏览器槽位。

        返回 Browser 对象（可直接 new_context()），或 None（需调用方自行 launch）。
        超时抛出 TimeoutError。
        """
        acquired = self._semaphore.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(f"BrowserPool: timeout waiting for slot (task={task_id})")

        with self._lock:
            # 找一个空闲槽位
            for slot in self._slots:
                if not slot.in_use:
                    slot.task_id = task_id
                    slot.last_used_at = time.time()
                    browser = slot.browser
                    logger.info(f"BrowserPool: slot {slot.index} acquired for task {task_id}")
                    return browser  # 可能是 None（冷启动路径）

        # 不应该到这里（信号量保证有空闲槽位），但作为兜底
        logger.warning(f"BrowserPool: no free slot found for task {task_id}, falling back to cold start")
        return None

    def release_sync(self, task_id: str, browser_healthy: bool = True):
        """
        释放槽位。

        如果 browser_healthy=False，会关闭旧 browser 并重新 launch 一个新的。
        """
        with self._lock:
            for slot in self._slots:
                if slot.task_id == task_id:
                    slot.task_id = None
                    slot.last_used_at = time.time()
                    if not browser_healthy:
                        # 异步替换损坏的 browser
                        old_browser = slot.browser
                        slot.browser = None
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._replace_browser(slot, old_browser),
                                self._loop,
                            )
                    logger.info(f"BrowserPool: slot {slot.index} released by task {task_id}")
                    break

        self._semaphore.release()

    async def _replace_browser(self, slot: _Slot, old_browser):
        """替换损坏的 browser（在专用事件循环中执行）。"""
        if old_browser:
            try:
                await old_browser.close()
            except Exception:
                pass

        proxy_config = None
        if self._proxy:
            proxy_config = {"server": self._proxy}

        try:
            new_browser = await self._pw.chromium.launch(
                headless=self._headless,
                proxy=proxy_config,
            )
            with self._lock:
                slot.browser = new_browser
            logger.info(f"BrowserPool: slot {slot.index} browser replaced")
        except Exception as e:
            logger.error(f"BrowserPool: failed to replace browser for slot {slot.index}: {e}")

    # ── 调整大小 ──────────────────────────────────────────

    def resize_sync(self, new_size: int, timeout: float = 30):
        """调整最大并发数。"""
        if new_size < 1:
            new_size = 1
        old_size = self._max_size
        self._max_size = new_size
        diff = new_size - old_size

        if diff > 0:
            # 增大：启动新 browser
            if self._started and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._add_slots(diff),
                    self._loop,
                )
            else:
                # 未启动时只调整信号量
                for _ in range(diff):
                    self._semaphore.release()
        elif diff < 0:
            # 减小：best-effort 消耗空闲槽位
            for _ in range(-diff):
                acquired = self._semaphore.acquire(blocking=False)
                if not acquired:
                    break

        logger.info(f"BrowserPool resized: {old_size} → {new_size}")

    async def _add_slots(self, count: int):
        """添加新槽位（在专用事件循环中执行）。"""
        proxy_config = None
        if self._proxy:
            proxy_config = {"server": self._proxy}

        with self._lock:
            start_idx = len(self._slots)

        for i in range(count):
            idx = start_idx + i
            try:
                browser = await self._pw.chromium.launch(
                    headless=self._headless,
                    proxy=proxy_config,
                )
                slot = _Slot(index=idx, browser=browser)
            except Exception as e:
                logger.error(f"BrowserPool: failed to add slot {idx}: {e}")
                slot = _Slot(index=idx, browser=None)

            with self._lock:
                self._slots.append(slot)
            self._semaphore.release()

    def warmup_sync(self, timeout: float = 60):
        """预热（已在 start_sync 中完成，此方法为兼容旧接口）。"""
        pass

    # ── 状态查询 ──────────────────────────────────────────

    def stats(self) -> dict:
        """返回池状态快照。"""
        with self._lock:
            slots_copy = [(s.index, s.in_use, s.task_id, s.connected, s.created_at, s.last_used_at)
                          for s in self._slots]

        now = time.time()
        slots_info = []
        for idx, in_use, task_id, connected, created_at, last_used_at in slots_copy:
            slots_info.append({
                "index": idx,
                "in_use": in_use,
                "task_id": task_id,
                "connected": connected,
                "created_at": created_at,
                "last_used_at": last_used_at,
                "idle_seconds": round(now - last_used_at, 1) if not in_use else 0,
            })

        # 补全未初始化的槽位（start 前调用 stats）
        while len(slots_info) < self._max_size:
            i = len(slots_info)
            slots_info.append({
                "index": i, "in_use": False, "task_id": None,
                "connected": False, "created_at": now, "last_used_at": now, "idle_seconds": 0,
            })

        in_use_count = sum(1 for s in slots_info if s["in_use"])
        return {
            "max_size": self._max_size,
            "total": self._max_size,
            "in_use": in_use_count,
            "idle": self._max_size - in_use_count,
            "headless": self._headless,
            "idle_timeout": self._idle_timeout,
            "slots": slots_info,
        }
