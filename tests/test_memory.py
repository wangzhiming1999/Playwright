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
            {"id": "m1", "memory_type": "site", "domain": "a.com", "title": "A", "content": {"k": "v1"}},
            {"id": "m2", "memory_type": "site", "domain": "a.com", "title": "A", "content": {"k": "v2"}},  # dup → merge
        ]
        saved = mgr.save_memories(memories)
        assert len(saved) == 2  # 两次都返回 id（第二次是合并更新）
        # 数据库中只有 1 条
        all_mems = db_mod.load_memories()
        assert len(all_mems) == 1
        # content 被合并（第二次覆盖第一次的 k）
        assert all_mems[0]["content"]["k"] == "v2"

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


class TestTokenize:
    """Test improved _tokenize with bigram and stop words."""

    def test_chinese_bigram(self):
        from agent.memory import _tokenize
        tokens = _tokenize("搜索引擎")
        assert "搜索" in tokens
        assert "索引" in tokens
        assert "引擎" in tokens
        # 单字也应该在（非停用词）
        assert "搜" in tokens

    def test_stop_words_filtered(self):
        from agent.memory import _tokenize
        tokens = _tokenize("我的搜索")
        assert "我" not in tokens
        assert "搜索" in tokens

    def test_english_words(self):
        from agent.memory import _tokenize
        tokens = _tokenize("Login to GitHub")
        assert "login" in tokens
        assert "github" in tokens
        # 单字母 "to" 长度 2 应该在
        assert "to" in tokens

    def test_mixed_text(self):
        from agent.memory import _tokenize
        tokens = _tokenize("在 GitHub 上搜索代码")
        assert "github" in tokens
        assert "搜索" in tokens
        assert "代码" in tokens


class TestScoreMemoryEnhanced:
    """Test improved _score_memory with bigram weights and conditional failure bonus."""

    def test_subdomain_bidirectional(self):
        from agent.memory import MemoryManager, _tokenize
        mgr = MemoryManager()
        mem = {"domain": "github.com", "title": "test", "content": {}, "memory_type": "site", "hit_count": 0}
        task_words = _tokenize("test")
        # api.github.com should match github.com
        score = mgr._score_memory(mem, task_words, "api.github.com")
        assert score >= 5.0

    def test_subdomain_reverse(self):
        from agent.memory import MemoryManager, _tokenize
        mgr = MemoryManager()
        mem = {"domain": "api.github.com", "title": "test", "content": {}, "memory_type": "site", "hit_count": 0}
        task_words = _tokenize("test")
        # github.com should match api.github.com
        score = mgr._score_memory(mem, task_words, "github.com")
        assert score >= 5.0

    def test_failure_conditional_bonus(self):
        from agent.memory import MemoryManager, _tokenize
        mgr = MemoryManager()
        mem = {"domain": "", "title": "登录失败", "content": {}, "memory_type": "failure", "hit_count": 0}
        # 任务含失败关键词 → 加分
        task_words_fail = _tokenize("登录失败怎么办")
        score_fail = mgr._score_memory(mem, task_words_fail, "")
        # 任务不含失败关键词 → 不加分
        task_words_ok = _tokenize("登录 GitHub")
        score_ok = mgr._score_memory(mem, task_words_ok, "")
        assert score_fail > score_ok

    def test_bigram_higher_weight(self):
        from agent.memory import MemoryManager, _tokenize
        mgr = MemoryManager()
        mem = {"domain": "", "title": "搜索操作模式", "content": {}, "memory_type": "pattern", "hit_count": 0}
        # "搜索" bigram 匹配应该比单字匹配得分高
        task_words = _tokenize("在百度搜索 Python 教程")
        score = mgr._score_memory(mem, task_words, "")
        assert score > 0


class TestSaveMemoriesMerge:
    """Test that save_memories merges content instead of skipping."""

    def test_merge_update(self):
        from agent.memory import MemoryManager
        mgr = MemoryManager()
        # 第一次保存
        mem1 = [{"id": "m1", "memory_type": "site", "domain": "a.com", "title": "A",
                 "content": {"login_flow": ["step1"]}, "source_task_id": "t1"}]
        mgr.save_memories(mem1)
        # 第二次保存同 key，不同 content
        mem2 = [{"id": "m2", "memory_type": "site", "domain": "a.com", "title": "A",
                 "content": {"login_flow": ["step1", "step2"], "page_hints": "SPA"}, "source_task_id": "t2"}]
        saved = mgr.save_memories(mem2)
        assert len(saved) == 1
        # 验证 content 被合并
        m = db_mod.get_memory(saved[0])
        assert "page_hints" in m["content"]
        assert m["content"]["login_flow"] == ["step1", "step2"]


class TestExtractMemoriesRetry:
    """Test LLM JSON parse retry in extract_memories."""

    @patch("agent.memory.llm_chat")
    def test_json_retry_success(self, mock_llm):
        from agent.memory import MemoryManager
        # 第一次返回无效 JSON，第二次返回有效 JSON
        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = "not json {"

        good_resp = MagicMock()
        good_resp.choices = [MagicMock()]
        good_resp.choices[0].message.content = json.dumps({
            "site_memories": [{"title": "Test", "content": {"page_hints": "ok"}}],
            "pattern_memories": [], "failure_memories": [],
        })
        mock_llm.side_effect = [bad_resp, good_resp]

        mgr = MemoryManager()
        result = mgr.extract_memories(
            task_id="t1", task="test https://a.com",
            logs=["navigate({\"url\": \"https://a.com\"})", "done"],
            success=True, domain="a.com",
        )
        assert len(result) == 1
        assert mock_llm.call_count == 2


class TestFormatMemoriesTruncation:
    """Test format_memories_for_prompt with max_chars limit."""

    def test_truncation(self):
        from agent.memory import format_memories_for_prompt
        memories = [
            {"memory_type": "site", "domain": f"site{i}.com", "title": f"记忆标题{i}",
             "content": {"data": "x" * 100}}
            for i in range(20)
        ]
        text = format_memories_for_prompt(memories, max_chars=500)
        assert len(text) <= 600  # 允许最后一行截断提示略超
        assert "被截断" in text

    def test_no_truncation_small(self):
        from agent.memory import format_memories_for_prompt
        memories = [
            {"memory_type": "site", "domain": "a.com", "title": "短标题", "content": {"k": "v"}},
        ]
        text = format_memories_for_prompt(memories, max_chars=2000)
        assert "被截断" not in text


class TestMemoryPaged:
    """Test load_memories_paged in db.py."""

    def test_paged_basic(self):
        for i in range(10):
            db_mod.save_memory({"id": f"mp{i}", "memory_type": "site", "domain": "", "title": f"M{i}", "content": {}})
        result = db_mod.load_memories_paged(page=1, page_size=3)
        assert result["total"] == 10
        assert len(result["items"]) == 3
        assert result["page"] == 1
        assert result["page_size"] == 3

    def test_paged_with_filter(self):
        db_mod.save_memory({"id": "pf1", "memory_type": "site", "domain": "a.com", "title": "A", "content": {}})
        db_mod.save_memory({"id": "pf2", "memory_type": "failure", "domain": "a.com", "title": "B", "content": {}})
        db_mod.save_memory({"id": "pf3", "memory_type": "site", "domain": "b.com", "title": "C", "content": {}})
        result = db_mod.load_memories_paged(domain="a.com", page=1, page_size=50)
        assert result["total"] == 2
        assert len(result["items"]) == 2

    def test_paged_page2(self):
        for i in range(5):
            db_mod.save_memory({"id": f"p2_{i}", "memory_type": "site", "domain": "", "title": f"P{i}", "content": {}})
        result = db_mod.load_memories_paged(page=2, page_size=2)
        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["page"] == 2
