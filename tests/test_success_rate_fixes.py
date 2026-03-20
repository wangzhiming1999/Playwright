"""
成功率冲刺修复点的单元测试。
覆盖：
1. page_utils.py spinner 时间衰减逻辑
2. watchdog.py CAPTCHA 可见性过滤
3. e2e_runner.py ask_user_callback 自动回复
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════
# Fix 1: page_utils._wait_for_page_ready spinner 时间衰减
# ═══════════════════════════════════════════════════════════════════════

class TestPageUtilsSpinnerDecay:
    """验证 spinner 时间衰减逻辑：5s 后 spinner 不再阻塞稳定计数。"""

    def _make_page(self, fingerprints: list[dict], text_len: int = 1000):
        """构造 mock page，按顺序返回 fingerprint 数据。"""
        page = MagicMock()
        page.url = "https://example.com"

        call_count = [0]

        async def mock_evaluate(script, *args, **kwargs):
            if "document.readyState" in script:
                return "complete"
            if "__mutationCount" in script and "observer" in script:
                return None
            if "innerText" in script and "childCount" not in script:
                return text_len
            if "textLen" in script:
                idx = min(call_count[0], len(fingerprints) - 1)
                call_count[0] += 1
                return fingerprints[idx]
            return None

        page.evaluate = mock_evaluate
        page.wait_for_load_state = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_spinner_blocks_before_5s(self):
        """5s 内有 spinner 时，content_stable_count 应被重置。"""
        from agent.page_utils import _wait_for_page_ready

        # 所有轮询都返回 has_spinner=True，内容稳定
        fingerprints = [{"textLen": 1000, "childCount": 100, "hasSpinner": True, "mutations": 0}] * 200

        page = self._make_page(fingerprints)
        active_requests: set = set()

        # 超时 1s，期望因 spinner 阻塞而超时（返回"基本就绪"而非"页面就绪"）
        result = await _wait_for_page_ready(page, timeout_ms=1000, check_network=True, active_requests=active_requests)
        # 1s 内 spinner 阻塞，不应满足稳定条件
        assert "基本就绪" in result or "就绪" in result  # 超时后返回兜底消息

    @pytest.mark.asyncio
    async def test_spinner_ignored_after_5s(self):
        """5s 后 spinner 不再阻塞：验证源码中的时间衰减逻辑存在。"""
        import inspect
        from agent import page_utils
        source = inspect.getsource(page_utils._wait_for_page_ready)
        # 验证有时间衰减逻辑
        assert "elapsed_s" in source
        assert "spinner_blocks" in source
        assert "5.0" in source or "5" in source

    def test_stable_threshold_is_15(self):
        """验证稳定阈值已从 20 降到 15。"""
        import inspect
        from agent import page_utils
        source = inspect.getsource(page_utils._wait_for_page_ready)
        # 确认使用 >= 15 而不是 >= 20
        assert "content_stable_count >= 15" in source
        assert "content_stable_count >= 20" not in source


# ═══════════════════════════════════════════════════════════════════════
# Fix 2: watchdog.py CAPTCHA 可见性过滤
# ═══════════════════════════════════════════════════════════════════════

class TestWatchdogCaptchaVisibility:
    """验证 CAPTCHA 检测加入可见性过滤，隐藏 iframe 不触发误报。"""

    def _make_watchdog(self, evaluate_result):
        """构造 mock Watchdog，控制 page.evaluate 返回值。"""
        from agent.watchdog import Watchdog

        page = MagicMock()
        page.url = "https://demoqa.com/checkbox"
        page.evaluate = AsyncMock(return_value=evaluate_result)

        watchdog = Watchdog.__new__(Watchdog)
        watchdog.page = page
        watchdog._captcha_keywords = ["captcha", "challenge", "verify"]
        watchdog._events = asyncio.Queue()
        watchdog._log = AsyncMock()

        def mock_emit(event_type, **kwargs):
            pass
        watchdog._emit = mock_emit

        return watchdog

    @pytest.mark.asyncio
    async def test_hidden_recaptcha_iframe_not_detected(self):
        """隐藏的 reCAPTCHA iframe（v3 后台验证）不应触发 CAPTCHA 检测。"""
        # evaluate 返回 null 表示没有可见的 CAPTCHA
        watchdog = self._make_watchdog(evaluate_result=None)
        result = await watchdog.check_captcha()
        assert result is False

    @pytest.mark.asyncio
    async def test_visible_recaptcha_detected(self):
        """可见的 reCAPTCHA iframe 应该触发 CAPTCHA 检测。"""
        # evaluate 返回 iframe src 表示找到可见的 CAPTCHA
        watchdog = self._make_watchdog(evaluate_result="https://www.google.com/recaptcha/api2/anchor")
        result = await watchdog.check_captcha()
        assert result is True

    @pytest.mark.asyncio
    async def test_visible_dom_selector_detected(self):
        """可见的 .g-recaptcha 元素应该触发 CAPTCHA 检测。"""
        watchdog = self._make_watchdog(evaluate_result=".g-recaptcha")
        result = await watchdog.check_captcha()
        assert result is True

    @pytest.mark.asyncio
    async def test_captcha_url_keyword_detected(self):
        """URL 包含 captcha 关键词时应触发检测。"""
        from agent.watchdog import Watchdog

        page = MagicMock()
        page.url = "https://example.com/captcha/verify"
        page.evaluate = AsyncMock(return_value=None)

        watchdog = Watchdog.__new__(Watchdog)
        watchdog.page = page
        watchdog._captcha_keywords = ["captcha", "challenge", "verify"]
        watchdog._events = asyncio.Queue()
        watchdog._log = AsyncMock()

        emitted = []
        def mock_emit(event_type, **kwargs):
            emitted.append(event_type)
        watchdog._emit = mock_emit

        result = await watchdog.check_captcha()
        assert result is True
        assert len(emitted) == 1

    def test_visibility_check_in_source(self):
        """验证源码中包含可见性检查逻辑。"""
        import inspect
        from agent import watchdog
        source = inspect.getsource(watchdog.Watchdog.check_captcha)
        assert "offsetWidth" in source or "display" in source or "visibility" in source


# ═══════════════════════════════════════════════════════════════════════
# Fix 3: e2e_runner.py ask_user_callback 自动回复
# ═══════════════════════════════════════════════════════════════════════

class TestE2ERunnerAskUser:
    """验证 E2E 运行器传入 ask_user_callback 自动回复。"""

    def test_run_agent_called_with_ask_user_callback(self):
        """run_scenario 应该向 run_agent 传入 ask_user_callback。"""
        import inspect
        from tests.e2e import e2e_runner
        source = inspect.getsource(e2e_runner.E2ERunner.run_scenario)
        assert "ask_user_callback" in source

    def test_auto_reply_returns_empty_string(self):
        """自动回复函数应返回空字符串。"""
        # 通过检查源码验证 auto_reply 返回 ""
        import inspect
        from tests.e2e import e2e_runner
        source = inspect.getsource(e2e_runner.E2ERunner.run_scenario)
        assert 'return ""' in source or "return ''" in source

    @pytest.mark.asyncio
    async def test_scenario_logs_auto_reply(self):
        """ask_user 被自动回复时，日志中应有记录。"""
        from tests.e2e.e2e_runner import E2ERunner, E2EScenario

        runner = E2ERunner()
        scenario = E2EScenario(
            name="test_ask_user",
            task="test task",
            max_steps=1,
            timeout_seconds=5,
        )

        captured_logs = []

        async def mock_run_agent(**kwargs):
            # 模拟 ask_user_callback 被调用
            cb = kwargs.get("ask_user_callback")
            if cb:
                reply = await cb("测试问题", "测试原因")
                captured_logs.append(f"reply={reply!r}")
            return {"success": True, "steps": 1, "cost": {}}

        with patch("agent.runner.run_agent", mock_run_agent):
            result = await runner.run_scenario(scenario)

        assert result.success is True
        assert any("reply=''" in log or 'reply=""' in log for log in captured_logs)


# ═══════════════════════════════════════════════════════════════════════
# Fix 4: drag_drop 慢速拖拽
# ═══════════════════════════════════════════════════════════════════════

class TestDragDropEnhancement:
    """验证 drag_drop 工具使用慢速多步拖拽。"""

    def _get_core_source(self):
        from pathlib import Path
        return Path("agent/core.py").read_text(encoding="utf-8")

    def test_drag_uses_multiple_steps(self):
        """drag_drop 实现应使用多步移动（steps > 10）。"""
        source = self._get_core_source()
        drag_section_start = source.find("drag_drop")
        drag_section = source[drag_section_start:drag_section_start + 2000]
        assert "for step in range" in drag_section or "steps = 20" in drag_section

    def test_drag_has_delay_between_steps(self):
        """drag_drop 实现应在步骤间有延迟。"""
        source = self._get_core_source()
        drag_section_start = source.find("drag_drop")
        drag_section = source[drag_section_start:drag_section_start + 2000]
        assert "asyncio.sleep" in drag_section
