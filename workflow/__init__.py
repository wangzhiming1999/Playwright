"""
workflow 包 — YAML 声明式工作流引擎

模块结构：
  - models.py    Pydantic 模型（参数定义、Block 定义、工作流定义、API 请求/响应）
  - context.py   WorkflowContext（参数存储、Jinja2 解析、block 输出管理）
  - parser.py    YAML 解析 + 校验
  - blocks.py    Block 执行器（12 种 block_type）
  - engine.py    WorkflowEngine（顺序执行、重试、错误处理）
  - db.py        SQLite CRUD（workflows + workflow_runs）
  - loader.py    启动时扫描 workflows/ 目录
"""

from .engine import WorkflowEngine
from .context import WorkflowContext
from .parser import parse_workflow, validate_workflow
from .models import WorkflowDef, WorkflowCreateRequest, WorkflowRunRequest
from .db import (
    init_workflow_db, save_workflow, load_all_workflows, delete_workflow,
    save_workflow_run, load_workflow_runs, load_workflow_run,
)
from .loader import scan_workflow_directory

__all__ = [
    "WorkflowEngine", "WorkflowContext",
    "parse_workflow", "validate_workflow",
    "WorkflowDef", "WorkflowCreateRequest", "WorkflowRunRequest",
    "init_workflow_db", "save_workflow", "load_all_workflows", "delete_workflow",
    "save_workflow_run", "load_workflow_runs", "load_workflow_run",
    "scan_workflow_directory",
]
