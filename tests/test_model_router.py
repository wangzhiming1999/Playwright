"""Tests for agent/model_router.py — intelligent model selection."""

import pytest
from agent.model_router import select_model_tier


class TestSelectModelTier:
    """Test model routing logic."""

    def test_screenshot_mode_uses_default(self):
        assert select_model_tier(use_screenshot=True, step=3, last_tool="click",
                                  last_failed=False, consecutive_failures=0) == "default"

    def test_first_step_uses_default(self):
        assert select_model_tier(use_screenshot=False, step=0, last_tool=None,
                                  last_failed=False, consecutive_failures=0) == "default"

    def test_captcha_uses_default(self):
        assert select_model_tier(use_screenshot=False, step=3, last_tool="click",
                                  last_failed=False, consecutive_failures=0,
                                  has_captcha=True) == "default"

    def test_dialog_uses_default(self):
        assert select_model_tier(use_screenshot=False, step=3, last_tool="click",
                                  last_failed=False, consecutive_failures=0,
                                  has_dialog=True) == "default"

    def test_consecutive_failures_uses_default(self):
        assert select_model_tier(use_screenshot=False, step=5, last_tool="click",
                                  last_failed=True, consecutive_failures=2) == "default"

    def test_last_failed_uses_default(self):
        assert select_model_tier(use_screenshot=False, step=3, last_tool="click",
                                  last_failed=True, consecutive_failures=1) == "default"

    def test_complex_tool_uses_default(self):
        for tool in ("find_element", "get_page_html", "solve_captcha", "extract"):
            assert select_model_tier(use_screenshot=False, step=3, last_tool=tool,
                                      last_failed=False, consecutive_failures=0) == "default"

    def test_simple_dom_step_uses_mini(self):
        """Normal DOM mode step with no issues → mini model."""
        assert select_model_tier(use_screenshot=False, step=3, last_tool="click",
                                  last_failed=False, consecutive_failures=0) == "mini"

    def test_type_text_uses_mini(self):
        assert select_model_tier(use_screenshot=False, step=2, last_tool="type_text",
                                  last_failed=False, consecutive_failures=0) == "mini"

    def test_scroll_uses_mini(self):
        assert select_model_tier(use_screenshot=False, step=4, last_tool="scroll",
                                  last_failed=False, consecutive_failures=0) == "mini"

    def test_wait_uses_mini(self):
        assert select_model_tier(use_screenshot=False, step=3, last_tool="wait",
                                  last_failed=False, consecutive_failures=0) == "mini"

    def test_navigate_with_screenshot_uses_default(self):
        """Navigate triggers screenshot, so even if passed as last_tool, screenshot=True wins."""
        assert select_model_tier(use_screenshot=True, step=3, last_tool="navigate",
                                  last_failed=False, consecutive_failures=0) == "default"

    def test_one_failure_not_consecutive_uses_default(self):
        """Single failure → use default to analyze."""
        assert select_model_tier(use_screenshot=False, step=5, last_tool="click",
                                  last_failed=True, consecutive_failures=1) == "default"

    def test_zero_failures_after_recovery_uses_mini(self):
        """After recovery (failures reset), back to mini."""
        assert select_model_tier(use_screenshot=False, step=6, last_tool="click",
                                  last_failed=False, consecutive_failures=0) == "mini"
