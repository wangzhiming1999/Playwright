"""
Tests for workflow/blocks.py — block executors (code, wait, http_request, for_loop, conditional).
"""

import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from workflow.blocks import execute_block
from workflow.models import BlockDef
from workflow.context import WorkflowContext


def _ctx(**params):
    return WorkflowContext(workflow_id="wf1", run_id="run1", parameters=params)


# ── code block ───────────────────────────────────────────────────────────────


class TestCodeBlock:
    @pytest.mark.asyncio
    async def test_simple_expression(self):
        block = BlockDef(block_type="code", label="c1", code="1 + 2")
        result = await execute_block(block, _ctx())
        assert result == 3

    @pytest.mark.asyncio
    async def test_multiline_code(self):
        code = "x = [1, 2, 3]\nlen(x)"
        block = BlockDef(block_type="code", label="c2", code=code)
        result = await execute_block(block, _ctx())
        assert result == 3

    @pytest.mark.asyncio
    async def test_access_ctx_outputs(self):
        ctx = _ctx()
        ctx.set_output("prev", {"count": 10})
        block = BlockDef(block_type="code", label="c3", code="prev_output['count'] * 2")
        result = await execute_block(block, ctx)
        assert result == 20

    @pytest.mark.asyncio
    async def test_restricted_builtins(self):
        block = BlockDef(block_type="code", label="c4", code="sorted([3,1,2])")
        result = await execute_block(block, _ctx())
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_assignment_as_last_line(self):
        # "x = 42" gets wrapped as "_result_ = x = 42" which is valid Python
        code = "x = 42"
        block = BlockDef(block_type="code", label="c5", code=code)
        result = await execute_block(block, _ctx())
        assert result == 42

    @pytest.mark.asyncio
    async def test_jinja_template_in_code(self):
        ctx = _ctx(multiplier=5)
        block = BlockDef(block_type="code", label="c6", code="{{ multiplier }} * 3")
        result = await execute_block(block, ctx)
        assert result == 15


# ── wait block ───────────────────────────────────────────────────────────────


class TestWaitBlock:
    @pytest.mark.asyncio
    async def test_wait_returns_seconds(self):
        block = BlockDef(block_type="wait", label="w1", seconds=0.01)
        with patch("workflow.blocks.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await execute_block(block, _ctx())
        mock_sleep.assert_awaited_once_with(0.01)
        assert result == {"waited": 0.01}

    @pytest.mark.asyncio
    async def test_wait_default_1_second(self):
        block = BlockDef(block_type="wait", label="w2", seconds=None)
        with patch("workflow.blocks.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await execute_block(block, _ctx())
        mock_sleep.assert_awaited_once_with(1)


# ── http_request block ───────────────────────────────────────────────────────


class TestHttpRequestBlock:
    @pytest.mark.asyncio
    async def test_get_request(self):
        block = BlockDef(block_type="http_request", label="h1", url="https://api.example.com/data", method="GET")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"key": "value"}

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await execute_block(block, _ctx())

        assert result["status_code"] == 200
        assert result["body"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_post_with_json_body(self):
        block = BlockDef(
            block_type="http_request", label="h2",
            url="https://api.example.com/submit", method="POST",
            body={"name": "test"},
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {}
        mock_resp.json.return_value = {"id": 1}

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await execute_block(block, _ctx())

        assert result["status_code"] == 201
        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["json"] == {"name": "test"}

    @pytest.mark.asyncio
    async def test_url_template_resolved(self):
        block = BlockDef(
            block_type="http_request", label="h3",
            url="https://{{ host }}/api", method="GET",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await execute_block(block, _ctx(host="myapi.com"))

        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["url"] == "https://myapi.com/api"


# ── for_loop block ───────────────────────────────────────────────────────────


class TestForLoopBlock:
    @pytest.mark.asyncio
    async def test_iterates_items(self):
        block = BlockDef(
            block_type="for_loop", label="loop1",
            loop_over="{{ items }}",
            blocks=[{"block_type": "code", "label": "inner", "code": "ctx.current_value * 2"}],
        )
        ctx = _ctx(items=[1, 2, 3])
        result = await execute_block(block, ctx)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_sets_current_index(self):
        block = BlockDef(
            block_type="for_loop", label="loop2",
            loop_over="{{ items }}",
            blocks=[{"block_type": "code", "label": "idx", "code": "ctx.current_index"}],
        )
        ctx = _ctx(items=["a", "b"])
        result = await execute_block(block, ctx)
        assert result[0]["idx"] == 0
        assert result[1]["idx"] == 1

    @pytest.mark.asyncio
    async def test_non_iterable_raises(self):
        block = BlockDef(
            block_type="for_loop", label="loop3",
            loop_over="{{ count }}",
            blocks=[],
        )
        ctx = _ctx(count=42)
        with pytest.raises(ValueError, match="可迭代"):
            await execute_block(block, ctx)

    @pytest.mark.asyncio
    async def test_empty_items(self):
        block = BlockDef(
            block_type="for_loop", label="loop4",
            loop_over="{{ items }}",
            blocks=[{"block_type": "code", "label": "noop", "code": "1"}],
        )
        ctx = _ctx(items=[])
        result = await execute_block(block, ctx)
        assert result == []


# ── conditional block ────────────────────────────────────────────────────────


class TestConditionalBlock:
    @pytest.mark.asyncio
    async def test_true_branch(self):
        block = BlockDef(
            block_type="conditional", label="cond1",
            condition="{{ x > 5 }}",
            then_blocks=[{"block_type": "code", "label": "yes", "code": "'big'"}],
            else_blocks=[{"block_type": "code", "label": "no", "code": "'small'"}],
        )
        result = await execute_block(block, _ctx(x=10))
        assert result == "big"

    @pytest.mark.asyncio
    async def test_false_branch(self):
        block = BlockDef(
            block_type="conditional", label="cond2",
            condition="{{ x > 5 }}",
            then_blocks=[{"block_type": "code", "label": "yes2", "code": "'big'"}],
            else_blocks=[{"block_type": "code", "label": "no2", "code": "'small'"}],
        )
        result = await execute_block(block, _ctx(x=2))
        assert result == "small"

    @pytest.mark.asyncio
    async def test_no_else_branch(self):
        block = BlockDef(
            block_type="conditional", label="cond3",
            condition="{{ flag }}",
            then_blocks=[{"block_type": "code", "label": "yes3", "code": "'ok'"}],
        )
        result = await execute_block(block, _ctx(flag=False))
        assert result is None


# ── execute_block dispatch ───────────────────────────────────────────────────


class TestExecuteBlockDispatch:
    @pytest.mark.asyncio
    async def test_unknown_block_type_raises(self):
        block = BlockDef(block_type="nonexistent", label="bad")
        with pytest.raises(ValueError, match="未知"):
            await execute_block(block, _ctx())
