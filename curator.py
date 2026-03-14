"""
Screenshot Curation Module
- VLM scoring (marketing value, visual quality)
- Perceptual hash dedup
- Sensitive info detection
- Asset card generation (title + summary + tags + score)
"""

import base64
import json
import os
import re
from pathlib import Path

import imagehash
from PIL import Image, ImageFilter

from utils import llm_call, get_openai_client


# ── Sensitive info patterns ───────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",   # email
    r"\b1[3-9]\d{9}\b",                                          # CN phone
    r"\b(?:\d[ -]?){13,16}\b",                                   # card-like numbers
    r"(?i)(password|token|secret|api[_-]?key)\s*[:=]\s*\S+",    # credentials
]
_SENSITIVE_RE = [re.compile(p) for p in _SENSITIVE_PATTERNS]


def _has_sensitive_text(text: str) -> bool:
    for pattern in _SENSITIVE_RE:
        if pattern.search(text):
            return True
    return False


# ── Sensitive info blur ───────────────────────────────────────────────────────

def blur_sensitive_regions(image_path: Path, regions: list[dict]) -> Path:
    """
    Blur rectangular regions in an image and save as a new file.
    regions: list of {x, y, width, height} in pixels (0-1 normalized or absolute).
    Returns path to the blurred image (overwrites in-place with _blurred suffix).
    """
    if not regions:
        return image_path

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    for r in regions:
        # Support both normalized (0-1) and absolute pixel coords
        x = int(r.get("x", 0) * w) if r.get("x", 0) <= 1 else int(r.get("x", 0))
        y = int(r.get("y", 0) * h) if r.get("y", 0) <= 1 else int(r.get("y", 0))
        rw = int(r.get("width", 0.2) * w) if r.get("width", 0) <= 1 else int(r.get("width", 100))
        rh = int(r.get("height", 0.05) * h) if r.get("height", 0) <= 1 else int(r.get("height", 30))

        # Clamp to image bounds
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w, x + rw), min(h, y + rh)
        if x2 <= x1 or y2 <= y1:
            continue

        region = img.crop((x1, y1, x2, y2))
        blurred = region.resize((max(1, (x2-x1)//10), max(1, (y2-y1)//10)), Image.BOX)
        blurred = blurred.resize((x2-x1, y2-y1), Image.NEAREST)
        img.paste(blurred, (x1, y1))

    out_path = image_path.with_stem(image_path.stem + "_safe")
    img.save(out_path)
    return out_path


# ── Perceptual hash dedup ─────────────────────────────────────────────────────

def dedup_screenshots(paths: list[Path], threshold: int = 8) -> list[Path]:
    """Remove near-duplicate images using perceptual hashing. Returns unique paths."""
    kept: list[tuple[Path, imagehash.ImageHash]] = []
    for p in paths:
        try:
            h = imagehash.phash(Image.open(p))
        except Exception:
            kept.append((p, None))
            continue
        is_dup = any(
            existing_h is not None and abs(h - existing_h) <= threshold
            for _, existing_h in kept
        )
        if not is_dup:
            kept.append((p, h))
    return [p for p, _ in kept]


# ── VLM scoring + asset card ──────────────────────────────────────────────────

_SCORE_PROMPT = """You are a marketing asset curator. Analyze this product screenshot and return a JSON object with:
- marketing_score: float 0-10 (how valuable is this for product marketing/promotion)
- visual_quality: float 0-10 (visual clarity, no clutter, no popups blocking content)
- page_type: string (e.g. "dashboard", "landing", "settings", "onboarding", "empty_state", "login", "docs")
- is_marketing_worthy: boolean (true if score >= 6 and not settings/login/empty)
- title: string (short Chinese title for this feature, e.g. "实时数据看板")
- summary: string (1-2 sentence Chinese marketing copy describing the value shown)
- feature_tags: array of strings (e.g. ["analytics", "real-time", "charts"])
- sensitive_detected: boolean (true if you see emails, phone numbers, passwords, or tokens)
- reason: string (brief explanation of your score)

Respond ONLY with valid JSON, no markdown fences."""


def score_screenshot(image_path: Path, product_context: str = "") -> dict:
    """Call VLM to score and generate asset card for a single screenshot."""
    client = get_openai_client()

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = image_path.suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else "image/jpeg"

    prompt = _SCORE_PROMPT
    if product_context:
        prompt = f"Product context: {product_context}\n\n{_SCORE_PROMPT}"

    response = llm_call(
        client.chat.completions.create,
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": "high",
                    }},
                ],
            }
        ],
        max_tokens=600,
        temperature=0.2,
    )

    raw = response.choices[0].message.content.strip()
    # strip markdown fences if model ignores instruction
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "marketing_score": 0,
            "visual_quality": 0,
            "page_type": "unknown",
            "is_marketing_worthy": False,
            "title": image_path.stem,
            "summary": "",
            "feature_tags": [],
            "sensitive_detected": False,
            "reason": f"parse error: {raw[:200]}",
        }

    result["filename"] = image_path.name
    result["image_path"] = str(image_path)
    return result


# ── Main curation pipeline ────────────────────────────────────────────────────

def curate(
    screenshots_dir: Path,
    product_context: str = "",
    min_score: float = 5.0,
    max_cards: int = 8,
) -> dict:
    """
    Full curation pipeline for a task's screenshots directory.
    Returns a dict with:
      - cards: list of asset card dicts (sorted by score, filtered)
      - all_results: raw scores for every image
      - stats: summary counts
    """
    png_files = sorted(screenshots_dir.glob("*.png"))
    if not png_files:
        return {"cards": [], "all_results": [], "stats": {"total": 0, "kept": 0}}

    # 1. Dedup
    unique_files = dedup_screenshots(png_files)

    # 2. Score each unique screenshot
    all_results = []
    for p in unique_files:
        result = score_screenshot(p, product_context)

        # Also run local regex check on filename/path as a cheap extra signal
        if _has_sensitive_text(p.stem):
            result["sensitive_detected"] = True

        all_results.append(result)

    # 3. Filter: marketing worthy, not sensitive, above min_score
    worthy = [
        r for r in all_results
        if r.get("is_marketing_worthy")
        and not r.get("sensitive_detected")
        and r.get("marketing_score", 0) >= min_score
    ]

    # 4. Sort by marketing_score desc, take top N
    worthy.sort(key=lambda r: r.get("marketing_score", 0), reverse=True)
    cards = worthy[:max_cards]

    return {
        "cards": cards,
        "all_results": all_results,
        "stats": {
            "total": len(png_files),
            "after_dedup": len(unique_files),
            "kept": len(cards),
        },
    }
