"""Tests for agent/a11y_tree.py — Accessibility Tree extraction and screenshot strategy."""

import pytest
from agent.a11y_tree import should_use_screenshot


class TestShouldUseScreenshot:
    """Test the screenshot decision logic."""

    def test_first_step_always_screenshot(self):
        assert should_use_screenshot(step=0, last_tool=None, page_summary={}, consecutive_dom_steps=0) is True

    def test_navigate_triggers_screenshot(self):
        assert should_use_screenshot(step=3, last_tool="navigate", page_summary={}, consecutive_dom_steps=2) is True

    def test_switch_tab_triggers_screenshot(self):
        assert should_use_screenshot(step=5, last_tool="switch_tab", page_summary={}, consecutive_dom_steps=1) is True

    def test_switch_iframe_triggers_screenshot(self):
        assert should_use_screenshot(step=2, last_tool="switch_iframe", page_summary={}, consecutive_dom_steps=0) is True

    def test_captcha_triggers_screenshot(self):
        assert should_use_screenshot(step=3, last_tool="click", page_summary={"has_captcha": True}, consecutive_dom_steps=1) is True

    def test_dialog_triggers_screenshot(self):
        assert should_use_screenshot(step=3, last_tool="click", page_summary={"has_dialog": True}, consecutive_dom_steps=1) is True

    def test_consecutive_dom_steps_triggers_screenshot(self):
        """After 6 consecutive DOM-only steps, force a screenshot."""
        assert should_use_screenshot(step=7, last_tool="click", page_summary={}, consecutive_dom_steps=6) is True

    def test_consecutive_dom_steps_below_threshold(self):
        """4 consecutive DOM steps should NOT trigger screenshot (threshold is 6)."""
        assert should_use_screenshot(step=5, last_tool="click", page_summary={}, consecutive_dom_steps=4) is False

    def test_visual_tools_trigger_screenshot(self):
        for tool in ("find_element", "save_element", "solve_captcha", "screenshot", "drag_drop"):
            assert should_use_screenshot(step=3, last_tool=tool, page_summary={}, consecutive_dom_steps=1) is True

    def test_many_images_triggers_screenshot(self):
        assert should_use_screenshot(step=3, last_tool="click", page_summary={"images": 15}, consecutive_dom_steps=1) is True

    def test_simple_step_uses_dom(self):
        """Normal step with no special conditions should use DOM mode."""
        assert should_use_screenshot(step=3, last_tool="click", page_summary={}, consecutive_dom_steps=1) is False

    def test_type_text_uses_dom(self):
        assert should_use_screenshot(step=2, last_tool="type_text", page_summary={}, consecutive_dom_steps=0) is False

    def test_scroll_uses_dom(self):
        assert should_use_screenshot(step=4, last_tool="scroll", page_summary={}, consecutive_dom_steps=2) is False

    def test_wait_uses_dom(self):
        assert should_use_screenshot(step=3, last_tool="wait", page_summary={}, consecutive_dom_steps=1) is False

    def test_few_images_uses_dom(self):
        assert should_use_screenshot(step=3, last_tool="click", page_summary={"images": 5}, consecutive_dom_steps=1) is False

    def test_empty_page_summary(self):
        """Empty page summary with no special conditions → DOM mode."""
        assert should_use_screenshot(step=2, last_tool="type_text", page_summary={}, consecutive_dom_steps=0) is False
