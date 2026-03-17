"""
agent 包 — 模块化的 Playwright + GPT 网页操作 Agent

模块结构：
  - core.py        BrowserAgent 类（截图、点击、输入、工具执行）
  - runner.py      run_agent() 主函数（GPT 决策循环）
  - tools.py       TOOLS 工具定义（GPT 可调用的操作列表）
  - llm_helpers.py 任务分解、步骤验证、上下文压缩、失败分析
  - page_utils.py  页面就绪等待、安全打印
  - chrome_detector.py  Chrome/Edge 用户数据目录检测
  - loop_detector.py    循环检测器（防止 agent 原地打转）
"""

from .runner import run_agent
from .tools import TOOLS, TERMINATES_SEQUENCE
from .core import BrowserAgent
from .llm_helpers import robust_json_loads, trim_elements, estimate_message_tokens, estimate_messages_tokens
from .page_utils import structured_log
from .loop_detector import ActionLoopDetector
from .plan_manager import PlanManager
from .watchdog import Watchdog, EventType
from .action_registry import action, load_custom_actions, get_custom_tools, get_registry
from .task_pool import TaskPool

__version__ = "0.1.0"

__all__ = [
    "run_agent", "TOOLS", "TERMINATES_SEQUENCE", "BrowserAgent",
    "robust_json_loads", "trim_elements", "structured_log",
    "estimate_message_tokens", "estimate_messages_tokens",
    "ActionLoopDetector", "PlanManager",
    "Watchdog", "EventType",
    "action", "load_custom_actions", "get_custom_tools", "get_registry",
    "TaskPool",
    "__version__",
]
