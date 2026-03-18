"""
Agent 成功率提升迭代的单元测试。
覆盖：ModelRouter、循环检测增强、错误恢复渐进衰减、A11y Tree 增强、任务难度预估、布局摘要。
"""

import pytest
from agent.model_router import ModelRouter, estimate_task_difficulty, is_claude_model, select_model_tier
from agent.loop_detector import ActionLoopDetector
from agent.error_recovery import FailureTracker, FailureType, _MAX_FAILURES
from agent.a11y_tree import should_use_screenshot


# ═══════════════════════════════════════════════════════════════════════
# ModelRouter 有状态路由
# ═══════════════════════════════════════════════════════════════════════

class TestModelRouter:
    def test_initial_state(self):
        router = ModelRouter()
        assert router.mini_success_rate == 1.0
        assert router.mini_total_calls == 0

    def test_select_default_for_screenshot(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=True, step=1, last_tool=None,
                             last_failed=False, consecutive_failures=0)
        assert tier == "default"

    def test_select_default_for_first_step(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=0, last_tool=None,
                             last_failed=False, consecutive_failures=0)
        assert tier == "default"

    def test_select_mini_for_simple_dom(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=2, last_tool="click",
                             last_failed=False, consecutive_failures=0)
        assert tier == "mini"

    def test_select_default_for_captcha(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=2, last_tool="click",
                             last_failed=False, consecutive_failures=0,
                             has_captcha=True)
        assert tier == "default"

    def test_select_default_for_dialog(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=2, last_tool="click",
                             last_failed=False, consecutive_failures=0,
                             has_dialog=True)
        assert tier == "default"

    def test_select_default_for_consecutive_failures(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=2, last_tool="click",
                             last_failed=False, consecutive_failures=2)
        assert tier == "default"

    def test_select_default_for_last_failed(self):
        router = ModelRouter()
        tier = router.select(use_screenshot=False, step=2, last_tool="click",
                             last_failed=True, consecutive_failures=1)
        assert tier == "default"

    def test_select_default_for_complex_tool(self):
        for tool in ("find_element", "get_page_html", "solve_captcha", "extract"):
            router = ModelRouter()
            tier = router.select(use_screenshot=False, step=2, last_tool=tool,
                                 last_failed=False, consecutive_failures=0)
            assert tier == "default", f"Expected default for last_tool={tool}"

    def test_record_result_tracks_mini(self):
        router = ModelRouter()
        router.record_result("mini", success=True)
        router.record_result("mini", success=False)
        assert router._mini_success == 1
        assert router._mini_fail == 1
        assert router.mini_success_rate == 0.5

    def test_record_result_ignores_default(self):
        router = ModelRouter()
        router.record_result("default", success=True)
        router.record_result("default", success=False)
        assert router.mini_total_calls == 0

    def test_mini_low_success_rate_upgrades(self):
        """mini 成功率 < 60% 且 ≥5 次采样后，全部升级到 default。"""
        router = ModelRouter()
        # 5 次调用：2 成功 3 失败 = 40% 成功率
        router.record_result("mini", success=True)
        router.record_result("mini", success=True)
        router.record_result("mini", success=False)
        router.record_result("mini", success=False)
        router.record_result("mini", success=False)
        assert router.mini_success_rate == 0.4
        # 此时应该升级到 default
        tier = router.select(use_screenshot=False, step=5, last_tool="click",
                             last_failed=False, consecutive_failures=0)
        assert tier == "default"

    def test_mini_high_success_rate_stays_mini(self):
        """mini 成功率 >= 60% 时继续使用 mini。"""
        router = ModelRouter()
        for _ in range(4):
            router.record_result("mini", success=True)
        router.record_result("mini", success=False)
        assert router.mini_success_rate == 0.8
        tier = router.select(use_screenshot=False, step=5, last_tool="click",
                             last_failed=False, consecutive_failures=0)
        assert tier == "mini"

    def test_stats(self):
        router = ModelRouter()
        router.record_result("mini", success=True)
        router.record_result("mini", success=False)
        stats = router.stats()
        assert stats["mini_success"] == 1
        assert stats["mini_fail"] == 1
        assert stats["mini_rate"] == 0.5

    def test_backward_compat_select_model_tier(self):
        """向后兼容的纯函数接口。"""
        tier = select_model_tier(
            use_screenshot=True, step=0, last_tool=None,
            last_failed=False, consecutive_failures=0
        )
        assert tier == "default"


# ═══════════════════════════════════════════════════════════════════════
# 任务难度预估
# ═══════════════════════════════════════════════════════════════════════

class TestTaskDifficulty:
    def test_hard_tasks(self):
        assert estimate_task_difficulty("登录到 GitHub") == "hard"
        assert estimate_task_difficulty("Login to the website") == "hard"
        assert estimate_task_difficulty("完成 checkout 流程") == "hard"
        assert estimate_task_difficulty("OAuth 认证") == "hard"

    def test_easy_tasks(self):
        assert estimate_task_difficulty("截图保存") == "easy"
        assert estimate_task_difficulty("Navigate to google.com") == "easy"
        assert estimate_task_difficulty("提取页面标题") == "easy"
        assert estimate_task_difficulty("下载文件") == "easy"

    def test_medium_tasks(self):
        assert estimate_task_difficulty("搜索关键词并点击第一个结果") == "medium"
        assert estimate_task_difficulty("Fill in the form") == "medium"


# ═══════════════════════════════════════════════════════════════════════
# 循环检测增强
# ═══════════════════════════════════════════════════════════════════════

class TestLoopDetectorEnhanced:
    def test_result_affects_hash(self):
        """不同 result 的相同 action 应产生不同 hash。"""
        detector = ActionLoopDetector()
        h1 = detector._hash_action("click", {"index": 5}, "点击成功")
        h2 = detector._hash_action("click", {"index": 5}, "操作失败: 元素未找到")
        assert h1 != h2

    def test_same_action_same_result_same_hash(self):
        detector = ActionLoopDetector()
        h1 = detector._hash_action("click", {"index": 5}, "点击成功")
        h2 = detector._hash_action("click", {"index": 5}, "点击成功")
        assert h1 == h2

    def test_record_action_with_result(self):
        detector = ActionLoopDetector()
        detector.record_action("click", {"index": 1}, "点击成功")
        assert len(detector._action_hashes) == 1

    def test_lower_thresholds(self):
        """阈值应为 [4, 6, 9]。"""
        detector = ActionLoopDetector()
        assert detector._nudge_thresholds == [4, 6, 9]

    def test_nudge_at_threshold_4(self):
        """4 次重复时应触发轻度提醒。"""
        detector = ActionLoopDetector()
        for _ in range(4):
            detector.record_action("click", {"index": 3}, "点击成功")
        is_loop, nudge = detector.check_loop()
        assert is_loop is True
        assert "重复" in nudge or "已重复" in nudge

    def test_nudge_at_threshold_6(self):
        """6 次重复时应触发中度警告。"""
        detector = ActionLoopDetector()
        for _ in range(6):
            detector.record_action("click", {"index": 3}, "点击成功")
        is_loop, nudge = detector.check_loop()
        assert is_loop is True
        assert "循环" in nudge or "无效" in nudge

    def test_nudge_at_threshold_9(self):
        """9 次重复时应触发强烈警告。"""
        detector = ActionLoopDetector()
        for _ in range(9):
            detector.record_action("click", {"index": 3}, "点击成功")
        is_loop, nudge = detector.check_loop()
        assert is_loop is True
        assert "严重" in nudge or "彻底" in nudge

    def test_no_nudge_below_threshold(self):
        """3 次重复不应触发提醒。"""
        detector = ActionLoopDetector()
        for _ in range(3):
            detector.record_action("click", {"index": 3}, "点击成功")
        is_loop, nudge = detector.check_loop()
        assert is_loop is False


# ═══════════════════════════════════════════════════════════════════════
# 错误恢复渐进衰减
# ═══════════════════════════════════════════════════════════════════════

class TestErrorRecoveryDecay:
    def test_success_halves_counters(self):
        """record_success 应将计数器减半而非清零。"""
        tracker = FailureTracker()
        for _ in range(6):
            tracker.record_failure("click", "元素未找到")
        tracker.record_success()
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 3
        assert tracker._total_consecutive == 0

    def test_success_halves_odd_number(self):
        """奇数减半应向下取整。"""
        tracker = FailureTracker()
        for _ in range(5):
            tracker.record_failure("click", "元素未找到")
        tracker.record_success()
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 2

    def test_multiple_successes_decay(self):
        """多次成功应持续衰减。"""
        tracker = FailureTracker()
        for _ in range(8):
            tracker.record_failure("click", "元素未找到")
        tracker.record_success()  # 8 -> 4
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 4
        tracker.record_success()  # 4 -> 2
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 2
        tracker.record_success()  # 2 -> 1
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 1
        tracker.record_success()  # 1 -> 0
        assert tracker._counts[FailureType.ELEMENT_NOT_FOUND] == 0

    def test_updated_thresholds(self):
        """阈值应已更新（+2）。"""
        assert _MAX_FAILURES[FailureType.ELEMENT_NOT_FOUND] == 10
        assert _MAX_FAILURES[FailureType.PAGE_NOT_LOADED] == 6
        assert _MAX_FAILURES[FailureType.LLM_INVALID] == 7
        assert _MAX_FAILURES[FailureType.NETWORK_ERROR] == 5
        assert _MAX_FAILURES[FailureType.UNKNOWN] == 7

    def test_abort_with_new_thresholds(self):
        """新阈值下的 abort 判断。"""
        tracker = FailureTracker()
        # NETWORK_ERROR 新阈值 5
        for _ in range(4):
            tracker.record_failure("api", "connection refused")
        abort, _ = tracker.should_abort()
        assert abort is False
        tracker.record_failure("api", "connection refused")
        abort, _ = tracker.should_abort()
        assert abort is True


# ═══════════════════════════════════════════════════════════════════════
# A11y Tree 增强
# ═══════════════════════════════════════════════════════════════════════

class TestA11yTreeEnhanced:
    def test_should_use_screenshot_first_step(self):
        assert should_use_screenshot(step=0, last_tool=None,
                                     page_summary={}, consecutive_dom_steps=0) is True

    def test_should_use_screenshot_after_navigate(self):
        assert should_use_screenshot(step=2, last_tool="navigate",
                                     page_summary={}, consecutive_dom_steps=1) is True

    def test_should_not_use_screenshot_simple_dom(self):
        assert should_use_screenshot(step=2, last_tool="click",
                                     page_summary={"images": 2}, consecutive_dom_steps=1) is False

    def test_should_use_screenshot_many_images(self):
        assert should_use_screenshot(step=2, last_tool="click",
                                     page_summary={"images": 15}, consecutive_dom_steps=1) is True

    def test_should_use_screenshot_consecutive_dom(self):
        assert should_use_screenshot(step=5, last_tool="click",
                                     page_summary={}, consecutive_dom_steps=4) is True


# ═══════════════════════════════════════════════════════════════════════
# Tools 定义检查
# ═══════════════════════════════════════════════════════════════════════

class TestToolsDefinition:
    def test_wait_for_text_tool_exists(self):
        from agent.tools import TOOLS
        tool_names = [t["function"]["name"] for t in TOOLS]
        assert "wait_for_text" in tool_names

    def test_wait_for_text_tool_schema(self):
        from agent.tools import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "wait_for_text")
        params = tool["function"]["parameters"]
        assert "text" in params["properties"]
        assert "timeout" in params["properties"]
        assert "text" in params["required"]
