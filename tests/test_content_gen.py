"""
Tests for content_gen.py
- generate_ai_page (mocked LLM)
- generate_tweets (mocked LLM)
- review_copy (mocked LLM)
- generate_all pipeline
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from content_gen import generate_ai_page, generate_tweets, review_copy, generate_all


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_CARDS = [
    {
        "title": "实时数据看板",
        "summary": "集中展示核心业务指标，帮助团队快速决策。",
        "feature_tags": ["analytics", "dashboard", "real-time"],
        "marketing_score": 9.0,
    },
    {
        "title": "自动化报告",
        "summary": "一键生成周报月报，自动发送给相关人员。",
        "feature_tags": ["automation", "reports"],
        "marketing_score": 8.0,
    },
]

_MOCK_PAGE = {
    "hero": {
        "headline": "让数据驱动每一个决策",
        "subheadline": "实时看板 + 自动化报告，帮助团队提升效率",
        "cta_text": "免费试用",
    },
    "features": [
        {"title": "实时看板", "description": "随时掌握业务动态。", "card_index": 0},
        {"title": "自动报告", "description": "省去手动整理的时间。", "card_index": 1},
    ],
    "social_proof": "已有 500+ 团队在使用",
    "faq": [
        {"q": "支持哪些数据源？", "a": "支持主流数据库和 API。"},
        {"q": "如何导出报告？", "a": "支持 PDF 和 Excel 格式。"},
        {"q": "有免费版吗？", "a": "有 14 天免费试用。"},
    ],
}

_MOCK_TWEETS = {
    "single_tweet": "🚀 介绍 Acme Analytics：实时看板 + 自动报告，让数据驱动决策。",
    "thread": [
        "1/ 你的团队每周花多少时间整理数据报告？",
        "2/ Acme Analytics 可以帮你自动化这一切。",
        "3/ 实时看板，随时掌握业务动态。",
    ],
    "founder_voice": "我们花了 2 年时间打磨这个产品，终于可以分享给大家了。",
}

_MOCK_REVIEW_APPROVED = {
    "approved": True,
    "issues": [],
    "revised_copy": _MOCK_PAGE,
}

_MOCK_REVIEW_REJECTED = {
    "approved": False,
    "issues": ["Headline mentions '10x faster' but no such claim in cards"],
    "revised_copy": {**_MOCK_PAGE, "hero": {**_MOCK_PAGE["hero"], "headline": "让数据驱动决策"}},
}


def _mock_client(response_json):
    mock = MagicMock()
    mock.chat.completions.create.return_value.choices[0].message.content = (
        json.dumps(response_json)
    )
    return mock


# ── generate_ai_page ──────────────────────────────────────────────────────────

class TestGenerateAiPage:
    def test_returns_hero_and_features(self):
        with patch("content_gen._get_client", return_value=_mock_client(_MOCK_PAGE)):
            result = generate_ai_page(SAMPLE_CARDS, product_context="analytics platform")

        assert "hero" in result
        assert result["hero"]["headline"] == "让数据驱动每一个决策"
        assert len(result["features"]) == 2
        assert len(result["faq"]) == 3

    def test_handles_json_parse_error(self):
        mock = MagicMock()
        mock.chat.completions.create.return_value.choices[0].message.content = "not json"
        with patch("content_gen._get_client", return_value=mock):
            result = generate_ai_page(SAMPLE_CARDS)

        assert "_parse_error" in result

    def test_strips_markdown_fences(self):
        mock = MagicMock()
        mock.chat.completions.create.return_value.choices[0].message.content = (
            f"```json\n{json.dumps(_MOCK_PAGE)}\n```"
        )
        with patch("content_gen._get_client", return_value=mock):
            result = generate_ai_page(SAMPLE_CARDS)

        assert "hero" in result
        assert "_parse_error" not in result

    def test_passes_language_in_prompt(self):
        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs["messages"]
            resp = MagicMock()
            resp.choices[0].message.content = json.dumps(_MOCK_PAGE)
            return resp

        mock = MagicMock()
        mock.chat.completions.create.side_effect = fake_create
        with patch("content_gen._get_client", return_value=mock):
            generate_ai_page(SAMPLE_CARDS, language="en-US")

        prompt = captured["messages"][0]["content"]
        assert "en-US" in prompt

    def test_empty_cards_still_calls_llm(self):
        with patch("content_gen._get_client", return_value=_mock_client(_MOCK_PAGE)):
            result = generate_ai_page([])

        assert "hero" in result


# ── generate_tweets ───────────────────────────────────────────────────────────

class TestGenerateTweets:
    def test_returns_tweet_fields(self):
        with patch("content_gen._get_client", return_value=_mock_client(_MOCK_TWEETS)):
            result = generate_tweets(SAMPLE_CARDS, product_context="analytics")

        assert "single_tweet" in result
        assert "thread" in result
        assert "founder_voice" in result
        assert len(result["thread"]) == 3

    def test_handles_parse_error(self):
        mock = MagicMock()
        mock.chat.completions.create.return_value.choices[0].message.content = "bad"
        with patch("content_gen._get_client", return_value=mock):
            result = generate_tweets(SAMPLE_CARDS)

        assert "_parse_error" in result

    def test_strips_markdown_fences(self):
        mock = MagicMock()
        mock.chat.completions.create.return_value.choices[0].message.content = (
            f"```json\n{json.dumps(_MOCK_TWEETS)}\n```"
        )
        with patch("content_gen._get_client", return_value=mock):
            result = generate_tweets(SAMPLE_CARDS)

        assert "single_tweet" in result

    def test_passes_language_in_prompt(self):
        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs["messages"]
            resp = MagicMock()
            resp.choices[0].message.content = json.dumps(_MOCK_TWEETS)
            return resp

        mock = MagicMock()
        mock.chat.completions.create.side_effect = fake_create
        with patch("content_gen._get_client", return_value=mock):
            generate_tweets(SAMPLE_CARDS, language="zh-CN")

        prompt = captured["messages"][0]["content"]
        assert "zh-CN" in prompt


# ── review_copy ───────────────────────────────────────────────────────────────

class TestReviewCopy:
    def test_approved_result(self):
        with patch("content_gen._get_client",
                   return_value=_mock_client(_MOCK_REVIEW_APPROVED)):
            result = review_copy(SAMPLE_CARDS, _MOCK_PAGE)

        assert result["approved"] is True
        assert result["issues"] == []

    def test_rejected_result_has_issues(self):
        with patch("content_gen._get_client",
                   return_value=_mock_client(_MOCK_REVIEW_REJECTED)):
            result = review_copy(SAMPLE_CARDS, _MOCK_PAGE)

        assert result["approved"] is False
        assert len(result["issues"]) > 0
        assert "revised_copy" in result

    def test_handles_parse_error(self):
        mock = MagicMock()
        mock.chat.completions.create.return_value.choices[0].message.content = "bad"
        with patch("content_gen._get_client", return_value=mock):
            result = review_copy(SAMPLE_CARDS, _MOCK_PAGE)

        assert result["approved"] is False
        assert result["revised_copy"] == _MOCK_PAGE


# ── generate_all pipeline ─────────────────────────────────────────────────────

class TestGenerateAll:
    def test_returns_ai_page_and_tweets(self):
        with patch("content_gen.generate_ai_page", return_value=_MOCK_PAGE), \
             patch("content_gen.generate_tweets", return_value=_MOCK_TWEETS), \
             patch("content_gen.review_copy", return_value=_MOCK_REVIEW_APPROVED):
            result = generate_all(SAMPLE_CARDS, product_context="test", run_review=True)

        assert "ai_page" in result
        assert "tweets" in result
        assert "review" in result

    def test_skips_review_when_disabled(self):
        with patch("content_gen.generate_ai_page", return_value=_MOCK_PAGE), \
             patch("content_gen.generate_tweets", return_value=_MOCK_TWEETS):
            result = generate_all(SAMPLE_CARDS, run_review=False)

        assert "review" not in result

    def test_applies_revised_copy_when_rejected(self):
        revised = {**_MOCK_PAGE, "hero": {**_MOCK_PAGE["hero"], "headline": "修正后标题"}}
        rejected = {"approved": False, "issues": ["bad claim"], "revised_copy": revised}

        with patch("content_gen.generate_ai_page", return_value=_MOCK_PAGE), \
             patch("content_gen.generate_tweets", return_value=_MOCK_TWEETS), \
             patch("content_gen.review_copy", return_value=rejected):
            result = generate_all(SAMPLE_CARDS, run_review=True)

        assert result["ai_page"]["hero"]["headline"] == "修正后标题"

    def test_skips_review_if_page_has_parse_error(self):
        bad_page = {"_parse_error": "could not parse"}
        review_called = []

        def fake_review(*args, **kwargs):
            review_called.append(True)
            return _MOCK_REVIEW_APPROVED

        with patch("content_gen.generate_ai_page", return_value=bad_page), \
             patch("content_gen.generate_tweets", return_value=_MOCK_TWEETS), \
             patch("content_gen.review_copy", side_effect=fake_review):
            result = generate_all(SAMPLE_CARDS, run_review=True)

        assert not review_called
        assert result["ai_page"] == bad_page

    def test_passes_language_and_tone(self):
        captured_lang = []
        captured_tone = []

        def fake_page(cards, product_context="", language="zh-CN", tone="professional"):
            captured_lang.append(language)
            captured_tone.append(tone)
            return _MOCK_PAGE

        with patch("content_gen.generate_ai_page", side_effect=fake_page), \
             patch("content_gen.generate_tweets", return_value=_MOCK_TWEETS):
            generate_all(SAMPLE_CARDS, language="en-US", tone="casual", run_review=False)

        assert captured_lang[0] == "en-US"
        assert captured_tone[0] == "casual"
