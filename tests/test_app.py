"""
Tests for app.py API endpoints
- POST /run
- POST /curate
- POST /explore
- POST /generate
- GET /tasks, /tasks/{id}/logs, /tasks/{id}/curation, /tasks/{id}/generated
- GET /explore/{eid}, /explore/{eid}/generated
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
def done_task(client, tmp_path):
    """Inject a completed task with screenshots into TASKS."""
    from app import TASKS
    tid = "test0001"
    shot_dir = Path(f"screenshots/{tid}")
    shot_dir.mkdir(parents=True, exist_ok=True)
    img = shot_dir / "result.png"
    Image.new("RGB", (100, 80), color=(100, 150, 200)).save(img)

    TASKS[tid] = {
        "id": tid,
        "task": "open example.com and screenshot",
        "status": "done",
        "logs": ["step 1", "step 2"],
        "screenshots": ["result.png"],
    }
    yield tid
    # cleanup
    import shutil
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    TASKS.pop(tid, None)


@pytest.fixture
def done_explore(client, tmp_path):
    """Inject a completed explore task with screenshots."""
    from app import EXPLORE_TASKS
    eid = "exp00001"
    shot_dir = Path(f"screenshots/explore_{eid}")
    shot_dir.mkdir(parents=True, exist_ok=True)
    img = shot_dir / "00_homepage.png"
    Image.new("RGB", (100, 80), color=(200, 100, 50)).save(img)

    EXPLORE_TASKS[eid] = {
        "id": eid,
        "url": "https://example.com",
        "product_context": "test product",
        "status": "done",
        "logs": [],
        "screenshots": [{"filename": "00_homepage.png", "url": "https://example.com",
                          "title": "Homepage", "score": 7.0, "page_type": "landing"}],
        "result": {
            "site_understanding": {"site_category": "Docs", "site_name": "Example"},
            "visited_pages": [],
            "screenshots": [],
        },
    }
    yield eid
    import shutil
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    EXPLORE_TASKS.pop(eid, None)


# ── GET / ─────────────────────────────────────────────────────────────────────

def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /tasks ────────────────────────────────────────────────────────────────

def test_list_tasks_empty(client):
    r = client.get("/tasks")
    assert r.status_code == 200
    assert r.json()["tasks"] == []


def test_list_tasks_after_submit(client):
    with patch("app._run_task"):  # don't actually run
        r = client.post("/run", json={"tasks": ["open example.com"]})
    assert r.status_code == 200
    ids = r.json()["task_ids"]
    assert len(ids) == 1

    r = client.get("/tasks")
    tasks = r.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"


# ── POST /run ─────────────────────────────────────────────────────────────────

def test_run_empty_tasks(client):
    r = client.post("/run", json={"tasks": []})
    assert r.status_code == 200
    assert r.json()["task_ids"] == []


def test_run_skips_blank_tasks(client):
    with patch("app._run_task"):
        r = client.post("/run", json={"tasks": ["", "  ", "real task"]})
    assert len(r.json()["task_ids"]) == 1


def test_run_multiple_tasks(client):
    with patch("app._run_task"):
        r = client.post("/run", json={"tasks": ["task A", "task B", "task C"]})
    assert len(r.json()["task_ids"]) == 3


# ── GET /tasks/{id}/logs ──────────────────────────────────────────────────────

def test_get_logs_not_found(client):
    r = client.get("/tasks/nonexistent/logs")
    assert r.status_code == 404
    assert r.json()["detail"] == "task not found"


def test_get_logs_returns_list(client, done_task):
    r = client.get(f"/tasks/{done_task}/logs")
    assert r.status_code == 200
    assert isinstance(r.json()["logs"], list)
    assert len(r.json()["logs"]) == 2


# ── POST /curate ──────────────────────────────────────────────────────────────

def test_curate_task_not_found(client):
    r = client.post("/curate", json={"task_id": "missing"})
    assert r.status_code == 404
    assert r.json()["detail"] == "task not found"


def test_curate_task_not_done(client):
    from app import TASKS
    TASKS["running1"] = {"id": "running1", "task": "x", "status": "running",
                          "logs": [], "screenshots": []}
    r = client.post("/curate", json={"task_id": "running1"})
    assert r.status_code == 400
    assert "must be 'done'" in r.json()["detail"]


def test_curate_no_screenshots_dir(client):
    from app import TASKS
    TASKS["nodirx1"] = {"id": "nodirx1", "task": "x", "status": "done",
                         "logs": [], "screenshots": []}
    r = client.post("/curate", json={"task_id": "nodirx1"})
    assert r.status_code == 400
    assert "screenshots directory not found" in r.json()["detail"]


_MOCK_CURATE_RESULT = {
    "cards": [
        {
            "filename": "result.png",
            "marketing_score": 8.5,
            "title": "看板",
            "summary": "核心指标一览",
            "feature_tags": ["analytics"],
            "page_type": "dashboard",
            "is_marketing_worthy": True,
            "sensitive_detected": False,
        }
    ],
    "all_results": [],
    "stats": {"total": 1, "after_dedup": 1, "kept": 1},
}


def test_curate_success(client, done_task):
    with patch("app.curate", return_value=_MOCK_CURATE_RESULT):
        r = client.post("/curate", json={"task_id": done_task, "product_context": "test"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["cards"]) == 1
    assert data["cards"][0]["image_url"] == f"/screenshots/{done_task}/result.png"
    assert data["stats"]["kept"] == 1


def test_curate_result_cached_on_task(client, done_task):
    from app import TASKS
    with patch("app.curate", return_value=_MOCK_CURATE_RESULT):
        client.post("/curate", json={"task_id": done_task})

    assert TASKS[done_task].get("curation") is not None


# ── GET /tasks/{id}/curation ──────────────────────────────────────────────────

def test_get_curation_not_found(client):
    r = client.get("/tasks/missing/curation")
    assert r.json()["error"] == "not found"


def test_get_curation_not_yet(client, done_task):
    r = client.get(f"/tasks/{done_task}/curation")
    assert r.json()["error"] == "not curated yet"


def test_get_curation_after_curate(client, done_task):
    with patch("app.curate", return_value=_MOCK_CURATE_RESULT):
        client.post("/curate", json={"task_id": done_task})

    r = client.get(f"/tasks/{done_task}/curation")
    assert r.status_code == 200
    assert "cards" in r.json()


# ── POST /explore ─────────────────────────────────────────────────────────────

def test_explore_creates_task(client):
    with patch("app._run_explore_task"):
        r = client.post("/explore", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert "eid" in r.json()


def test_explore_rejects_invalid_url(client):
    r = client.post("/explore", json={"url": "ftp://example.com"})
    assert r.status_code == 400
    assert "无效" in r.json()["detail"]


def test_explore_rejects_localhost(client):
    r = client.post("/explore", json={"url": "http://localhost:8080"})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_explore_rejects_empty_url(client):
    r = client.post("/explore", json={"url": ""})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_explore_rejects_private_ip(client):
    r = client.post("/explore", json={"url": "http://192.168.1.1"})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_explore_task_stored(client):
    from app import EXPLORE_TASKS
    with patch("app._run_explore_task"):
        r = client.post("/explore", json={
            "url": "https://example.com",
            "product_context": "my product",
        })
    eid = r.json()["eid"]
    assert eid in EXPLORE_TASKS
    assert EXPLORE_TASKS[eid]["url"] == "https://example.com"
    assert EXPLORE_TASKS[eid]["product_context"] == "my product"


# ── GET /explore/{eid} ────────────────────────────────────────────────────────

def test_get_explore_not_found(client):
    r = client.get("/explore/missing")
    assert r.status_code == 404
    assert r.json()["detail"] == "explore task not found"


def test_get_explore_returns_task(client, done_explore):
    r = client.get(f"/explore/{done_explore}")
    assert r.status_code == 200
    assert r.json()["url"] == "https://example.com"
    assert r.json()["status"] == "done"


# ── POST /explore/{eid}/curate ────────────────────────────────────────────────

def test_explore_curate_not_found(client):
    r = client.post("/explore/missing/curate", json={"task_id": "missing"})
    assert r.status_code == 404
    assert r.json()["detail"] == "explore task not found"


def test_explore_curate_not_done(client):
    from app import EXPLORE_TASKS
    EXPLORE_TASKS["epend1"] = {"id": "epend1", "url": "x", "product_context": "",
                                "status": "running", "logs": [], "screenshots": []}
    r = client.post("/explore/epend1/curate", json={"task_id": "epend1"})
    assert r.status_code == 400
    assert "must be 'done'" in r.json()["detail"]


def test_explore_curate_success(client, done_explore):
    with patch("app.curate", return_value=_MOCK_CURATE_RESULT):
        r = client.post(f"/explore/{done_explore}/curate", json={"task_id": done_explore})

    assert r.status_code == 200
    assert r.json()["cards"][0]["image_url"].startswith(f"/screenshots/explore_{done_explore}/")


# ── POST /generate ────────────────────────────────────────────────────────────

_MOCK_GENERATED = {
    "ai_page": {
        "hero": {"headline": "测试标题", "subheadline": "副标题", "cta_text": "立即开始"},
        "features": [{"title": "功能一", "description": "描述", "card_index": 0}],
        "social_proof": "500+ 团队在用",
        "faq": [{"q": "问题", "a": "答案"}],
    },
    "tweets": {
        "single_tweet": "这是一条推文",
        "thread": ["第一条", "第二条"],
        "founder_voice": "创始人口吻",
    },
}


def test_generate_bad_source(client):
    r = client.post("/generate", json={"source": "invalid", "source_id": "x"})
    assert r.status_code == 400
    assert "source must be" in r.json()["detail"]


def test_generate_source_not_found(client):
    r = client.post("/generate", json={"source": "task", "source_id": "missing"})
    assert r.status_code == 404
    assert r.json()["detail"] == "source not found"


def test_generate_no_curation(client, done_task):
    r = client.post("/generate", json={"source": "task", "source_id": done_task})
    assert r.status_code == 400
    assert "run curation first" in r.json()["detail"]


def test_generate_success(client, done_task):
    from app import TASKS
    TASKS[done_task]["curation"] = _MOCK_CURATE_RESULT

    with patch("app.generate_all", return_value=_MOCK_GENERATED):
        r = client.post("/generate", json={"source": "task", "source_id": done_task})

    assert r.status_code == 200
    assert r.json()["ai_page"]["hero"]["headline"] == "测试标题"
    assert len(r.json()["tweets"]["thread"]) == 2


def test_generate_cached_on_task(client, done_task):
    from app import TASKS
    TASKS[done_task]["curation"] = _MOCK_CURATE_RESULT

    with patch("app.generate_all", return_value=_MOCK_GENERATED):
        client.post("/generate", json={"source": "task", "source_id": done_task})

    assert TASKS[done_task].get("generated") is not None


def test_generate_explore_source(client, done_explore):
    from app import EXPLORE_TASKS
    EXPLORE_TASKS[done_explore]["curation"] = _MOCK_CURATE_RESULT

    with patch("app.generate_all", return_value=_MOCK_GENERATED):
        r = client.post("/generate", json={"source": "explore", "source_id": done_explore})

    assert r.status_code == 200
    assert "ai_page" in r.json()


# ── GET /tasks/{id}/generated ─────────────────────────────────────────────────

def test_get_generated_not_found(client):
    r = client.get("/tasks/missing/generated")
    assert r.status_code == 404
    assert r.json()["detail"] == "task not found"


def test_get_generated_not_yet(client, done_task):
    r = client.get(f"/tasks/{done_task}/generated")
    assert r.json()["error"] == "not generated yet"


def test_get_generated_after_generate(client, done_task):
    from app import TASKS
    TASKS[done_task]["curation"] = _MOCK_CURATE_RESULT

    with patch("app.generate_all", return_value=_MOCK_GENERATED):
        client.post("/generate", json={"source": "task", "source_id": done_task})

    r = client.get(f"/tasks/{done_task}/generated")
    assert r.status_code == 200
    assert "ai_page" in r.json()


# ── PATCH /generate/edit ──────────────────────────────────────────────────────

@pytest.fixture
def task_with_generated(client, done_task):
    from app import TASKS
    import copy
    TASKS[done_task]["generated"] = copy.deepcopy(_MOCK_GENERATED)
    return done_task


@pytest.fixture
def explore_with_generated(client, done_explore):
    from app import EXPLORE_TASKS
    import copy
    EXPLORE_TASKS[done_explore]["generated"] = copy.deepcopy(_MOCK_GENERATED)
    return done_explore


def test_edit_bad_source(client):
    r = client.patch("/generate/edit", json={
        "source": "invalid", "source_id": "x",
        "field": "ai_page.hero.headline", "value": "new"
    })
    assert r.status_code == 404
    assert r.json()["detail"] == "source not found"


def test_edit_source_not_found(client):
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": "missing",
        "field": "ai_page.hero.headline", "value": "new"
    })
    assert r.status_code == 404
    assert r.json()["detail"] == "source not found"


def test_edit_no_generated(client, done_task):
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": done_task,
        "field": "ai_page.hero.headline", "value": "new"
    })
    assert r.status_code == 400
    assert "no generated content" in r.json()["detail"]


def test_edit_headline(client, task_with_generated):
    from app import TASKS
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": task_with_generated,
        "field": "ai_page.hero.headline", "value": "新标题"
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert TASKS[task_with_generated]["generated"]["ai_page"]["hero"]["headline"] == "新标题"


def test_edit_single_tweet(client, task_with_generated):
    from app import TASKS
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": task_with_generated,
        "field": "tweets.single_tweet", "value": "新推文内容"
    })
    assert r.json()["ok"] is True
    assert TASKS[task_with_generated]["generated"]["tweets"]["single_tweet"] == "新推文内容"


def test_edit_thread_item(client, task_with_generated):
    from app import TASKS
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": task_with_generated,
        "field": "tweets.thread.0", "value": "修改后的第一条"
    })
    assert r.json()["ok"] is True
    assert TASKS[task_with_generated]["generated"]["tweets"]["thread"][0] == "修改后的第一条"


def test_edit_cta_text(client, task_with_generated):
    from app import TASKS
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": task_with_generated,
        "field": "ai_page.hero.cta_text", "value": "马上试用"
    })
    assert r.json()["ok"] is True
    assert TASKS[task_with_generated]["generated"]["ai_page"]["hero"]["cta_text"] == "马上试用"


def test_edit_explore_source(client, explore_with_generated):
    from app import EXPLORE_TASKS
    r = client.patch("/generate/edit", json={
        "source": "explore", "source_id": explore_with_generated,
        "field": "ai_page.hero.headline", "value": "探索新标题"
    })
    assert r.json()["ok"] is True
    assert EXPLORE_TASKS[explore_with_generated]["generated"]["ai_page"]["hero"]["headline"] == "探索新标题"


def test_edit_returns_field_and_value(client, task_with_generated):
    r = client.patch("/generate/edit", json={
        "source": "task", "source_id": task_with_generated,
        "field": "tweets.founder_voice", "value": "新创始人口吻"
    })
    data = r.json()
    assert data["field"] == "tweets.founder_voice"
    assert data["value"] == "新创始人口吻"


def test_edit_persists_to_db(client, task_with_generated):
    """Edit should call save_task so the change survives."""
    with patch("app.save_task") as mock_save:
        client.patch("/generate/edit", json={
            "source": "task", "source_id": task_with_generated,
            "field": "ai_page.hero.headline", "value": "持久化测试"
        })
    mock_save.assert_called_once()


# ── DELETE /tasks/{id} ────────────────────────────────────────────────────────

def test_delete_task_not_found(client):
    r = client.delete("/tasks/missing")
    assert r.status_code == 404


def test_delete_task_removes_from_store(client, done_task):
    from app import TASKS
    assert done_task in TASKS
    r = client.delete(f"/tasks/{done_task}")
    assert r.status_code == 200
    assert done_task not in TASKS


def test_delete_task_returns_info(client, done_task):
    r = client.delete(f"/tasks/{done_task}")
    data = r.json()
    assert data["deleted"] == done_task
    assert "task" in data


def test_delete_task_removes_screenshots(client, done_task):
    shot_dir = Path(f"screenshots/{done_task}")
    assert shot_dir.exists()
    client.delete(f"/tasks/{done_task}")
    assert not shot_dir.exists()


# ── DELETE /explore/{eid} ─────────────────────────────────────────────────────

def test_delete_explore_not_found(client):
    r = client.delete("/explore/missing")
    assert r.status_code == 404


def test_delete_explore_removes_from_store(client, done_explore):
    from app import EXPLORE_TASKS
    assert done_explore in EXPLORE_TASKS
    r = client.delete(f"/explore/{done_explore}")
    assert r.status_code == 200
    assert done_explore not in EXPLORE_TASKS


def test_delete_explore_removes_screenshots(client, done_explore):
    shot_dir = Path(f"screenshots/explore_{done_explore}")
    assert shot_dir.exists()
    client.delete(f"/explore/{done_explore}")
    assert not shot_dir.exists()


# ── POST /cleanup ─────────────────────────────────────────────────────────────

def test_cleanup_empty_store(client):
    r = client.post("/cleanup?keep_last=10")
    assert r.status_code == 200
    data = r.json()
    assert data["deleted_tasks"] == 0
    assert data["deleted_explores"] == 0


def test_cleanup_keeps_recent_tasks(client):
    from app import TASKS
    # Add 5 done tasks
    for i in range(5):
        tid = f"clean{i:04d}"
        TASKS[tid] = {"id": tid, "task": f"task {i}", "status": "done",
                       "logs": [], "screenshots": [], "created_at": f"2024-01-0{i+1}"}
    r = client.post("/cleanup?keep_last=3")
    assert r.json()["deleted_tasks"] == 2
    # 3 most recent should remain
    remaining = [t for t in TASKS.values() if t["id"].startswith("clean")]
    assert len(remaining) == 3


def test_cleanup_skips_running_tasks(client):
    from app import TASKS
    TASKS["running1"] = {"id": "running1", "task": "x", "status": "running",
                          "logs": [], "screenshots": [], "created_at": "2024-01-01"}
    r = client.post("/cleanup?keep_last=0")
    # running task should NOT be deleted
    assert "running1" in TASKS


def test_cleanup_returns_deleted_ids(client):
    from app import TASKS
    for i in range(3):
        tid = f"del{i:04d}"
        TASKS[tid] = {"id": tid, "task": "x", "status": "done",
                       "logs": [], "screenshots": [], "created_at": f"2024-01-0{i+1}"}
    r = client.post("/cleanup?keep_last=0")
    data = r.json()
    assert len(data["ids"]) == 3
