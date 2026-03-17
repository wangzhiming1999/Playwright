"""
自定义 Action 注册系统。

用法：在 custom_actions/ 目录下创建 Python 文件，用 @action 装饰器注册：

    # custom_actions/my_actions.py
    from agent.action_registry import action

    @action(
        name="send_slack",
        description="发送 Slack 消息到指定频道",
        parameters={
            "channel": {"type": "string", "description": "频道名称", "required": True},
            "message": {"type": "string", "description": "消息内容", "required": True},
        },
    )
    async def send_slack(channel: str, message: str, **ctx) -> str:
        # ctx 包含 page, agent, log_fn 等上下文
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json={"channel": channel, "text": message})
        return f"已发送消息到 #{channel}"

启动时自动扫描 custom_actions/ 目录，注册所有 @action 装饰的函数。
"""

import importlib.util
import inspect
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

from .page_utils import _safe_print


@dataclass
class ActionDef:
    """一个已注册的自定义 action。"""
    name: str
    description: str
    parameters: dict  # OpenAI function calling 格式的 parameters schema
    handler: Callable[..., Awaitable[str]]  # async (args, **ctx) -> str
    allowed_domains: list[str] = field(default_factory=list)  # 空 = 不限制
    source_file: str = ""  # 来源文件路径


# ── 全局注册表 ────────────────────────────────────────────────────────────────

_registry: dict[str, ActionDef] = {}


def get_registry() -> dict[str, ActionDef]:
    """返回当前注册表（只读视图）。"""
    return _registry


def get_custom_tools() -> list[dict]:
    """
    返回所有自定义 action 的 OpenAI function calling 格式工具定义。
    可直接追加到 TOOLS 列表中。
    """
    tools = []
    for action_def in _registry.values():
        tools.append({
            "type": "function",
            "function": {
                "name": action_def.name,
                "description": action_def.description,
                "parameters": action_def.parameters,
            },
        })
    return tools


def is_custom_action(tool_name: str) -> bool:
    """检查是否是自定义 action。"""
    return tool_name in _registry


async def execute_custom_action(tool_name: str, args: dict, **ctx) -> str:
    """
    执行自定义 action。

    ctx 可包含：
    - page: Playwright Page 对象
    - agent: BrowserAgent 实例
    - log_fn: async (msg) -> None 日志函数
    """
    action_def = _registry.get(tool_name)
    if not action_def:
        return f"操作失败: 未知的自定义 action '{tool_name}'"

    # 域名过滤
    if action_def.allowed_domains:
        page = ctx.get("page")
        if page:
            try:
                from urllib.parse import urlparse
                current_domain = urlparse(page.url).hostname or ""
                if not any(current_domain.endswith(d) for d in action_def.allowed_domains):
                    return f"操作失败: action '{tool_name}' 仅允许在以下域名使用: {action_def.allowed_domains}"
            except Exception:
                pass

    # 参数校验
    required = []
    props = action_def.parameters.get("properties", {})
    for key, schema in props.items():
        if key in action_def.parameters.get("required", []):
            required.append(key)

    for key in required:
        if key not in args or args[key] is None or args[key] == "":
            return f"操作失败: 缺少必需参数 '{key}'"

    # 类型转换
    for key, value in list(args.items()):
        schema = props.get(key, {})
        expected_type = schema.get("type", "string")
        try:
            if expected_type == "integer" and not isinstance(value, int):
                args[key] = int(value)
            elif expected_type == "number" and not isinstance(value, (int, float)):
                args[key] = float(value)
            elif expected_type == "boolean" and not isinstance(value, bool):
                args[key] = str(value).lower() in ("true", "1", "yes")
        except (ValueError, TypeError):
            return f"操作失败: 参数 '{key}' 类型错误，期望 {expected_type}"

    # 执行
    try:
        handler = action_def.handler
        sig = inspect.signature(handler)
        param_names = list(sig.parameters.keys())

        # 构建调用参数：handler 声明的参数从 args 取，**ctx 传入上下文
        call_kwargs = {}
        for name in param_names:
            if name in args:
                call_kwargs[name] = args[name]
            elif name in ctx:
                call_kwargs[name] = ctx[name]

        # 如果 handler 接受 **kwargs，把剩余的 ctx 也传进去
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if has_var_keyword:
            for k, v in ctx.items():
                if k not in call_kwargs:
                    call_kwargs[k] = v

        result = await handler(**call_kwargs)
        return str(result) if result is not None else "操作完成"

    except Exception as e:
        return f"操作失败: 自定义 action '{tool_name}' 执行异常 — {e}"


# ── @action 装饰器 ────────────────────────────────────────────────────────────

def action(
    name: str,
    description: str,
    parameters: dict = None,
    allowed_domains: list[str] = None,
):
    """
    装饰器：注册一个自定义 action。

    用法：
        @action(
            name="my_tool",
            description="做某事",
            parameters={
                "url": {"type": "string", "description": "目标 URL", "required": True},
                "count": {"type": "integer", "description": "数量", "default": 1},
            },
        )
        async def my_tool(url: str, count: int = 1, **ctx) -> str:
            ...
            return "结果"

    parameters 格式简化版（自动转为 OpenAI schema）：
        {"param_name": {"type": "string", "description": "...", "required": True}}
    """
    def decorator(fn: Callable):
        # 将简化参数格式转为 OpenAI function calling schema
        schema = _build_schema(parameters or {})

        action_def = ActionDef(
            name=name,
            description=description,
            parameters=schema,
            handler=fn,
            allowed_domains=allowed_domains or [],
            source_file=inspect.getfile(fn) if hasattr(fn, '__code__') else "",
        )

        if name in _registry:
            _safe_print(f"  [action_registry] 覆盖已有 action: {name}")
        _registry[name] = action_def
        _safe_print(f"  [action_registry] 注册: {name} — {description}")

        return fn
    return decorator


def _build_schema(params: dict) -> dict:
    """
    将简化参数格式转为 OpenAI function calling 的 parameters schema。

    输入: {"url": {"type": "string", "description": "目标 URL", "required": True}}
    输出: {"type": "object", "properties": {...}, "required": [...]}
    """
    if "type" in params and params["type"] == "object":
        # 已经是标准格式
        return params

    properties = {}
    required = []

    for key, spec in params.items():
        if not isinstance(spec, dict):
            # 简写：直接给类型字符串
            properties[key] = {"type": str(spec), "description": key}
            continue

        prop = {"type": spec.get("type", "string")}
        if "description" in spec:
            prop["description"] = spec["description"]
        if "enum" in spec:
            prop["enum"] = spec["enum"]
        if "default" in spec:
            prop["default"] = spec["default"]

        properties[key] = prop

        if spec.get("required", False):
            required.append(key)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# ── 自动发现 + 加载 ──────────────────────────────────────────────────────────

def load_custom_actions(directory: str = "custom_actions") -> int:
    """
    扫描指定目录下的所有 .py 文件，导入并执行（触发 @action 装饰器注册）。
    返回新注册的 action 数量。
    """
    actions_dir = Path(directory)
    if not actions_dir.exists():
        return 0

    count_before = len(_registry)
    loaded_files = []

    for py_file in sorted(actions_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # 跳过 __init__.py 等

        module_name = f"custom_actions.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                loaded_files.append(py_file.name)
        except Exception as e:
            _safe_print(f"  [action_registry] 加载 {py_file.name} 失败: {e}")

    new_count = len(_registry) - count_before
    if loaded_files:
        _safe_print(f"  [action_registry] 已加载 {len(loaded_files)} 个文件，新增 {new_count} 个 action")
    return new_count


def unregister(name: str) -> bool:
    """移除一个已注册的 action。"""
    if name in _registry:
        del _registry[name]
        return True
    return False


def clear_registry():
    """清空注册表（测试用）。"""
    _registry.clear()
