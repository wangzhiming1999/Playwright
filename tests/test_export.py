"""
Tests for export endpoints
- GET /export/{source}/{source_id}/json
- GET /export/{source}/{source_id}/zip
"""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def client():
    from app import app, TASKS, EXPLORE_TASKS
    TASKS.clear()
    EXPLORE_TASKS.clear()
    return TestClient(app)


@pytest.fixture
def done_task_with_data(client, tmp_path):
    from app import TASKS
    tid = "exptest1"
    shot_dir = Path(f"screenshots/{tid}")
    shot_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 80), color=(100, 150, 200)).save(shot_dir / "result.png")

    TASKS[tid] = {
        "id": tid,
        "task": "open example.com",
        "status": "done",
        "logs": ["step 1"],
        "screenshots": ["result.png"],
        "curation": {
            "cards": [{"title": "看板", "marketing_score": 8.5, "filename": "result.png",
                        "image_url": f"/screenshots/{tid}/result.png"}],
            "stats": {"total": 1, "after_dedup": 1, "kept": 1},
        },
        "generated": {
            "ai_page": {"hero": {"headline": "测试标题", "subheadline": "副标题", "cta_text": "开始"},
                         "features": [], "faq": []},
            "tweets": {"single_tweet": "这是推文", "thread": ["第一条", "第二条"],
                        "founder_voice": "创始人口吻"},
        },
    }
    yield tid
    import shutil
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    TASKS.pop(tid, None)


@pytest.fixture
def done_explore_with_data(client, tmp_path):
    from app import EXPLORE_TASKS
    eid = "exptest2"
    shot_dir = Path(f"screenshots/explore_{eid}")
    shot_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 80), color=(200, 100, 50)).save(shot_dir / "00_homepage.png")

    EXPLORE_TASKS[eid] = {
        "id": eid,
        "url": "https://example.com",
        "product_context": "test product",
        "status": "done",
        "logs": [],
        "screenshots": [{"filename": "00_homepage.png", "score": 7.0}],
        "result": {"site_understanding": {"site_name": "Example", "site_category": "Docs"}},
        "curation": {
            "cards": [{"title": "首页", "marketing_score": 7.0, "filename": "00_homepage.png",
                        "image_url": f"/screenshots/explore_{eid}/00_homepage.png"}],
            "stats": {"total": 1, "after_dedup": 1, "kept": 1},
        },
        "generated": {
            "ai_page": {"hero": {"headline": "探索标题", "subheadline": "副标题", "cta_text": "开始"},
                         "features": [], "faq": []},
            "tweets": {"single_tweet": "探索推文", "thread": [], "founder_voice": ""},
        },
    }
    yield eid
    import shutil
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    EXPLORE_TASKS.pop(eid, None)


# ── Bad source ────────────────────────────────────────────────────────────────

def test_export_json_bad_source(client):
    r = client.get("/export/invalid/abc/json")
    assert r.json()["error"] == "source must be task or explore"


def test_export_zip_bad_source(client):
    r = client.get("/export/invalid/abc/zip")
    assert r.json()["error"] == "source must be task or explore"


def test_export_json_not_found(client):
    r = client.get("/export/task/missing/json")
    assert r.json()["error"] == "not found"


def test_export_zip_not_found(client):
    r = client.get("/export/explore/missing/zip")
    assert r.json()["error"] == "not found"


# ── JSON export ───────────────────────────────────────────────────────────────

class TestJsonExport:
    def test_returns_json_content_type(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        assert r.status_code == 200
        assert "application/json" in r.headers["content-type"]

    def test_has_attachment_header(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        assert "attachment" in r.headers.get("content-disposition", "")
        assert ".json" in r.headers.get("content-disposition", "")

    def test_bundle_structure(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        data = r.json()
        assert data["source"] == "task"
        assert data["source_id"] == done_task_with_data
        assert data["status"] == "done"
        assert "curation" in data
        assert "generated" in data

    def test_curation_included(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        data = r.json()
        assert data["curation"]["cards"][0]["title"] == "看板"

    def test_generated_included(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        data = r.json()
        assert data["generated"]["ai_page"]["hero"]["headline"] == "测试标题"
        assert data["generated"]["tweets"]["single_tweet"] == "这是推文"

    def test_explore_json_export(self, client, done_explore_with_data):
        r = client.get(f"/export/explore/{done_explore_with_data}/json")
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "explore"
        assert data["url"] == "https://example.com"

    def test_valid_utf8_json(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/json")
        # Should be decodable as UTF-8
        text = r.content.decode("utf-8")
        parsed = json.loads(text)
        assert parsed["curation"]["cards"][0]["title"] == "看板"


# ── ZIP export ────────────────────────────────────────────────────────────────

class TestZipExport:
    def test_returns_zip_content_type(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        assert r.status_code == 200
        assert "application/zip" in r.headers["content-type"]

    def test_has_attachment_header(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        assert "attachment" in r.headers.get("content-disposition", "")
        assert ".zip" in r.headers.get("content-disposition", "")

    def test_zip_is_valid(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        buf = io.BytesIO(r.content)
        assert zipfile.is_zipfile(buf)

    def test_zip_contains_screenshot(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert any("result.png" in n for n in names)

    def test_zip_contains_curation_json(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert "curation.json" in zf.namelist()
            curation = json.loads(zf.read("curation.json").decode("utf-8"))
        assert curation["cards"][0]["title"] == "看板"

    def test_zip_contains_generated_json(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert "generated.json" in zf.namelist()
            gen = json.loads(zf.read("generated.json").decode("utf-8"))
        assert gen["ai_page"]["hero"]["headline"] == "测试标题"

    def test_zip_contains_tweets_txt(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert "tweets.txt" in zf.namelist()
            tweets_text = zf.read("tweets.txt").decode("utf-8")
        assert "这是推文" in tweets_text
        assert "第一条" in tweets_text

    def test_zip_contains_ai_page_md(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert "ai_page.md" in zf.namelist()
            md = zf.read("ai_page.md").decode("utf-8")
        assert "测试标题" in md
        assert "开始" in md

    def test_zip_contains_summary_json(self, client, done_task_with_data):
        r = client.get(f"/export/task/{done_task_with_data}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert "summary.json" in zf.namelist()
            summary = json.loads(zf.read("summary.json").decode("utf-8"))
        assert summary["source"] == "task"
        assert summary["has_generated"] is True
        assert summary["cards_count"] == 1

    def test_explore_zip_export(self, client, done_explore_with_data):
        r = client.get(f"/export/explore/{done_explore_with_data}/zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert "summary.json" in names
        assert any("homepage" in n for n in names)

    def test_zip_without_generated_skips_tweets_md(self, client):
        from app import TASKS
        tid = "nogendef"
        shot_dir = Path(f"screenshots/{tid}")
        shot_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (50, 50)).save(shot_dir / "a.png")
        TASKS[tid] = {
            "id": tid, "task": "x", "status": "done",
            "logs": [], "screenshots": ["a.png"],
            "curation": {"cards": [], "stats": {"total": 1, "after_dedup": 1, "kept": 0}},
        }
        r = client.get(f"/export/task/{tid}/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert "tweets.txt" not in names
        assert "ai_page.md" not in names
        import shutil; shutil.rmtree(shot_dir, ignore_errors=True)
        TASKS.pop(tid, None)
