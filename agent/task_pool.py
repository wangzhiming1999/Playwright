"""
并行任务执行池：控制浏览器实例并发数，提供队列管理和状态查询。

设计：
- Semaphore 控制最大并发浏览器数（默认 3）
- 每个任务独立的 screenshots 目录和 cookies 文件（已有）
- 队列状态查询：running/queued/completed 计数
- 动态调整并发数
"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class PoolStats:
    """任务池统计信息。"""
    max_workers: int
    running: int
    queued: int
    completed: int
    failed: int
    total_submitted: int
    uptime_seconds: float


class TaskPool:
    """
    并行任务执行池。

    用法：
        pool = TaskPool(max_workers=3)

        # 提交任务（非阻塞，立即返回）
        await pool.submit(task_id, coro_fn, *args)

        # 查询状态
        stats = pool.stats()

        # 动态调整并发数
        pool.resize(5)
    """

    def __init__(self, max_workers: int = 3):
        self._max_workers = max_workers
        self._semaphore = asyncio.Semaphore(max_workers)
        self._running: set[str] = set()       # 正在执行的 task_id
        self._queued: set[str] = set()         # 等待 semaphore 的 task_id
        self._completed_count = 0
        self._failed_count = 0
        self._total_submitted = 0
        self._start_time = time.time()
        self._lock = asyncio.Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def queued_count(self) -> int:
        return len(self._queued)

    async def submit(self, task_id: str, coro_fn, *args, **kwargs):
        """
        提交一个任务到池中。

        coro_fn 是一个 async 函数，接受 task_id 作为第一个参数。
        如果池已满，任务会排队等待。
        """
        async with self._lock:
            self._total_submitted += 1
            self._queued.add(task_id)

        asyncio.ensure_future(self._run_with_semaphore(task_id, coro_fn, *args, **kwargs))

    async def _run_with_semaphore(self, task_id: str, coro_fn, *args, **kwargs):
        """获取 semaphore 后执行任务。"""
        try:
            async with self._semaphore:
                async with self._lock:
                    self._queued.discard(task_id)
                    self._running.add(task_id)

                try:
                    await coro_fn(task_id, *args, **kwargs)
                    async with self._lock:
                        self._completed_count += 1
                except Exception:
                    async with self._lock:
                        self._failed_count += 1
                finally:
                    async with self._lock:
                        self._running.discard(task_id)
        except Exception:
            async with self._lock:
                self._queued.discard(task_id)
                self._running.discard(task_id)
                self._failed_count += 1

    def resize(self, new_max: int):
        """
        动态调整最大并发数。

        增大：立即释放额外的 semaphore 槽位
        减小：等当前任务完成后自然收缩（不会中断正在运行的任务）
        """
        if new_max < 1:
            new_max = 1
        if new_max == self._max_workers:
            return

        diff = new_max - self._max_workers
        self._max_workers = new_max

        if diff > 0:
            # 增大：释放额外槽位
            for _ in range(diff):
                self._semaphore.release()
        else:
            # 减小：通过获取 semaphore 来减少可用槽位
            # 注意：这是 best-effort，不会阻塞
            async def _shrink():
                for _ in range(-diff):
                    try:
                        # 非阻塞尝试获取
                        acquired = self._semaphore._value > 0
                        if acquired:
                            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.01)
                    except (asyncio.TimeoutError, Exception):
                        break
            asyncio.ensure_future(_shrink())

    def stats(self) -> PoolStats:
        """返回当前池状态。"""
        return PoolStats(
            max_workers=self._max_workers,
            running=len(self._running),
            queued=len(self._queued),
            completed=self._completed_count,
            failed=self._failed_count,
            total_submitted=self._total_submitted,
            uptime_seconds=round(time.time() - self._start_time, 1),
        )

    def stats_dict(self) -> dict:
        """返回 JSON 可序列化的状态字典。"""
        s = self.stats()
        return {
            "max_workers": s.max_workers,
            "running": s.running,
            "queued": s.queued,
            "completed": s.completed,
            "failed": s.failed,
            "total_submitted": s.total_submitted,
            "uptime_seconds": s.uptime_seconds,
            "running_task_ids": list(self._running),
            "queued_task_ids": list(self._queued),
        }

    def is_task_running(self, task_id: str) -> bool:
        return task_id in self._running

    def is_task_queued(self, task_id: str) -> bool:
        return task_id in self._queued
