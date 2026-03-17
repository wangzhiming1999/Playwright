"""
Circuit Breaker — 对外部依赖（LLM API、目标网站）的熔断保护。

状态机：CLOSED → OPEN → HALF_OPEN → CLOSED
- CLOSED: 正常工作，记录失败次数
- OPEN: 熔断中，所有请求直接失败，等待冷却期
- HALF_OPEN: 冷却期结束，允许一次试探请求
"""

import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    通用熔断器。

    用法：
        breaker = CircuitBreaker("llm_api", failure_threshold=3, cooldown=30)
        breaker.check()          # 如果熔断中，抛出 CircuitOpenError
        try:
            result = call_api()
            breaker.record_success()
        except Exception:
            breaker.record_failure()
    """

    def __init__(self, name: str, failure_threshold: int = 3,
                 cooldown: float = 30.0, log_fn=None):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.log_fn = log_fn

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._open_time = 0.0

    @property
    def state(self) -> CircuitState:
        # 自动从 OPEN 转 HALF_OPEN
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._open_time >= self.cooldown:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def check(self) -> bool:
        """
        检查是否可以发起请求。
        返回 True 表示可以继续，False 表示熔断中需要等待。
        """
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return True  # 允许试探
        # OPEN
        remaining = self.cooldown - (time.monotonic() - self._open_time)
        if self.log_fn:
            self.log_fn(f"  [熔断器:{self.name}] 熔断中，{remaining:.0f}s 后重试")
        return False

    def record_success(self):
        """记录成功，重置状态。"""
        if self._state != CircuitState.CLOSED:
            if self.log_fn:
                self.log_fn(f"  [熔断器:{self.name}] 恢复正常")
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self):
        """记录失败，可能触发熔断。"""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._open_time = time.monotonic()
            if self.log_fn:
                self.log_fn(
                    f"  [熔断器:{self.name}] 触发熔断！连续 {self._failure_count} 次失败，"
                    f"冷却 {self.cooldown}s"
                )

    def reset(self):
        """手动重置。"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
