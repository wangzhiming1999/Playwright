"""
WorkflowContext — 工作流运行时上下文。
管理参数、block 输出、Jinja2 模板解析、循环状态。
"""

from __future__ import annotations
from typing import Any, Callable, Awaitable

from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined


class WorkflowContext:
    def __init__(
        self,
        workflow_id: str,
        run_id: str,
        parameters: dict[str, Any],
        page=None,          # Playwright Page（浏览器 block 用）
        agent=None,         # BrowserAgent 实例
        log_callback: Callable[[str, str], Awaitable[None]] | None = None,
        screenshot_callback: Callable[[str, str], Awaitable[None]] | None = None,
        ask_user_callback: Callable | None = None,
    ):
        self.workflow_id = workflow_id
        self.run_id = run_id
        self.parameters = parameters
        self.page = page
        self.agent = agent
        self.log_callback = log_callback
        self.screenshot_callback = screenshot_callback
        self.ask_user_callback = ask_user_callback

        # Block 输出存储：{"label_output": value}
        self._outputs: dict[str, Any] = {}

        # 循环状态
        self.current_value: Any = None
        self.current_index: int = 0

        # Jinja2 沙箱环境
        self._jinja_env = SandboxedEnvironment(undefined=StrictUndefined)

    def set_output(self, label: str, value: Any):
        """存储 block 输出，可通过 {{ label_output }} 引用。"""
        self._outputs[f"{label}_output"] = value

    def get_output(self, label: str) -> Any:
        return self._outputs.get(f"{label}_output")

    def get_all_outputs(self) -> dict:
        return dict(self._outputs)

    def _build_namespace(self) -> dict[str, Any]:
        """构建 Jinja2 渲染的变量命名空间。"""
        ns = {}
        ns.update(self.parameters)
        ns.update(self._outputs)
        ns["current_value"] = self.current_value
        ns["current_index"] = self.current_index
        return ns

    def resolve(self, template_str: str) -> str:
        """解析 Jinja2 模板字符串，返回渲染后的字符串。"""
        if not isinstance(template_str, str) or "{{" not in template_str:
            return template_str
        tmpl = self._jinja_env.from_string(template_str)
        return tmpl.render(**self._build_namespace())

    def resolve_expression(self, expr: str) -> Any:
        """
        解析 Jinja2 表达式并返回原生 Python 对象（非字符串）。
        用于 loop_over、condition 等需要非字符串结果的场景。

        例如：
          "{{ get_results_output.items }}" → 返回实际的 list 对象
          "{{ price > 100 }}" → 返回 True/False
        """
        if not isinstance(expr, str):
            return expr

        # 提取 {{ ... }} 中的表达式
        stripped = expr.strip()
        if stripped.startswith("{{") and stripped.endswith("}}"):
            inner = stripped[2:-2].strip()
            # 用 Jinja2 编译表达式并求值
            compiled = self._jinja_env.compile_expression(inner)
            return compiled(**self._build_namespace())

        # 不是纯表达式，按字符串模板解析
        return self.resolve(expr)

    def resolve_deep(self, obj: Any) -> Any:
        """递归解析 dict/list 中所有字符串值的 Jinja2 模板。"""
        if isinstance(obj, str):
            return self.resolve(obj)
        if isinstance(obj, dict):
            return {k: self.resolve_deep(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_deep(item) for item in obj]
        return obj

    async def log(self, msg: str):
        if self.log_callback:
            await self.log_callback(self.run_id, msg)
