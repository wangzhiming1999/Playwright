"""
Tests for workflow/parser.py — YAML parsing and validation.
"""

import pytest

from workflow.parser import parse_workflow, validate_workflow, _parse_blocks
from workflow.models import WorkflowDef, BlockDef, ParameterDef


VALID_YAML = """
title: Test Workflow
description: A test
parameters:
  - key: url
    type: string
    description: Target URL
    required: true
  - key: count
    type: integer
    default: 3
blocks:
  - block_type: navigation
    label: step1
    url: "{{ url }}"
  - block_type: wait
    label: step2
    seconds: 2
  - block_type: code
    label: step3
    code: "len([1,2,3])"
"""


class TestParseWorkflow:
    def test_valid_yaml(self):
        wf = parse_workflow(VALID_YAML)
        assert wf.title == "Test Workflow"
        assert len(wf.parameters) == 2
        assert len(wf.blocks) == 3

    def test_parameters_parsed(self):
        wf = parse_workflow(VALID_YAML)
        assert wf.parameters[0].key == "url"
        assert wf.parameters[0].type == "string"
        assert wf.parameters[0].required is True
        assert wf.parameters[1].key == "count"
        assert wf.parameters[1].default == 3

    def test_blocks_parsed(self):
        wf = parse_workflow(VALID_YAML)
        assert wf.blocks[0].block_type == "navigation"
        assert wf.blocks[0].label == "step1"
        assert wf.blocks[1].block_type == "wait"
        assert wf.blocks[2].block_type == "code"

    def test_string_parameter_shorthand(self):
        yaml_str = """
title: Test
parameters:
  - simple_param
blocks: []
"""
        wf = parse_workflow(yaml_str)
        assert wf.parameters[0].key == "simple_param"
        assert wf.parameters[0].type == "string"

    def test_non_dict_top_level_raises(self):
        with pytest.raises(ValueError, match="顶层必须是一个字典"):
            parse_workflow("- item1\n- item2")

    def test_empty_blocks(self):
        wf = parse_workflow("title: Empty\nblocks: []")
        assert len(wf.blocks) == 0


class TestParseBlocks:
    def test_non_dict_block_raises(self):
        with pytest.raises(ValueError, match="block 必须是字典"):
            _parse_blocks(["not a dict"])

    def test_valid_blocks(self):
        blocks = _parse_blocks([
            {"block_type": "wait", "label": "w1", "seconds": 5},
        ])
        assert len(blocks) == 1
        assert blocks[0].seconds == 5


class TestValidateWorkflow:
    def test_invalid_block_type(self):
        wf = WorkflowDef(blocks=[BlockDef(block_type="invalid_type", label="b1")])
        with pytest.raises(ValueError, match="不合法"):
            validate_workflow(wf)

    def test_duplicate_label(self):
        wf = WorkflowDef(blocks=[
            BlockDef(block_type="wait", label="dup", seconds=1),
            BlockDef(block_type="wait", label="dup", seconds=2),
        ])
        with pytest.raises(ValueError, match="重复"):
            validate_workflow(wf)

    def test_missing_required_field(self):
        wf = WorkflowDef(blocks=[
            BlockDef(block_type="code", label="c1"),  # missing 'code' field
        ])
        with pytest.raises(ValueError, match="缺少必填字段"):
            validate_workflow(wf)

    def test_invalid_param_type(self):
        wf = WorkflowDef(
            parameters=[ParameterDef(key="x", type="invalid_type")],
            blocks=[],
        )
        with pytest.raises(ValueError, match="不合法"):
            validate_workflow(wf)

    def test_valid_all_block_types(self):
        """Ensure all 12 block types pass validation with required fields."""
        blocks = [
            BlockDef(block_type="task", label="b1", task="do something"),
            BlockDef(block_type="navigation", label="b2", url="https://example.com"),
            BlockDef(block_type="extraction", label="b3", data_extraction_goal="get prices"),
            BlockDef(block_type="login", label="b4", url="https://x.com", credentials="github"),
            BlockDef(block_type="file_upload", label="b5", file_path="/tmp/f.txt"),
            BlockDef(block_type="file_download", label="b6"),
            BlockDef(block_type="code", label="b7", code="1+1"),
            BlockDef(block_type="text_prompt", label="b8", prompt="summarize"),
            BlockDef(block_type="http_request", label="b9", url="https://api.com", method="GET"),
            BlockDef(block_type="for_loop", label="b10", loop_over="{{ items }}", blocks=[]),
            BlockDef(block_type="conditional", label="b11", condition="{{ x > 1 }}", then_blocks=[]),
            BlockDef(block_type="wait", label="b12", seconds=1),
        ]
        wf = WorkflowDef(blocks=blocks)
        validate_workflow(wf)  # should not raise

    def test_nested_for_loop_validation(self):
        yaml_str = """
title: Nested
blocks:
  - block_type: for_loop
    label: loop1
    loop_over: "{{ items }}"
    blocks:
      - block_type: wait
        label: inner_wait
        seconds: 1
"""
        wf = parse_workflow(yaml_str)
        assert wf.blocks[0].block_type == "for_loop"
