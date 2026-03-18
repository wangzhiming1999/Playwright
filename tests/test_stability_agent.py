"""
稳定性测试 — Agent 核心组件容错测试。

覆盖：
- FailureTracker 极端场景（大量混合失败、快速成功重置）
- CircuitBreaker 状态机完整性（快速切换、边界时间）
- ActionLoopDetector 大窗口 / 边界检测
- PlanManager 畸形输入 / 大量步骤
- CostTracker 大量记录 / 未知模型
- Watchdog 事件队列溢出 / 并发 drain
- BrowserAgent._safe_evaluate 超时与异常
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from agent.error_recovery import FailureTracker, FailureType, classify_failure
from agent.circuit_breaker import CircuitBreaker, CircuitState
from agent.loop_detector import ActionLoopDetector
from agent.plan_manager import PlanManager
from agent.cost_tracker import CostTracker
from agent.watchdog import Watchdog, WatchdogEvent, EventType
from agent.core import BrowserAgent


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_agent(page=None):
    if page is None:
        page = AsyncMock()
        page.url = "https://example.com"
        page.viewport_size = {"width": 1920, "height": 1080}
    return BrowserAgent(page, Path("/tmp/test_screenshots"))


# ══════════════════════════════════════════════════════════════════════════════
# FailureTracker 极端场景
# ══════════════════════════════════════════════════════════════════════════════

class TestFailureTrackerStress:

    def test_rapid_mixed_failures(self):
        """快速交替不同类型的失败，不应混淆计数。"""
        tracker = FailureTracker()
        for i in range(50):
            if i % 3 == 0:
                tracker.record_failure("click", "element 未找到")
            elif i % 3 == 1:
                tracker.record_failure("navigate", "timeout 超时")
            else:
                tracker.record_failure("type", "connection refused")

        # 各类型计数应独立
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 17
        assert tracker._counts[FailureType.PAGE_NOT_LOADED] == 17
        assert tracker._counts[FailureType.NETWORK_ERROR] == 16
        assert tracker._total_consecutive == 50

    def test_success_resets_all_counters(self):
        """成功后计数器减半（渐进衰减），连续失败归零。"""
        tracker = FailureTracker()
        for _ in range(5):
            tracker.record_failure("click", "未找到")
        for _ in range(3):
            tracker.record_failure("navigate", "timeout")

        tracker.record_success()
        assert tracker._total_consecutive == 0
        # 渐进衰减：5 // 2 = 2, 3 // 2 = 1
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 2
        assert tracker._counts[FailureType.PAGE_NOT_LOADED] == 1

    def test_abort_triggers_on_single_type_threshold(self):
        """单一类型达到阈值应触发 abort。"""
        tracker = FailureTracker()
        # NETWORK_ERROR 阈值是 5
        for _ in range(5):
            tracker.record_failure("api", "connection refused")
        should, reason = tracker.should_abort()
        assert should is True
        assert "network_error" in reason

    def test_abort_triggers_on_total_consecutive(self):
        """跨类型总连续失败 10 次应触发 abort。"""
        tracker = FailureTracker()
        # 每种类型各 2 次，不触发单类型阈值，但总计 10 次
        for _ in range(2):
            tracker.record_failure("click", "未找到")
        for _ in range(2):
            tracker.record_failure("navigate", "timeout")
        for _ in range(2):
            tracker.record_failure("api", "connection refused")
        for _ in range(2):
            tracker.record_failure("parse", "json 解析失败")
        for _ in range(2):
            tracker.record_failure("unknown", "some error")

        should, reason = tracker.should_abort()
        assert should is True
        assert "总连续失败" in reason

    def test_no_abort_below_thresholds(self):
        """低于所有阈值不应 abort。"""
        tracker = FailureTracker()
        tracker.record_failure("click", "未找到")
        tracker.record_failure("navigate", "timeout")
        should, _ = tracker.should_abort()
        assert should is False

    def test_classify_failure_edge_cases(self):
        """分类函数对各种输入的鲁棒性。"""
        assert classify_failure("click", "") == FailureType.UNKNOWN
        assert classify_failure("", "timeout") == FailureType.PAGE_NOT_LOADED
        assert classify_failure("click", "INDEX out of range") == FailureType.ELEMENT_NOT_FOUND
        # ERR_ 前缀先匹配 PAGE_NOT_LOADED 规则（err_ 关键词）
        assert classify_failure("navigate", "ERR_CONNECTION_REFUSED") == FailureType.PAGE_NOT_LOADED
        assert classify_failure("api", "network unreachable") == FailureType.NETWORK_ERROR
        assert classify_failure("parse", "JSON parse error") == FailureType.LLM_INVALID


# ══════════════════════════════════════════════════════════════════════════════
# CircuitBreaker 状态机完整性
# ══════════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerStress:

    def test_full_state_cycle(self):
        """CLOSED → OPEN → HALF_OPEN → CLOSED 完整循环。"""
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=0.1)

        assert cb.state == CircuitState.CLOSED
        assert cb.check() is True

        # 触发熔断
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.check() is False

        # 等待冷却
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.check() is True

        # 试探成功
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        """HALF_OPEN 状态下失败应重新 OPEN。"""
        cb = CircuitBreaker("test", failure_threshold=1, cooldown=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # 试探失败
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_rapid_success_failure_alternation(self):
        """快速交替成功/失败不应导致状态异常。"""
        cb = CircuitBreaker("test", failure_threshold=3, cooldown=0.05)
        for _ in range(100):
            cb.record_failure()
            cb.record_success()

        # 每次 success 都重置，不应触发熔断
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_reset_from_any_state(self):
        """reset() 从任何状态都应回到 CLOSED。"""
        cb = CircuitBreaker("test", failure_threshold=1, cooldown=100)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_concurrent_failures_exact_threshold(self):
        """恰好达到阈值时应触发。"""
        cb = CircuitBreaker("test", failure_threshold=5, cooldown=1)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()  # 第 5 次
        assert cb.state == CircuitState.OPEN

    def test_log_fn_called_on_open(self):
        """熔断时应调用 log_fn。"""
        logs = []
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=1, log_fn=logs.append)
        cb.record_failure()
        cb.record_failure()
        assert len(logs) == 1
        assert "熔断" in logs[0]


# ══════════════════════════════════════════════════════════════════════════════
# ActionLoopDetector 大窗口 / 边界检测
# ══════════════════════════════════════════════════════════════════════════════

class TestLoopDetectorStress:

    def test_no_false_positive_with_varied_actions(self):
        """不同 action 不应触发循环检测。"""
        detector = ActionLoopDetector(window_size=20)
        for i in range(20):
            detector.record_action(f"tool_{i}", {"arg": i})
        is_loop, msg = detector.check_loop()
        assert is_loop is False

    def test_detects_loop_at_threshold_5(self):
        """5 次重复应触发轻度提醒。"""
        detector = ActionLoopDetector(window_size=20)
        for _ in range(5):
            detector.record_action("click", {"index": 3})
        is_loop, msg = detector.check_loop()
        assert is_loop is True
        assert "5" in msg

    def test_escalating_nudges(self):
        """递进提醒：5 → 8 → 12。"""
        detector = ActionLoopDetector(window_size=30)
        nudges = []

        for i in range(15):
            detector.record_action("click", {"index": 3})
            is_loop, msg = detector.check_loop()
            if msg:
                nudges.append((i + 1, msg))

        # 应有 3 次递进提醒
        assert len(nudges) >= 3
        # 最后一次应是严重警告
        assert "严重" in nudges[-1][1] or "彻底" in nudges[-1][1]

    def test_page_fingerprint_stagnation(self):
        """页面指纹连续 6 步不变应触发提醒。"""
        detector = ActionLoopDetector(window_size=20)
        # 先填充不同 action 避免 action 重复检测
        for i in range(6):
            detector.record_action(f"tool_{i}", {"arg": i})
            detector.record_page_fingerprint("https://example.com", 1000)

        is_loop, msg = detector.check_loop()
        assert is_loop is True
        assert "页面状态" in msg

    def test_window_size_respected(self):
        """超出窗口大小的旧 action 不应影响检测。"""
        detector = ActionLoopDetector(window_size=10)
        # 先填充 5 次重复
        for _ in range(5):
            detector.record_action("click", {"index": 1})
        # 再填充 10 个不同 action（窗口大小 10，旧的被挤出）
        for i in range(10):
            detector.record_action(f"tool_{i}", {"arg": i})

        is_loop, msg = detector.check_loop()
        assert is_loop is False

    def test_reset_clears_all(self):
        """reset 后不应有残留状态。"""
        detector = ActionLoopDetector(window_size=20)
        for _ in range(10):
            detector.record_action("click", {"index": 1})
        detector.reset()

        is_loop, msg = detector.check_loop()
        assert is_loop is False
        assert len(detector._action_hashes) == 0

    def test_empty_args(self):
        """空参数不应崩溃。"""
        detector = ActionLoopDetector()
        detector.record_action("click", {})
        detector.record_action("", {})
        detector.record_action("", {"": ""})
        is_loop, _ = detector.check_loop()
        assert is_loop is False


# ══════════════════════════════════════════════════════════════════════════════
# PlanManager 畸形输入
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanManagerStress:

    def test_empty_init(self):
        """空初始化不应崩溃。"""
        pm = PlanManager()
        assert pm.format_hint() == ""

    def test_none_init(self):
        """None 初始化不应崩溃。"""
        pm = PlanManager(task_steps=None)
        assert pm.format_hint() == ""

    def test_large_plan(self):
        """50 步计划不应崩溃。"""
        steps = [
            {"step": i, "action": f"action {i}", "done_signal": f"signal {i}", "expected": f"expected {i}"}
            for i in range(1, 51)
        ]
        pm = PlanManager(task_steps=steps)
        assert len(pm._steps) == 50
        assert pm._steps[0].status == "current"

    def test_missing_fields_in_steps(self):
        """步骤缺少字段不应崩溃。"""
        steps = [
            {"step": 1},
            {"action": "do something"},
            {},
        ]
        pm = PlanManager(task_steps=steps)
        assert len(pm._steps) == 3

    def test_process_malformed_plan_update(self):
        """畸形 PLAN_UPDATE 不应崩溃。"""
        pm = PlanManager()
        # 各种畸形输入
        pm.process_llm_content("[PLAN_UPDATE]not json[/PLAN_UPDATE]")
        pm.process_llm_content("[PLAN_UPDATE]{invalid json}[/PLAN_UPDATE]")
        pm.process_llm_content("[PLAN_UPDATE][/PLAN_UPDATE]")
        pm.process_llm_content("no plan update here")
        pm.process_llm_content("")
        pm.process_llm_content(None)

    def test_check_stall_increments(self):
        """连续调用 check_stall 应递增停滞计数。"""
        steps = [{"step": 1, "action": "test", "done_signal": "done", "expected": "ok"}]
        pm = PlanManager(task_steps=steps)
        nudges = []
        for i in range(15):
            nudge = pm.check_stall(iteration=i)
            if nudge:
                nudges.append(nudge)
        # 应有递进提醒
        assert len(nudges) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# CostTracker 大量记录
# ══════════════════════════════════════════════════════════════════════════════

class TestCostTrackerStress:

    def test_1000_records(self):
        """1000 次记录不应崩溃，summary 应正确。"""
        ct = CostTracker()
        for i in range(1000):
            ct.record("gpt-4o", {
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_tokens": 30,
            }, purpose=f"step-{i}")

        s = ct.summary()
        assert s["total_calls"] == 1000
        assert s["total_input_tokens"] == 100_000
        assert s["total_output_tokens"] == 50_000
        assert s["total_cached_tokens"] == 30_000
        assert s["cache_hit_rate"] == 0.3
        assert s["total_cost_usd"] > 0

    def test_unknown_model(self):
        """未知模型不应崩溃，成本为 0。"""
        ct = CostTracker()
        ct.record("unknown-model-v99", {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0})
        s = ct.summary()
        assert s["total_calls"] == 1
        assert s["total_cost_usd"] == 0.0

    def test_empty_usage(self):
        """空 usage 不应崩溃。"""
        ct = CostTracker()
        ct.record("gpt-4o", {})    # empty dict → `not {}` is True → skipped
        ct.record("gpt-4o", None)  # None → skipped
        # 两者都被 `if not usage: return` 跳过
        assert ct.summary()["total_calls"] == 0

    def test_reset(self):
        """reset 后 summary 应为空。"""
        ct = CostTracker()
        for _ in range(10):
            ct.record("gpt-4o", {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 0})
        ct.reset()
        assert ct.summary()["total_calls"] == 0

    def test_all_cached(self):
        """全部缓存命中时 cache_hit_rate 应为 1.0。"""
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 1000})
        s = ct.summary()
        assert s["cache_hit_rate"] == 1.0


# ══════════════════════════════════════════════════════════════════════════════
# Watchdog 事件队列
# ══════════════════════════════════════════════════════════════════════════════

class TestWatchdogStress:

    def test_emit_and_drain_many_events(self):
        """大量事件 emit + drain 不应丢失。"""
        page = AsyncMock()
        context = AsyncMock()
        wd = Watchdog(page, context)

        for i in range(100):
            wd._emit(EventType.CONSOLE_ERROR, message=f"error-{i}")

        events = wd.drain_events()
        assert len(events) == 100

        # drain 后应为空
        events2 = wd.drain_events()
        assert len(events2) == 0

    def test_peek_does_not_consume(self):
        """peek 不应消费事件。"""
        page = AsyncMock()
        context = AsyncMock()
        wd = Watchdog(page, context)

        wd._emit(EventType.CAPTCHA_DETECTED)
        wd._emit(EventType.PAGE_CRASHED)

        peeked = wd.peek_events()
        assert len(peeked) == 2

        # peek 后 drain 仍应有事件
        drained = wd.drain_events()
        assert len(drained) == 2

    def test_has_event_filter(self):
        """has_event 按类型过滤。"""
        page = AsyncMock()
        context = AsyncMock()
        wd = Watchdog(page, context)

        wd._emit(EventType.DOWNLOAD_STARTED, url="http://example.com/file.zip")
        assert wd.has_event(EventType.DOWNLOAD_STARTED) is True
        assert wd.has_event(EventType.PAGE_CRASHED) is False

    def test_mixed_event_types(self):
        """混合事件类型 drain 后按类型过滤。"""
        page = AsyncMock()
        context = AsyncMock()
        wd = Watchdog(page, context)

        wd._emit(EventType.CAPTCHA_DETECTED)
        wd._emit(EventType.DOWNLOAD_STARTED, url="a")
        wd._emit(EventType.DIALOG_APPEARED, dialog_type="alert")
        wd._emit(EventType.CAPTCHA_DETECTED)

        captchas = wd.peek_events(EventType.CAPTCHA_DETECTED)
        assert len(captchas) == 2

        downloads = wd.peek_events(EventType.DOWNLOAD_STARTED)
        assert len(downloads) == 1


# ══════════════════════════════════════════════════════════════════════════════
# BrowserAgent._safe_evaluate 容错
# ══════════════════════════════════════════════════════════════════════════════

class TestSafeEvaluateStress:

    @pytest.mark.asyncio
    async def test_timeout_returns_default(self):
        """evaluate 超时应返回 default。"""
        agent = _make_agent()

        async def slow_eval(expr):
            await asyncio.sleep(10)

        agent.page.evaluate = slow_eval
        result = await agent._safe_evaluate("slow()", timeout_ms=50, default="fallback")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_exception_returns_default(self):
        """evaluate 异常应返回 default。"""
        agent = _make_agent()
        agent.page.evaluate = AsyncMock(side_effect=RuntimeError("page crashed"))
        result = await agent._safe_evaluate("crash()", default=42)
        assert result == 42

    @pytest.mark.asyncio
    async def test_none_default(self):
        """default=None 时超时返回 None。"""
        agent = _make_agent()

        async def slow_eval(expr):
            await asyncio.sleep(10)

        agent.page.evaluate = slow_eval
        result = await agent._safe_evaluate("slow()", timeout_ms=50)
        assert result is None

    @pytest.mark.asyncio
    async def test_rapid_sequential_evaluates(self):
        """快速连续调用不应累积超时。"""
        agent = _make_agent()
        agent.page.evaluate = AsyncMock(return_value="ok")

        results = []
        for _ in range(50):
            r = await agent._safe_evaluate("fast()", timeout_ms=1000, default="fail")
            results.append(r)

        assert all(r == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_validate_index_no_elements(self):
        """没有元素列表时 _validate_index 不应崩溃。"""
        agent = _make_agent()
        with patch("agent.core.get_last_elements", return_value=[]):
            result = agent._validate_index(999)
        assert result is None  # 没有元素时不校验

    @pytest.mark.asyncio
    async def test_validate_index_out_of_range(self):
        """index 超出范围应返回错误消息。"""
        agent = _make_agent()
        elements = [{"index": 0}, {"index": 5}, {"index": 10}]
        with patch("agent.core.get_last_elements", return_value=elements):
            result = agent._validate_index(99)
        assert result is not None
        assert "超出有效范围" in result

    @pytest.mark.asyncio
    async def test_validate_index_negative(self):
        """负数 index 应返回错误消息。"""
        agent = _make_agent()
        elements = [{"index": 0}, {"index": 5}]
        with patch("agent.core.get_last_elements", return_value=elements):
            result = agent._validate_index(-1)
        assert result is not None
        assert "超出有效范围" in result

    @pytest.mark.asyncio
    async def test_screenshot_base64_failure(self):
        """截图失败应返回空字符串。"""
        agent = _make_agent()
        agent.page.screenshot = AsyncMock(side_effect=RuntimeError("browser disconnected"))
        result = await agent.screenshot_base64()
        assert result == ""

    @pytest.mark.asyncio
    async def test_get_active_page_frame_lifecycle(self):
        """frame 设置和清除的生命周期。"""
        agent = _make_agent()
        assert agent.get_active_page() is agent.page

        mock_frame = MagicMock()
        agent._active_frame = mock_frame
        assert agent.get_active_page() is mock_frame

        agent._active_frame = None
        assert agent.get_active_page() is agent.page
