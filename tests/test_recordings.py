"""Tests for agent/recorder.py and agent/recording_converter.py"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import db as db_mod


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", test_db)
    db_mod.init_recording_db()
    yield test_db


class TestRecordingDB:
    """Test db.py recording CRUD functions."""

    def test_save_and_load_recording(self):
        r = {
            "id": "rec_001",
            "title": "测试录制",
            "start_url": "https://example.com",
            "actions": [
                {"type": "click", "timestamp": 1000, "url": "https://example.com", "selector": "#btn", "text": "按钮", "tag": "button"},
            ],
            "parameters": [{"key": "search_query", "type": "string", "description": "搜索词", "default_value": "test"}],
            "status": "completed",
        }
        db_mod.save_recording(r)
        result = db_mod.get_recording("rec_001")
        assert result is not None
        assert result["title"] == "测试录制"
        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "click"
        assert len(result["parameters"]) == 1

    def test_load_all_recordings(self):
        db_mod.save_recording({"id": "r1", "title": "A", "actions": [], "parameters": [], "status": "completed"})
        db_mod.save_recording({"id": "r2", "title": "B", "actions": [], "parameters": [], "status": "recording"})
        result = db_mod.load_all_recordings()
        assert len(result) == 2

    def test_delete_recording(self):
        db_mod.save_recording({"id": "r1", "title": "A", "actions": [], "parameters": [], "status": "completed"})
        assert db_mod.delete_recording("r1") is True
        assert db_mod.get_recording("r1") is None
        assert db_mod.delete_recording("nonexistent") is False


class TestRecordingConverter:
    """Test agent/recording_converter.py"""

    def test_empty_actions(self):
        from agent.recording_converter import RecordingConverter
        converter = RecordingConverter({"actions": []})
        yaml_str = converter.to_workflow_yaml(title="空录制")
        assert "空录制" in yaml_str

    def test_single_navigate(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "navigate", "timestamp": 1000, "url": "https://example.com", "selector": "", "text": "Example", "tag": ""},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        yaml_str = converter.to_workflow_yaml()
        assert "navigation" in yaml_str
        assert "example.com" in yaml_str

    def test_click_and_type_sequence(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "click", "timestamp": 1000, "url": "https://example.com", "selector": "#search", "text": "搜索框", "tag": "input"},
            {"type": "type_text", "timestamp": 1500, "url": "https://example.com", "selector": "#search", "text": "Python", "tag": "input", "input_type": "text"},
            {"type": "press_key", "timestamp": 2000, "url": "https://example.com", "selector": "#search", "text": "", "tag": "input", "meta": {"key": "Enter"}},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": "https://example.com"})
        yaml_str = converter.to_workflow_yaml()
        assert "task" in yaml_str
        assert "点击" in yaml_str
        assert "输入" in yaml_str

    def test_group_by_url_change(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "click", "timestamp": 1000, "url": "https://a.com/page1", "selector": "#btn", "text": "按钮", "tag": "button"},
            {"type": "navigate", "timestamp": 2000, "url": "https://a.com/page2", "selector": "", "text": "", "tag": ""},
            {"type": "click", "timestamp": 3000, "url": "https://a.com/page2", "selector": "#btn2", "text": "按钮2", "tag": "button"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        groups = converter._group_actions_by_page()
        assert len(groups) == 2

    def test_detect_parameters_search(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "input.search", "text": "Python 教程", "tag": "input", "input_type": "text"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": "https://example.com"})
        params = converter._detect_parameters()
        assert len(params) >= 1
        # 应该检测到输入参数
        input_params = [p for p in params if p["type"] == "string"]
        assert len(input_params) >= 1

    def test_detect_parameters_password(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "#password", "text": "secret123", "tag": "input", "input_type": "password"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        pwd_params = [p for p in params if p["key"] == "password"]
        assert len(pwd_params) == 1

    def test_detect_parameters_email(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "#email", "text": "user@example.com", "tag": "input", "input_type": "email"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        email_params = [p for p in params if p["key"] == "email"]
        assert len(email_params) == 1

    def test_detect_parameters_start_url(self):
        from agent.recording_converter import RecordingConverter
        converter = RecordingConverter({"actions": [], "start_url": "https://example.com"})
        params = converter._detect_parameters()
        url_params = [p for p in params if p["key"] == "start_url"]
        assert len(url_params) == 1
        assert url_params[0]["default_value"] == "https://example.com"

    def test_full_workflow_yaml_structure(self):
        import yaml as yaml_mod
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "navigate", "timestamp": 1000, "url": "https://bing.com", "selector": "", "text": "Bing", "tag": ""},
            {"type": "type_text", "timestamp": 2000, "url": "https://bing.com", "selector": "#search", "text": "AI", "tag": "input", "input_type": "text"},
            {"type": "press_key", "timestamp": 2500, "url": "https://bing.com", "selector": "#search", "text": "", "tag": "input", "meta": {"key": "Enter"}},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": "https://bing.com", "title": "Bing 搜索"})
        yaml_str = converter.to_workflow_yaml()
        wf = yaml_mod.safe_load(yaml_str)
        assert "title" in wf
        assert "blocks" in wf
        assert len(wf["blocks"]) >= 1
        assert "parameters" in wf


class TestActionRecorder:
    """Test agent/recorder.py ActionRecorder (unit tests without browser)."""

    def test_on_action_callback(self):
        from agent.recorder import ActionRecorder
        page = MagicMock()
        recorder = ActionRecorder(page)
        recorder._recording = True

        # Simulate JS callback
        action_json = json.dumps({
            "type": "click", "timestamp": 1000,
            "url": "https://example.com", "selector": "#btn",
            "text": "按钮", "tag": "button",
        })
        recorder._on_action(action_json)

        assert len(recorder._actions) == 1
        assert recorder._actions[0]["type"] == "click"
        assert recorder._actions[0]["text"] == "按钮"

    def test_on_action_invalid_json(self):
        from agent.recorder import ActionRecorder
        page = MagicMock()
        recorder = ActionRecorder(page)
        recorder._on_action("not json")
        assert len(recorder._actions) == 0

    def test_multiple_actions(self):
        from agent.recorder import ActionRecorder
        page = MagicMock()
        recorder = ActionRecorder(page)

        for i in range(5):
            recorder._on_action(json.dumps({
                "type": "click", "timestamp": 1000 + i * 100,
                "url": "https://example.com", "selector": f"#btn{i}",
                "text": f"按钮{i}", "tag": "button",
            }))

        assert len(recorder._actions) == 5


class TestRecorderJSCleanup:
    """Test that RECORDER_JS properly cleans up event listeners."""

    def test_stop_removes_event_listeners(self):
        from agent.recorder import RECORDER_JS
        assert "removeEventListener('click'" in RECORDER_JS
        assert "removeEventListener('input'" in RECORDER_JS
        assert "removeEventListener('change'" in RECORDER_JS
        assert "removeEventListener('keydown'" in RECORDER_JS
        assert "removeEventListener('scroll'" in RECORDER_JS
        assert "removeEventListener('popstate'" in RECORDER_JS

    def test_stop_clears_timers(self):
        from agent.recorder import RECORDER_JS
        assert "clearTimeout(inputTimer)" in RECORDER_JS
        assert "clearTimeout(scrollTimer)" in RECORDER_JS

    def test_stop_restores_history(self):
        from agent.recorder import RECORDER_JS
        assert "history.pushState = origPush" in RECORDER_JS
        assert "history.replaceState = origReplace" in RECORDER_JS


class TestParameterDetectionEnhanced:
    """Test enhanced parameter detection rules."""

    def test_detect_parameters_username(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "input#username", "text": "admin", "tag": "input", "input_type": "text"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        assert any(p["key"] == "username" for p in params)

    def test_detect_parameters_phone(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "input.phone-input", "text": "13800138000", "tag": "input", "input_type": "tel"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        assert any(p["key"] == "phone" for p in params)

    def test_detect_parameters_search_input_type(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "input#q", "text": "hello", "tag": "input", "input_type": "search"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        assert any(p["key"] == "search_query" for p in params)

    def test_detect_parameters_account_selector(self):
        from agent.recording_converter import RecordingConverter
        actions = [
            {"type": "type_text", "timestamp": 1000, "url": "https://example.com",
             "selector": "input[name=\"account\"]", "text": "myuser", "tag": "input", "input_type": "text"},
        ]
        converter = RecordingConverter({"actions": actions, "start_url": ""})
        params = converter._detect_parameters()
        assert any(p["key"] == "username" for p in params)


class TestURLNormalization:
    """Test enhanced URL normalization."""

    def test_strips_tracking_params(self):
        from agent.recording_converter import RecordingConverter
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&q=test"
        result = RecordingConverter._normalize_url(url)
        assert "utm_source" not in result
        assert "q=test" in result

    def test_strips_pagination(self):
        from agent.recording_converter import RecordingConverter
        url1 = "https://example.com/list?page=1"
        url2 = "https://example.com/list?page=2"
        assert RecordingConverter._normalize_url(url1) == RecordingConverter._normalize_url(url2)

    def test_preserves_meaningful_params(self):
        from agent.recording_converter import RecordingConverter
        url = "https://example.com/search?q=python&lang=en"
        result = RecordingConverter._normalize_url(url)
        assert "q=python" in result
        assert "lang=en" in result

    def test_strips_hash(self):
        from agent.recording_converter import RecordingConverter
        url = "https://example.com/page#section1"
        result = RecordingConverter._normalize_url(url)
        assert "#" not in result


class TestRecordingActionEdit:
    """Test recording action delete/update via db."""

    def test_delete_action(self):
        actions = [
            {"type": "click", "text": "A"},
            {"type": "click", "text": "B"},
            {"type": "click", "text": "C"},
        ]
        db_mod.save_recording({"id": "r1", "title": "T", "actions": actions, "parameters": [], "status": "completed"})
        r = db_mod.get_recording("r1")
        r["actions"].pop(1)  # remove B
        db_mod.save_recording(r)
        r2 = db_mod.get_recording("r1")
        assert len(r2["actions"]) == 2
        assert r2["actions"][0]["text"] == "A"
        assert r2["actions"][1]["text"] == "C"

    def test_update_action(self):
        actions = [{"type": "type_text", "text": "old", "selector": "#input"}]
        db_mod.save_recording({"id": "r2", "title": "T", "actions": actions, "parameters": [], "status": "completed"})
        r = db_mod.get_recording("r2")
        r["actions"][0]["text"] = "new"
        db_mod.save_recording(r)
        r2 = db_mod.get_recording("r2")
        assert r2["actions"][0]["text"] == "new"

    def test_recording_timeout_field(self):
        """RecordingStartRequest should accept timeout field."""
        from pydantic import ValidationError
        # 动态导入避免 app 启动副作用
        import importlib
        import sys
        # 简单验证 timeout 字段存在于 RecordingStartRequest
        from app import RecordingStartRequest
        req = RecordingStartRequest(timeout=300)
        assert req.timeout == 300
        # 验证范围
        try:
            RecordingStartRequest(timeout=10)  # < 60
            assert False, "Should reject timeout < 60"
        except ValidationError:
            pass
