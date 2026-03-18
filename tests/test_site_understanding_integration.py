"""
Tests for site_understanding integration into agent main loop.
- _format_site_understanding helper
- analyze_current_page tool definition
- Tool execution logic (mocked)
- Caching behavior
"""

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from agent.runner import _format_site_understanding
from agent.tools import TOOLS, TERMINATES_SEQUENCE


# ── _format_site_understanding ────────────────────────────────────────────────

class TestFormatSiteUnderstanding:
    def test_formats_basic_analysis(self):
        analysis = {
            "site_category": "B2B SaaS",
            "site_name": "Acme Analytics",
            "needs_login": False,
            "entry_points": [
                {"label": "Dashboard", "path": "/dashboard", "priority": 5},
                {"label": "Reports", "path": "/reports", "priority": 4},
            ],
            "key_features_visible": ["real-time charts", "export"],
            "exploration_strategy": "Start with dashboard",
        }
        result = _format_site_understanding(analysis)
        assert "Acme Analytics" in result
        assert "B2B SaaS" in result
        assert "real-time charts" in result
        assert "Dashboard" in result

    def test_returns_empty_for_unknown(self):
        analysis = {"site_category": "unknown", "site_name": "x"}
        assert _format_site_understanding(analysis) == ""

    def test_returns_empty_for_none(self):
        assert _format_site_understanding(None) == ""

    def test_returns_empty_for_empty_dict(self):
        assert _format_site_understanding({}) == ""

    def test_includes_login_hint(self):
        analysis = {
            "site_category": "SaaS",
            "site_name": "App",
            "needs_login": True,
            "entry_points": [],
            "key_features_visible": [],
            "exploration_strategy": "",
        }
        result = _format_site_understanding(analysis)
        assert "登录" in result

    def test_truncates_features_to_5(self):
        analysis = {
            "site_category": "SaaS",
            "site_name": "App",
            "needs_login": False,
            "entry_points": [],
            "key_features_visible": ["f1", "f2", "f3", "f4", "f5", "f6", "f7"],
            "exploration_strategy": "",
        }
        result = _format_site_understanding(analysis)
        assert "f5" in result
        assert "f6" not in result

    def test_sorts_entry_points_by_priority(self):
        analysis = {
            "site_category": "SaaS",
            "site_name": "App",
            "needs_login": False,
            "entry_points": [
                {"label": "Low", "path": "/low", "priority": 1},
                {"label": "High", "path": "/high", "priority": 5},
                {"label": "Mid", "path": "/mid", "priority": 3},
            ],
            "key_features_visible": [],
            "exploration_strategy": "",
        }
        result = _format_site_understanding(analysis)
        # High priority should appear first
        high_pos = result.index("High")
        low_pos = result.index("Low") if "Low" in result else float("inf")
        assert high_pos < low_pos


# ── Tool definition ───────────────────────────────────────────────────────────

class TestAnalyzeCurrentPageTool:
    def test_tool_in_tools_list(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert "analyze_current_page" in names

    def test_tool_not_in_terminates_sequence(self):
        assert "analyze_current_page" not in TERMINATES_SEQUENCE

    def test_tool_has_context_param(self):
        tool = next(t for t in TOOLS if t["function"]["name"] == "analyze_current_page")
        props = tool["function"]["parameters"]["properties"]
        assert "context" in props


# ── Tool execution (mocked) ───────────────────────────────────────────��──────

_MOCK_ANALYSIS = {
    "site_category": "E-commerce",
    "site_name": "ShopTest",
    "needs_login": False,
    "entry_points": [{"label": "Products", "path": "/products", "priority": 5}],
    "candidate_feature_pages": [],
    "exploration_strategy": "Browse product catalog",
    "key_features_visible": ["search", "cart"],
    "analyzed_url": "https://shop.test",
}


class TestAnalyzeCurrentPageExecution:
    @pytest.mark.asyncio
    async def test_execute_returns_formatted_result(self):
        from agent.core import BrowserAgent
        from pathlib import Path

        mock_page = AsyncMock()
        mock_page.url = "https://shop.test"
        mock_page.screenshot = AsyncMock(return_value=b"\x00")

        agent = BrowserAgent(mock_page, Path("/tmp/test_screenshots"))

        with patch.object(agent, '_safe_evaluate', return_value="<html></html>"), \
             patch("agent.core._llm_chat"), \
             patch("site_understanding.llm_chat") as mock_llm:
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = json.dumps(_MOCK_ANALYSIS)
            mock_llm.return_value = mock_resp

            result = await agent.execute("analyze_current_page", {"context": "test"})

        assert "ShopTest" in result
        assert "E-commerce" in result
        assert "Products" in result

    @pytest.mark.asyncio
    async def test_execute_uses_cache(self):
        from agent.core import BrowserAgent
        from pathlib import Path

        mock_page = AsyncMock()
        mock_page.url = "https://shop.test"

        agent = BrowserAgent(mock_page, Path("/tmp/test_screenshots"))
        agent._site_analysis = _MOCK_ANALYSIS  # Pre-cache

        # Should not call LLM since cache matches URL
        with patch("site_understanding.llm_chat") as mock_llm:
            result = await agent.execute("analyze_current_page", {})

        mock_llm.assert_not_called()
        assert "ShopTest" in result

    @pytest.mark.asyncio
    async def test_execute_refreshes_on_different_url(self):
        from agent.core import BrowserAgent
        from pathlib import Path

        mock_page = AsyncMock()
        mock_page.url = "https://other.test"  # Different from cached URL
        mock_page.screenshot = AsyncMock(return_value=b"\x00")

        agent = BrowserAgent(mock_page, Path("/tmp/test_screenshots"))
        agent._site_analysis = _MOCK_ANALYSIS  # Cached for shop.test

        new_analysis = {**_MOCK_ANALYSIS, "site_name": "OtherSite", "analyzed_url": "https://other.test"}

        with patch.object(agent, '_safe_evaluate', return_value="<html></html>"), \
             patch("site_understanding.llm_chat") as mock_llm:
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = json.dumps(new_analysis)
            mock_llm.return_value = mock_resp

            result = await agent.execute("analyze_current_page", {})

        mock_llm.assert_called_once()
        assert "OtherSite" in result
