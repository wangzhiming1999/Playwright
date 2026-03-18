"""
浏览器并发控制池（信号量模式）

设计原则：
- Playwright 对象（Browser/Context/Page）绑定到创建它的事件循环，不能跨线程传递
- 本模块只做"槽位占用"控制，限制同时运行的浏览器数量
- 每个 agent 线程自己启动 Playwright、创建浏览器和 context
- acquire_sync 阻塞直到有空闲槽位，release_sync 释放槽位
"""

import threading
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BrowserPool:
    """
    浏览器并发控制池（信号量模式）。

    不共享任何 Playwright 对象，只限制同时运行的任务数。
    acquire_sync/release_sync 是线程安全的同步接口。
    """

    def __init__(
        self,
        max_size: int = 3,
        idle_timeout: float = 300.0,  # 保留参数，兼容旧调用，不再使用
        headless: bool = False,
        proxy: Optional[str] = None,
    ):
        self._max_size = max_size
        self._headless = headless
        self._proxy = proxy
        self._semaphore = threading.Semaphore(max_size)
        self._lock = threading.Lock()
        self._active: dict[str, float] = {}  # task_id -> acquired_at
        self._started = False

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def started(self) -> bool:
        return self._started

    def start_sync(self):
        """启动池（信号量模式下只是标记 started）。"""
        self._started = True
        logger.info(f"BrowserPool (semaphore mode) started: max_size={self._max_size}")

    def acquire_sync(self, task_id: str, timeout: float = 60) -> bool:
        """
        阻塞直到获取到一个槽位。
        返回 True 表示成功获取，调用方可以自行启动浏览器。
        超时抛出 TimeoutError。
        """
        acquired = self._semaphore.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(f"BrowserPool: timeout waiting for slot (task={task_id})")
        with self._lock:
            self._active[task_id] = time.time()
        logger.info(f"BrowserPool: slot acquired for task {task_id} ({len(self._active)}/{self._max_size} in use)")
        return True

    def release_sync(self, task_id: str, timeout: float = 30):
        """释放槽位。"""
        with self._lock:
            self._active.pop(task_id, None)
        self._semaphore.release()
        logger.info(f"BrowserPool: slot released for task {task_id} ({len(self._active)}/{self._max_size} in use)")

    def shutdown_sync(self, timeout: float = 30):
        """关闭池。"""
        self._started = False
        logger.info("BrowserPool shutdown complete")

    def resize_sync(self, new_size: int, timeout: float = 30):
        """调整最大并发数（重建信号量）。"""
        if new_size < 1:
            new_size = 1
        old_size = self._max_size
        # 计算当前已占用的槽位数
        with self._lock:
            in_use = len(self._active)
        # 重建信号量：新容量 = new_size，当前已用 = in_use
        self._semaphore = threading.Semaphore(max(0, new_size - in_use))
        self._max_size = new_size
        logger.info(f"BrowserPool resized: {old_size} → {new_size}")

    def warmup_sync(self, timeout: float = 60):
        """预热（信号量模式下无需预热）。"""
        pass

    def stats(self) -> dict:
        """返回池状态快照。"""
        with self._lock:
            active_copy = dict(self._active)
        now = time.time()
        slots_info = []
        for i in range(self._max_size):
            task_ids = list(active_copy.keys())
            if i < len(task_ids):
                tid = task_ids[i]
                slots_info.append({
                    "index": i,
                    "in_use": True,
                    "task_id": tid,
                    "connected": True,
                    "created_at": active_copy[tid],
                    "last_used_at": active_copy[tid],
                    "idle_seconds": 0,
                })
            else:
                slots_info.append({
                    "index": i,
                    "in_use": False,
                    "task_id": None,
                    "connected": False,
                    "created_at": now,
                    "last_used_at": now,
                    "idle_seconds": 0,
                })
        return {
            "max_size": self._max_size,
            "total": self._max_size,
            "in_use": len(active_copy),
            "idle": self._max_size - len(active_copy),
            "headless": self._headless,
            "idle_timeout": 0,
            "slots": slots_info,
        }
