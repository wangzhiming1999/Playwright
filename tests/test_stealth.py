"""
浏览器反检测模块 + 模型路由增强 单元测试。
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.stealth import (
    STEALTH_JS, random_fingerprint, get_stealth_fingerprint,
    get_proxy_config, apply_stealth,
    _USER_AGENTS, _VIEWPORTS, _TIMEZONES,
)
from agent.model_router import select_model_tier, is_claude_model, get_claude_prompt_hints


# ── Stealth JS 测试 ──────────────────────────────────────────

class TestStealthJS:
    def test_js_not_empty(self):
        assert len(STEALTH_JS) > 100

    def test_js_hides_webdriver(self):
        assert "webdriver" in STEALTH_JS

    def test_js_fakes_plugins(self):
        assert "plugins" in STEALTH_JS

    def test_js_fakes_languages(self):
        assert "languages" in STEALTH_JS

    def test_js_hides_playwright(self):
        assert "__playwright" in STEALTH_JS

    def test_js_fixes_chrome_runtime(self):
        assert "chrome.runtime" in STEALTH_JS


# ── 指纹随机化测试 ──────────────────────────────────────────

class TestRandomFingerprint:
    def test_returns_dict(self):
        fp = random_fingerprint()
        assert isinstance(fp, dict)

    def test_has_required_keys(self):
        fp = random_fingerprint()
        assert "user_agent" in fp
        assert "viewport" in fp
        assert "locale" in fp
        assert "timezone_id" in fp

    def test_viewport_has_dimensions(self):
        fp = random_fingerprint()
        assert "width" in fp["viewport"]
        assert "height" in fp["viewport"]
        assert fp["viewport"]["width"] > 0
        assert fp["viewport"]["height"] > 0

    def test_user_agent_from_pool(self):
        fp = random_fingerprint()
        assert fp["user_agent"] in _USER_AGENTS

    def test_viewport_from_pool(self):
        fp = random_fingerprint()
        assert fp["viewport"] in _VIEWPORTS

    def test_timezone_from_pool(self):
        fp = random_fingerprint()
        assert fp["timezone_id"] in _TIMEZONES

    def test_randomness(self):
        """多次调用应产生不同结果（概率性，跑10次至少有2种不同UA）。"""
        uas = {random_fingerprint()["user_agent"] for _ in range(20)}
        assert len(uas) >= 2


class TestGetStealthFingerprint:
    def test_default_no_randomize(self, monkeypatch):
        monkeypatch.delenv("BROWSER_RANDOMIZE", raising=False)
        monkeypatch.delenv("BROWSER_USER_AGENT", raising=False)
        monkeypatch.delenv("BROWSER_VIEWPORT", raising=False)
        fp = get_stealth_fingerprint()
        assert fp["viewport"] == {"width": 1920, "height": 1080}
        assert fp["locale"] == "zh-CN"
        assert "user_agent" not in fp  # 默认不设 UA

    def test_randomize_enabled(self, monkeypatch):
        monkeypatch.setenv("BROWSER_RANDOMIZE", "true")
        monkeypatch.delenv("BROWSER_USER_AGENT", raising=False)
        fp = get_stealth_fingerprint()
        assert "user_agent" in fp
        assert fp["user_agent"] in _USER_AGENTS

    def test_env_ua_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_USER_AGENT", "CustomUA/1.0")
        fp = get_stealth_fingerprint()
        assert fp["user_agent"] == "CustomUA/1.0"

    def test_env_viewport_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_VIEWPORT", "800x600")
        fp = get_stealth_fingerprint()
        assert fp["viewport"] == {"width": 800, "height": 600}

    def test_env_viewport_invalid(self, monkeypatch):
        monkeypatch.setenv("BROWSER_VIEWPORT", "invalid")
        fp = get_stealth_fingerprint()
        # 无效值不崩溃，保持默认
        assert "width" in fp["viewport"]

    def test_env_timezone_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_TIMEZONE", "America/Chicago")
        fp = get_stealth_fingerprint()
        assert fp["timezone_id"] == "America/Chicago"

    def test_env_locale_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_LOCALE", "ja-JP")
        fp = get_stealth_fingerprint()
        assert fp["locale"] == "ja-JP"


# ── 代理配置测试 ──────────────────────────────────────────

class TestGetProxyConfig:
    def test_no_proxy(self, monkeypatch):
        monkeypatch.delenv("BROWSER_PROXY", raising=False)
        monkeypatch.delenv("USE_PROXY", raising=False)
        assert get_proxy_config() is None

    def test_browser_proxy_http(self, monkeypatch):
        monkeypatch.setenv("BROWSER_PROXY", "http://proxy.example.com:8080")
        monkeypatch.delenv("USE_PROXY", raising=False)
        config = get_proxy_config()
        assert config["server"] == "http://proxy.example.com:8080"

    def test_browser_proxy_socks5(self, monkeypatch):
        monkeypatch.setenv("BROWSER_PROXY", "socks5://host:1080")
        config = get_proxy_config()
        assert "socks5" in config["server"]

    def test_browser_proxy_with_auth(self, monkeypatch):
        monkeypatch.setenv("BROWSER_PROXY", "http://user:pass@proxy.com:8080")
        config = get_proxy_config()
        assert config["username"] == "user"
        assert config["password"] == "pass"
        assert "@" not in config["server"]

    def test_use_proxy_legacy(self, monkeypatch):
        monkeypatch.delenv("BROWSER_PROXY", raising=False)
        monkeypatch.setenv("USE_PROXY", "1")
        config = get_proxy_config()
        assert config["server"] == "http://127.0.0.1:7897"

    def test_browser_proxy_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("BROWSER_PROXY", "http://custom:9999")
        monkeypatch.setenv("USE_PROXY", "1")
        config = get_proxy_config()
        assert config["server"] == "http://custom:9999"


# ── apply_stealth 测试 ──────────────────────────────────────

class TestApplyStealth:
    @pytest.mark.asyncio
    async def test_adds_init_script(self):
        mock_context = AsyncMock()
        await apply_stealth(mock_context)
        mock_context.add_init_script.assert_called_once_with(STEALTH_JS)


# ── 模型路由增强测试 ──────────────────────────────────────

class TestIsClaudeModel:
    def test_claude_model_string(self):
        assert is_claude_model("claude-sonnet-4-20250514") is True

    def test_gpt_model_string(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        assert is_claude_model("gpt-4o") is False

    def test_empty_string_no_env(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        assert is_claude_model("") is False

    def test_env_model_claude(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "claude-haiku-4-5-20251001")
        assert is_claude_model() is True

    def test_env_backend_anthropic(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_BACKEND", "anthropic")
        assert is_claude_model() is True

    def test_env_backend_openai(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_BACKEND", "openai")
        assert is_claude_model() is False


class TestGetClaudePromptHints:
    def test_returns_string(self):
        hints = get_claude_prompt_hints()
        assert isinstance(hints, str)
        assert len(hints) > 0

    def test_contains_format_guidance(self):
        hints = get_claude_prompt_hints()
        assert "工具" in hints or "tool" in hints.lower()


# ── 数据池完整性测试 ──────────────────────────────────────

class TestDataPools:
    def test_user_agents_not_empty(self):
        assert len(_USER_AGENTS) >= 5

    def test_viewports_not_empty(self):
        assert len(_VIEWPORTS) >= 4

    def test_timezones_not_empty(self):
        assert len(_TIMEZONES) >= 4

    def test_all_user_agents_valid(self):
        for ua in _USER_AGENTS:
            assert "Mozilla" in ua

    def test_all_viewports_valid(self):
        for vp in _VIEWPORTS:
            assert vp["width"] >= 1024
            assert vp["height"] >= 600
