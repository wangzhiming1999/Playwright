"""
FastAPI backend for Playwright + GPT Agent
Run: uvicorn app:app --reload --port 8000
"""

import asyncio
import concurrent.futures
import io
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
import zipfile
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import run_agent
from curator import curate
from explorer import run_exploration
from content_gen import generate_all
from db import init_db, save_task, load_all_tasks, save_explore_task, load_all_explore_tasks, DB_PATH
from utils import validate_url
from workflow import (
    init_workflow_db, parse_workflow, validate_workflow,
    save_workflow, load_all_workflows, delete_workflow,
    save_workflow_run, load_workflow_runs, load_workflow_run,
    scan_workflow_directory,
    WorkflowEngine, WorkflowCreateRequest, WorkflowRunRequest,
)

# ── Configuration constants ───────────────────────────────────────────────────

_AGENT_THREAD_WORKERS = 4
_CALLBACK_TIMEOUT = 30      # seconds for cross-thread future.result()
_BROADCAST_TIMEOUT = 5
_USER_REPLY_TIMEOUT = 300   # 5 minutes

API_KEY = os.getenv("API_KEY")  # Optional API key for authentication
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
MAX_TASKS_KEEP = int(os.getenv("MAX_TASKS_KEEP", "50"))
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")

app = FastAPI(
    title="Skyvern",
    version="0.1.0",
    description="AI-driven browser automation platform",
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
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

Path("screenshots").mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)
init_db()
init_workflow_db()

# 启动时扫描 workflows/ 目录，加载 YAML 工作流
WORKFLOWS: dict[str, dict] = load_all_workflows()
_loaded_wfs = scan_workflow_directory()
for wf in _loaded_wfs:
    WORKFLOWS[wf["id"]] = wf

app.mount("/static", StaticFiles(directory="static"), name="static")

# 专用线程池：在独立线程里用 ProactorEventLoop 跑 Playwright，避免 Windows 上主循环的 NotImplementedError
_agent_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_AGENT_THREAD_WORKERS, thread_name_prefix="playwright_agent")

# ── In-memory store (backed by SQLite) ────────────────────────────────────────

TASKS: dict[str, dict] = load_all_tasks()
# { id, task, status: pending|running|done|failed|waiting_input, logs: [], screenshots: [] }

EXPLORE_TASKS: dict[str, dict] = load_all_explore_tasks()


def _startup_cleanup():
    for store in (TASKS, EXPLORE_TASKS):
        done = sorted(
            [t for t in store.values() if t["status"] in ("done", "failed")],
            key=lambda t: t.get("created_at", ""),
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
) -> tuple[bool, list[str] | str]:
    """
    在独立线程中运行 run_agent，使用本线程的 ProactorEventLoop。
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def thread_safe_log(tid: str, msg: str):
        try:
            fut = asyncio.run_coroutine_threadsafe(_log_callback(tid, msg), main_loop)
            fut.result(timeout=_CALLBACK_TIMEOUT)
        except Exception as e:
            print(f"[warn] log callback error: {e}", file=sys.stderr)

    async def thread_safe_screenshot(tid: str, filename: str):
        try:
            fut = asyncio.run_coroutine_threadsafe(_screenshot_callback(tid, filename), main_loop)
            fut.result(timeout=_CALLBACK_TIMEOUT)
        except Exception as e:
            print(f"[warn] screenshot callback error: {e}", file=sys.stderr)

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

        answered = ev.wait(timeout=_USER_REPLY_TIMEOUT)
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
                browser_mode=t.get("browser_mode", "builtin"),
                cdp_url=t.get("cdp_url", "http://localhost:9222"),
                chrome_profile=t.get("chrome_profile", "Default"),
            )
        )
        # agent_result: {"success": bool, "reason": str, "steps": int}
        task_succeeded = agent_result.get("success", False) if isinstance(agent_result, dict) else True
        shot_dir = Path(f"screenshots/{task_id}")
        screenshots = []
        if shot_dir.exists():
            screenshots = sorted(
                [f.name for f in shot_dir.glob("*.png")] + [f.name for f in shot_dir.glob("*.jpg")],
                key=lambda n: (shot_dir / n).stat().st_mtime
            )
        return (task_succeeded, screenshots)
    except Exception as e:
        return (False, str(e))
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
        )

        # 超时控制
        if timeout_sec > 0:
            try:
                ok, result = await asyncio.wait_for(future, timeout=timeout_sec)
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
            ok, result = await future

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

        if ok:
            TASKS[task_id]["screenshots"] = result
            TASKS[task_id]["status"] = "done"
            final_status = "done"
            save_task(TASKS[task_id])
            await _broadcast({"type": "status", "task_id": task_id, "data": "done", "screenshots": result})
            await _send_webhook(webhook_url, {
                "task_id": task_id, "status": "done", "task": task,
                "screenshots": result,
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
        _CANCEL_EVENTS.pop(task_id, None)


# ── API endpoints ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    tasks: list[str]
    browser_mode: str = "builtin"   # "builtin" | "user_chrome" | "cdp"
    cdp_url: str = "http://localhost:9222"
    chrome_profile: str = "Default"
    webhook_url: str = ""           # 任务完成/失败时 POST 回调
    timeout: int = 0                # 单任务超时秒数，0 表示不限


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/health")
def health_check():
    return {"status": "ok", "tasks": len(TASKS), "explore_tasks": len(EXPLORE_TASKS)}


@app.get("/screenshots/{task_id}/{filename}")
async def serve_screenshot(task_id: str, filename: str, _: None = Depends(_verify_api_key)):
    if ".." in task_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid path")
    path = Path("screenshots") / task_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(str(path))


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

    async def run_all():
        await asyncio.gather(*[_run_task(tid, t) for tid, t in zip(ids, task_texts)])

    background_tasks.add_task(run_all)

    # broadcast new pending tasks
    for tid, t in zip(ids, task_texts):
        await _broadcast({"type": "new_task", "task": TASKS[tid]})

    return {"task_ids": ids}


@app.get("/tasks")
def list_tasks():
    return list(TASKS.values())


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
        key=lambda t: t.get("created_at", ""),
    )
    done_explores = sorted(
        [t for t in EXPLORE_TASKS.values() if t["status"] in ("done", "failed")],
        key=lambda t: t.get("created_at", ""),
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
