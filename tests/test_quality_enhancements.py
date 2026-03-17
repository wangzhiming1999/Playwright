"""
Tests for index validation and page annotator enhancements.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

import page_annotator


class TestGetLastElements:
    def test_returns_empty_initially(self):
        # Reset state
        page_annotator._last_elements = []
        assert page_annotator.get_last_elements() == []

    def test_returns_cached_elements(self):
        fake_elements = [
            {"index": 0, "tag": "button", "text": "OK"},
            {"index": 1, "tag": "input", "type": "text"},
            {"index": 2, "tag": "a", "text": "Link"},
        ]
        page_annotator._last_elements = fake_elements
        result = page_annotator.get_last_elements()
        assert len(result) == 3
        assert result[0]["tag"] == "button"
        assert result[2]["index"] == 2

    def test_returns_same_reference(self):
        fake = [{"index": 0}]
        page_annotator._last_elements = fake
        assert page_annotator.get_last_elements() is fake


class TestValidateIndex:
    """Test BrowserAgent._validate_index via direct instantiation."""

    def _make_agent(self):
        from agent.core import BrowserAgent
        page = MagicMock()
        agent = BrowserAgent(
            page=page,
            screenshots_dir=Path("/tmp/test_screenshots"),
            log_fn=None,
        )
        return agent

    def test_valid_index(self):
        page_annotator._last_elements = [
            {"index": 0}, {"index": 1}, {"index": 5},
        ]
        agent = self._make_agent()
        assert agent._validate_index(0) is None
        assert agent._validate_index(3) is None
        assert agent._validate_index(5) is None

    def test_invalid_index_too_high(self):
        page_annotator._last_elements = [
            {"index": 0}, {"index": 1}, {"index": 5},
        ]
        agent = self._make_agent()
        err = agent._validate_index(10)
        assert err is not None
        assert "index=10" in err
        assert "0~5" in err

    def test_invalid_index_negative(self):
        page_annotator._last_elements = [
            {"index": 0}, {"index": 1},
        ]
        agent = self._make_agent()
        err = agent._validate_index(-1)
        assert err is not None
        assert "index=-1" in err

    def test_no_elements_skips_validation(self):
        page_annotator._last_elements = []
        agent = self._make_agent()
        assert agent._validate_index(999) is None

    def test_boundary_index(self):
        page_annotator._last_elements = [
            {"index": 0}, {"index": 10},
        ]
        agent = self._make_agent()
        assert agent._validate_index(10) is None
        err = agent._validate_index(11)
        assert err is not None


class TestWrappedResponseUsage:
    """Test that _WrappedResponse correctly extracts usage info."""

    def test_usage_default_empty(self):
        from utils import _WrappedResponse
        resp = _WrappedResponse(choices=[])
        assert resp.usage == {}

    def test_usage_passed_through(self):
        from utils import _WrappedResponse
        usage = {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 20}
        resp = _WrappedResponse(choices=[], usage=usage)
        assert resp.usage["input_tokens"] == 100
        assert resp.usage["cached_tokens"] == 20
