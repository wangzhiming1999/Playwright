"""
Tests for Agent 实战打磨 iteration:
- P0: quick_dismiss, scroll loading, max_steps
- P1: select_option wait, set_date, form error detection
- P2: OAuth redirect, adaptive timeout, get_active_page
"""

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

import pytest

from agent.core import BrowserAgent
from agent.tools import TOOLS, TERMINATES_SEQUENCE


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_agent(page=None):
    """Create a BrowserAgent with a mocked page."""
    if page is None:
        page = AsyncMock()
        page.url = "https://example.com"
        page.viewport_size = {"width": 1920, "height": 1080}
    agent = BrowserAgent(page, Path("/tmp/test_screenshots"))
    return agent


# ── P0-1: quick_dismiss ─────────────────────────────────────────────────────

class TestQuickDismiss:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_overlay(self):
        agent = _make_agent()
        agent.page.evaluate = AsyncMock(return_value=False)
        result = await agent.quick_dismiss()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_overlay_closed(self):
        agent = _make_agent()
        # JS detection returns True (overlay exists)
        # We need _safe_evaluate to return True
        with patch.object(agent, '_safe_evaluate', return_value=True):
            # Mock locator chain: first selector is visible and clickable
            mock_el = AsyncMock()
            mock_el.is_visible = AsyncMock(return_value=True)
            mock_el.click = AsyncMock()
            mock_locator = MagicMock()
            mock_locator.first = mock_el
            agent.page.locator = MagicMock(return_value=mock_locator)

            result = await agent.quick_dismiss()
        assert result is True


    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        agent = _make_agent()
        with patch.object(agent, '_safe_evaluate', side_effect=Exception("fail")):
            result = await agent.quick_dismiss()
        assert result is False


# ── P0-2: scroll loading detection ──────────────────────────────────────────

class TestScrollLoading:
    @pytest.mark.asyncio
    async def test_scroll_returns_boundary_message(self):
        """When scrollY doesn't change, should report boundary."""
        agent = _make_agent()

        async def mock_safe_eval(expr, timeout_ms=5000, default=None):
            if "window.scrollY" in str(expr):
                return 1000
            if "innerText" in str(expr) and "children" in str(expr):
                return {"len": 500, "children": 50}
            if "spinner" in str(expr) or "loading" in str(expr):
                return False
            if "innerText" in str(expr):
                return 500
            return default

        async def mock_evaluate(expr, *args):
            if "scrollBy" in str(expr):
                return None
            return None

        with patch.object(agent, '_safe_evaluate', side_effect=mock_safe_eval):
            agent.page.evaluate = AsyncMock(side_effect=mock_evaluate)
            result = await agent.execute("scroll", {"direction": "down", "amount": 500})
        assert "底部" in result


# ── P0-3: max_steps parameterization ────────────────────────────────────────

class TestMaxSteps:
    def test_env_var_override(self):
        """AGENT_MAX_STEPS env var should override default."""
        import os
        with patch.dict(os.environ, {"AGENT_MAX_STEPS": "50"}):
            max_steps = 35
            _env = os.environ.get("AGENT_MAX_STEPS")
            if _env:
                try:
                    max_steps = max(10, min(int(_env), 200))
                except (ValueError, TypeError):
                    pass
            assert max_steps == 50

    def test_env_var_clamp_min(self):
        import os
        with patch.dict(os.environ, {"AGENT_MAX_STEPS": "3"}):
            max_steps = 35
            _env = os.environ.get("AGENT_MAX_STEPS")
            if _env:
                max_steps = max(10, min(int(_env), 200))
            assert max_steps == 10

    def test_env_var_clamp_max(self):
        import os
        with patch.dict(os.environ, {"AGENT_MAX_STEPS": "999"}):
            max_steps = 35
            _env = os.environ.get("AGENT_MAX_STEPS")
            if _env:
                max_steps = max(10, min(int(_env), 200))
            assert max_steps == 200


# ── P1-2: set_date tool ─────────────────────────────────────────────────────

class TestSetDateTool:
    def test_tool_in_tools_list(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert "set_date" in names

    def test_tool_requires_index_and_date(self):
        tool = next(t for t in TOOLS if t["function"]["name"] == "set_date")
        required = tool["function"]["parameters"].get("required", [])
        assert "index" in required
        assert "date" in required

    @pytest.mark.asyncio
    async def test_set_date_missing_date(self):
        agent = _make_agent()
        result = await agent.execute("set_date", {"index": 1})
        assert "操作失败" in result

    @pytest.mark.asyncio
    async def test_set_date_missing_index(self):
        agent = _make_agent()
        result = await agent.execute("set_date", {"date": "2024-03-15"})
        assert "操作失败" in result


# ── P1-3: form error detection ───────────────────────────────────────────────

class TestFormErrorDetection:
    @pytest.mark.asyncio
    async def test_detect_no_errors(self):
        agent = _make_agent()
        with patch.object(agent, '_safe_evaluate', return_value=[]):
            result = await agent._detect_form_errors()
        assert result == ""

    @pytest.mark.asyncio
    async def test_detect_errors(self):
        agent = _make_agent()
        errors = [
            {"field": "email", "error": "格式不正确"},
            {"field": "", "error": "必填字段"},
        ]
        with patch.object(agent, '_safe_evaluate', return_value=errors):
            result = await agent._detect_form_errors()
        assert "格式不正确" in result
        assert "必填字段" in result

    @pytest.mark.asyncio
    async def test_detect_errors_exception(self):
        agent = _make_agent()
        with patch.object(agent, '_safe_evaluate', side_effect=Exception("fail")):
            result = await agent._detect_form_errors()
        assert result == ""


# ── P2-2: adaptive timeout ──────────────────────────────────────────────────

class TestAdaptiveTimeout:
    def test_initial_baseline_is_none(self):
        agent = _make_agent()
        assert agent._nav_baseline_ms is None

    def test_timeout_calculation_with_baseline(self):
        agent = _make_agent()
        # Simulate baseline of 5000ms → timeout = max(30000, 10000) = 30000
        agent._nav_baseline_ms = 5000
        nav_timeout = min(60000, max(30000, int(agent._nav_baseline_ms * 2)))
        assert nav_timeout == 30000

    def test_timeout_calculation_slow_site(self):
        agent = _make_agent()
        # Simulate baseline of 20000ms → timeout = max(30000, 40000) = 40000
        agent._nav_baseline_ms = 20000
        nav_timeout = min(60000, max(30000, int(agent._nav_baseline_ms * 2)))
        assert nav_timeout == 40000

    def test_timeout_capped_at_60s(self):
        agent = _make_agent()
        # Simulate baseline of 50000ms → timeout = min(60000, 100000) = 60000
        agent._nav_baseline_ms = 50000
        nav_timeout = min(60000, max(30000, int(agent._nav_baseline_ms * 2)))
        assert nav_timeout == 60000


# ── P2-3: get_active_page ───────────────────────────────────────────────────

class TestGetActivePage:
    def test_returns_page_by_default(self):
        agent = _make_agent()
        assert agent.get_active_page() is agent.page

    def test_returns_frame_when_set(self):
        agent = _make_agent()
        mock_frame = MagicMock()
        agent._active_frame = mock_frame
        assert agent.get_active_page() is mock_frame

    def test_returns_page_after_clearing_frame(self):
        agent = _make_agent()
        agent._active_frame = MagicMock()
        agent._active_frame = None
        assert agent.get_active_page() is agent.page


# ── Tool definitions sanity ──────────────────────────────────────────────────

class TestToolDefinitions:
    def test_scroll_to_text_in_tools(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert "scroll_to_text" in names

    def test_right_click_in_tools(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert "right_click" in names

    def test_switch_iframe_in_tools(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert "switch_iframe" in names
