"""
Agent 可观测性追踪 — 记录每步决策链路，支持事后分析和调试。

每步记录：
  - LLM 输入模式（截图/DOM）
  - 工具选择 + 参数
  - 执行结果
  - 视觉验证结果
  - 页面状态变化
  - token 消耗
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class StepTrace:
    """单步决策链路追踪。"""
    step: int = 0
    timestamp: float = 0.0

    # LLM 输入
    input_mode: str = ""          # "screenshot" | "dom"
    elements_count: int = 0       # 标注元素数量
    page_url: str = ""
    page_title: str = ""

    # LLM 输出
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    is_multi_action: bool = False  # 是否批量 action
    action_count: int = 1          # 本步 action 数量

    # 执行结果
    result: str = ""
    result_is_error: bool = False
    duration_ms: float = 0.0       # 本步执行耗时

    # 视觉验证
    verify_changed: bool = True
    verify_type: str = ""          # url/content/scroll/input/none/skip
    verify_nudge: str = ""         # 注入的 nudge

    # 页面变化
    url_before: str = ""
    url_after: str = ""
    page_changed: bool = False

    # token / 成本
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    # 额外事件
    nudges: list[str] = field(default_factory=list)  # 循环检测/停滞检测等 nudge
    events: list[str] = field(default_factory=list)   # watchdog 事件


@dataclass
class TaskTrace:
    """整个任务的追踪数据。"""
    task_id: str = ""
    task: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    success: bool = False
    reason: str = ""
    total_steps: int = 0
    total_cost_usd: float = 0.0
    steps: list[StepTrace] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at and self.started_at:
            return round(self.finished_at - self.started_at, 1)
        return 0.0

    @property
    def tool_usage(self) -> dict[str, int]:
        """统计每个工具的使用次数。"""
        usage: dict[str, int] = {}
        for s in self.steps:
            if s.tool_name:
                usage[s.tool_name] = usage.get(s.tool_name, 0) + 1
        return usage

    @property
    def error_steps(self) -> list[StepTrace]:
        """返回所有出错的步骤。"""
        return [s for s in self.steps if s.result_is_error]

    @property
    def verify_failures(self) -> list[StepTrace]:
        """返回所有视觉验证失败的步骤。"""
        return [s for s in self.steps if not s.verify_changed and s.verify_type == "none"]

    def summary(self) -> dict:
        """生成追踪摘要。"""
        return {
            "task_id": self.task_id,
            "task": self.task[:100],
            "success": self.success,
            "reason": self.reason,
            "duration_seconds": self.duration_seconds,
            "total_steps": self.total_steps,
            "total_cost_usd": self.total_cost_usd,
            "tool_usage": self.tool_usage,
            "error_count": len(self.error_steps),
            "verify_failure_count": len(self.verify_failures),
        }


class TraceCollector:
    """追踪数据收集器 — 在 runner.py 主循环中使用。"""

    def __init__(self, task_id: str = "", task: str = ""):
        self._trace = TaskTrace(task_id=task_id, task=task, started_at=time.time())
        self._current_step: StepTrace | None = None

    def begin_step(self, step: int, page_url: str = "", page_title: str = ""):
        """开始新的一步。"""
        self._current_step = StepTrace(
            step=step,
            timestamp=time.time(),
            page_url=page_url,
            page_title=page_title,
        )

    def set_input_mode(self, mode: str, elements_count: int = 0):
        """记录 LLM 输入模式。"""
        if self._current_step:
            self._current_step.input_mode = mode
            self._current_step.elements_count = elements_count

    def set_tool_call(self, tool_name: str, tool_args: dict, action_count: int = 1):
        """记录工具调用。"""
        if self._current_step:
            self._current_step.tool_name = tool_name
            self._current_step.tool_args = tool_args
            self._current_step.is_multi_action = action_count > 1
            self._current_step.action_count = action_count

    def set_result(self, result: str, is_error: bool = False, duration_ms: float = 0.0):
        """记录执行结果。"""
        if self._current_step:
            self._current_step.result = result[:500]
            self._current_step.result_is_error = is_error
            self._current_step.duration_ms = duration_ms

    def set_verify(self, changed: bool, verify_type: str = "", nudge: str = ""):
        """记录视觉验证结果。"""
        if self._current_step:
            self._current_step.verify_changed = changed
            self._current_step.verify_type = verify_type
            self._current_step.verify_nudge = nudge

    def set_page_change(self, url_before: str, url_after: str, changed: bool):
        """记录页面变化。"""
        if self._current_step:
            self._current_step.url_before = url_before
            self._current_step.url_after = url_after
            self._current_step.page_changed = changed

    def set_llm_usage(self, input_tokens: int = 0, output_tokens: int = 0,
                       cached_tokens: int = 0, cost_usd: float = 0.0, model: str = ""):
        """记录 LLM token 消耗。"""
        if self._current_step:
            self._current_step.input_tokens = input_tokens
            self._current_step.output_tokens = output_tokens
            self._current_step.cached_tokens = cached_tokens
            self._current_step.cost_usd = cost_usd
            self._current_step.model = model

    def add_nudge(self, nudge: str):
        """记录 nudge 事件。"""
        if self._current_step:
            self._current_step.nudges.append(nudge)

    def add_event(self, event: str):
        """记录 watchdog 事件。"""
        if self._current_step:
            self._current_step.events.append(event)

    def end_step(self):
        """结束当前步骤，加入追踪列表。"""
        if self._current_step:
            self._trace.steps.append(self._current_step)
            self._current_step = None

    def finish(self, success: bool, reason: str, total_steps: int, total_cost_usd: float = 0.0):
        """完成任务追踪。"""
        self._trace.finished_at = time.time()
        self._trace.success = success
        self._trace.reason = reason
        self._trace.total_steps = total_steps
        self._trace.total_cost_usd = total_cost_usd

    @property
    def trace(self) -> TaskTrace:
        return self._trace

    def to_dict(self) -> dict:
        """序列化为 dict。"""
        return asdict(self._trace)

    def to_json(self) -> str:
        """序列化为 JSON。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def save(self, path: str | Path):
        """保存到文件。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "TaskTrace":
        """从文件加载。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = [StepTrace(**s) for s in data.pop("steps", [])]
        trace = TaskTrace(**data)
        trace.steps = steps
        return trace
