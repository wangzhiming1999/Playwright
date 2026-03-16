"""
Block 执行器 — 12 种 block_type 的具体执行逻辑。

每个执行器签名：async def execute_xxx(block, ctx) -> Any
  - block: BlockDef 实例
  - ctx: WorkflowContext 实例
  - 返回值存入 ctx.set_output(block.label, result)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from .models import BlockDef
from .context import WorkflowContext


# ── 注册表 ────────────────────────────────────────────────────────────────────

_EXECUTORS: dict[str, Any] = {}


def _register(block_type: str):
    def decorator(fn):
        _EXECUTORS[block_type] = fn
        return fn
    return decorator


async def execute_block(block: BlockDef, ctx: WorkflowContext) -> Any:
    """根据 block_type 分发到对应执行器。"""
    executor = _EXECUTORS.get(block.block_type)
    if not executor:
        raise ValueError(f"未知的 block_type: {block.block_type}")
    return await executor(block, ctx)


# ── 浏览器类 Block ─────────────────────────────────────────────────────────────

@_register("task")
async def _exec_task(block: BlockDef, ctx: WorkflowContext) -> Any:
    """
    通用浏览器任务 — 委托给 agent.run_agent() 执行自然语言指令。
    """
    from agent import run_agent

    task_text = ctx.resolve(block.task)
    max_steps = block.max_steps or 35

    await ctx.log(f"[block:{block.label}] 执行任务: {task_text}")

    # 复用 workflow 级别的 page（如果有），否则 run_agent 自己启动浏览器
    if ctx.page:
        # 直接用已有 page 跑 agent 循环（需要 agent 支持传入 page）
        from agent.core import BrowserAgent
        from agent.runner import run_agent as _run_agent
        result = await _run_agent(
            task=task_text,
            task_id=ctx.run_id,
            log_callback=ctx.log_callback,
            screenshot_callback=ctx.screenshot_callback,
            ask_user_callback=ctx.ask_user_callback,
        )
    else:
        result = await run_agent(
            task=task_text,
            task_id=ctx.run_id,
            log_callback=ctx.log_callback,
            screenshot_callback=ctx.screenshot_callback,
            ask_user_callback=ctx.ask_user_callback,
        )

    return result


@_register("navigation")
async def _exec_navigation(block: BlockDef, ctx: WorkflowContext) -> Any:
    """导航到指定 URL。"""
    url = ctx.resolve(block.url)
    goal = ctx.resolve(block.navigation_goal) if block.navigation_goal else None

    await ctx.log(f"[block:{block.label}] 导航到: {url}")

    if ctx.page:
        await ctx.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if goal:
            # 有导航目标时，委托 agent 完成
            return await _exec_task(
                BlockDef(block_type="task", label=block.label + "_nav", task=goal),
                ctx,
            )
        return {"url": url, "title": await ctx.page.title()}
    else:
        # 无 page 时降级为 task block
        task_text = f"打开 {url}"
        if goal:
            task_text += f"，然后{goal}"
        return await _exec_task(
            BlockDef(block_type="task", label=block.label + "_nav", task=task_text),
            ctx,
        )


@_register("extraction")
async def _exec_extraction(block: BlockDef, ctx: WorkflowContext) -> Any:
    """从当前页面提取结构化数据。"""
    goal = ctx.resolve(block.data_extraction_goal)
    schema = block.data_schema

    await ctx.log(f"[block:{block.label}] 提取数据: {goal}")

    if not ctx.page:
        raise RuntimeError(f"block '{block.label}' (extraction) 需要浏览器页面")

    # 获取页面文本内容
    text_content = await ctx.page.evaluate("() => document.body.innerText")
    # 截断避免 token 爆炸
    text_content = text_content[:8000] if len(text_content) > 8000 else text_content

    from utils import llm_chat

    schema_hint = ""
    if schema:
        schema_hint = f"\n期望的数据结构：{json.dumps(schema, ensure_ascii=False)}"

    resp = llm_chat(
        messages=[{
            "role": "user",
            "content": (
                f"从以下网页文本中提取数据。\n"
                f"提取目标：{goal}{schema_hint}\n\n"
                f"网页文本：\n{text_content}\n\n"
                f"返回 JSON 格式的提取结果。"
            ),
        }],
        response_format={"type": "json_object"},
        max_tokens=2000,
    )

    if resp.choices:
        try:
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError:
            return {"raw": resp.choices[0].message.content}
    return {}


@_register("login")
async def _exec_login(block: BlockDef, ctx: WorkflowContext) -> Any:
    """登录指定网站。"""
    url = ctx.resolve(block.url)
    cred_key = ctx.resolve(block.credentials)

    await ctx.log(f"[block:{block.label}] 登录: {url} (凭证: {cred_key})")

    # 从环境变量读取凭证
    prefix = cred_key.upper()
    email = os.environ.get(f"{prefix}_EMAIL", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")

    if not email or not password:
        raise RuntimeError(
            f"缺少登录凭证：请设置环境变量 {prefix}_EMAIL 和 {prefix}_PASSWORD"
        )

    task_text = (
        f"打开 {url}，用以下凭证登录：\n"
        f"账号是 {email}\n密码是 {password}\n"
        f"登录成功后确认已进入主页面。"
    )
    return await _exec_task(
        BlockDef(block_type="task", label=block.label + "_login", task=task_text),
        ctx,
    )


@_register("file_upload")
async def _exec_file_upload(block: BlockDef, ctx: WorkflowContext) -> Any:
    """上传文件。"""
    file_path = ctx.resolve(block.file_path)
    await ctx.log(f"[block:{block.label}] 上传文件: {file_path}")

    task_text = f"在当前页面找到文件上传区域，上传文件：{file_path}"
    return await _exec_task(
        BlockDef(block_type="task", label=block.label + "_upload", task=task_text),
        ctx,
    )


@_register("file_download")
async def _exec_file_download(block: BlockDef, ctx: WorkflowContext) -> Any:
    """下载文件。"""
    goal = ctx.resolve(block.download_goal) if block.download_goal else "下载页面上的文件"
    await ctx.log(f"[block:{block.label}] 下载文件: {goal}")

    task_text = f"在当前页面{goal}"
    return await _exec_task(
        BlockDef(block_type="task", label=block.label + "_download", task=task_text),
        ctx,
    )


# ── 数据处理类 Block ──────────────────────────────────────────────────────────

@_register("code")
async def _exec_code(block: BlockDef, ctx: WorkflowContext) -> Any:
    """
    执行 Python 代码片段。
    代码可以访问 ctx（WorkflowContext）和所有 block 输出。
    最后一个表达式的值作为 block 输出。
    """
    code_str = ctx.resolve(block.code)
    await ctx.log(f"[block:{block.label}] 执行代码")

    # 构建安全的执行命名空间
    namespace = {
        "ctx": ctx,
        "json": json,
        "re": re,
        "__builtins__": {
            "len": len, "str": str, "int": int, "float": float, "bool": bool,
            "list": list, "dict": dict, "set": set, "tuple": tuple,
            "range": range, "enumerate": enumerate, "zip": zip,
            "sorted": sorted, "reversed": reversed, "filter": filter, "map": map,
            "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
            "isinstance": isinstance, "type": type,
            "print": lambda *a, **kw: None,  # 静默 print
            "True": True, "False": False, "None": None,
        },
    }
    namespace.update(ctx.get_all_outputs())

    # 包装代码：把最后一行表达式赋值给 _result_
    lines = code_str.strip().split("\n")
    # 尝试把最后一行当作表达式
    wrapped = "\n".join(lines[:-1]) + f"\n_result_ = {lines[-1]}" if lines else "_result_ = None"

    try:
        exec(wrapped, namespace)
    except SyntaxError:
        # 最后一行不是表达式，直接执行全部代码
        namespace["_result_"] = None
        exec(code_str, namespace)

    return namespace.get("_result_")


@_register("text_prompt")
async def _exec_text_prompt(block: BlockDef, ctx: WorkflowContext) -> Any:
    """调用 LLM 处理文本 prompt。"""
    prompt_text = ctx.resolve(block.prompt)
    await ctx.log(f"[block:{block.label}] LLM prompt: {prompt_text[:80]}...")

    from utils import llm_chat

    resp = llm_chat(
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=2000,
    )

    if resp.choices:
        content = resp.choices[0].message.content
        # 尝试解析为 JSON
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content
    return ""


@_register("http_request")
async def _exec_http_request(block: BlockDef, ctx: WorkflowContext) -> Any:
    """发送 HTTP 请求。"""
    import httpx

    url = ctx.resolve(block.url)
    method = (block.method or "GET").upper()
    headers = ctx.resolve_deep(block.headers) if block.headers else {}
    body = ctx.resolve_deep(block.body) if block.body else None

    await ctx.log(f"[block:{block.label}] {method} {url}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        kwargs = {"method": method, "url": url, "headers": headers}
        if body and method in ("POST", "PUT", "PATCH"):
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["content"] = str(body)

        resp = await client.request(**kwargs)

    result = {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
    }
    # 尝试解析 JSON 响应
    try:
        result["body"] = resp.json()
    except Exception:
        result["body"] = resp.text[:5000]

    return result


# ── 控制流类 Block ─────────────────────────────────────────────────────────────

@_register("for_loop")
async def _exec_for_loop(block: BlockDef, ctx: WorkflowContext) -> Any:
    """
    循环执行嵌套 blocks。
    loop_over 解析为可迭代对象，每次迭代设置 ctx.current_value / ctx.current_index。
    """
    items = ctx.resolve_expression(block.loop_over)
    if not hasattr(items, "__iter__"):
        raise ValueError(f"for_loop 的 loop_over 必须是可迭代对象，实际: {type(items)}")

    await ctx.log(f"[block:{block.label}] 循环 {len(list(items))} 次")

    from .parser import _parse_blocks

    nested_blocks = _parse_blocks(block.blocks) if block.blocks else []
    results = []

    for i, item in enumerate(items):
        ctx.current_index = i
        ctx.current_value = item
        await ctx.log(f"  [loop:{block.label}] 迭代 {i}: {str(item)[:60]}")

        iteration_result = {}
        for nb in nested_blocks:
            result = await execute_block(nb, ctx)
            ctx.set_output(nb.label, result)
            iteration_result[nb.label] = result

        results.append(iteration_result)

    return results


@_register("conditional")
async def _exec_conditional(block: BlockDef, ctx: WorkflowContext) -> Any:
    """条件分支：根据 condition 表达式选择执行 then_blocks 或 else_blocks。"""
    condition_result = ctx.resolve_expression(block.condition)

    await ctx.log(f"[block:{block.label}] 条件: {block.condition} → {bool(condition_result)}")

    from .parser import _parse_blocks

    if condition_result:
        branches = _parse_blocks(block.then_blocks) if block.then_blocks else []
    else:
        branches = _parse_blocks(block.else_blocks) if block.else_blocks else []

    result = None
    for nb in branches:
        result = await execute_block(nb, ctx)
        ctx.set_output(nb.label, result)

    return result


@_register("wait")
async def _exec_wait(block: BlockDef, ctx: WorkflowContext) -> Any:
    """等待指定秒数。"""
    seconds = block.seconds or 1
    await ctx.log(f"[block:{block.label}] 等待 {seconds} 秒")
    await asyncio.sleep(seconds)
    return {"waited": seconds}
