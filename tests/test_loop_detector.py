"""
Tests for agent/loop_detector.py — ActionLoopDetector pure state machine.
"""

from agent.loop_detector import ActionLoopDetector


class TestHashAction:
    def test_same_tool_args_same_hash(self):
        d = ActionLoopDetector()
        h1 = d._hash_action("click", {"index": 5})
        h2 = d._hash_action("click", {"index": 5})
        assert h1 == h2

    def test_different_args_different_hash(self):
        d = ActionLoopDetector()
        h1 = d._hash_action("click", {"index": 5})
        h2 = d._hash_action("click", {"index": 6})
        assert h1 != h2

    def test_different_tool_different_hash(self):
        d = ActionLoopDetector()
        h1 = d._hash_action("click", {"index": 5})
        h2 = d._hash_action("type_text", {"index": 5})
        assert h1 != h2


class TestRecordAction:
    def test_records_action(self):
        d = ActionLoopDetector(window_size=5)
        d.record_action("click", {"index": 1})
        assert len(d._action_hashes) == 1
        assert len(d._action_history) == 1

    def test_sliding_window_trims(self):
        d = ActionLoopDetector(window_size=3)
        for i in range(5):
            d.record_action("click", {"index": i})
        assert len(d._action_hashes) == 3
        assert len(d._action_history) == 3
        # oldest should be trimmed
        assert d._action_history[0]["args"]["index"] == 2


class TestRecordPageFingerprint:
    def test_records_fingerprint(self):
        d = ActionLoopDetector()
        d.record_page_fingerprint("https://example.com", 1000)
        assert len(d._page_fingerprints) == 1

    def test_sliding_window_trims(self):
        d = ActionLoopDetector(window_size=3)
        for i in range(5):
            d.record_page_fingerprint(f"https://example.com/{i}", 100)
        assert len(d._page_fingerprints) == 3


class TestCheckLoop:
    def test_no_loop_with_few_actions(self):
        d = ActionLoopDetector()
        d.record_action("click", {"index": 1})
        d.record_action("click", {"index": 2})
        is_loop, msg = d.check_loop()
        assert is_loop is False
        assert msg == ""

    def test_no_loop_with_varied_actions(self):
        d = ActionLoopDetector()
        for i in range(10):
            d.record_action("click", {"index": i})
        is_loop, msg = d.check_loop()
        assert is_loop is False

    def test_light_nudge_at_5(self):
        d = ActionLoopDetector()
        for _ in range(5):
            d.record_action("click", {"index": 5})
        is_loop, msg = d.check_loop()
        assert is_loop is True
        assert "重复" in msg
        assert "换一种方式" in msg

    def test_medium_nudge_at_8(self):
        d = ActionLoopDetector()
        for _ in range(8):
            d.record_action("click", {"index": 5})
        is_loop, msg = d.check_loop()
        assert is_loop is True
        assert "循环" in msg
        assert "不同的操作方式" in msg

    def test_severe_nudge_at_12(self):
        d = ActionLoopDetector()
        for _ in range(12):
            d.record_action("click", {"index": 5})
        is_loop, msg = d.check_loop()
        assert is_loop is True
        assert "严重循环" in msg
        assert "彻底改变策略" in msg

    def test_no_duplicate_nudge_at_same_count(self):
        d = ActionLoopDetector()
        for _ in range(5):
            d.record_action("click", {"index": 5})
        _, msg1 = d.check_loop()
        assert msg1 != ""
        # Second check at same count should not nudge again
        _, msg2 = d.check_loop()
        assert msg2 == ""

    def test_page_fingerprint_stall(self):
        d = ActionLoopDetector()
        # Need >= 4 actions to pass the early return
        for i in range(6):
            d.record_action("click", {"index": i})
            d.record_page_fingerprint("https://example.com", 5000)
        is_loop, msg = d.check_loop()
        assert is_loop is True
        assert "页面状态" in msg

    def test_page_fingerprint_no_stall_when_changing(self):
        d = ActionLoopDetector()
        for i in range(6):
            d.record_action("click", {"index": i})
            d.record_page_fingerprint("https://example.com", 5000 + i)
        is_loop, msg = d.check_loop()
        assert is_loop is False


class TestReset:
    def test_reset_clears_all(self):
        d = ActionLoopDetector()
        for _ in range(10):
            d.record_action("click", {"index": 5})
            d.record_page_fingerprint("https://example.com", 100)
        d.reset()
        assert len(d._action_hashes) == 0
        assert len(d._action_history) == 0
        assert len(d._page_fingerprints) == 0
        assert d._last_nudge_count == 0

    def test_no_loop_after_reset(self):
        d = ActionLoopDetector()
        for _ in range(10):
            d.record_action("click", {"index": 5})
        d.reset()
        is_loop, msg = d.check_loop()
        assert is_loop is False
