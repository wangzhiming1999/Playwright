"""
Tests for site_understanding.py
- extract_nav_summary
- extract_page_text
- analyze_site (mocked LLM)
- score_page (mocked LLM)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from site_understanding import (
    analyze_site,
    extract_nav_summary,
    extract_page_text,
    score_page,
)


# ── extract_page_text ─────────────────────────────────────────────────────────

class TestExtractPageText:
    def test_strips_tags(self):
        html = "<h1>Hello</h1><p>World</p>"
        result = extract_page_text(html)
        assert "Hello" in result
        assert "World" in result
        assert "<" not in result

    def test_removes_scripts(self):
        html = "<script>var x = 1;</script><p>Content</p>"
        result = extract_page_text(html)
        assert "var x" not in result
        assert "Content" in result

    def test_removes_styles(self):
        html = "<style>.foo { color: red; }</style><p>Text</p>"
        result = extract_page_text(html)
        assert "color" not in result
        assert "Text" in result

    def test_truncates_to_max_chars(self):
        html = "<p>" + "a" * 5000 + "</p>"
        result = extract_page_text(html, max_chars=100)
        assert len(result) <= 100

    def test_empty_html(self):
        assert extract_page_text("") == ""

    def test_collapses_whitespace(self):
        html = "<p>  too   many   spaces  </p>"
        result = extract_page_text(html)
        assert "  " not in result


# ── extract_nav_summary ───────────────────────────────────────────────────────

class TestExtractNavSummary:
    def test_extracts_nav_text(self):
        html = "<nav><a href='/dashboard'>Dashboard</a><a href='/reports'>Reports</a></nav>"
        result = extract_nav_summary(html)
        assert "Dashboard" in result
        assert "Reports" in result

    def test_extracts_links(self):
        html = '<a href="/features">Features</a><a href="/pricing">Pricing</a>'
        result = extract_nav_summary(html)
        assert "Features" in result
        assert "Pricing" in result

    def test_skips_javascript_links(self):
        html = '<a href="javascript:void(0)">Click</a><a href="/real">Real</a>'
        result = extract_nav_summary(html)
        assert "javascript" not in result

    def test_skips_mailto_links(self):
        html = '<a href="mailto:hi@example.com">Email</a><a href="/page">Page</a>'
        result = extract_nav_summary(html)
        assert "mailto" not in result

    def test_empty_html(self):
        result = extract_nav_summary("")
        assert isinstance(result, str)


# ── analyze_site (mocked) ─────────────────────────────────────────────────────

_MOCK_SITE = {
    "site_category": "B2B SaaS",
    "site_name": "Acme Analytics",
    "needs_login": True,
    "entry_points": [
        {"label": "Dashboard", "path": "/dashboard", "priority": 5},
        {"label": "Reports", "path": "/reports", "priority": 4},
    ],
    "candidate_feature_pages": [
        {"path": "/dashboard", "label": "Dashboard", "marketing_score": 9.0,
         "reason": "core product UI", "page_type": "dashboard"},
        {"path": "/settings", "label": "Settings", "marketing_score": 2.0,
         "reason": "not marketing worthy", "page_type": "settings"},
    ],
    "exploration_strategy": "Start with dashboard, then reports",
    "key_features_visible": ["real-time charts", "export"],
}


def _mock_llm_response(response_json):
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(response_json)
    return mock_resp


class TestAnalyzeSite:
    def test_returns_structured_result(self):
        with patch("site_understanding.llm_chat", return_value=_mock_llm_response(_MOCK_SITE)):
            result = analyze_site("https://example.com", "<html><nav>Dashboard</nav></html>")

        assert result["site_category"] == "B2B SaaS"
        assert result["site_name"] == "Acme Analytics"
        assert result["analyzed_url"] == "https://example.com"
        assert len(result["candidate_feature_pages"]) == 2

    def test_attaches_analyzed_url(self):
        with patch("site_understanding.llm_chat", return_value=_mock_llm_response(_MOCK_SITE)):
            result = analyze_site("https://myapp.io", "<html></html>")

        assert result["analyzed_url"] == "https://myapp.io"

    def test_handles_json_parse_error(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not json"
        with patch("site_understanding.llm_chat", return_value=mock_resp):
            result = analyze_site("https://example.com", "<html></html>")

        assert result["site_category"] == "unknown"
        assert result["entry_points"] == []
        assert "_parse_error" in result

    def test_strips_markdown_fences(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = f"```json\n{json.dumps(_MOCK_SITE)}\n```"
        with patch("site_understanding.llm_chat", return_value=mock_resp):
            result = analyze_site("https://example.com", "<html></html>")

        assert result["site_category"] == "B2B SaaS"

    def test_accepts_product_context(self):
        captured = {}

        def fake_llm_chat(**kwargs):
            captured["messages"] = kwargs["messages"]
            return _mock_llm_response(_MOCK_SITE)

        with patch("site_understanding.llm_chat", side_effect=fake_llm_chat):
            analyze_site("https://example.com", "<html></html>",
                         product_context="AI analytics for e-commerce")

        prompt_text = captured["messages"][0]["content"][0]["text"]
        assert "AI analytics for e-commerce" in prompt_text


# ── score_page (mocked) ───────────────────────────────────────────────────────

_MOCK_PAGE_SCORE = {
    "marketing_score": 7.5,
    "page_type": "analytics",
    "is_worth_screenshot": True,
    "recommended_regions": [".chart-panel", ".kpi-cards"],
    "reason": "Shows key metrics clearly",
}


class TestScorePage:
    def test_returns_score_dict(self):
        with patch("site_understanding.llm_chat",
                   return_value=_mock_llm_response(_MOCK_PAGE_SCORE)):
            result = score_page("https://app.io/dashboard", "<html></html>", "base64img")

        assert result["marketing_score"] == 7.5
        assert result["is_worth_screenshot"] is True
        assert result["page_type"] == "analytics"

    def test_handles_parse_error(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "bad"
        with patch("site_understanding.llm_chat", return_value=mock_resp):
            result = score_page("https://x.com", "<html></html>", "img")

        assert result["marketing_score"] == 0
        assert result["is_worth_screenshot"] is False

    def test_low_score_page_not_worth_screenshot(self):
        low = {**_MOCK_PAGE_SCORE, "marketing_score": 2.0, "is_worth_screenshot": False}
        with patch("site_understanding.llm_chat", return_value=_mock_llm_response(low)):
            result = score_page("https://app.io/settings", "<html></html>", "img")

        assert result["is_worth_screenshot"] is False
