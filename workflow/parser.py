"""
YAML 工作流解析 + 校验。
"""

from __future__ import annotations
import yaml

from .models import (
    WorkflowDef, ParameterDef, BlockDef,
    BLOCK_TYPES, BLOCK_REQUIRED_FIELDS, PARAM_TYPES,
)


def parse_workflow(yaml_str: str) -> WorkflowDef:
    """解析 YAML 字符串为 WorkflowDef，同时做基本校验。"""
    raw = yaml.safe_load(yaml_str)
    if not isinstance(raw, dict):
        raise ValueError("YAML 顶层必须是一个字典")

    # 解析 parameters
    params = []
    for p in raw.get("parameters", []):
        if isinstance(p, str):
            params.append(ParameterDef(key=p))
        elif isinstance(p, dict):
            params.append(ParameterDef(
                key=p["key"],
                type=p.get("type", "string"),
                description=p.get("description", ""),
                default=p.get("default"),
                required=p.get("required", p.get("default") is None),
            ))

    # 解析 blocks
    blocks = _parse_blocks(raw.get("blocks", []))

    wf = WorkflowDef(
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        parameters=params,
        blocks=blocks,
    )

    validate_workflow(wf)
    return wf


def _parse_blocks(raw_blocks: list[dict]) -> list[BlockDef]:
    """递归解析 block 列表。"""
    blocks = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            raise ValueError(f"block 必须是字典，收到: {type(b)}")
        blocks.append(BlockDef(**b))
    return blocks


def validate_workflow(wf: WorkflowDef):
    """校验工作流定义的完整性。"""
    errors = []

    # 1. 参数类型校验
    for p in wf.parameters:
        if p.type not in PARAM_TYPES:
            errors.append(f"参数 '{p.key}' 的类型 '{p.type}' 不合法，可选: {PARAM_TYPES}")

    # 2. Block 校验
    labels = set()
    _validate_blocks(wf.blocks, labels, errors)

    if errors:
        raise ValueError("工作流校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


def _validate_blocks(blocks: list[BlockDef], labels: set, errors: list):
    """递归校验 block 列表。"""
    for block in blocks:
        # block_type 合法性
        if block.block_type not in BLOCK_TYPES:
            errors.append(f"block '{block.label}' 的类型 '{block.block_type}' 不合法，可选: {BLOCK_TYPES}")

        # label 唯一性
        if block.label in labels:
            errors.append(f"block label '{block.label}' 重复")
        labels.add(block.label)

        # 必填字段
        required = BLOCK_REQUIRED_FIELDS.get(block.block_type, [])
        for field in required:
            val = getattr(block, field, None)
            if val is None:
                errors.append(f"block '{block.label}' (type={block.block_type}) 缺少必填字段 '{field}'")

        # 递归校验嵌套 blocks
        if block.block_type == "for_loop" and block.blocks:
            nested = _parse_blocks(block.blocks) if block.blocks and isinstance(block.blocks[0], dict) else []
            _validate_blocks(nested, labels, errors)
        if block.block_type == "conditional":
            if block.then_blocks:
                nested = _parse_blocks(block.then_blocks) if isinstance(block.then_blocks[0], dict) else []
                _validate_blocks(nested, labels, errors)
            if block.else_blocks:
                nested = _parse_blocks(block.else_blocks) if isinstance(block.else_blocks[0], dict) else []
                _validate_blocks(nested, labels, errors)
