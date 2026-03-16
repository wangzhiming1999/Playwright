"""
Site Understanding Layer
- Extracts nav structure, page type, candidate feature pages
- Scores pages for marketing value
- Returns structured site graph for exploration decisions
"""

import json
import os
import re
from pathlib import Path

from utils import llm_chat


# ── DOM summarizer ────────────────────────────────────────────────────────────

def extract_nav_summary(html: str) -> str:
    """Extract nav/sidebar/menu text from raw HTML, truncated for LLM input."""
    # Pull text from nav-like elements
    nav_pattern = re.compile(
        r'<(?:nav|header|aside)[^>]*>(.*?)</(?:nav|header|aside)>',
        re.DOTALL | re.IGNORECASE,
    )
    tag_pattern = re.compile(r'<[^>]+>')

    chunks = []
    for m in nav_pattern.finditer(html):
        text = tag_pattern.sub(' ', m.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        if text:
            chunks.append(text)

    # Also grab all <a> hrefs + text as fallback
    link_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    links = []
    for href, text in link_pattern.findall(html):
        text = tag_pattern.sub('', text).strip()
        if text and not href.startswith(('javascript:', 'mailto:', '#')):
            links.append(f"{text} -> {href}")

    nav_text = '\n'.join(chunks)[:2000]
    links_text = '\n'.join(links[:60])
    return f"NAV ELEMENTS:\n{nav_text}\n\nLINKS:\n{links_text}"


def extract_page_text(html: str, max_chars: int = 3000) -> str:
    """Strip tags and return visible text."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


# ── LLM analysis ─────────────────────────────────────────────────────────────

_UNDERSTAND_PROMPT = """You are a product marketing analyst. Analyze this website's structure and return a JSON object with:

- site_category: string (e.g. "B2B SaaS", "E-commerce", "Developer Tool", "Marketing Site", "Docs", "Community")
- site_name: string (inferred product/company name)
- needs_login: boolean (true if core features require authentication)
- entry_points: array of objects with {label, path, priority} where priority is 1-5 (5=most important for marketing)
- candidate_feature_pages: array of objects with {path, label, marketing_score (0-10), reason, page_type}
  - page_type options: "dashboard", "analytics", "editor", "automation", "onboarding", "landing", "pricing", "docs", "settings", "login", "other"
  - Only include pages likely to show product value (skip settings, login, account, billing)
- exploration_strategy: string (brief note on best exploration approach)
- key_features_visible: array of strings (features you can already see from the homepage)

Respond ONLY with valid JSON, no markdown fences."""


def analyze_site(
    url: str,
    html: str,
    screenshot_b64: str | None = None,
    product_context: str = "",
) -> dict:
    """
    Analyze a website's homepage to build a site understanding graph.
    Returns structured dict with nav, candidate pages, and exploration strategy.
    """
    client = None  # no longer needed

    nav_summary = extract_nav_summary(html)
    page_text = extract_page_text(html)

    prompt_parts = [f"URL: {url}\n"]
    if product_context:
        prompt_parts.append(f"Product context provided by user: {product_context}\n")
    prompt_parts.append(f"Page text (truncated):\n{page_text}\n\n")
    prompt_parts.append(f"Navigation structure:\n{nav_summary}\n\n")
    prompt_parts.append(_UNDERSTAND_PROMPT)

    content: list = [{"type": "text", "text": "".join(prompt_parts)}]

    if screenshot_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}", "detail": "low"},
        })

    response = llm_chat(
        messages=[{"role": "user", "content": content}],
        max_tokens=1200,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "site_category": "unknown",
            "site_name": url,
            "needs_login": False,
            "entry_points": [],
            "candidate_feature_pages": [],
            "exploration_strategy": "fallback: crawl top-level nav",
            "key_features_visible": [],
            "_parse_error": raw[:300],
        }

    result["analyzed_url"] = url
    return result


# ── Page scorer (for pages visited during exploration) ────────────────────────

_PAGE_SCORE_PROMPT = """You are evaluating whether this webpage is worth including as a marketing screenshot.
Return a JSON object with:
- marketing_score: float 0-10
- page_type: string
- is_worth_screenshot: boolean (true if score >= 5 and not settings/login/empty)
- recommended_regions: array of CSS selectors or descriptions of areas worth cropping
- reason: string

Respond ONLY with valid JSON, no markdown fences."""


def score_page(url: str, html: str, screenshot_b64: str, product_context: str = "") -> dict:
    """Score a visited page for marketing value during exploration."""
    page_text = extract_page_text(html, max_chars=1500)
    prompt = f"URL: {url}\n"
    if product_context:
        prompt += f"Product: {product_context}\n"
    prompt += f"Page text: {page_text}\n\n{_PAGE_SCORE_PROMPT}"

    response = llm_chat(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_b64}",
                    "detail": "low",
                }},
            ],
        }],
        max_tokens=400,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "marketing_score": 0,
            "page_type": "unknown",
            "is_worth_screenshot": False,
            "recommended_regions": [],
            "reason": f"parse error: {raw[:200]}",
        }
