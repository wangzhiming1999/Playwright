"""
Tests for workflow/engine.py — WorkflowEngine execution, retry, continue_on_failure.
"""

import pytest
from unittest.mock import patch, AsyncMock

from workflow.engine import WorkflowEngine, _safe_serialize


# ── _safe_serialize ──────────────────────────────────────────────────────────


class TestSafeSerialize:
    def test_primitives(self):
        assert _safe_serialize(None) is None
        assert _safe_serialize("hello") == "hello"
        assert _safe_serialize(42) == 42
        assert _safe_serialize(3.14) == 3.14
        assert _safe_serialize(True) is True

    def test_nested_dict(self):
        result = _safe_serialize({"a": 1, "b": {"c": 2}})
        assert result == {"a": 1, "b": {"c": 2}}

    def test_list(self):
        assert _safe_serialize([1, "two", 3.0]) == [1, "two", 3.0]

    def test_non_serializable_becomes_str(self):
        result = _safe_serialize(object())
        assert isinstance(result, str)


# ── WorkflowEngine ──────────────────────────────────────────────────────────


def _simple_workflow(*blocks):
    return {
        "id": "wf_test",
        "title": "Test",
        "blocks": list(blocks),
    }


class TestEngineSequentialExecution:
    @pytest.mark.asyncio
    async def test_two_blocks_run_in_order(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "a", "code": "10"},
            {"block_type": "code", "label": "b", "code": "a_output + 5"},
        )
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["status"] == "completed"
        assert result["block_results"]["a"] == 10
        assert result["block_results"]["b"] == 15

    @pytest.mark.asyncio
    async def test_empty_workflow_completes(self):
        wf = _simple_workflow()
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_parameters_passed_to_context(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "p", "code": "ctx.parameters['x'] * 2"},
        )
        engine = WorkflowEngine(wf, parameters={"x": 7})
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["block_results"]["p"] == 14


class TestEngineRetry:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "flaky", "code": "1/0", "max_retries": 2},
        )
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        # Should fail after 1 + 2 retries = 3 attempts
        assert result["status"] == "failed"
        assert "flaky" in result["error"]

    @pytest.mark.asyncio
    async def test_continue_on_failure(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "fail_ok", "code": "1/0", "continue_on_failure": True},
            {"block_type": "code", "label": "after", "code": "42"},
        )
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["status"] == "completed"
        assert result["block_results"]["after"] == 42
        assert "error" in result["block_results"]["fail_ok"]


class TestEngineJump:
    @pytest.mark.asyncio
    async def test_next_block_label_jump(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "start", "code": "'jumped'", "next_block_label": "target"},
            {"block_type": "code", "label": "skipped", "code": "'should not run'"},
            {"block_type": "code", "label": "target", "code": "'arrived'"},
        )
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["status"] == "completed"
        assert "skipped" not in result["block_results"]
        assert result["block_results"]["target"] == "arrived"

    @pytest.mark.asyncio
    async def test_invalid_jump_label_continues(self):
        wf = _simple_workflow(
            {"block_type": "code", "label": "s1", "code": "1", "next_block_label": "nonexistent"},
            {"block_type": "code", "label": "s2", "code": "2"},
        )
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        # Invalid label falls through to sequential
        assert result["status"] == "completed"
        assert result["block_results"]["s2"] == 2


class TestEngineTimestamps:
    @pytest.mark.asyncio
    async def test_has_timestamps(self):
        wf = _simple_workflow({"block_type": "code", "label": "t", "code": "1"})
        engine = WorkflowEngine(wf)
        with patch.object(engine, "_persist"):
            result = await engine.run()
        assert result["started_at"] is not None
        assert result["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_run_id_generated(self):
        wf = _simple_workflow()
        engine = WorkflowEngine(wf)
        assert len(engine.run_id) == 12
