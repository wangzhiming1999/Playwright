"""
Pydantic 模型：工作流定义、参数、Block、API 请求/响应。
"""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


# ── 参数定义 ─────────────────────────────────────────────────────────────────

PARAM_TYPES = {"string", "integer", "float", "boolean", "json"}


class ParameterDef(BaseModel):
    key: str
    type: str = "string"
    description: str = ""
    default: Any = None
    required: bool = True


# ── Block 定义 ────────────────────────────────────────────────────────────────

BLOCK_TYPES = {
    # 浏览器
    "task", "navigation", "extraction", "login", "file_upload", "file_download",
    # 数据处理
    "code", "text_prompt", "http_request",
    # 控制流
    "for_loop", "conditional", "wait",
}

# 每种 block_type 的必填字段
BLOCK_REQUIRED_FIELDS = {
    "task": ["task"],
    "navigation": ["url"],
    "extraction": ["data_extraction_goal"],
    "login": ["url", "credentials"],
    "file_upload": ["file_path"],
    "file_download": [],
    "code": ["code"],
    "text_prompt": ["prompt"],
    "http_request": ["url", "method"],
    "for_loop": ["loop_over", "blocks"],
    "conditional": ["condition", "then_blocks"],
    "wait": ["seconds"],
}


class BlockDef(BaseModel):
    """通用 block 定义，所有 block_type 共享字段 + 各类型特有字段通过 extra 传递。"""
    block_type: str
    label: str
    continue_on_failure: bool = False
    max_retries: int = 0
    next_block_label: str | None = None

    # 各 block_type 特有字段（动态）
    # task
    task: str | None = None
    max_steps: int = 35

    # navigation
    url: str | None = None
    navigation_goal: str | None = None

    # extraction
    data_extraction_goal: str | None = None
    data_schema: dict | None = None

    # login
    credentials: str | None = None  # 环境变量 key，如 "github" → GITHUB_EMAIL/GITHUB_PASSWORD

    # code
    code: str | None = None

    # text_prompt
    prompt: str | None = None

    # http_request
    method: str = "GET"
    headers: dict | None = None
    body: Any = None

    # for_loop
    loop_over: str | None = None
    blocks: list[dict] | None = None  # 嵌套 blocks（原始 dict，运行时解析）

    # conditional
    condition: str | None = None
    then_blocks: list[dict] | None = None
    else_blocks: list[dict] | None = None

    # wait
    seconds: float | None = None

    # file_upload
    file_path: str | None = None

    # file_download
    download_goal: str | None = None


# ── 工作流定义 ────────────────────────────────────────────────────────────────

class WorkflowDef(BaseModel):
    title: str = ""
    description: str = ""
    parameters: list[ParameterDef] = Field(default_factory=list)
    blocks: list[BlockDef] = Field(default_factory=list)


# ── API 请求模型 ──────────────────────────────────────────────────────────────

class WorkflowCreateRequest(BaseModel):
    yaml_content: str  # 原始 YAML 字符串


class WorkflowUpdateRequest(BaseModel):
    yaml_content: str


class WorkflowRunRequest(BaseModel):
    parameters: dict = Field(default_factory=dict)
    browser_mode: str = "builtin"
    cdp_url: str = "http://localhost:9222"
    chrome_profile: str = "Default"
    webhook_url: str = ""
    timeout: int = 0
