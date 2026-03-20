"""
FastAPI backend for Playwright + GPT Agent
Run: uvicorn app:app --reload --port 8000
"""

import asyncio
import concurrent.futures
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import socket
import sys
import threading
import time
import uuid
import zipfile
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from agent import run_agent
from agent.task_pool import TaskPool
from agent.browser_pool import BrowserPool
from curator import curate
from explorer import run_exploration
from content_gen import generate_all
from db import init_db, save_task, load_all_tasks, save_explore_task, load_all_explore_tasks, DB_PATH
from db import (
    init_memory_db, save_memory, load_memories, load_memories_paged, get_memory, delete_memory,
    delete_memories_batch, update_memory_hit, get_memory_stats,
    init_recording_db, save_recording, load_all_recordings, get_recording, delete_recording,
)
from utils import validate_url
from workflow import (
    init_workflow_db, parse_workflow, validate_workflow,
    save_workflow, load_all_workflows, delete_workflow,
    save_workflow_run, load_workflow_runs, load_workflow_run,
    scan_workflow_directory,
    WorkflowEngine, WorkflowCreateRequest, WorkflowRunRequest,
)
from template_loader import scan_templates, TEMPLATE_CATEGORIES
from urllib.parse import urlparse

# ── Configuration constants ───────────────────────────────────────────────────

_AGENT_THREAD_WORKERS = int(os.getenv("AGENT_WORKERS", "4"))
_MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
_CALLBACK_TIMEOUT = 30      # seconds for cross-thread future.result()
_BROADCAST_TIMEOUT = 5
_USER_REPLY_TIMEOUT = 300   # 5 minutes

API_KEY = os.getenv("API_KEY")  # Optional API key for authentication
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
MAX_TASKS_KEEP = int(os.getenv("MAX_TASKS_KEEP", "50"))
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
_BROWSER_POOL_SIZE = int(os.getenv("BROWSER_POOL_SIZE", str(_MAX_CONCURRENT_TASKS)))
_BROWSER_POOL_IDLE_TIMEOUT = float(os.getenv("BROWSER_POOL_IDLE_TIMEOUT", "300"))
_USE_BROWSER_POOL = os.getenv("USE_BROWSER_POOL", "true").lower() in ("true", "1", "yes")

@asynccontextmanager
async def lifespan(app):
    """启动时预热浏览器池，关闭时清理。"""
    if _browser_pool:
        try:
            _browser_pool.start_sync()
            print(f"[BrowserPool] Started with {_browser_pool.max_size} browsers (headless={HEADLESS})")
        except Exception as e:
            print(f"[BrowserPool] Failed to start: {e}", file=sys.stderr)
    yield
    if _browser_pool and _browser_pool.started:
        try:
            _browser_pool.shutdown_sync()
            print("[BrowserPool] Shutdown complete")
        except Exception as e:
            print(f"[BrowserPool] Shutdown error: {e}", file=sys.stderr)


app = FastAPI(
    title="Skyvern",
    version="0.1.0",
    description="AI-driven browser automation platform",
    lifespan=lifespan,
)

# CORS configuration
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else ["http://localhost:8000", "http://127.0.0.1:8000"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _verify_api_key(x_api_key: str | None = Header(default=None)):
    """Optional API key check. If API_KEY env var is not set, auth is skipped."""
    if API_KEY:
        if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

SCREENSHOTS_ROOT = Path("screenshots").resolve()
Path("screenshots").mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)
init_db()
init_workflow_db()
init_memory_db()
init_recording_db()

# 启动时扫描 workflows/ 目录，加载 YAML 工作流
WORKFLOWS: dict[str, dict] = load_all_workflows()
_loaded_wfs = scan_workflow_directory()
for wf in _loaded_wfs:
    WORKFLOWS[wf["id"]] = wf

TEMPLATES: dict[str, dict] = scan_templates()

app.mount("/static", StaticFiles(directory="static"), name="static")

# 专用线程池：在独立线程里用 ProactorEventLoop 跑 Playwright，避免 Windows 上主循环的 NotImplementedError
_agent_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_AGENT_THREAD_WORKERS, thread_name_prefix="playwright_agent")

# 并行任务执行池：Semaphore 控制最大并发浏览器数
_task_pool = TaskPool(max_workers=_MAX_CONCURRENT_TASKS)

# 浏览器实例池：池化复用���览器实例，避免每次冷启动
_browser_pool: BrowserPool | None = None
if _USE_BROWSER_POOL:
    def _proxy_reachable(proxy_url: str) -> bool:
        try:
            p = urlparse(proxy_url)
            host = p.hostname
            port = p.port
            if not host or not port:
                return False
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            return False

    _proxy = None
    if os.environ.get("USE_PROXY"):
        _proxy_candidate = os.getenv("PROXY_SERVER", "http://127.0.0.1:7897")
        if _proxy_reachable(_proxy_candidate):
            _proxy = _proxy_candidate
        else:
            print(f"[BrowserPool] ⚠ 代理不可用，已禁用: {_proxy_candidate}", file=sys.stderr)
    _browser_pool = BrowserPool(
        max_size=_BROWSER_POOL_SIZE,
        idle_timeout=_BROWSER_POOL_IDLE_TIMEOUT,
        headless=HEADLESS,
        proxy=_proxy,
    )

# ── In-memory store (backed by SQLite) ────────────────────────────────────────

TASKS: dict[str, dict] = load_all_tasks()
# { id, task, status: pending|running|done|failed|waiting_input, logs: [], screenshots: [] }

EXPLORE_TASKS: dict[str, dict] = load_all_explore_tasks()

def _created_at_key(t: dict) -> float:
    """用于排序的 created_at 归一化（兼容历史数据的 str/float 混用）。"""
    v = t.get("created_at", 0)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except Exception:
            return 0.0
    return 0.0


def _startup_cleanup():
    for store in (TASKS, EXPLORE_TASKS):
        # 重置残留的 running/pending 任务（上次异常退出遗留）
        _save_fn = save_task if store is TASKS else save_explore_task
        for t in store.values():
            if t["status"] in ("running", "pending", "waiting_input"):
                t["status"] = "failed"
                t["logs"] = t.get("logs", [])
                t["logs"].append("服务重启，任务被中断")
                _save_fn(t)

        done = sorted(
            [t for t in store.values() if t["status"] in ("done", "failed")],
            key=_created_at_key,
        )
        for t in (done[:-MAX_TASKS_KEEP] if len(done) > MAX_TASKS_KEEP else []):
            store.pop(t["id"], None)

_startup_cleanup()


# Per-client SSE queues for broadcast
_SSE_CLIENTS: list[asyncio.Queue] = []
_SSE_LOCK = asyncio.Lock()  # 保护 _SSE_CLIENTS 并发修改

# 每个任务的"等待用户输入"状态
_PENDING_QUESTIONS: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()  # 保护 _PENDING_QUESTIONS 跨线程访问

# 任务取消信号
_CANCEL_EVENTS: dict[str, threading.Event] = {}


async def _send_webhook(webhook_url: str, payload: dict):
    """异步 POST webhook 回调，失败静默（不影响主流程）"""
    if not webhook_url:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            print(f"[webhook] POST {webhook_url} → {resp.status_code}")
    except Exception as e:
        print(f"[webhook] POST {webhook_url} failed: {e}", file=sys.stderr)


async def _broadcast(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    async with _SSE_LOCK:
        clients = list(_SSE_CLIENTS)
    for q in clients:
        try:
            await q.put(data)
        except Exception:
            pass


async def _log_callback(task_id: str, message: str):
    if task_id not in TASKS:
        return
    # 解析进度消息，广播为独立事件
    if message.startswith("__PROGRESS__:"):
        progress = message.replace("__PROGRESS__:", "")
        current, total = progress.split("/")
        TASKS[task_id]["progress"] = {"current": int(current), "total": int(total)}
        await _broadcast({"type": "progress", "task_id": task_id, "current": int(current), "total": int(total)})
        return
    TASKS[task_id]["logs"].append(message)
    await _broadcast({"type": "log", "task_id": task_id, "data": message})


async def _screenshot_callback(task_id: str, filename: str):
    """实时推送新截图给前端"""
    if task_id not in TASKS:
        return
    if filename not in TASKS[task_id]["screenshots"]:
        TASKS[task_id]["screenshots"].append(filename)
    await _broadcast({"type": "new_screenshot", "task_id": task_id, "filename": filename})


def _run_agent_in_thread(
    task_id: str,
    task: str,
    main_loop: asyncio.AbstractEventLoop,
    cancel_event: threading.Event,
) -> tuple[bool, list[str] | str]:
    """
    在独立线程中运行 run_agent，使用本线程的 ProactorEventLoop。
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def thread_safe_log(tid: str, msg: str):
        # fire-and-forget：不阻塞 agent 事件循环
        asyncio.run_coroutine_threadsafe(_log_callback(tid, msg), main_loop)

    async def thread_safe_screenshot(tid: str, filename: str):
        asyncio.run_coroutine_threadsafe(_screenshot_callback(tid, filename), main_loop)

    async def ask_user_callback(tid: str, question: str, reason: str) -> str:
        ev = threading.Event()
        with _PENDING_LOCK:
            _PENDING_QUESTIONS[tid] = {"question": question, "reason": reason, "event": ev, "answer": None}

        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "waiting_input", "task_id": tid, "question": question, "reason": reason}),
            main_loop,
        ).result(timeout=_BROADCAST_TIMEOUT)
        asyncio.run_coroutine_threadsafe(
            _update_task_status(tid, "waiting_input"),
            main_loop,
        ).result(timeout=_BROADCAST_TIMEOUT)

        # 分段等待：允许在“取消任务”时尽快退出，从而释放 BrowserPool 槽位
        answered = False
        start_ts = time.monotonic()
        while time.monotonic() - start_ts < _USER_REPLY_TIMEOUT:
            if cancel_event.is_set():
                with _PENDING_LOCK:
                    _PENDING_QUESTIONS.pop(tid, None)
                raise TimeoutError("用户已取消")
            answered = ev.wait(timeout=1.0)
            if answered:
                break

        with _PENDING_LOCK:
            entry = _PENDING_QUESTIONS.pop(tid, {})

        if not answered or not entry.get("answer"):
            raise TimeoutError("用户未在5分钟内回答")

        asyncio.run_coroutine_threadsafe(
            _update_task_status(tid, "running"),
            main_loop,
        ).result(timeout=_BROADCAST_TIMEOUT)

        return entry["answer"]

    try:
        t = TASKS.get(task_id, {})
        browser_mode = t.get("browser_mode", "builtin")

        # 浏览器池：builtin 模式且池可用时，走 pool 模式（复用预热 browser，跳过冷启动）
        _use_pool = (
            _browser_pool is not None
            and _browser_pool.started
            and browser_mode == "builtin"
        )
        _effective_browser_mode = "pool" if _use_pool else browser_mode

        agent_result = loop.run_until_complete(
            run_agent(
                task=task,
                headless=HEADLESS,
                task_id=task_id,
                log_callback=thread_safe_log,
                cookies_path=f"data/cookies/cookies_{task_id}.json",
                screenshots_dir=f"screenshots/{task_id}",
                ask_user_callback=ask_user_callback,
                screenshot_callback=thread_safe_screenshot,
                browser_mode=_effective_browser_mode,
                cdp_url=t.get("cdp_url", "http://localhost:9222"),
                chrome_profile=t.get("chrome_profile", "Default"),
                pool=_browser_pool if _use_pool else None,
                cancel_event=cancel_event,
            )
        )

        # agent_result: {"success": bool, "reason": str, "steps": int, "cost": dict}
        task_succeeded = agent_result.get("success", False) if isinstance(agent_result, dict) else True
        cost_data = agent_result.get("cost", {}) if isinstance(agent_result, dict) else {}
        shot_dir = Path(f"screenshots/{task_id}")
        screenshots = []
        if shot_dir.exists():
            screenshots = sorted(
                [f.name for f in shot_dir.glob("*.png")] + [f.name for f in shot_dir.glob("*.jpg")],
                key=lambda n: (shot_dir / n).stat().st_mtime
            )
        return (task_succeeded, screenshots, cost_data)
    except Exception as e:
        return (False, str(e), {})
    finally:
        loop.close()


async def _update_task_status(task_id: str, status: str):
    if task_id not in TASKS:
        return
    TASKS[task_id]["status"] = status
    save_task(TASKS[task_id])
    await _broadcast({"type": "status", "task_id": task_id, "data": status})


async def _run_task(task_id: str, task: str):
    TASKS[task_id]["status"] = "running"
    TASKS[task_id]["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_task(TASKS[task_id])
    await _broadcast({"type": "status", "task_id": task_id, "data": "running"})

    webhook_url = TASKS[task_id].get("webhook_url", "")
    timeout_sec = TASKS[task_id].get("timeout", 0)

    # 注册取消信号
    cancel_event = threading.Event()
    _CANCEL_EVENTS[task_id] = cancel_event

    main_loop = asyncio.get_running_loop()
    final_status = "failed"
    try:
        future = asyncio.get_event_loop().run_in_executor(
            _agent_executor,
            _run_agent_in_thread,
            task_id,
            task,
            main_loop,
            cancel_event,
        )

        # 超时控制
        if timeout_sec > 0:
            try:
                ok, result, cost_data = await asyncio.wait_for(future, timeout=timeout_sec)
            except asyncio.TimeoutError:
                TASKS[task_id]["status"] = "failed"
                TASKS[task_id]["logs"].append(f"ERROR: 任务超时（{timeout_sec}秒）")
                save_task(TASKS[task_id])
                await _broadcast({"type": "status", "task_id": task_id, "data": "failed"})
                await _send_webhook(webhook_url, {
                    "task_id": task_id, "status": "failed",
                    "reason": f"timeout ({timeout_sec}s)", "task": task,
                })
                return
        else:
            ok, result, cost_data = await future

        # 检查是否被取消
        if cancel_event.is_set():
            TASKS[task_id]["status"] = "cancelled"
            TASKS[task_id]["logs"].append("任务已被用户取消")
            save_task(TASKS[task_id])
            await _broadcast({"type": "status", "task_id": task_id, "data": "cancelled"})
            await _send_webhook(webhook_url, {
                "task_id": task_id, "status": "cancelled", "task": task,
            })
            return

        # 保存成本数据
        if cost_data:
            TASKS[task_id]["cost"] = cost_data

        if ok:
            TASKS[task_id]["screenshots"] = result
            TASKS[task_id]["status"] = "done"
            final_status = "done"
            save_task(TASKS[task_id])
            await _broadcast({"type": "status", "task_id": task_id, "data": "done", "screenshots": result, "cost": cost_data})
            await _send_webhook(webhook_url, {
                "task_id": task_id, "status": "done", "task": task,
                "screenshots": result, "cost": cost_data,
            })
        else:
            TASKS[task_id]["status"] = "failed"
            TASKS[task_id]["logs"].append(f"ERROR: {result}")
            save_task(TASKS[task_id])
            await _broadcast({"type": "status", "task_id": task_id, "data": "failed"})
            await _send_webhook(webhook_url, {
                "task_id": task_id, "status": "failed",
                "reason": str(result)[:500], "task": task,
            })
    except Exception as e:
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["logs"].append(f"ERROR: {e}")
        save_task(TASKS[task_id])
        await _broadcast({"type": "status", "task_id": task_id, "data": "failed"})
        await _send_webhook(webhook_url, {
            "task_id": task_id, "status": "failed",
            "reason": str(e)[:500], "task": task,
        })
    finally:
        TASKS[task_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_task(TASKS[task_id])
        _CANCEL_EVENTS.pop(task_id, None)

        # ── 记忆提取：任务完成后自动提取经验 ──
        try:
            from agent.memory import MemoryManager
            _mem_mgr = MemoryManager()
            _task_success = TASKS[task_id]["status"] == "done"
            _task_logs = TASKS[task_id].get("logs", [])
            _extracted = _mem_mgr.extract_memories(
                task_id=task_id, task=task,
                logs=_task_logs, success=_task_success,
            )
            if _extracted:
                saved_ids = _mem_mgr.save_memories(_extracted)
                if saved_ids:
                    print(f"  [memory] 提取并保存 {len(saved_ids)} 条记忆 (task={task_id})")
        except Exception as e:
            print(f"  [memory] 提取失败: {e}")


# ── API endpoints ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    tasks: list[str] = Field(max_length=50)
    browser_mode: str = Field(default="builtin", pattern=r'^(builtin|user_chrome|cdp)$')
    cdp_url: str = Field(default="http://localhost:9222", max_length=500)
    chrome_profile: str = Field(default="Default", max_length=100)
    webhook_url: str = Field(default="", max_length=500)
    timeout: int = Field(default=0, ge=0, le=3600)

    @field_validator('tasks')
    @classmethod
    def validate_task_texts(cls, v):
        for i, text in enumerate(v):
            if len(text) > 10000:
                raise ValueError(f'Task {i} exceeds 10000 character limit')
        return v


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/health")
def health_check():
    bp = _browser_pool.stats() if _browser_pool and _browser_pool.started else {"enabled": False}
    return {"status": "ok", "tasks": len(TASKS), "explore_tasks": len(EXPLORE_TASKS), "pool": _task_pool.stats_dict(), "browser_pool": bp}


@app.get("/pool")
def pool_status(_: None = Depends(_verify_api_key)):
    """查询并行任务池状态：并发数、运行中/排队/已完成任务数。"""
    return _task_pool.stats_dict()


class PoolResizeRequest(BaseModel):
    max_workers: int


@app.put("/pool")
def pool_resize(req: PoolResizeRequest, _: None = Depends(_verify_api_key)):
    """动态调整最大并发浏览器数。"""
    if req.max_workers < 1 or req.max_workers > 10:
        raise HTTPException(status_code=400, detail="max_workers must be 1-10")
    old = _task_pool.max_workers
    _task_pool.resize(req.max_workers)
    return {"old_max_workers": old, "new_max_workers": req.max_workers, "pool": _task_pool.stats_dict()}


# ── 浏览器池 API ─────────────────────────────────────────────────────────────

@app.get("/browser-pool")
def browser_pool_status(_: None = Depends(_verify_api_key)):
    """查询浏览器池状态。"""
    if not _browser_pool or not _browser_pool.started:
        return {"enabled": False}
    return {"enabled": True, **_browser_pool.stats()}


class BrowserPoolResizeRequest(BaseModel):
    max_size: int


@app.put("/browser-pool")
def browser_pool_resize(req: BrowserPoolResizeRequest, _: None = Depends(_verify_api_key)):
    """动态调整浏览器池大小。"""
    if not _browser_pool or not _browser_pool.started:
        raise HTTPException(status_code=400, detail="Browser pool is not enabled")
    if req.max_size < 1 or req.max_size > 10:
        raise HTTPException(status_code=400, detail="max_size must be 1-10")
    old = _browser_pool.max_size
    _browser_pool.resize_sync(req.max_size)
    return {"old_max_size": old, "new_max_size": req.max_size, **_browser_pool.stats()}


@app.post("/browser-pool/warmup")
def browser_pool_warmup(_: None = Depends(_verify_api_key)):
    """手动触发浏览器池预热。"""
    if not _browser_pool or not _browser_pool.started:
        raise HTTPException(status_code=400, detail="Browser pool is not enabled")
    _browser_pool.warmup_sync()
    return {"status": "ok", **_browser_pool.stats()}


@app.get("/screenshots/{task_id}/{filename}")
async def serve_screenshot(task_id: str, filename: str, _: None = Depends(_verify_api_key)):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', task_id) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', filename):
        raise HTTPException(status_code=400, detail="invalid path")
    resolved = (SCREENSHOTS_ROOT / task_id / filename).resolve()
    if not resolved.is_relative_to(SCREENSHOTS_ROOT):
        raise HTTPException(status_code=400, detail="invalid path")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(str(resolved))


@app.post("/run")
async def submit_tasks(req: RunRequest, background_tasks: BackgroundTasks, _: None = Depends(_verify_api_key)):
    active_count = sum(1 for t in TASKS.values() if t["status"] in ("pending", "running"))
    if active_count >= MAX_QUEUE_SIZE:
        raise HTTPException(status_code=429, detail=f"queue full ({active_count}/{MAX_QUEUE_SIZE} active tasks)")
    ids = []
    task_texts = []
    for task_text in req.tasks:
        if not task_text.strip():
            continue
        tid = uuid.uuid4().hex[:8]
        TASKS[tid] = {
            "id": tid, "task": task_text, "status": "pending",
            "logs": [], "screenshots": [],
            "browser_mode": req.browser_mode,
            "cdp_url": req.cdp_url,
            "chrome_profile": req.chrome_profile,
            "webhook_url": req.webhook_url,
            "timeout": req.timeout,
            "created_at": time.time(),
        }
        save_task(TASKS[tid])
        ids.append(tid)
        task_texts.append(task_text)

    # 通过 TaskPool 提交任务（Semaphore 控制并发）
    for tid, t in zip(ids, task_texts):
        await _task_pool.submit(tid, _run_task, t)

    # broadcast new pending tasks
    for tid, t in zip(ids, task_texts):
        await _broadcast({"type": "new_task", "task": TASKS[tid]})

    return {"task_ids": ids, "pool": _task_pool.stats_dict()}


@app.get("/tasks")
def list_tasks(
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _: None = Depends(_verify_api_key),
):
    """列出任务，支持分页和过滤。"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    all_tasks = list(TASKS.values())
    # 按 created_at 倒序
    all_tasks.sort(key=_created_at_key, reverse=True)
    if status:
        all_tasks = [t for t in all_tasks if t.get("status") == status]
    if q:
        q_lower = q.lower()
        all_tasks = [t for t in all_tasks if q_lower in t.get("task", "").lower()]
    total = len(all_tasks)
    return {"tasks": all_tasks[offset:offset + limit], "total": total, "limit": limit, "offset": offset}


class BatchDeleteRequest(BaseModel):
    task_ids: list[str] = Field(max_length=100)


@app.post("/tasks/batch-delete")
async def batch_delete_tasks(req: BatchDeleteRequest, _: None = Depends(_verify_api_key)):
    """批量删除任务。"""
    deleted = []
    for task_id in req.task_ids:
        if task_id not in TASKS:
            continue
        TASKS.pop(task_id)
        shot_dir = Path(f"screenshots/{task_id}")
        if shot_dir.exists():
            shutil.rmtree(shot_dir, ignore_errors=True)
        deleted.append(task_id)
    if deleted:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("DELETE FROM tasks WHERE id=?", [(tid,) for tid in deleted])
    return {"deleted": len(deleted), "deleted_ids": deleted}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, _: None = Depends(_verify_api_key)):
    """取消运行中的任务"""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    status = TASKS[task_id]["status"]
    if status in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"task already {status}")

    # 设置取消信号
    cancel_ev = _CANCEL_EVENTS.get(task_id)
    if cancel_ev:
        cancel_ev.set()

    # 如果还在 pending 状态（未开始执行），直接标记取消
    if status == "pending":
        TASKS[task_id]["status"] = "cancelled"
        TASKS[task_id]["logs"].append("任务已被用户取消（未开始执行）")
        save_task(TASKS[task_id])
        await _broadcast({"type": "status", "task_id": task_id, "data": "cancelled"})
        webhook_url = TASKS[task_id].get("webhook_url", "")
        if webhook_url:
            await _send_webhook(webhook_url, {
                "task_id": task_id, "status": "cancelled",
                "task": TASKS[task_id].get("task", ""),
            })

    return {"ok": True, "task_id": task_id, "message": "cancel signal sent"}


# NOTE: /tasks/stream must be defined BEFORE /tasks/{task_id} to avoid route conflict
@app.get("/tasks/stream")
async def sse_stream():
    queue: asyncio.Queue = asyncio.Queue()
    async with _SSE_LOCK:
        _SSE_CLIENTS.append(queue)

    async def event_generator():
        try:
            # send current snapshot on connect (agent tasks + explore tasks)
            snapshot = json.dumps(
                {
                    "type": "snapshot",
                    "tasks": list(TASKS.values()),
                    "explore_tasks": list(EXPLORE_TASKS.values()),
                },
                ensure_ascii=False,
            )
            yield f"data: {snapshot}\n\n"

            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            async with _SSE_LOCK:
                try:
                    _SSE_CLIENTS.remove(queue)
                except ValueError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/tasks/{task_id}/logs")
def get_logs(task_id: str):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    return {"logs": TASKS[task_id]["logs"]}


@app.post("/tasks/{task_id}/reply")
async def reply_to_task(task_id: str, answer: str = ""):
    """
    用户回答 agent 的提问，唤醒等待中的 agent 线程。
    """
    with _PENDING_LOCK:
        entry = _PENDING_QUESTIONS.get(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="no pending question for this task")

    entry["answer"] = answer
    entry["event"].set()  # 唤醒 agent 线程

    return {"ok": True, "answer": answer}


@app.get("/tasks/{task_id}")
def get_task(task_id: str, _: None = Depends(_verify_api_key)):
    """获取单个任务详情。"""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    return TASKS[task_id]


@app.get("/tasks/{task_id}/trace")
def get_task_trace(task_id: str, _: None = Depends(_verify_api_key)):
    """获取任务的决策链路追踪数据。"""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    task = TASKS[task_id]
    # 优先从内存中的 result 获取 trace
    result = task.get("result", {})
    if isinstance(result, dict) and "trace" in result:
        return result["trace"]
    # 尝试从文件加载
    trace_path = Path("screenshots") / task_id / "trace.json"
    if trace_path.exists():
        try:
            return json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="trace not found for this task")


@app.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, _: None = Depends(_verify_api_key)):
    """重试任务：用原始参数创建新任务。"""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    original = TASKS[task_id]
    if original["status"] not in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"task is still {original['status']}, cannot retry")

    new_id = uuid.uuid4().hex[:8]
    TASKS[new_id] = {
        "id": new_id,
        "task": original["task"],
        "status": "pending",
        "logs": [],
        "screenshots": [],
        "browser_mode": original.get("browser_mode", "builtin"),
        "cdp_url": original.get("cdp_url", "http://localhost:9222"),
        "chrome_profile": original.get("chrome_profile", "Default"),
        "webhook_url": original.get("webhook_url", ""),
        "timeout": original.get("timeout", 0),
        "created_at": time.time(),
        "retry_of": task_id,
    }
    save_task(TASKS[new_id])
    await _task_pool.submit(new_id, _run_task, original["task"])
    await _broadcast({"type": "new_task", "task": TASKS[new_id]})
    return {"task_id": new_id, "retry_of": task_id}


# ── Curation endpoint ─────────────────────────────────────────────────────────

class CurateRequest(BaseModel):
    task_id: str
    product_context: str = ""
    min_score: float = 5.0
    max_cards: int = 8


@app.post("/curate")
async def curate_task(req: CurateRequest, _: None = Depends(_verify_api_key)):
    if req.task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")

    task = TASKS[req.task_id]
    if task["status"] != "done":
        raise HTTPException(status_code=400, detail=f"task status is '{task['status']}', must be 'done'")

    shot_dir = Path(f"screenshots/{req.task_id}")
    if not shot_dir.exists():
        raise HTTPException(status_code=400, detail="screenshots directory not found")

    # Run curation in thread pool to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: curate(
            shot_dir,
            product_context=req.product_context,
            min_score=req.min_score,
            max_cards=req.max_cards,
        ),
    )

    # Attach public URLs to each card
    for card in result["cards"]:
        card["image_url"] = f"/screenshots/{req.task_id}/{card['filename']}"
    for r in result["all_results"]:
        r["image_url"] = f"/screenshots/{req.task_id}/{r['filename']}"

    # Cache on the task object and persist
    TASKS[req.task_id]["curation"] = result
    save_task(TASKS[req.task_id])

    return result


@app.get("/tasks/{task_id}/curation")
def get_curation(task_id: str):
    if task_id not in TASKS:
        return {"error": "not found"}
    return TASKS[task_id].get("curation") or {"error": "not curated yet"}


# ── Explore endpoint ──────────────────────────────────────────────────────────

class ExploreRequest(BaseModel):
    url: str
    product_context: str = ""
    max_pages: int = 12
    cookies_path: str = ""


def _run_exploration_in_thread(
    eid: str,
    url: str,
    product_context: str,
    max_pages: int,
    cookies_path: str,
    main_loop: asyncio.AbstractEventLoop,
) -> tuple[bool, dict | str]:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def thread_safe_log(msg: str):
        async def _append_and_broadcast():
            if eid in EXPLORE_TASKS:
                EXPLORE_TASKS[eid]["logs"].append(msg)
            await _broadcast({"type": "explore_log", "eid": eid, "data": msg})
        fut = asyncio.run_coroutine_threadsafe(_append_and_broadcast(), main_loop)
        try:
            fut.result(timeout=_CALLBACK_TIMEOUT)
        except Exception as e:
            print(f"[warn] explore log callback error: {e}", file=sys.stderr)

    try:
        result = loop.run_until_complete(
            run_exploration(
                url=url,
                product_context=product_context,
                screenshots_dir=f"screenshots/explore_{eid}",
                cookies_path=cookies_path or None,
                max_pages=max_pages,
                headless=HEADLESS,
                log_fn=thread_safe_log,
            )
        )
        return (True, result)
    except Exception as e:
        return (False, str(e))
    finally:
        loop.close()


async def _run_explore_task(eid: str, url: str, product_context: str, max_pages: int, cookies_path: str):
    EXPLORE_TASKS[eid]["status"] = "running"
    save_explore_task(EXPLORE_TASKS[eid])
    await _broadcast({"type": "explore_status", "eid": eid, "data": "running"})

    main_loop = asyncio.get_running_loop()
    try:
        ok, result = await asyncio.get_event_loop().run_in_executor(
            _agent_executor,
            _run_exploration_in_thread,
            eid, url, product_context, max_pages, cookies_path, main_loop,
        )
        if ok:
            EXPLORE_TASKS[eid]["result"] = result
            EXPLORE_TASKS[eid]["screenshots"] = result.get("screenshots", [])
            EXPLORE_TASKS[eid]["status"] = "done"
            save_explore_task(EXPLORE_TASKS[eid])
            await _broadcast({"type": "explore_status", "eid": eid, "data": "done",
                               "screenshots": result.get("screenshots", [])})
        else:
            EXPLORE_TASKS[eid]["status"] = "failed"
            EXPLORE_TASKS[eid]["logs"].append(f"ERROR: {result}")
            save_explore_task(EXPLORE_TASKS[eid])
            await _broadcast({"type": "explore_status", "eid": eid, "data": "failed"})
    except Exception as e:
        EXPLORE_TASKS[eid]["status"] = "failed"
        EXPLORE_TASKS[eid]["logs"].append(f"ERROR: {e}")
        save_explore_task(EXPLORE_TASKS[eid])
        await _broadcast({"type": "explore_status", "eid": eid, "data": "failed"})


@app.post("/explore")
async def start_explore(req: ExploreRequest, background_tasks: BackgroundTasks, _: None = Depends(_verify_api_key)):
    valid, err = validate_url(req.url)
    if not valid:
        raise HTTPException(status_code=400, detail=f"无效 URL: {err}")

    eid = uuid.uuid4().hex[:8]
    EXPLORE_TASKS[eid] = {
        "id": eid, "url": req.url, "product_context": req.product_context,
        "status": "pending", "logs": [], "screenshots": [], "result": None,
        "created_at": time.time(),
    }
    save_explore_task(EXPLORE_TASKS[eid])
    background_tasks.add_task(
        _run_explore_task, eid, req.url, req.product_context, req.max_pages, req.cookies_path
    )
    await _broadcast({"type": "explore_new", "task": EXPLORE_TASKS[eid]})
    return {"eid": eid}


@app.get("/explore/{eid}")
def get_explore(eid: str):
    if eid not in EXPLORE_TASKS:
        raise HTTPException(status_code=404, detail="explore task not found")
    return EXPLORE_TASKS[eid]


@app.post("/explore/{eid}/curate")
async def curate_explore(eid: str, req: CurateRequest, _: None = Depends(_verify_api_key)):
    if eid not in EXPLORE_TASKS:
        raise HTTPException(status_code=404, detail="explore task not found")
    et = EXPLORE_TASKS[eid]
    if et["status"] != "done":
        raise HTTPException(status_code=400, detail=f"explore status is '{et['status']}', must be 'done'")

    shot_dir = Path(f"screenshots/explore_{eid}")
    if not shot_dir.exists():
        raise HTTPException(status_code=400, detail="screenshots directory not found")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: curate(shot_dir, product_context=et.get("product_context", ""),
                       min_score=req.min_score, max_cards=req.max_cards),
    )
    for card in result["cards"]:
        card["image_url"] = f"/screenshots/explore_{eid}/{card['filename']}"
    for r in result["all_results"]:
        r["image_url"] = f"/screenshots/explore_{eid}/{r['filename']}"

    EXPLORE_TASKS[eid]["curation"] = result
    save_explore_task(EXPLORE_TASKS[eid])
    return result


# ── Generate endpoint ─────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    source: str          # "task" or "explore"
    source_id: str       # task_id or eid
    language: str = "zh-CN"
    tone: str = "professional"
    run_review: bool = True


@app.post("/generate")
async def generate_content(req: GenerateRequest, _: None = Depends(_verify_api_key)):
    # Resolve curation cards from the right store
    if req.source == "task":
        store = TASKS
    elif req.source == "explore":
        store = EXPLORE_TASKS
    else:
        raise HTTPException(status_code=400, detail="source must be 'task' or 'explore'")

    if req.source_id not in store:
        raise HTTPException(status_code=404, detail="source not found")

    item = store[req.source_id]
    curation = item.get("curation")
    if not curation or not curation.get("cards"):
        raise HTTPException(status_code=400, detail="no curated cards found — run curation first")

    cards = curation["cards"]
    product_context = item.get("product_context") or item.get("task", "")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: generate_all(
            cards,
            product_context=product_context,
            language=req.language,
            tone=req.tone,
            run_review=req.run_review,
        ),
    )

    item["generated"] = result
    if req.source == "task":
        save_task(item)
    else:
        save_explore_task(item)
    return result


@app.get("/tasks/{task_id}/generated")
def get_generated_task(task_id: str):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    return TASKS[task_id].get("generated") or {"error": "not generated yet"}


@app.get("/explore/{eid}/generated")
def get_generated_explore(eid: str):
    if eid not in EXPLORE_TASKS:
        raise HTTPException(status_code=404, detail="explore task not found")
    return EXPLORE_TASKS[eid].get("generated") or {"error": "not generated yet"}


# ── Edit generated content ────────────────────────────────────────────────────

class EditGeneratedRequest(BaseModel):
    source: str       # "task" or "explore"
    source_id: str
    field: str        # dot-path: "ai_page.hero.headline", "tweets.single_tweet", etc.
    value: str


def _set_nested(obj, path: str, value: str):
    """Set a value at a dot-separated path in a nested dict/list."""
    keys = path.split(".")
    for k in keys[:-1]:
        idx = None
        try:
            idx = int(k)
        except ValueError:
            pass
        if idx is not None:
            obj = obj[idx]
        else:
            if k not in obj or not isinstance(obj[k], (dict, list)):
                obj[k] = {}
            obj = obj[k]
    last = keys[-1]
    try:
        last = int(last)
    except ValueError:
        pass
    obj[last] = value


@app.patch("/generate/edit")
async def edit_generated(req: EditGeneratedRequest, _: None = Depends(_verify_api_key)):
    store = TASKS if req.source == "task" else EXPLORE_TASKS
    if req.source_id not in store:
        raise HTTPException(status_code=404, detail="source not found")

    item = store[req.source_id]
    generated = item.get("generated")
    if not generated:
        raise HTTPException(status_code=400, detail="no generated content to edit")

    _set_nested(generated, req.field, req.value)
    item["generated"] = generated

    if req.source == "task":
        save_task(item)
    else:
        save_explore_task(item)

    return {"ok": True, "field": req.field, "value": req.value}


# ── Export endpoints ──────────────────────────────────────────────────────────

def _build_export_bundle(source: str, source_id: str) -> dict | None:
    """Assemble a full export bundle from task or explore data."""
    store = TASKS if source == "task" else EXPLORE_TASKS
    item = store.get(source_id)
    if not item:
        return None
    return {
        "source": source,
        "source_id": source_id,
        "url": item.get("url") or item.get("task", ""),
        "product_context": item.get("product_context") or item.get("task", ""),
        "status": item.get("status"),
        "site_understanding": (item.get("result") or {}).get("site_understanding"),
        "curation": item.get("curation"),
        "generated": item.get("generated"),
        "screenshots": item.get("screenshots", []),
    }


@app.get("/export/{source}/{source_id}/json")
def export_json(source: str, source_id: str):
    """Download full result bundle as JSON."""
    if source not in ("task", "explore"):
        return {"error": "source must be task or explore"}
    bundle = _build_export_bundle(source, source_id)
    if not bundle:
        return {"error": "not found"}

    content = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"export_{source_id}.json"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/{source}/{source_id}/zip")
def export_zip(source: str, source_id: str):
    """Download screenshots + generated content as a ZIP."""
    if source not in ("task", "explore"):
        return {"error": "source must be task or explore"}
    bundle = _build_export_bundle(source, source_id)
    if not bundle:
        return {"error": "not found"}

    shot_dir = (
        Path(f"screenshots/{source_id}")
        if source == "task"
        else Path(f"screenshots/explore_{source_id}")
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add screenshots
        if shot_dir.exists():
            for img in sorted(shot_dir.glob("*.png")):
                zf.write(img, f"screenshots/{img.name}")

        # Add curated cards metadata
        if bundle.get("curation"):
            zf.writestr(
                "curation.json",
                json.dumps(bundle["curation"], ensure_ascii=False, indent=2),
            )

        # Add generated content
        if bundle.get("generated"):
            gen = bundle["generated"]
            zf.writestr(
                "generated.json",
                json.dumps(gen, ensure_ascii=False, indent=2),
            )
            # Also write tweets as plain text for easy copy-paste
            tweets = gen.get("tweets", {})
            lines = []
            if tweets.get("single_tweet"):
                lines += ["=== 单条推文 ===", tweets["single_tweet"], ""]
            if tweets.get("founder_voice"):
                lines += ["=== 创始人口吻 ===", tweets["founder_voice"], ""]
            if tweets.get("thread"):
                lines += ["=== Thread ==="]
                for i, t in enumerate(tweets["thread"]):
                    lines.append(f"{i+1}. {t}")
                lines.append("")
            if lines:
                zf.writestr("tweets.txt", "\n".join(lines))

            # Write AI page as markdown
            ai_page = gen.get("ai_page", {})
            if ai_page and not ai_page.get("_parse_error"):
                hero = ai_page.get("hero", {})
                md = [
                    f"# {hero.get('headline', '')}",
                    f"\n{hero.get('subheadline', '')}",
                    f"\n**{hero.get('cta_text', '')}**",
                    "",
                ]
                if ai_page.get("social_proof"):
                    md += [f"> {ai_page['social_proof']}", ""]
                for f in ai_page.get("features", []):
                    md += [f"## {f.get('title', '')}", f.get("description", ""), ""]
                if ai_page.get("faq"):
                    md.append("## FAQ")
                    for item in ai_page["faq"]:
                        md += [f"**{item.get('q','')}**", item.get("a", ""), ""]
                zf.writestr("ai_page.md", "\n".join(md))

        # Add summary JSON
        zf.writestr(
            "summary.json",
            json.dumps({
                "source": source,
                "source_id": source_id,
                "url": bundle["url"],
                "screenshots_count": len(bundle["screenshots"]),
                "cards_count": len((bundle.get("curation") or {}).get("cards", [])),
                "has_generated": bool(bundle.get("generated")),
            }, ensure_ascii=False, indent=2),
        )

    buf.seek(0)
    filename = f"export_{source_id}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Delete / cleanup endpoints ────────────────────────────────────────────────


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, _: None = Depends(_verify_api_key)):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    t = TASKS.pop(task_id)
    # Remove from DB
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    # Remove screenshots dir
    shot_dir = Path(f"screenshots/{task_id}")
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    return {"deleted": task_id, "task": t.get("task", "")}


@app.delete("/explore/{eid}")
async def delete_explore(eid: str, _: None = Depends(_verify_api_key)):
    if eid not in EXPLORE_TASKS:
        raise HTTPException(status_code=404, detail="explore task not found")
    EXPLORE_TASKS.pop(eid)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM explore_tasks WHERE id=?", (eid,))
    shot_dir = Path(f"screenshots/explore_{eid}")
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    return {"deleted": eid}


@app.post("/cleanup")
async def cleanup_old_tasks(keep_last: int = 20, _: None = Depends(_verify_api_key)):
    """
    Delete oldest completed tasks beyond keep_last, freeing disk space.
    Returns counts of deleted tasks and freed screenshot dirs.
    """
    done_tasks = sorted(
        [t for t in TASKS.values() if t["status"] in ("done", "failed")],
        key=_created_at_key,
    )
    done_explores = sorted(
        [t for t in EXPLORE_TASKS.values() if t["status"] in ("done", "failed")],
        key=_created_at_key,
    )

    deleted_tasks = []
    to_delete_tasks = done_tasks if keep_last == 0 else done_tasks[:-keep_last] if len(done_tasks) > keep_last else []
    for t in to_delete_tasks:
        tid = t["id"]
        TASKS.pop(tid, None)
        shot_dir = Path(f"screenshots/{tid}")
        if shot_dir.exists():
            shutil.rmtree(shot_dir)
        deleted_tasks.append(tid)

    deleted_explores = []
    to_delete_explores = done_explores if keep_last == 0 else done_explores[:-keep_last] if len(done_explores) > keep_last else []
    for t in to_delete_explores:
        eid = t["id"]
        EXPLORE_TASKS.pop(eid, None)
        shot_dir = Path(f"screenshots/explore_{eid}")
        if shot_dir.exists():
            shutil.rmtree(shot_dir)
        deleted_explores.append(eid)

    # Batch delete from DB
    if deleted_tasks or deleted_explores:
        with sqlite3.connect(DB_PATH) as conn:
            if deleted_tasks:
                conn.executemany("DELETE FROM tasks WHERE id=?",
                                 [(tid,) for tid in deleted_tasks])
            if deleted_explores:
                conn.executemany("DELETE FROM explore_tasks WHERE id=?",
                                 [(eid,) for eid in deleted_explores])

    return {
        "deleted_tasks": len(deleted_tasks),
        "deleted_explores": len(deleted_explores),
        "ids": deleted_tasks + deleted_explores,
    }


# ── Workflow endpoints ───────────────────────────────────────────────────────

@app.get("/workflows")
def list_workflows(_: None = Depends(_verify_api_key)):
    """列出所有工作流。"""
    return list(WORKFLOWS.values())


@app.get("/workflows/{wf_id}")
def get_workflow(wf_id: str, _: None = Depends(_verify_api_key)):
    if wf_id not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow not found")
    return WORKFLOWS[wf_id]


@app.post("/workflows")
async def create_workflow(req: WorkflowCreateRequest, _: None = Depends(_verify_api_key)):
    """通过 YAML 创建工作流。"""
    try:
        wf_def = parse_workflow(req.yaml_content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    wf_id = uuid.uuid4().hex[:8]
    wf_dict = {
        "id": wf_id,
        "title": wf_def.title,
        "description": wf_def.description,
        "yaml_source": req.yaml_content,
        "parameters": [p.model_dump() for p in wf_def.parameters],
        "blocks": [b.model_dump() for b in wf_def.blocks],
        "source_type": "api",
    }
    save_workflow(wf_dict)
    WORKFLOWS[wf_id] = wf_dict
    return wf_dict


@app.put("/workflows/{wf_id}")
async def update_workflow(wf_id: str, req: WorkflowCreateRequest, _: None = Depends(_verify_api_key)):
    """更新工作流 YAML。"""
    if wf_id not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow not found")

    try:
        wf_def = parse_workflow(req.yaml_content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    wf_dict = WORKFLOWS[wf_id]
    wf_dict.update({
        "title": wf_def.title,
        "description": wf_def.description,
        "yaml_source": req.yaml_content,
        "parameters": [p.model_dump() for p in wf_def.parameters],
        "blocks": [b.model_dump() for b in wf_def.blocks],
    })
    save_workflow(wf_dict)
    return wf_dict


@app.delete("/workflows/{wf_id}")
async def remove_workflow(wf_id: str, _: None = Depends(_verify_api_key)):
    if wf_id not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow not found")
    WORKFLOWS.pop(wf_id)
    delete_workflow(wf_id)
    return {"deleted": wf_id}


@app.post("/workflows/{wf_id}/run")
async def run_workflow(
    wf_id: str,
    req: WorkflowRunRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(_verify_api_key),
):
    """运行工作流，返回 run_id。"""
    if wf_id not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow not found")

    wf = WORKFLOWS[wf_id]

    engine = WorkflowEngine(
        workflow=wf,
        parameters=req.parameters,
        log_callback=_log_callback,
        screenshot_callback=_screenshot_callback,
    )

    run_id = engine.run_id

    async def _run_workflow_bg():
        main_loop = asyncio.get_running_loop()

        def _run_in_thread():
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # 重建 engine（线程内需要独立的 event loop）
            eng = WorkflowEngine(
                workflow=wf,
                parameters=req.parameters,
                log_callback=lambda rid, msg: asyncio.run_coroutine_threadsafe(
                    _log_callback(rid, msg), main_loop
                ).result(timeout=_CALLBACK_TIMEOUT),
                screenshot_callback=lambda rid, fn: asyncio.run_coroutine_threadsafe(
                    _screenshot_callback(rid, fn), main_loop
                ).result(timeout=_CALLBACK_TIMEOUT),
            )
            eng.run_id = run_id  # 保持同一个 run_id

            try:
                result = loop.run_until_complete(eng.run())
                return result
            finally:
                loop.close()

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                _agent_executor, _run_in_thread
            )
            await _broadcast({
                "type": "workflow_done", "run_id": run_id,
                "workflow_id": wf_id, "status": result.get("status"),
            })
            # webhook
            if req.webhook_url:
                await _send_webhook(req.webhook_url, {
                    "run_id": run_id, "workflow_id": wf_id,
                    "status": result.get("status"),
                    "block_results": result.get("block_results"),
                })
        except Exception as e:
            await _broadcast({
                "type": "workflow_done", "run_id": run_id,
                "workflow_id": wf_id, "status": "failed", "error": str(e),
            })

    background_tasks.add_task(_run_workflow_bg)
    await _broadcast({
        "type": "workflow_started", "run_id": run_id, "workflow_id": wf_id,
    })

    return {"run_id": run_id, "workflow_id": wf_id}


@app.get("/workflows/{wf_id}/runs")
def list_workflow_runs(wf_id: str, _: None = Depends(_verify_api_key)):
    if wf_id not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow not found")
    return load_workflow_runs(wf_id)


@app.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, _: None = Depends(_verify_api_key)):
    run = load_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return run


# ── Template Marketplace endpoints ───────────────────────────────────────────

@app.get("/templates")
def list_templates(category: str | None = None, _: None = Depends(_verify_api_key)):
    templates = list(TEMPLATES.values())
    if category:
        templates = [t for t in templates if t.get("category") == category]
    # Don't send yaml_source in list view
    return [{k: v for k, v in t.items() if k != "yaml_source"} for t in templates]


@app.get("/templates/categories")
def list_template_categories(_: None = Depends(_verify_api_key)):
    counts: dict[str, int] = {}
    for t in TEMPLATES.values():
        cat = t.get("category", "")
        counts[cat] = counts.get(cat, 0) + 1
    return [
        {"id": cid, "label": info["label"], "icon": info["icon"], "count": counts.get(cid, 0)}
        for cid, info in TEMPLATE_CATEGORIES.items()
        if counts.get(cid, 0) > 0
    ]


@app.get("/templates/{template_id}")
def get_template(template_id: str, _: None = Depends(_verify_api_key)):
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")
    return tpl


@app.post("/templates/{template_id}/instantiate")
def instantiate_template(template_id: str, _: None = Depends(_verify_api_key)):
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")
    try:
        wf_def = parse_workflow(tpl["yaml_source"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    wf_id = uuid.uuid4().hex[:8]
    wf_dict = {
        "id": wf_id,
        "title": wf_def.title,
        "description": wf_def.description,
        "yaml_source": tpl["yaml_source"],
        "parameters": [p.model_dump() for p in wf_def.parameters],
        "blocks": [b.model_dump() for b in wf_def.blocks],
        "source_type": "template",
        "source_path": template_id,
    }
    save_workflow(wf_dict)
    WORKFLOWS[wf_id] = wf_dict
    return {"id": wf_id, "title": wf_dict["title"]}


class TemplateRunRequest(BaseModel):
    parameters: dict = {}
    browser_mode: str = "builtin"
    cdp_url: str = "http://localhost:9222"
    chrome_profile: str = "Default"
    webhook_url: str = ""
    timeout: int = 0


@app.post("/templates/{template_id}/run")
async def run_template(
    template_id: str, req: TemplateRunRequest,
    background_tasks: BackgroundTasks, _: None = Depends(_verify_api_key),
):
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")
    # Create ephemeral workflow
    try:
        wf_def = parse_workflow(tpl["yaml_source"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    wf_id = f"tpl_{uuid.uuid4().hex[:8]}"
    wf_dict = {
        "id": wf_id,
        "title": wf_def.title,
        "description": wf_def.description,
        "yaml_source": tpl["yaml_source"],
        "parameters": [p.model_dump() for p in wf_def.parameters],
        "blocks": [b.model_dump() for b in wf_def.blocks],
        "source_type": "template",
        "source_path": template_id,
    }
    save_workflow(wf_dict)
    WORKFLOWS[wf_id] = wf_dict
    # Run it (reuse workflow run logic)
    run_id = uuid.uuid4().hex[:12]
    run_record = {
        "id": run_id, "workflow_id": wf_id, "status": "pending",
        "parameters": req.parameters, "block_results": {},
        "current_block": None, "logs": [], "error": None,
        "started_at": None, "finished_at": None,
    }
    save_workflow_run(run_record)

    async def _execute():
        engine = WorkflowEngine(wf_dict, req.parameters, run_id)
        await engine.run()

    background_tasks.add_task(_execute)
    await _broadcast({"type": "workflow_started", "run_id": run_id, "workflow_id": wf_id})
    return {"run_id": run_id, "workflow_id": wf_id}


# ── Memory API ───────────────────────────────────────────────────────────────

class MemoryUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None  # JSON string — 无效 JSON 返回 400


@app.get("/memories")
async def list_memories(domain: str = None, type: str = None,
                        page: int = None, page_size: int = 50, _=Depends(_verify_api_key)):
    if page is not None:
        return load_memories_paged(domain=domain, memory_type=type, page=page, page_size=page_size)
    return load_memories(domain=domain, memory_type=type)


@app.get("/memories/stats")
async def memory_stats(_=Depends(_verify_api_key)):
    return get_memory_stats()


@app.get("/memories/{memory_id}")
async def get_memory_detail(memory_id: str, _=Depends(_verify_api_key)):
    m = get_memory(memory_id)
    if not m:
        raise HTTPException(404, "Memory not found")
    return m


@app.put("/memories/{memory_id}")
async def update_memory_endpoint(memory_id: str, req: MemoryUpdateRequest, _=Depends(_verify_api_key)):
    m = get_memory(memory_id)
    if not m:
        raise HTTPException(404, "Memory not found")
    if req.title is not None:
        m["title"] = req.title
    if req.content is not None:
        try:
            m["content"] = json.loads(req.content)
        except json.JSONDecodeError:
            raise HTTPException(400, "content 必须是有效的 JSON 格式")
    save_memory(m)
    return {"ok": True}


@app.delete("/memories/{memory_id}")
async def delete_memory_endpoint(memory_id: str, _=Depends(_verify_api_key)):
    if not delete_memory(memory_id):
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


class BatchDeleteMemoriesRequest(BaseModel):
    ids: list[str]


@app.post("/memories/batch-delete")
async def batch_delete_memories(req: BatchDeleteMemoriesRequest, _=Depends(_verify_api_key)):
    count = delete_memories_batch(req.ids)
    return {"deleted": count}


# ── Recording API ────────────────────────────────────────────────────────────

_RECORDING_SESSIONS: dict[str, dict] = {}  # recording_id -> {recorder, page, browser, pw, ...}


class RecordingStartRequest(BaseModel):
    title: str = ""
    start_url: str = "about:blank"
    browser_mode: str = Field(default="builtin", pattern=r'^(builtin|cdp)$')
    cdp_url: str = Field(default="http://localhost:9222", max_length=500)
    timeout: int = Field(default=1800, ge=60, le=7200)  # 默认 30 分钟，最大 2 小时


@app.post("/recordings/start")
async def start_recording(req: RecordingStartRequest, background_tasks: BackgroundTasks, _=Depends(_verify_api_key)):
    from agent.recorder import ActionRecorder
    from playwright.async_api import async_playwright

    recording_id = uuid.uuid4().hex[:12]

    async def _launch():
        pw_cm = async_playwright()
        pw = await pw_cm.start()
        browser = None
        try:
            if req.browser_mode == "cdp":
                browser = await pw.chromium.connect_over_cdp(req.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context(
                    viewport={"width": 1920, "height": 1080}, locale="zh-CN"
                )
            else:
                browser = await pw.chromium.launch(headless=False)
                context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")

            page = await context.new_page()
            if req.start_url and req.start_url != "about:blank":
                await page.goto(req.start_url, wait_until="domcontentloaded", timeout=30000)

            recorder = ActionRecorder(page)
            await recorder.start()

            session = {
                "recorder": recorder, "page": page, "browser": browser,
                "context": context, "pw": pw, "pw_cm": pw_cm, "title": req.title,
                "start_url": req.start_url, "broadcast_task": None,
                "started_at": time.time(), "timeout": req.timeout,
            }
            _RECORDING_SESSIONS[recording_id] = session

            # 实时推送录制的 action + 超时检测
            async def _broadcast_actions():
                last_count = 0
                while recording_id in _RECORDING_SESSIONS:
                    # 超时自动停止
                    elapsed = time.time() - session["started_at"]
                    if elapsed > session["timeout"]:
                        try:
                            await _auto_stop_recording(recording_id)
                        except Exception:
                            pass
                        break
                    actions = recorder._actions
                    if len(actions) > last_count:
                        for a in actions[last_count:]:
                            await _broadcast({
                                "type": "recording_action",
                                "recording_id": recording_id,
                                "action": a,
                            })
                        last_count = len(actions)
                    await asyncio.sleep(0.5)

            session["broadcast_task"] = asyncio.create_task(_broadcast_actions())
        except Exception:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
            _RECORDING_SESSIONS.pop(recording_id, None)
            raise

    await _launch()

    save_recording({
        "id": recording_id, "title": req.title, "start_url": req.start_url,
        "actions": [], "parameters": [], "status": "recording",
    })

    return {"recording_id": recording_id, "status": "recording"}


async def _auto_stop_recording(recording_id: str):
    """超时自动停止录制（内部调用）。"""
    session = _RECORDING_SESSIONS.pop(recording_id, None)
    if not session:
        return
    try:
        actions = await session["recorder"].stop()
        from agent.recording_converter import RecordingConverter
        converter = RecordingConverter({"actions": actions})
        parameters = converter._detect_parameters()
        save_recording({
            "id": recording_id, "title": session.get("title", ""),
            "start_url": session.get("start_url", ""),
            "actions": actions, "parameters": parameters, "status": "completed",
        })
        await _broadcast({"type": "recording_timeout", "recording_id": recording_id})
    except Exception:
        pass
    finally:
        try:
            await session["browser"].close()
        except Exception:
            pass
        pw_cm = session.get("pw_cm")
        if pw_cm:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass


@app.post("/recordings/{recording_id}/stop")
async def stop_recording(recording_id: str, _=Depends(_verify_api_key)):
    session = _RECORDING_SESSIONS.pop(recording_id, None)
    if not session:
        raise HTTPException(404, "Recording session not found or already stopped")

    try:
        # 取消广播协程
        task = session.get("broadcast_task")
        if task and not task.done():
            task.cancel()

        recorder = session["recorder"]
        actions = await recorder.stop()
    finally:
        # 确保浏览器一定被关闭
        try:
            await session["browser"].close()
        except Exception:
            pass
        pw_cm = session.get("pw_cm")
        if pw_cm:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass

    # 自动检测参数
    from agent.recording_converter import RecordingConverter
    converter = RecordingConverter({"actions": actions})
    parameters = converter._detect_parameters()

    # 保存到数据库
    save_recording({
        "id": recording_id, "title": session.get("title", ""),
        "start_url": session.get("start_url", ""),
        "actions": actions, "parameters": parameters, "status": "completed",
    })

    return {"recording_id": recording_id, "actions": actions, "parameters": parameters, "status": "completed"}


@app.get("/recordings")
async def list_recordings(_=Depends(_verify_api_key)):
    return load_all_recordings()


@app.get("/recordings/{recording_id}")
async def get_recording_detail(recording_id: str, _=Depends(_verify_api_key)):
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")
    return r


@app.delete("/recordings/{recording_id}")
async def delete_recording_endpoint(recording_id: str, _=Depends(_verify_api_key)):
    _RECORDING_SESSIONS.pop(recording_id, None)
    if not delete_recording(recording_id):
        raise HTTPException(404, "Recording not found")
    return {"ok": True}


class RecordingConvertRequest(BaseModel):
    title: str = ""
    parameters: list[dict] = Field(default_factory=list)


class RecordingActionUpdateRequest(BaseModel):
    text: str | None = None
    selector: str | None = None
    meta: dict | None = None


@app.delete("/recordings/{recording_id}/actions/{action_index}")
async def delete_recording_action(recording_id: str, action_index: int, _=Depends(_verify_api_key)):
    """删除录制中的单个操作（仅 completed 状态可编辑）。"""
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")
    if r.get("status") == "recording":
        raise HTTPException(400, "Cannot edit active recording")
    if action_index < 0 or action_index >= len(r.get("actions", [])):
        raise HTTPException(400, "Invalid action index")
    r["actions"].pop(action_index)
    save_recording(r)
    return {"ok": True, "actions_count": len(r["actions"])}


@app.put("/recordings/{recording_id}/actions/{action_index}")
async def update_recording_action(recording_id: str, action_index: int, req: RecordingActionUpdateRequest, _=Depends(_verify_api_key)):
    """���改录制中的单个操作（仅 completed 状态可编辑）。"""
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")
    if r.get("status") == "recording":
        raise HTTPException(400, "Cannot edit active recording")
    if action_index < 0 or action_index >= len(r.get("actions", [])):
        raise HTTPException(400, "Invalid action index")
    action = r["actions"][action_index]
    if req.text is not None:
        action["text"] = req.text
    if req.selector is not None:
        action["selector"] = req.selector
    if req.meta is not None:
        action["meta"] = req.meta
    save_recording(r)
    return {"ok": True}


@app.put("/recordings/{recording_id}/actions")
async def replace_recording_actions(recording_id: str, req: dict, _=Depends(_verify_api_key)):
    """批量替换录制的操作列表（用于拖拽排序后保存）。"""
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")
    if r.get("status") == "recording":
        raise HTTPException(400, "Cannot edit active recording")
    actions = req.get("actions")
    if not isinstance(actions, list):
        raise HTTPException(400, "actions must be a list")
    r["actions"] = actions
    save_recording(r)
    return {"ok": True, "actions_count": len(actions)}


@app.post("/recordings/{recording_id}/convert")
async def convert_recording(recording_id: str, req: RecordingConvertRequest, _=Depends(_verify_api_key)):
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")

    from agent.recording_converter import RecordingConverter
    converter = RecordingConverter(r)
    yaml_content = converter.to_workflow_yaml(
        params=req.parameters if req.parameters else None,
        title=req.title or r.get("title", ""),
    )

    # 保存为 workflow
    wf_data = parse_workflow(yaml_content)
    wf_id = uuid.uuid4().hex[:8]
    wf = {
        "id": wf_id,
        "title": wf_data.get("title", req.title or "录制工作流"),
        "description": wf_data.get("description", "从录制自动生成"),
        "yaml_content": yaml_content,
    }
    save_workflow(wf)
    WORKFLOWS[wf_id] = wf

    # 更新录制记录
    r["workflow_id"] = wf_id
    r["status"] = "converted"
    save_recording(r)

    return {"workflow_id": wf_id, "yaml_content": yaml_content}


@app.post("/recordings/{recording_id}/preview")
async def preview_recording_workflow(recording_id: str, req: RecordingConvertRequest, _=Depends(_verify_api_key)):
    """预览录制→工作流转换结果（不保存），返回清洗后的 actions 和生成的 YAML。"""
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")

    from agent.recording_converter import RecordingConverter
    converter = RecordingConverter(r)
    cleaned = converter.clean_actions()
    yaml_content = converter.to_workflow_yaml(
        params=req.parameters if req.parameters else None,
        title=req.title or r.get("title", ""),
    )
    detected_params = converter._detect_parameters()

    return {
        "original_count": len(r.get("actions", [])),
        "cleaned_count": len(cleaned),
        "cleaned_actions": cleaned,
        "detected_parameters": detected_params,
        "yaml_preview": yaml_content,
    }


class RecordingReplayRequest(BaseModel):
    parameters: dict = Field(default_factory=dict)


@app.post("/recordings/{recording_id}/replay")
async def replay_recording(
    recording_id: str, req: RecordingReplayRequest,
    background_tasks: BackgroundTasks, _=Depends(_verify_api_key),
):
    r = get_recording(recording_id)
    if not r:
        raise HTTPException(404, "Recording not found")

    # 如果还没转换，先转换
    wf_id = r.get("workflow_id")
    if not wf_id or wf_id not in WORKFLOWS:
        from agent.recording_converter import RecordingConverter
        converter = RecordingConverter(r)
        yaml_content = converter.to_workflow_yaml()
        wf_data = parse_workflow(yaml_content)
        wf_id = uuid.uuid4().hex[:8]
        wf = {
            "id": wf_id,
            "title": wf_data.get("title", r.get("title", "录制工作流")),
            "description": "从录制自动生成",
            "yaml_content": yaml_content,
        }
        save_workflow(wf)
        WORKFLOWS[wf_id] = wf
        r["workflow_id"] = wf_id
        r["status"] = "converted"
        save_recording(r)

    # 运行 workflow（复用现有逻辑）
    wf = WORKFLOWS[wf_id]
    run_id = uuid.uuid4().hex[:12]
    wf_data = parse_workflow(wf["yaml_content"])
    errors = validate_workflow(wf_data)
    if errors:
        raise HTTPException(400, f"Workflow validation failed: {errors}")

    engine = WorkflowEngine(
        workflow=wf_data, parameters=req.parameters,
        log_callback=_log_callback, screenshot_callback=_screenshot_callback,
        ask_user_callback=None,
    )

    async def _execute():
        result = await engine.run()
        save_workflow_run(result)

    background_tasks.add_task(_execute)
    return {"run_id": run_id, "workflow_id": wf_id}


# ── SPA fallback: React Router 需要所有非 API 路径返回 index.html ──────────
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    static_file = Path("static") / full_path
    if static_file.exists() and static_file.is_file():
        return FileResponse(str(static_file))
    index = Path("static/index.html")
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="not found")
