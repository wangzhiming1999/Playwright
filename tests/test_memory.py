"""Tests for agent/memory.py — MemoryManager"""

import json
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure db module uses a temp database
import db as db_mod


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", test_db)
    db_mod.init_memory_db()
    yield test_db


class TestMemoryDB:
    """Test db.py memory CRUD functions."""

    def test_save_and_load_memory(self):
        m = {
            "id": "mem_001",
            "memory_type": "site",
            "domain": "github.com",
            "title": "GitHub 登录流程",
            "content": {"login_flow": ["navigate", "fill email", "fill password"]},
            "source_task_id": "task_abc",
        }
        db_mod.save_memory(m)
        result = db_mod.get_memory("mem_001")
        assert result is not None
        assert result["title"] == "GitHub 登录流程"
        assert result["domain"] == "github.com"
        assert isinstance(result["content"], dict)
        assert result["content"]["login_flow"][0] == "navigate"

    def test_load_memories_filter_by_domain(self):
        db_mod.save_memory({"id": "m1", "memory_type": "site", "domain": "a.com", "title": "A", "content": {}})
        db_mod.save_memory({"id": "m2", "memory_type": "site", "domain": "b.com", "title": "B", "content": {}})
        result = db_mod.load_memories(domain="a.com")
        assert len(result) == 1
        assert result[0]["domain"] == "a.com"

    def test_load_memories_filter_by_type(self):
        db_mod.save_memory({"id": "m1", "memory_type": "site", "domain": "", "title": "A", "content": {}})
        db_mod.save_memory({"id": "m2", "memory_type": "failure", "domain": "", "title": "B", "content": {}})
        result = db_mod.load_memories(memory_type="failure")
        assert len(result) == 1
        assert result[0]["memory_type"] == "failure"

    def test_delete_memory(self):
        db_mod.save_memory({"id": "m1", "memory_type": "site", "domain": "", "title": "A", "content": {}})
        assert db_mod.delete_memory("m1") is True
        assert db_mod.get_memory("m1") is None
        assert db_mod.delete_memory("nonexistent") is False

    def test_batch_delete(self):
        for i in range(5):
            db_mod.save_memory({"id": f"m{i}", "memory_type": "site", "domain": "", "title": f"M{i}", "content": {}})
        count = db_mod.delete_memories_batch(["m0", "m1", "m2"])
        assert count == 3
        assert len(db_mod.load_memories()) == 2

    def test_update_hit_count(self):
        db_mod.save_memory({"id": "m1", "memory_type": "site", "domain": "", "title": "A", "content": {}})
        db_mod.update_memory_hit("m1")
        db_mod.update_memory_hit("m1")
        m = db_mod.get_memory("m1")
        assert m["hit_count"] == 2
        assert m["last_used_at"] is not None

    def test_memory_stats(self):
        db_mod.save_memory({"id": "m1", "memory_type": "site", "domain": "a.com", "title": "A", "content": {}})
        db_mod.save_memory({"id": "m2", "memory_type": "site", "domain": "a.com", "title": "B", "content": {}})
        db_mod.save_memory({"id": "m3", "memory_type": "failure", "domain": "b.com", "title": "C", "content": {}})
        stats = db_mod.get_memory_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["site"] == 2
        assert stats["by_type"]["failure"] == 1
        assert stats["top_domains"][0]["domain"] == "a.com"


class TestMemoryManager:
    """Test agent/memory.py MemoryManager."""

    def test_retrieve_relevant_domain_match(self):
        from agent.memory import MemoryManager
        db_mod.save_memory({
            "id": "m1", "memory_type": "site", "domain": "github.com",
            "title": "GitHub 登录", "content": {"login_flow": ["fill email"]},
        })
        db_mod.save_memory({
            "id": "m2", "memory_type": "site", "domain": "google.com",
            "title": "Google 搜索", "content": {},
        })
        mgr = MemoryManager()
        results = mgr.retrieve_relevant("登录 GitHub", domain="github.com")
        assert len(results) >= 1
        assert results[0]["domain"] == "github.com"

    def test_retrieve_relevant_keyword_match(self):
        from agent.memory import MemoryManager
        db_mod.save_memory({
            "id": "m1", "memory_type": "pattern", "domain": "",
            "title": "搜索操作模式", "content": {"action_sequence": ["type_text", "press_enter"]},
        })
        mgr = MemoryManager()
        results = mgr.retrieve_relevant("在百度搜索 Python 教程")
        assert len(results) >= 1
        assert "搜索" in results[0]["title"]

    def test_retrieve_empty(self):
        from agent.memory import MemoryManager
        mgr = MemoryManager()
        results = mgr.retrieve_relevant("随便一个任务")
        assert results == []

    def test_save_memories_dedup(self):
        from agent.memory import MemoryManager
        mgr = MemoryManager()
        memories = [
            {"id": "m1", "memory_type": "site", "domain": "a.com", "title": "A", "content": {}},
            {"id": "m2", "memory_type": "site", "domain": "a.com", "title": "A", "content": {}},  # dup
        ]
        saved = mgr.save_memories(memories)
        assert len(saved) == 1  # 去重后只保存 1 条

    @patch("agent.memory.llm_chat")
    def test_extract_memories(self, mock_llm):
        from agent.memory import MemoryManager
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = json.dumps({
            "site_memories": [{"title": "测试站点", "content": {"page_hints": "SPA"}}],
            "pattern_memories": [],
            "failure_memories": [],
        })
        mock_llm.return_value = mock_resp

        mgr = MemoryManager()
        result = mgr.extract_memories(
            task_id="t1", task="打开 https://test.com 并登录",
            logs=["navigate({\"url\": \"https://test.com\"})", "click 登录", "done"],
            success=True, domain="test.com",
        )
        assert len(result) == 1
        assert result[0]["memory_type"] == "site"
        assert result[0]["domain"] == "test.com"

    @patch("agent.memory.llm_chat")
    def test_extract_memories_empty_logs(self, mock_llm):
        from agent.memory import MemoryManager
        mgr = MemoryManager()
        result = mgr.extract_memories(task_id="t1", task="test", logs=[], success=True)
        assert result == []
        mock_llm.assert_not_called()


class TestFormatMemories:
    def test_format_memories_for_prompt(self):
        from agent.memory import format_memories_for_prompt
        memories = [
            {"memory_type": "site", "domain": "github.com", "title": "GitHub 登录",
             "content": {"login_flow": ["fill email", "click submit"]}},
            {"memory_type": "failure", "domain": "test.com", "title": "验证码失败",
             "content": {"error_type": "captcha", "solution": "用 ask_user"}},
        ]
        text = format_memories_for_prompt(memories)
        assert "历史经验" in text
        assert "GitHub 登录" in text
        assert "验证码失败" in text
        assert "站点经验" in text
        assert "失败教训" in text

    def test_format_empty(self):
        from agent.memory import format_memories_for_prompt
        assert format_memories_for_prompt([]) == ""
