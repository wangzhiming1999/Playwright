"""
视觉验证模块单元测试 — 测试 PageSnapshot / verify_action / ActionVerifier 的逻辑。
"""

import pytest
from agent.visual_verify import (
    PageSnapshot, VerifyResult, take_snapshot, verify_action,
    ActionVerifier, EXPECTS_CHANGE, SKIP_VERIFY,
)


# ── PageSnapshot 测试 ──────────────────────────────────────────

class TestPageSnapshot:
    def test_default_fields(self):
        snap = PageSnapshot()
        assert snap.url == ""
        assert snap.body_text_len == 0
        assert snap.scroll_y == 0

    def test_fingerprint(self):
        snap = PageSnapshot(url="https://example.com", title="Test", body_text_len=100, child_count=5, scroll_y=200)
        fp = snap.fingerprint()
        assert "https://example.com" in fp
        assert "100" in fp

    def test_fingerprint_changes_with_url(self):
        s1 = PageSnapshot(url="https://a.com")
        s2 = PageSnapshot(url="https://b.com")
        assert s1.fingerprint() != s2.fingerprint()

    def test_fingerprint_same_for_identical(self):
        s1 = PageSnapshot(url="https://a.com", title="T", body_text_len=10, child_count=3, scroll_y=0)
        s2 = PageSnapshot(url="https://a.com", title="T", body_text_len=10, child_count=3, scroll_y=0)
        assert s1.fingerprint() == s2.fingerprint()


# ── verify_action 测试 ──────────────────────────────────────────

class TestVerifyAction:
    def _snap(self, **kwargs):
        return PageSnapshot(**kwargs)

    def test_skip_verify_tools(self):
        for tool in SKIP_VERIFY:
            r = verify_action(tool, {}, self._snap(), self._snap(), "ok")
            assert r.changed is True
            assert r.change_type == "skip"

    def test_action_error_detected(self):
        r = verify_action("click", {"index": 5}, self._snap(), self._snap(), "操作失败: 元素不存在")
        assert r.changed is False
        assert r.change_type == "error"

    def test_url_change_detected(self):
        before = self._snap(url="https://a.com")
        after = self._snap(url="https://b.com")
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True
        assert r.change_type == "url"

    def test_content_change_by_text_len(self):
        before = self._snap(url="https://a.com", body_text_len=100)
        after = self._snap(url="https://a.com", body_text_len=500)
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True
        assert "content" in r.change_type

    def test_content_change_by_child_count(self):
        before = self._snap(url="https://a.com", child_count=10)
        after = self._snap(url="https://a.com", child_count=20)
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True

    def test_content_change_by_text_hash(self):
        before = self._snap(url="https://a.com", visible_text_hash="aaa")
        after = self._snap(url="https://a.com", visible_text_hash="bbb")
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True

    def test_scroll_change_detected(self):
        before = self._snap(url="https://a.com", scroll_y=0)
        after = self._snap(url="https://a.com", scroll_y=500)
        r = verify_action("scroll", {"direction": "down"}, before, after, "ok")
        assert r.changed is True
        assert "scroll" in r.change_type

    def test_input_change_detected(self):
        before = self._snap(url="https://a.com", focused_value="")
        after = self._snap(url="https://a.com", focused_value="hello")
        r = verify_action("type_text", {"text": "hello"}, before, after, "ok")
        assert r.changed is True
        assert "input" in r.change_type

    def test_title_change_detected(self):
        before = self._snap(url="https://a.com", title="Page 1")
        after = self._snap(url="https://a.com", title="Page 2")
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True
        assert "title" in r.change_type

    def test_click_no_change_triggers_retry(self):
        snap = self._snap(url="https://a.com", body_text_len=100, child_count=5, visible_text_hash="abc")
        r = verify_action("click", {"index": 3, "text": "Submit"}, snap, snap, "ok")
        assert r.changed is False
        assert r.should_retry is True
        assert "点击" in r.nudge
        assert r.change_type == "none"

    def test_type_text_no_change_triggers_retry(self):
        snap = self._snap(url="https://a.com", focused_value="old")
        r = verify_action("type_text", {"text": "new"}, snap, snap, "ok")
        assert r.changed is False
        assert r.should_retry is True
        assert "输入" in r.nudge

    def test_scroll_no_change_no_retry(self):
        snap = self._snap(url="https://a.com", scroll_y=1000)
        r = verify_action("scroll", {"direction": "down"}, snap, snap, "ok")
        assert r.changed is False
        assert r.should_retry is False  # 滚动到底不需要重试

    def test_select_option_no_change(self):
        snap = self._snap(url="https://a.com")
        r = verify_action("select_option", {"index": 1, "value": "opt"}, snap, snap, "ok")
        assert r.changed is False
        assert r.should_retry is True
        assert "选择" in r.nudge

    def test_set_date_no_change(self):
        snap = self._snap(url="https://a.com")
        r = verify_action("set_date", {"index": 1, "date": "2024-01-01"}, snap, snap, "ok")
        assert r.changed is False
        assert "日期" in r.nudge

    def test_navigate_no_change(self):
        snap = self._snap(url="https://a.com")
        r = verify_action("navigate", {"url": "https://b.com"}, snap, snap, "ok")
        assert r.changed is False
        assert r.should_retry is True

    def test_unknown_tool_no_change_ok(self):
        """不在 EXPECTS_CHANGE 中的工具，无变化也不报错。"""
        snap = self._snap(url="https://a.com")
        r = verify_action("hover", {}, snap, snap, "ok")
        assert r.changed is True
        assert r.change_type == "none_expected"

    def test_multiple_changes_combined(self):
        before = self._snap(url="https://a.com", body_text_len=100, scroll_y=0, focused_value="")
        after = self._snap(url="https://a.com", body_text_len=500, scroll_y=300, focused_value="x")
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is True
        assert "content" in r.change_type
        assert "scroll" in r.change_type

    def test_small_text_len_diff_ignored(self):
        """文本长度差异 <= 10 不算变化。"""
        before = self._snap(url="https://a.com", body_text_len=100, visible_text_hash="same")
        after = self._snap(url="https://a.com", body_text_len=105, visible_text_hash="same")
        r = verify_action("click", {"index": 1}, before, after, "ok")
        assert r.changed is False  # 差异太小

    def test_small_scroll_diff_ignored(self):
        """滚动差异 <= 50 不算变化。"""
        before = self._snap(url="https://a.com", scroll_y=100, visible_text_hash="same")
        after = self._snap(url="https://a.com", scroll_y=130, visible_text_hash="same")
        r = verify_action("scroll", {"direction": "down"}, before, after, "ok")
        assert r.changed is False


# ── ActionVerifier 递进提醒测试 ──────────────────────────────────

class TestActionVerifier:
    def _no_change_result(self):
        return VerifyResult(changed=False, should_retry=True)

    def _changed_result(self):
        return VerifyResult(changed=True)

    def test_initial_state(self):
        v = ActionVerifier()
        assert v.stats["consecutive_no_change"] == 0
        assert v.stats["total_no_change"] == 0

    def test_changed_resets_counter(self):
        v = ActionVerifier()
        v.record(self._no_change_result())
        v.record(self._no_change_result())
        v.record(self._changed_result())
        assert v.stats["consecutive_no_change"] == 0

    def test_no_escalation_for_single_failure(self):
        v = ActionVerifier()
        msg = v.record(self._no_change_result())
        assert msg is None

    def test_escalation_at_2(self):
        v = ActionVerifier()
        v.record(self._no_change_result())
        msg = v.record(self._no_change_result())
        assert msg is not None
        assert "2 次" in msg

    def test_escalation_at_4(self):
        v = ActionVerifier()
        for _ in range(3):
            v.record(self._no_change_result())
        msg = v.record(self._no_change_result())
        assert msg is not None
        assert "4 次" in msg

    def test_escalation_at_6(self):
        v = ActionVerifier()
        for _ in range(5):
            v.record(self._no_change_result())
        msg = v.record(self._no_change_result())
        assert msg is not None
        assert "6 次" in msg

    def test_counter_resets_after_6(self):
        v = ActionVerifier()
        for _ in range(6):
            v.record(self._no_change_result())
        # 第 6 次后 consecutive 重置
        assert v.stats["consecutive_no_change"] == 0

    def test_total_accumulates(self):
        v = ActionVerifier()
        for _ in range(3):
            v.record(self._no_change_result())
        v.record(self._changed_result())
        for _ in range(2):
            v.record(self._no_change_result())
        assert v.stats["total_no_change"] == 5

    def test_reset(self):
        v = ActionVerifier()
        for _ in range(3):
            v.record(self._no_change_result())
        v.reset()
        assert v.stats["consecutive_no_change"] == 0
        assert v.stats["total_no_change"] == 0

    def test_no_retry_skips_escalation(self):
        """should_retry=False 的结果不触发递进提醒。"""
        v = ActionVerifier()
        no_retry = VerifyResult(changed=False, should_retry=False)
        for _ in range(10):
            msg = v.record(no_retry)
            assert msg is None

    def test_changed_result_returns_none(self):
        v = ActionVerifier()
        msg = v.record(self._changed_result())
        assert msg is None


# ── EXPECTS_CHANGE / SKIP_VERIFY 配置测试 ──────────────────────

class TestConfig:
    def test_expects_change_keys(self):
        assert "click" in EXPECTS_CHANGE
        assert "type_text" in EXPECTS_CHANGE
        assert "navigate" in EXPECTS_CHANGE
        assert "scroll" in EXPECTS_CHANGE

    def test_skip_verify_keys(self):
        assert "screenshot" in SKIP_VERIFY
        assert "done" in SKIP_VERIFY
        assert "extract" in SKIP_VERIFY
        assert "wait" in SKIP_VERIFY

    def test_no_overlap(self):
        """EXPECTS_CHANGE 和 SKIP_VERIFY 不应有交集。"""
        overlap = set(EXPECTS_CHANGE.keys()) & SKIP_VERIFY
        assert len(overlap) == 0, f"重叠工具: {overlap}"
