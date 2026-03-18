"""
MCP Server — 将浏览器 Agent 能力暴露为 MCP 工具。

支持 Claude Code / Cursor / 其他 MCP 客户端直接调用浏览器自动化能力。

启动方式：
  python mcp_server.py                    # stdio 模式（IDE 集成）
  python mcp_server.py --transport sse    # SSE 模式（HTTP 远程调用）
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from fastmcp import FastMCP

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

mcp = FastMCP(
    "Browser Agent",
    instructions="AI 驱动的浏览器自动化工具，支持网页导航、数据提取、表单填写、截图等操作",
)

# ── 任务管理 ──────────────────────────────────────────────────

_tasks: dict[str, dict] = {}  # task_id -> {status, result, logs, ...}


async def _run_task_async(task_id: str, task: str, headless: bool, max_steps: int):
    """后台运行 agent 任务。"""
    from agent.runner import run_agent

    logs: list[str] = []

    async def _log(msg: str):
        logs.append(msg)

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["started_at"] = time.time()

    try:
        result = await run_agent(
            task=task,
            headless=headless,
            task_id=task_id,
            log_callback=_log,
            screenshots_dir=f"screenshots/mcp_{task_id}",
            max_steps=max_steps,
        )
        _tasks[task_id]["status"] = "completed" if result.get("success") else "failed"
        _tasks[task_id]["result"] = result
    except Exception as e:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["result"] = {"success": False, "error": str(e)}
    finally:
        _tasks[task_id]["finished_at"] = time.time()
        _tasks[task_id]["logs"] = logs


# ── MCP 工具定义 ──────────────────────────────────────────────


@mcp.tool()
async def run_browser_task(
    task: str,
    headless: bool = True,
    max_steps: int = 25,
    wait_for_completion: bool = True,
) -> str:
    """
    执行浏览器自动化任务。AI Agent 会自动规划步骤、操作浏览器完成任务。

    Args:
        task: 任务描述，如 "打开 https://example.com 搜索 python 并截图"
        headless: 是否无头模式运行（默认 True）
        max_steps: 最大执行步数（默认 25）
        wait_for_completion: 是否等待任务完成（默认 True，False 则立即返回 task_id）

    Returns:
        任务结果 JSON，包含 success/steps/reason 等信息
    """
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {"id": task_id, "task": task, "status": "pending", "logs": []}

    if wait_for_completion:
        await _run_task_async(task_id, task, headless, max_steps)
        result = _tasks[task_id].get("result", {})
        return json.dumps({
            "task_id": task_id,
            "success": result.get("success", False),
            "steps": result.get("steps", 0),
            "reason": result.get("reason", ""),
            "cost": result.get("cost", {}),
        }, ensure_ascii=False)
    else:
        asyncio.create_task(_run_task_async(task_id, task, headless, max_steps))
        return json.dumps({"task_id": task_id, "status": "running"}, ensure_ascii=False)


@mcp.tool()
async def get_task_status(task_id: str) -> str:
    """
    查询浏览器任务的执行状态。

    Args:
        task_id: 任务 ID（由 run_browser_task 返回）

    Returns:
        任务状态 JSON
    """
    info = _tasks.get(task_id)
    if not info:
        return json.dumps({"error": f"任务 {task_id} 不存在"}, ensure_ascii=False)

    result = {
        "task_id": task_id,
        "status": info["status"],
        "task": info.get("task", ""),
    }
    if info.get("result"):
        result["success"] = info["result"].get("success", False)
        result["steps"] = info["result"].get("steps", 0)
        result["reason"] = info["result"].get("reason", "")
    if info.get("started_at"):
        elapsed = (info.get("finished_at") or time.time()) - info["started_at"]
        result["elapsed_seconds"] = round(elapsed, 1)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def list_browser_tasks() -> str:
    """
    列出所有浏览器任务及其状态。

    Returns:
        任务列表 JSON
    """
    tasks = []
    for tid, info in _tasks.items():
        tasks.append({
            "task_id": tid,
            "task": info.get("task", "")[:100],
            "status": info["status"],
        })
    return json.dumps({"tasks": tasks, "total": len(tasks)}, ensure_ascii=False)


@mcp.tool()
async def extract_page_data(url: str, question: str, headless: bool = True) -> str:
    """
    从指定网页提取信息。打开 URL，用 AI 分析页面内容并回答问题。

    Args:
        url: 目标网页 URL
        question: 要从页面提取的信息，如 "页面上的价格是多少"
        headless: 是否无头模式（默认 True）

    Returns:
        提取结果 JSON
    """
    task = f"打开 {url}，用 extract 工具回答以下问题：{question}，然后调用 done"
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {"id": task_id, "task": task, "status": "pending", "logs": []}

    await _run_task_async(task_id, task, headless, max_steps=10)
    result = _tasks[task_id].get("result", {})

    return json.dumps({
        "url": url,
        "question": question,
        "success": result.get("success", False),
        "reason": result.get("reason", ""),
    }, ensure_ascii=False)


@mcp.tool()
async def take_screenshot(url: str, full_page: bool = False, headless: bool = True) -> str:
    """
    对指定网页截图。

    Args:
        url: 目标网页 URL
        full_page: 是否截取完整页面（默认 False，只截可见区域）
        headless: 是否无头模式（默认 True）

    Returns:
        截图文件路径
    """
    task_id = uuid.uuid4().hex[:12]
    full_flag = "全页" if full_page else ""
    task = f"打开 {url}，截{full_flag}图并保存为 screenshot.png，然后调用 done"
    _tasks[task_id] = {"id": task_id, "task": task, "status": "pending", "logs": []}

    await _run_task_async(task_id, task, headless, max_steps=8)
    result = _tasks[task_id].get("result", {})
    screenshot_dir = Path(f"screenshots/mcp_{task_id}")

    # 查找截图文件
    screenshots = list(screenshot_dir.glob("*.png")) + list(screenshot_dir.glob("*.jpg"))
    screenshot_path = str(screenshots[0]) if screenshots else ""

    return json.dumps({
        "url": url,
        "success": result.get("success", False),
        "screenshot_path": screenshot_path,
    }, ensure_ascii=False)


@mcp.tool()
async def run_workflow(
    workflow_name: str,
    parameters: dict | None = None,
    headless: bool = True,
) -> str:
    """
    执行已保存的 YAML 工作流。

    Args:
        workflow_name: 工作流名称或 ID
        parameters: 工作流参数（键值对）
        headless: 是否无头模式（默认 True）

    Returns:
        工作流执行结果 JSON
    """
    from workflow import load_all_workflows, parse_workflow
    from workflow.engine import WorkflowEngine

    workflows = load_all_workflows()

    # 按名称或 ID 查找
    wf = None
    for w in workflows.values():
        if w.get("id") == workflow_name or w.get("title", "").lower() == workflow_name.lower():
            wf = w
            break

    if not wf:
        return json.dumps({"error": f"工作流 '{workflow_name}' 不存在"}, ensure_ascii=False)

    wf_data = parse_workflow(wf["yaml_content"])
    engine = WorkflowEngine(wf_data, parameters or {}, headless=headless)

    try:
        result = await engine.run()
        return json.dumps({
            "workflow": workflow_name,
            "success": result.get("success", False),
            "blocks_executed": result.get("blocks_executed", 0),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "workflow": workflow_name,
            "success": False,
            "error": str(e),
        }, ensure_ascii=False)


@mcp.tool()
async def list_workflows() -> str:
    """
    列出所有可用的工作流模板。

    Returns:
        工作流列表 JSON
    """
    from workflow import load_all_workflows

    workflows = load_all_workflows()
    items = []
    for wid, w in workflows.items():
        items.append({
            "id": wid,
            "title": w.get("title", ""),
            "description": w.get("description", ""),
        })

    return json.dumps({"workflows": items, "total": len(items)}, ensure_ascii=False)


@mcp.tool()
async def list_templates() -> str:
    """
    列出所有可用的任务模板（预置的常用自动化场景）。

    Returns:
        模板列表 JSON
    """
    from template_loader import load_templates

    templates = load_templates()
    items = []
    for t in templates:
        items.append({
            "name": t.get("name", ""),
            "title": t.get("title", ""),
            "category": t.get("category", ""),
            "description": t.get("description", ""),
        })

    return json.dumps({"templates": items, "total": len(items)}, ensure_ascii=False)


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Browser Agent MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")
