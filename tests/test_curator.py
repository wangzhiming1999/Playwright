"""
Tests for curator.py
- dedup_screenshots
- _has_sensitive_text
- blur_sensitive_regions
- curate pipeline (mocked VLM)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from curator import (
    _has_sensitive_text,
    blur_sensitive_regions,
    curate,
    dedup_screenshots,
    score_screenshot,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_distinct_image(path: Path, seed: int):
    """Create a visually distinct image using a checkerboard pattern seeded by index."""
    import random
    rng = random.Random(seed * 9999)
    img = Image.new("RGB", (256, 256))
    pixels = img.load()
    for y in range(256):
        for x in range(256):
            pixels[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    img.save(path)
    return path


@pytest.fixture
def tmp_screenshots(tmp_path):
    """Create visually distinct PNG files."""
    imgs = [_make_distinct_image(tmp_path / f"shot_{i}.png", i) for i in range(3)]
    return tmp_path, imgs


@pytest.fixture
def duplicate_screenshots(tmp_path):
    """Two identical images + one visually distinct one."""
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    p3 = tmp_path / "c.png"
    _make_distinct_image(p1, 42)
    # exact copy of p1
    Image.open(p1).save(p2)
    _make_distinct_image(p3, 999)
    return tmp_path, [p1, p2, p3]


# ── _has_sensitive_text ───────────────────────────────────────────────────────

class TestHasSensitiveText:
    def test_email_detected(self):
        assert _has_sensitive_text("contact user@example.com for help")

    def test_cn_phone_detected(self):
        assert _has_sensitive_text("call 13812345678 now")

    def test_credential_detected(self):
        assert _has_sensitive_text("api_key=sk-abc123xyz")
        assert _has_sensitive_text("password: hunter2")

    def test_clean_text(self):
        assert not _has_sensitive_text("This is a normal product description")
        assert not _has_sensitive_text("Dashboard showing 1234 active users")

    def test_empty_string(self):
        assert not _has_sensitive_text("")


# ── dedup_screenshots ─────────────────────────────────────────────────────────

class TestDedupScreenshots:
    def test_keeps_unique_images(self, tmp_screenshots):
        tmp_path, imgs = tmp_screenshots
        result = dedup_screenshots(imgs)
        assert len(result) == 3

    def test_removes_duplicates(self, duplicate_screenshots):
        tmp_path, imgs = duplicate_screenshots
        result = dedup_screenshots(imgs)
        # a and b are identical, so only one should survive
        assert len(result) == 2

    def test_empty_list(self):
        assert dedup_screenshots([]) == []

    def test_single_image(self, tmp_screenshots):
        tmp_path, imgs = tmp_screenshots
        result = dedup_screenshots([imgs[0]])
        assert result == [imgs[0]]

    def test_missing_file_handled(self, tmp_path):
        missing = tmp_path / "nonexistent.png"
        real = tmp_path / "real.png"
        Image.new("RGB", (50, 50), color=(10, 20, 30)).save(real)
        # Should not raise, missing file gets kept (no hash to compare)
        result = dedup_screenshots([missing, real])
        assert len(result) == 2


# ── blur_sensitive_regions ────────────────────────────────────────────────────

class TestBlurSensitiveRegions:
    def test_no_regions_returns_original(self, tmp_path):
        p = tmp_path / "img.png"
        Image.new("RGB", (200, 100), color=(100, 150, 200)).save(p)
        result = blur_sensitive_regions(p, [])
        assert result == p

    def test_blur_creates_new_file(self, tmp_path):
        p = tmp_path / "img.png"
        Image.new("RGB", (200, 100), color=(100, 150, 200)).save(p)
        regions = [{"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.2}]
        result = blur_sensitive_regions(p, regions)
        assert result != p
        assert result.exists()
        assert "_safe" in result.stem

    def test_blurred_image_same_size(self, tmp_path):
        p = tmp_path / "img.png"
        img = Image.new("RGB", (300, 200), color=(50, 100, 150))
        img.save(p)
        regions = [{"x": 0, "y": 0, "width": 0.5, "height": 0.5}]
        result = blur_sensitive_regions(p, regions)
        blurred = Image.open(result)
        assert blurred.size == (300, 200)

    def test_absolute_pixel_coords(self, tmp_path):
        p = tmp_path / "img.png"
        Image.new("RGB", (400, 300)).save(p)
        regions = [{"x": 10, "y": 10, "width": 100, "height": 50}]
        result = blur_sensitive_regions(p, regions)
        assert result.exists()

    def test_out_of_bounds_region_handled(self, tmp_path):
        p = tmp_path / "img.png"
        Image.new("RGB", (100, 100)).save(p)
        regions = [{"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5}]
        result = blur_sensitive_regions(p, regions)
        assert result.exists()


# ── score_screenshot (mocked) ─────────────────────────────────────────────────

_MOCK_SCORE = {
    "marketing_score": 8.5,
    "visual_quality": 7.0,
    "page_type": "dashboard",
    "is_marketing_worthy": True,
    "title": "实时数据看板",
    "summary": "集中展示核心业务指标，帮助团队快速决策。",
    "feature_tags": ["analytics", "dashboard"],
    "sensitive_detected": False,
    "reason": "Shows core product value clearly",
}


class TestScoreScreenshot:
    def test_returns_card_with_filename(self, tmp_path):
        p = tmp_path / "test.png"
        Image.new("RGB", (100, 80), color=(200, 200, 200)).save(p)

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(_MOCK_SCORE)
        with patch("curator.llm_chat", return_value=mock_resp):
            result = score_screenshot(p)

        assert result["filename"] == "test.png"
        assert result["marketing_score"] == 8.5
        assert result["title"] == "实时数据看板"

    def test_handles_json_parse_error(self, tmp_path):
        p = tmp_path / "bad.png"
        Image.new("RGB", (100, 80)).save(p)

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not valid json at all"
        with patch("curator.llm_chat", return_value=mock_resp):
            result = score_screenshot(p)

        assert result["marketing_score"] == 0
        assert result["is_marketing_worthy"] is False
        assert "parse error" in result["reason"]

    def test_strips_markdown_fences(self, tmp_path):
        p = tmp_path / "fenced.png"
        Image.new("RGB", (100, 80)).save(p)

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = f"```json\n{json.dumps(_MOCK_SCORE)}\n```"
        with patch("curator.llm_chat", return_value=mock_resp):
            result = score_screenshot(p)

        assert result["marketing_score"] == 8.5


# ── curate pipeline (mocked) ──────────────────────────────────────────────────

class TestCurate:
    def _make_shots(self, tmp_path, n=3):
        paths = []
        for i in range(n):
            p = tmp_path / f"shot_{i}.png"
            Image.new("RGB", (100, 80), color=(i * 80, 100, 200)).save(p)
            paths.append(p)
        return paths

    def test_empty_dir_returns_empty(self, tmp_path):
        result = curate(tmp_path)
        assert result["cards"] == []
        assert result["stats"]["total"] == 0

    def test_filters_below_min_score(self, tmp_path):
        self._make_shots(tmp_path)
        low_score = {**_MOCK_SCORE, "marketing_score": 3.0, "is_marketing_worthy": False}

        with patch("curator.score_screenshot", return_value=low_score):
            result = curate(tmp_path, min_score=5.0)

        assert result["cards"] == []
        assert result["stats"]["total"] == 3

    def test_filters_sensitive_images(self, tmp_path):
        self._make_shots(tmp_path)
        sensitive = {**_MOCK_SCORE, "sensitive_detected": True}

        with patch("curator.score_screenshot", return_value=sensitive):
            result = curate(tmp_path)

        assert result["cards"] == []

    def test_returns_sorted_by_score(self, tmp_path):
        self._make_shots(tmp_path, n=3)
        scores = [7.0, 9.0, 5.5]
        call_count = [0]

        def mock_score(path, product_context=""):
            i = call_count[0] % len(scores)
            call_count[0] += 1
            return {**_MOCK_SCORE, "marketing_score": scores[i], "filename": path.name}

        with patch("curator.score_screenshot", side_effect=mock_score):
            result = curate(tmp_path, min_score=5.0)

        card_scores = [c["marketing_score"] for c in result["cards"]]
        assert card_scores == sorted(card_scores, reverse=True)

    def test_respects_max_cards(self, tmp_path):
        self._make_shots(tmp_path, n=5)

        with patch("curator.score_screenshot", return_value=_MOCK_SCORE):
            result = curate(tmp_path, max_cards=2)

        assert len(result["cards"]) <= 2

    def test_stats_counts(self, tmp_path):
        self._make_shots(tmp_path, n=4)

        with patch("curator.score_screenshot", return_value=_MOCK_SCORE):
            result = curate(tmp_path)

        assert result["stats"]["total"] == 4
        assert "after_dedup" in result["stats"]
        assert "kept" in result["stats"]
