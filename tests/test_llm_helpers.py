"""
Tests for agent/llm_helpers.py — pure logic functions (no browser/LLM needed).
"""

import json
import pytest

from agent.llm_helpers import (
    estimate_message_tokens,
    estimate_messages_tokens,
    robust_json_loads,
    _filter_decorative,
    _merge_similar_siblings,
    trim_elements,
    _match_failure_pattern,
    _CHARS_PER_TEXT_TOKEN,
    _IMG_TOKENS_HIGH,
    _IMG_TOKENS_LOW,
)


# ── estimate_message_tokens ──────────────────────────────────────────────────


class TestEstimateMessageTokens:
    def test_text_only(self):
        msg = {"role": "user", "content": "hello world"}  # 11 chars
        tokens = estimate_message_tokens(msg)
        assert tokens == 4 + max(1, 11 // _CHARS_PER_TEXT_TOKEN)

    def test_empty_content(self):
        msg = {"role": "user", "content": ""}
        tokens = estimate_message_tokens(msg)
        # empty string: len=0, max(1, 0//3) = 1
        assert tokens == 4 + 1

    def test_no_content_key(self):
        msg = {"role": "assistant"}
        tokens = estimate_message_tokens(msg)
        # content="" default, max(1, 0) = 1
        assert tokens == 4 + 1

    def test_image_url_high_detail(self):
        msg = {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "high"}},
        ]}
        tokens = estimate_message_tokens(msg)
        assert tokens == 4 + _IMG_TOKENS_HIGH

    def test_image_url_low_detail(self):
        msg = {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "low"}},
        ]}
        tokens = estimate_message_tokens(msg)
        assert tokens == 4 + _IMG_TOKENS_LOW

    def test_image_url_default_detail_is_high(self):
        msg = {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]}
        tokens = estimate_message_tokens(msg)
        assert tokens == 4 + _IMG_TOKENS_HIGH

    def test_mixed_content_list(self):
        msg = {"role": "user", "content": [
            {"type": "text", "text": "describe this image"},
            {"type": "image_url", "image_url": {"url": "x", "detail": "low"}},
        ]}
        tokens = estimate_message_tokens(msg)
        text_tokens = max(1, len("describe this image") // _CHARS_PER_TEXT_TOKEN)
        assert tokens == 4 + text_tokens + _IMG_TOKENS_LOW

    def test_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "click", "arguments": '{"index": 5}'},
            }],
        }
        tokens = estimate_message_tokens(msg)
        name_tokens = max(1, len("click") // _CHARS_PER_TEXT_TOKEN)
        args_tokens = max(1, len('{"index": 5}') // _CHARS_PER_TEXT_TOKEN)
        # 4 base + 1 (empty content) + name + args + 10 overhead
        assert tokens == 4 + 1 + name_tokens + args_tokens + 10

    def test_content_list_skips_non_dict(self):
        msg = {"role": "user", "content": ["plain string", 42]}
        tokens = estimate_message_tokens(msg)
        assert tokens == 4  # non-dict blocks are skipped

    def test_long_text(self):
        text = "a" * 3000
        msg = {"role": "user", "content": text}
        tokens = estimate_message_tokens(msg)
        assert tokens == 4 + 3000 // _CHARS_PER_TEXT_TOKEN


class TestEstimateMessagesTokens:
    def test_empty_list(self):
        assert estimate_messages_tokens([]) == 0

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        total = estimate_messages_tokens(msgs)
        expected = estimate_message_tokens(msgs[0]) + estimate_message_tokens(msgs[1])
        assert total == expected


# ── robust_json_loads ────────────────────────────────────────────────────────


class TestRobustJsonLoads:
    def test_valid_json_dict(self):
        assert robust_json_loads('{"a": 1}') == {"a": 1}

    def test_valid_json_list(self):
        assert robust_json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_markdown_code_block(self):
        raw = '```json\n{"key": "value"}\n```'
        assert robust_json_loads(raw) == {"key": "value"}

    def test_markdown_code_block_no_lang(self):
        raw = '```\n{"key": "value"}\n```'
        assert robust_json_loads(raw) == {"key": "value"}

    def test_trailing_comma_repaired(self):
        raw = '{"a": 1, "b": 2,}'
        result = robust_json_loads(raw)
        assert result["a"] == 1
        assert result["b"] == 2

    def test_truncated_json_bracket_repair(self):
        raw = '{"items": [{"name": "a"}, {"name": "b"'
        result = robust_json_loads(raw)
        assert isinstance(result, dict)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="空字符串"):
            robust_json_loads("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="空字符串"):
            robust_json_loads("   \n  ")

    def test_unicode_content(self):
        raw = '{"名称": "测试", "描述": "中文内容"}'
        result = robust_json_loads(raw)
        assert result["名称"] == "测试"

    def test_completely_invalid_raises(self):
        with pytest.raises(ValueError):
            robust_json_loads("this is not json at all !!!")


# ── _filter_decorative ───────────────────────────────────────────────────────


class TestFilterDecorative:
    def test_filters_decorative(self):
        elements = [
            {"index": 0, "tag": "div", "text": "hello"},
            {"index": 1, "tag": "svg", "is_decorative": True},
            {"index": 2, "tag": "button", "text": "click"},
        ]
        result = _filter_decorative(elements)
        assert len(result) == 2
        assert result[0]["index"] == 0
        assert result[1]["index"] == 2

    def test_keeps_all_when_none_decorative(self):
        elements = [{"index": 0, "tag": "a"}, {"index": 1, "tag": "button"}]
        assert len(_filter_decorative(elements)) == 2

    def test_empty_list(self):
        assert _filter_decorative([]) == []


# ── _merge_similar_siblings ──────────────────────────────────────────────────


class TestMergeSimilarSiblings:
    def test_no_merge_when_small(self):
        elements = [{"tag": "li", "text": str(i)} for i in range(3)]
        result = _merge_similar_siblings(elements, max_group=5)
        assert len(result) == 3

    def test_merges_consecutive_li_beyond_threshold(self):
        elements = [{"tag": "li", "text": str(i), "index": i} for i in range(10)]
        result = _merge_similar_siblings(elements, max_group=3)
        # 3 kept + 1 placeholder
        assert len(result) == 4
        assert "还有 7 个" in result[-1]["text"]

    def test_non_list_tags_pass_through(self):
        elements = [{"tag": "div", "text": str(i)} for i in range(10)]
        result = _merge_similar_siblings(elements, max_group=3)
        assert len(result) == 10  # div is not in merge list

    def test_mixed_tag_sequences(self):
        elements = [
            {"tag": "li", "text": "a", "index": 0},
            {"tag": "li", "text": "b", "index": 1},
            {"tag": "li", "text": "c", "index": 2},
            {"tag": "div", "text": "d", "index": 3},
            {"tag": "a", "text": "e", "index": 4},
            {"tag": "a", "text": "f", "index": 5},
        ]
        result = _merge_similar_siblings(elements, max_group=5)
        # 3 li + 1 div + 2 a = 6, all under threshold
        assert len(result) == 6

    def test_empty_list(self):
        assert _merge_similar_siblings([]) == []

    def test_option_tags_merged(self):
        elements = [{"tag": "option", "text": str(i), "index": i} for i in range(8)]
        result = _merge_similar_siblings(elements, max_group=3)
        assert len(result) == 4
        assert "还有 5 个" in result[-1]["text"]


# ── trim_elements ────────────────────────────────────────────────────────────


class TestTrimElements:
    def test_empty_returns_brackets(self):
        assert trim_elements([]) == "[]"

    def test_small_list_returns_compact(self):
        elements = [
            {"index": 0, "tag": "button", "text": "OK", "type": "submit"},
        ]
        result = trim_elements(elements, max_tokens=5000)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert "index" in parsed[0]
        assert "tag" in parsed[0]

    def test_decorative_filtered_before_sizing(self):
        elements = [
            {"index": 0, "tag": "button", "text": "OK"},
            {"index": 1, "tag": "svg", "is_decorative": True},
        ]
        result = trim_elements(elements, max_tokens=5000)
        parsed = json.loads(result)
        assert len(parsed) == 1

    def test_large_list_triggers_truncation(self):
        # Create elements that exceed compact token budget
        elements = [
            {"index": i, "tag": "a", "text": f"This is a very long link text number {i} with extra words to inflate tokens", "href": f"https://example.com/page/{i}"}
            for i in range(200)
        ]
        result = trim_elements(elements, max_tokens=500)
        parsed = json.loads(result)
        # Should have been trimmed somehow
        total_chars = len(result)
        assert total_chars < len(json.dumps(elements, ensure_ascii=False))

    def test_very_large_triggers_interactive_only(self):
        # Mix of interactive and non-interactive elements
        elements = []
        for i in range(500):
            if i % 5 == 0:
                elements.append({"index": i, "tag": "input", "type": "text", "placeholder": "Enter value " * 10})
            else:
                elements.append({"index": i, "tag": "div", "text": "Non-interactive content " * 20})
        result = trim_elements(elements, max_tokens=100)
        parsed = json.loads(result)
        # At the most aggressive level, only interactive elements remain
        for el in parsed:
            assert el.get("tag") in ("input", "textarea", "button", "select")

    def test_returns_valid_json(self):
        elements = [
            {"index": 0, "tag": "a", "text": "Link", "href": "/page"},
            {"index": 1, "tag": "input", "type": "text", "placeholder": "Search"},
        ]
        result = trim_elements(elements)
        parsed = json.loads(result)
        assert isinstance(parsed, list)


# ── _match_failure_pattern ───────────────────────────────────────────────────


class TestMatchFailurePattern:
    def test_login_wall(self):
        result = _match_failure_pattern("Please login to continue")
        assert result is not None
        assert "login_wall" in result

    def test_captcha(self):
        result = _match_failure_pattern("reCAPTCHA challenge detected")
        assert result is not None
        assert "captcha" in result

    def test_anti_bot(self):
        result = _match_failure_pattern("403 Forbidden - Access Denied")
        assert result is not None
        assert "anti_bot" in result

    def test_redirect(self):
        result = _match_failure_pattern("Page redirect detected: 302")
        assert result is not None
        assert "redirect" in result

    def test_rate_limit(self):
        result = _match_failure_pattern("429 Too Many Requests")
        assert result is not None
        assert "rate_limit" in result

    def test_no_match(self):
        result = _match_failure_pattern("Element not found at index 5")
        assert result is None

    def test_case_insensitive(self):
        result = _match_failure_pattern("PLEASE LOGIN TO CONTINUE")
        assert result is not None

    def test_chinese_keywords(self):
        result = _match_failure_pattern("请先登录后再操作")
        assert result is not None
        assert "login_wall" in result

    def test_chinese_captcha(self):
        result = _match_failure_pattern("请完成人机验证")
        assert result is not None
        assert "captcha" in result
