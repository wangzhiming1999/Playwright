"""
Content Generation Layer
- Takes curated asset cards
- Generates AI Page sections (hero, features, CTA)
- Generates tweets / thread / launch copy
- Reviewer pass for factual consistency
"""

import json
import os
import re

from openai import OpenAI

from utils import llm_call


def _get_client():
    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        http_client=__import__("httpx").Client(proxy=proxy) if proxy else None,
    )


# ── AI Page generation ────────────────────────────────────────────────────────

_PAGE_PROMPT = """You are a product marketing copywriter. Based on the product info and feature cards below, generate a landing page structure.

Return a JSON object with:
- hero: {{ headline: string, subheadline: string, cta_text: string }}
- features: array of {{ title: string, description: string (2-3 sentences), card_index: int (which card this maps to, 0-based) }}
- social_proof: string (one-line trust statement, e.g. "Trusted by 500+ teams")
- faq: array of {{ q: string, a: string }} (3 items)

Rules:
- Language: {language}
- Tone: {tone}
- Keep hero headline under 12 words
- Each feature description should highlight user benefit, not just feature name
- Do NOT invent metrics or numbers not present in the cards
- Respond ONLY with valid JSON, no markdown fences"""

_TWEET_PROMPT = """You are a product launch copywriter. Based on the product info and feature cards below, generate tweet copy.

Return a JSON object with:
- single_tweet: string (under 280 chars, punchy launch announcement)
- thread: array of strings (5-7 tweets forming a product thread, each under 280 chars)
- founder_voice: string (1 tweet in casual founder voice, under 280 chars)

Rules:
- Language: {language}
- No hashtag spam (max 2 hashtags total across all tweets)
- Be specific about features, avoid generic "game-changing" language
- Respond ONLY with valid JSON, no markdown fences"""


def _build_cards_summary(cards: list[dict]) -> str:
    lines = []
    for i, c in enumerate(cards):
        lines.append(
            f"[Card {i}] {c.get('title','')}: {c.get('summary','')} "
            f"(tags: {', '.join(c.get('feature_tags',[]))})"
        )
    return "\n".join(lines)


def generate_ai_page(
    cards: list[dict],
    product_context: str = "",
    language: str = "zh-CN",
    tone: str = "professional",
) -> dict:
    client = _get_client()
    cards_summary = _build_cards_summary(cards)
    prompt = _PAGE_PROMPT.format(language=language, tone=tone)
    user_content = f"Product: {product_context}\n\nFeature cards:\n{cards_summary}\n\n{prompt}"

    response = llm_call(
        client.chat.completions.create,
        model="gpt-4o",
        messages=[{"role": "user", "content": user_content}],
        max_tokens=1500,
        temperature=0.4,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw[:300]}


def generate_tweets(
    cards: list[dict],
    product_context: str = "",
    language: str = "zh-CN",
) -> dict:
    client = _get_client()
    cards_summary = _build_cards_summary(cards)
    prompt = _TWEET_PROMPT.format(language=language)
    user_content = f"Product: {product_context}\n\nFeature cards:\n{cards_summary}\n\n{prompt}"

    response = llm_call(
        client.chat.completions.create,
        model="gpt-4o",
        messages=[{"role": "user", "content": user_content}],
        max_tokens=1000,
        temperature=0.6,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw[:300]}


# ── Reviewer pass ─────────────────────────────────────────────────────────────

_REVIEW_PROMPT = """You are a fact-checker reviewing marketing copy against actual product screenshots.

Cards (ground truth):
{cards_summary}

Generated copy to review:
{copy}

Return a JSON object with:
- approved: boolean
- issues: array of strings (specific factual problems found, empty if none)
- revised_copy: the corrected version (same structure as input, only fix factual issues)

Respond ONLY with valid JSON, no markdown fences."""


def review_copy(cards: list[dict], copy: dict) -> dict:
    client = _get_client()
    cards_summary = _build_cards_summary(cards)
    copy_str = json.dumps(copy, ensure_ascii=False, indent=2)
    prompt = _REVIEW_PROMPT.format(cards_summary=cards_summary, copy=copy_str)

    response = llm_call(
        client.chat.completions.create,
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"approved": False, "issues": [f"parse error: {raw[:200]}"], "revised_copy": copy}


# ── Full generation pipeline ──────────────────────────────────────────────────

def generate_all(
    cards: list[dict],
    product_context: str = "",
    language: str = "zh-CN",
    tone: str = "professional",
    run_review: bool = True,
) -> dict:
    """
    Full pipeline: generate AI page + tweets, then optionally run reviewer.
    Returns:
      {
        "ai_page": {...},
        "tweets": {...},
        "review": {...},   # only if run_review=True
      }
    """
    ai_page = generate_ai_page(cards, product_context, language, tone)
    tweets = generate_tweets(cards, product_context, language)

    result = {"ai_page": ai_page, "tweets": tweets}

    if run_review and "_parse_error" not in ai_page:
        review = review_copy(cards, ai_page)
        if not review.get("approved") and review.get("revised_copy"):
            result["ai_page"] = review["revised_copy"]
        result["review"] = review

    return result
