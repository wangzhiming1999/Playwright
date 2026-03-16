"""
agent 包 — 模块化的 Playwright + GPT 网页操作 Agent

模块结构：
  - core.py        BrowserAgent 类（截图、点击、输入、工具执行）
  - runner.py      run_agent() 主函数（GPT 决策循环）
  - tools.py       TOOLS 工具定义（GPT 可调用的操作列表）
  - llm_helpers.py 任务分解、步骤验证、上下文压缩、失败分析
  - page_utils.py  页面就绪等待、安全打印
  - chrome_detector.py  Chrome/Edge 用户数据目录检测
"""

from .runner import run_agent, SYSTEM_PROMPT_STATIC
from .tools import TOOLS
from .core import BrowserAgent
from .llm_helpers import robust_json_loads, trim_elements
from .page_utils import structured_log

__all__ = ["run_agent", "TOOLS", "BrowserAgent", "robust_json_loads", "trim_elements", "SYSTEM_PROMPT_STATIC", "structured_log"]
