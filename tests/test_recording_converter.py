"""
录制→工作流转换 单元测试 — 测试动作清洗、参数检测、YAML 生成。
"""

import pytest
import yaml
from agent.recording_converter import RecordingConverter


# ── 动作清洗测试 ──────────────────────────────────────────

class TestCleanActions:
    def test_empty_actions(self):
        c = RecordingConverter({"actions": []})
        assert c.clean_actions() == []

    def test_filter_empty_type_text(self):
        actions = [
            {"type": "type_text", "text": "", "selector": "#input"},
            {"type": "type_text", "text": "hello", "selector": "#input"},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 1
        assert cleaned[0]["text"] == "hello"

    def test_filter_empty_click(self):
        actions = [
            {"type": "click", "selector": "", "text": ""},
            {"type": "click", "selector": "#btn", "text": "Submit"},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 1
        assert cleaned[0]["text"] == "Submit"

    def test_merge_consecutive_inputs(self):
        """同一输入框的连续输入只保留最后一次。"""
        actions = [
            {"type": "type_text", "text": "h", "selector": "#search"},
            {"type": "type_text", "text": "he", "selector": "#search"},
            {"type": "type_text", "text": "hello", "selector": "#search"},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 1
        assert cleaned[0]["text"] == "hello"

    def test_no_merge_different_inputs(self):
        """不同输入框不合并。"""
        actions = [
            {"type": "type_text", "text": "user", "selector": "#username"},
            {"type": "type_text", "text": "pass", "selector": "#password"},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 2

    def test_dedup_rapid_clicks(self):
        """500ms 内同一元素的重复 click 去重。"""
        actions = [
            {"type": "click", "selector": "#btn", "text": "OK", "timestamp": 1000},
            {"type": "click", "selector": "#btn", "text": "OK", "timestamp": 1200},
            {"type": "click", "selector": "#btn", "text": "OK", "timestamp": 1400},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 1

    def test_keep_slow_clicks(self):
        """间隔 > 500ms 的 click 保留。"""
        actions = [
            {"type": "click", "selector": "#btn", "text": "OK", "timestamp": 1000},
            {"type": "click", "selector": "#btn", "text": "OK", "timestamp": 2000},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 2

    def test_merge_scrolls(self):
        """连续同方向 scroll 合并。"""
        actions = [
            {"type": "scroll", "meta": {"direction": "down", "amount": 300}},
            {"type": "scroll", "meta": {"direction": "down", "amount": 400}},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 1
        assert cleaned[0]["meta"]["amount"] == 700

    def test_no_merge_different_direction_scrolls(self):
        actions = [
            {"type": "scroll", "meta": {"direction": "down", "amount": 300}},
            {"type": "scroll", "meta": {"direction": "up", "amount": 200}},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        assert len(cleaned) == 2

    def test_mixed_actions_cleaning(self):
        """混合操作的完整清洗流程。"""
        actions = [
            {"type": "click", "selector": "#search", "text": "搜索框", "timestamp": 1000},
            {"type": "type_text", "text": "p", "selector": "#search"},
            {"type": "type_text", "text": "py", "selector": "#search"},
            {"type": "type_text", "text": "python", "selector": "#search"},
            {"type": "click", "selector": "#btn", "text": "搜索", "timestamp": 3000},
            {"type": "click", "selector": "#btn", "text": "搜索", "timestamp": 3100},
            {"type": "scroll", "meta": {"direction": "down", "amount": 200}},
            {"type": "scroll", "meta": {"direction": "down", "amount": 300}},
        ]
        c = RecordingConverter({"actions": actions})
        cleaned = c.clean_actions()
        # click搜索框 + type_text(python) + click搜索 + scroll(500)
        assert len(cleaned) == 4
        assert cleaned[1]["text"] == "python"
        assert cleaned[3]["meta"]["amount"] == 500


# ── 参数检测测试 ──────────────────────────────────────────

class TestDetectParameters:
    def test_detect_password(self):
        actions = [{"type": "type_text", "text": "secret123", "input_type": "password", "selector": "#pwd"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"] == "password" for p in params)

    def test_detect_email(self):
        actions = [{"type": "type_text", "text": "test@example.com", "input_type": "email", "selector": "#email"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"] == "email" for p in params)

    def test_detect_email_by_at_sign(self):
        actions = [{"type": "type_text", "text": "user@domain.com", "input_type": "text", "selector": "#field"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"] == "email" for p in params)

    def test_detect_username(self):
        actions = [{"type": "type_text", "text": "admin", "selector": "input[name=\"username\"]"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"] == "username" for p in params)

    def test_detect_search_query(self):
        actions = [{"type": "type_text", "text": "python tutorial", "input_type": "search", "selector": "#q"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"] == "search_query" for p in params)

    def test_detect_start_url(self):
        c = RecordingConverter({"actions": [{"type": "click", "text": "ok"}], "start_url": "https://example.com"})
        params = c._detect_parameters()
        assert any(p["key"] == "start_url" for p in params)

    def test_no_duplicate_keys(self):
        actions = [
            {"type": "type_text", "text": "pass1", "input_type": "password", "selector": "#p1"},
            {"type": "type_text", "text": "pass2", "input_type": "password", "selector": "#p2"},
        ]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        keys = [p["key"] for p in params]
        assert keys.count("password") == 1  # 去重

    def test_generic_input_fallback(self):
        actions = [{"type": "type_text", "text": "some value", "selector": "#field", "input_type": "text"}]
        c = RecordingConverter({"actions": actions})
        params = c._detect_parameters()
        assert any(p["key"].startswith("input_") for p in params)


# ── YAML 生成测试 ──────────────────────────────────────────

class TestToWorkflowYaml:
    def test_empty_recording(self):
        c = RecordingConverter({"actions": []})
        result = c.to_workflow_yaml(title="Empty")
        data = yaml.safe_load(result)
        assert data["title"] == "Empty"
        assert data["blocks"] == []

    def test_navigate_only(self):
        actions = [{"type": "navigate", "url": "https://example.com", "text": "Example"}]
        c = RecordingConverter({"actions": actions})
        result = c.to_workflow_yaml()
        data = yaml.safe_load(result)
        assert len(data["blocks"]) == 1
        assert data["blocks"][0]["block_type"] == "navigation"
        assert "example.com" in data["blocks"][0]["url"]

    def test_task_block_generated(self):
        actions = [
            {"type": "click", "text": "Login", "selector": "#login", "url": "https://a.com"},
            {"type": "type_text", "text": "admin", "selector": "#user", "url": "https://a.com"},
        ]
        c = RecordingConverter({"actions": actions})
        result = c.to_workflow_yaml()
        data = yaml.safe_load(result)
        assert len(data["blocks"]) == 1
        assert data["blocks"][0]["block_type"] == "task"
        assert "Login" in data["blocks"][0]["task"]

    def test_url_parameterized(self):
        recording = {
            "actions": [{"type": "navigate", "url": "https://example.com", "text": ""}],
            "start_url": "https://example.com",
        }
        c = RecordingConverter(recording)
        result = c.to_workflow_yaml()
        data = yaml.safe_load(result)
        # start_url 应该被参数化
        assert any(p["key"] == "start_url" for p in data.get("parameters", []))

    def test_password_parameterized(self):
        actions = [
            {"type": "type_text", "text": "secret", "input_type": "password", "selector": "#pwd", "url": "https://a.com"},
        ]
        c = RecordingConverter({"actions": actions})
        result = c.to_workflow_yaml()
        data = yaml.safe_load(result)
        assert any(p["key"] == "password" for p in data.get("parameters", []))
        # task 描述中不应该出现明文密码
        for block in data.get("blocks", []):
            if block.get("task"):
                assert "secret" not in block["task"]

    def test_custom_title(self):
        actions = [{"type": "click", "text": "OK", "selector": "#ok", "url": "https://a.com"}]
        c = RecordingConverter({"actions": actions})
        result = c.to_workflow_yaml(title="My Workflow")
        data = yaml.safe_load(result)
        assert data["title"] == "My Workflow"

    def test_clean_false_preserves_raw(self):
        """clean=False 不清洗，保留原始操作。"""
        actions = [
            {"type": "type_text", "text": "a", "selector": "#s"},
            {"type": "type_text", "text": "ab", "selector": "#s"},
            {"type": "type_text", "text": "abc", "selector": "#s"},
        ]
        c = RecordingConverter({"actions": actions})
        result_clean = c.to_workflow_yaml(clean=True)
        result_raw = c.to_workflow_yaml(clean=False)
        data_clean = yaml.safe_load(result_clean)
        data_raw = yaml.safe_load(result_raw)
        # 清洗后只有 1 步，原始有 3 步
        assert "1 步" in data_clean["description"]
        assert "3 步" in data_raw["description"]

    def test_multi_page_groups(self):
        """跨页面操作应分成多个 block。"""
        actions = [
            {"type": "click", "text": "Link", "selector": "#link", "url": "https://a.com"},
            {"type": "navigate", "url": "https://b.com", "text": "Page B"},
            {"type": "click", "text": "Button", "selector": "#btn", "url": "https://b.com"},
        ]
        c = RecordingConverter({"actions": actions})
        result = c.to_workflow_yaml()
        data = yaml.safe_load(result)
        assert len(data["blocks"]) >= 2


# ── URL 规范化测试 ──────────────────────────────────────────

class TestNormalizeUrl:
    def test_strip_utm(self):
        url = "https://example.com/page?utm_source=google&q=test"
        result = RecordingConverter._normalize_url(url)
        assert "utm_source" not in result
        assert "q=test" in result

    def test_strip_fbclid(self):
        url = "https://example.com/?fbclid=abc123&id=1"
        result = RecordingConverter._normalize_url(url)
        assert "fbclid" not in result
        assert "id=1" in result

    def test_no_query(self):
        url = "https://example.com/path"
        result = RecordingConverter._normalize_url(url)
        assert result == "https://example.com/path"

    def test_invalid_url(self):
        result = RecordingConverter._normalize_url("not a url")
        # 无效 URL 不崩溃即可
        assert isinstance(result, str)


# ── 分组测试 ──────────────────────────────────────────

class TestGroupActions:
    def test_single_page(self):
        actions = [
            {"type": "click", "text": "A", "url": "https://a.com"},
            {"type": "click", "text": "B", "url": "https://a.com"},
        ]
        c = RecordingConverter({"actions": actions})
        groups = c._group_actions_by_page()
        assert len(groups) == 1

    def test_navigate_splits_group(self):
        actions = [
            {"type": "click", "text": "A", "url": "https://a.com"},
            {"type": "navigate", "url": "https://b.com", "text": ""},
            {"type": "click", "text": "B", "url": "https://b.com"},
        ]
        c = RecordingConverter({"actions": actions})
        groups = c._group_actions_by_page()
        assert len(groups) == 2

    def test_url_change_splits_group(self):
        actions = [
            {"type": "click", "text": "A", "url": "https://a.com"},
            {"type": "click", "text": "B", "url": "https://b.com"},
        ]
        c = RecordingConverter({"actions": actions})
        groups = c._group_actions_by_page()
        assert len(groups) == 2

    def test_empty_actions(self):
        c = RecordingConverter({"actions": []})
        assert c._group_actions_by_page() == []
