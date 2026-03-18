"""
Tests for agent/error_recovery.py — FailureTracker and classify_failure.
"""

from agent.error_recovery import classify_failure, FailureType, FailureTracker


class TestClassifyFailure:
    def test_element_not_found(self):
        assert classify_failure("click", "元素未找到 index=5") == FailureType.ELEMENT_NOT_FOUND

    def test_element_not_found_english(self):
        assert classify_failure("click", "Element not found at selector") == FailureType.ELEMENT_NOT_FOUND

    def test_page_not_loaded(self):
        assert classify_failure("navigate", "Timeout 30000ms exceeded") == FailureType.PAGE_NOT_LOADED

    def test_page_crashed(self):
        assert classify_failure("click", "page crashed") == FailureType.PAGE_NOT_LOADED

    def test_network_error(self):
        assert classify_failure("navigate", "Connection refused") == FailureType.NETWORK_ERROR

    def test_network_dns(self):
        assert classify_failure("navigate", "DNS resolution failed") == FailureType.NETWORK_ERROR

    def test_llm_invalid(self):
        assert classify_failure("click", "JSON 解析失败") == FailureType.LLM_INVALID

    def test_llm_parse_error(self):
        assert classify_failure("click", "Failed to parse response") == FailureType.LLM_INVALID

    def test_unknown(self):
        assert classify_failure("click", "something weird happened") == FailureType.UNKNOWN


class TestFailureTracker:
    def test_record_failure_returns_type_and_count(self):
        ft = FailureTracker()
        ftype, count, hint = ft.record_failure("click", "元素未找到")
        assert ftype == FailureType.ELEMENT_NOT_FOUND
        assert count == 1
        assert hint != ""

    def test_record_failure_increments_count(self):
        ft = FailureTracker()
        ft.record_failure("click", "元素未找到")
        _, count, _ = ft.record_failure("click", "元素未找到 again")
        assert count == 2

    def test_record_success_resets_all(self):
        ft = FailureTracker()
        ft.record_failure("click", "元素未找到")
        ft.record_failure("navigate", "Timeout exceeded")
        ft.record_success()
        assert ft.total_consecutive == 0
        # Next failure should start from 1
        _, count, _ = ft.record_failure("click", "元素未找到")
        assert count == 1

    def test_different_types_independent(self):
        ft = FailureTracker()
        ft.record_failure("click", "元素未找到")
        ft.record_failure("click", "元素未找到")
        ft.record_failure("navigate", "Timeout exceeded")
        # element_not_found = 2, page_not_loaded = 1
        assert ft._counts[FailureType.ELEMENT_NOT_FOUND] == 2
        assert ft._counts[FailureType.PAGE_NOT_LOADED] == 1

    def test_should_abort_single_type_threshold(self):
        ft = FailureTracker()
        # network_error threshold is 5
        for _ in range(5):
            ft.record_failure("navigate", "Connection refused")
        abort, reason = ft.should_abort()
        assert abort is True
        assert "network_error" in reason

    def test_should_abort_total_consecutive(self):
        ft = FailureTracker()
        # Mix different types so no single type hits its threshold first
        # element_not_found threshold=8, page_not_loaded=4, so alternate carefully
        errors = [
            ("click", "元素未找到"),       # element_not_found 1
            ("navigate", "Timeout"),       # page_not_loaded 1
            ("click", "元素未找到"),       # element_not_found 2
            ("navigate", "Timeout"),       # page_not_loaded 2
            ("click", "元素未找到"),       # element_not_found 3
            ("navigate", "Timeout"),       # page_not_loaded 3
            ("click", "元素未找到"),       # element_not_found 4
            ("click", "元素未找到"),       # element_not_found 5
            ("click", "元素未找到"),       # element_not_found 6
            ("click", "元素未找到"),       # element_not_found 7
        ]
        for tool, msg in errors:
            ft.record_failure(tool, msg)
        abort, reason = ft.should_abort()
        assert abort is True
        assert "总连续失败" in reason

    def test_should_not_abort_under_threshold(self):
        ft = FailureTracker()
        ft.record_failure("click", "元素未找到")
        ft.record_failure("click", "元素未找到")
        abort, _ = ft.should_abort()
        assert abort is False

    def test_element_not_found_high_threshold(self):
        ft = FailureTracker()
        # element_not_found threshold is 10
        for _ in range(9):
            ft.record_failure("click", "元素未找到")
        abort, _ = ft.should_abort()
        assert abort is False
        ft.record_failure("click", "元素未找到")
        abort, _ = ft.should_abort()
        assert abort is True
