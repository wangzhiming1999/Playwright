"""
MCP Server 单元测试 — 测试工具注册、任务管理逻辑、_run_task_async。
直接测试内部函数，绕过 fastmcp 装饰器。
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from mcp_server import mcp, _tasks, _run_task_async


# ── 工具注册测试 ──────────────────────────────────────────

class TestMCPToolRegistration:
    def test_tools_registered(self):
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "run_browser_task" in tool_names
        assert "get_task_status" in tool_names
        assert "list_browser_tasks" in tool_names
        assert "extract_page_data" in tool_names
        assert "take_screenshot" in tool_names
        assert "run_workflow" in tool_names
        assert "list_workflows" in tool_names
        assert "list_templates" in tool_names

    def test_tool_count(self):
        assert len(mcp._tool_manager._tools) == 8

    def test_tool_has_description(self):
        for name, tool in mcp._tool_manager._tools.items():
            assert tool.description, f"工具 {name} 缺少 description"

    def test_server_name(self):
        assert mcp.name == "Browser Agent"


# ── _run_task_async 测试 ──────────────────────────────────

class TestRunTaskAsync:
    @pytest.fixture(autouse=True)
    def clear_tasks(self):
        _tasks.clear()
        yield
        _tasks.clear()

    @pytest.mark.asyncio
    async def test_success(self):
        task_id = "test_ok"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        mock_result = {"success": True, "steps": 3, "reason": "done", "cost": {"total_cost_usd": 0.01}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            await _run_task_async(task_id, "test task", True, 25)

        assert _tasks[task_id]["status"] == "completed"
        assert _tasks[task_id]["result"]["success"] is True
        assert _tasks[task_id]["result"]["steps"] == 3
        assert "started_at" in _tasks[task_id]
        assert "finished_at" in _tasks[task_id]

    @pytest.mark.asyncio
    async def test_failure(self):
        task_id = "test_fail"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        mock_result = {"success": False, "steps": 5, "reason": "timeout", "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            await _run_task_async(task_id, "failing task", True, 25)

        assert _tasks[task_id]["status"] == "failed"
        assert _tasks[task_id]["result"]["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self):
        task_id = "test_err"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        with patch("agent.runner.run_agent", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await _run_task_async(task_id, "error task", True, 25)

        assert _tasks[task_id]["status"] == "failed"
        assert "boom" in _tasks[task_id]["result"]["error"]
        assert "finished_at" in _tasks[task_id]

    @pytest.mark.asyncio
    async def test_logs_collected(self):
        task_id = "test_logs"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        async def mock_agent(**kwargs):
            cb = kwargs.get("log_callback")
            if cb:
                await cb("step 1")
                await cb("step 2")
            return {"success": True, "steps": 2, "cost": {}}

        with patch("agent.runner.run_agent", side_effect=mock_agent):
            await _run_task_async(task_id, "log task", True, 25)

        assert len(_tasks[task_id]["logs"]) == 2
        assert "step 1" in _tasks[task_id]["logs"]

    @pytest.mark.asyncio
    async def test_status_transitions(self):
        task_id = "test_status"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        statuses = []

        async def mock_agent(**kwargs):
            statuses.append(_tasks[task_id]["status"])  # 应该是 running
            return {"success": True, "steps": 1, "cost": {}}

        with patch("agent.runner.run_agent", side_effect=mock_agent):
            await _run_task_async(task_id, "test", True, 25)

        assert statuses[0] == "running"
        assert _tasks[task_id]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_timing_recorded(self):
        task_id = "test_time"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        mock_result = {"success": True, "steps": 1, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            await _run_task_async(task_id, "test", True, 25)

        assert _tasks[task_id]["started_at"] <= _tasks[task_id]["finished_at"]

    @pytest.mark.asyncio
    async def test_max_steps_passed(self):
        task_id = "test_steps"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        captured_kwargs = {}

        async def mock_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "steps": 1, "cost": {}}

        with patch("agent.runner.run_agent", side_effect=mock_agent):
            await _run_task_async(task_id, "test", True, 42)

        assert captured_kwargs["max_steps"] == 42

    @pytest.mark.asyncio
    async def test_headless_passed(self):
        task_id = "test_headless"
        _tasks[task_id] = {"id": task_id, "task": "test", "status": "pending", "logs": []}

        captured_kwargs = {}

        async def mock_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "steps": 1, "cost": {}}

        with patch("agent.runner.run_agent", side_effect=mock_agent):
            await _run_task_async(task_id, "test", False, 25)

        assert captured_kwargs["headless"] is False


# ── 任务管理状态测试 ──────────────────────────────────────

class TestTaskManagement:
    @pytest.fixture(autouse=True)
    def clear_tasks(self):
        _tasks.clear()
        yield
        _tasks.clear()

    def test_tasks_dict_empty(self):
        assert len(_tasks) == 0

    def test_tasks_stored_after_run(self):
        import asyncio
        mock_result = {"success": True, "steps": 1, "cost": {}}

        async def _run():
            _tasks["t1"] = {"id": "t1", "task": "test", "status": "pending", "logs": []}
            with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
                await _run_task_async("t1", "test", True, 25)

        asyncio.get_event_loop().run_until_complete(_run())
        assert "t1" in _tasks
        assert _tasks["t1"]["status"] == "completed"

    def test_multiple_tasks(self):
        _tasks["t1"] = {"id": "t1", "task": "task 1", "status": "completed", "logs": []}
        _tasks["t2"] = {"id": "t2", "task": "task 2", "status": "running", "logs": []}
        _tasks["t3"] = {"id": "t3", "task": "task 3", "status": "pending", "logs": []}
        assert len(_tasks) == 3

    def test_task_result_structure(self):
        import asyncio
        mock_result = {"success": True, "steps": 5, "reason": "完成", "cost": {"total_cost_usd": 0.05}}

        async def _run():
            _tasks["t1"] = {"id": "t1", "task": "test", "status": "pending", "logs": []}
            with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
                await _run_task_async("t1", "test", True, 25)

        asyncio.get_event_loop().run_until_complete(_run())
        result = _tasks["t1"]["result"]
        assert result["success"] is True
        assert result["steps"] == 5
        assert result["reason"] == "完成"
        assert result["cost"]["total_cost_usd"] == 0.05


# ── 工具函数签名测试（通过 fastmcp 内部结构） ──────────────

class TestToolSchemas:
    def test_run_browser_task_params(self):
        tool = mcp._tool_manager._tools["run_browser_task"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "task" in props
        assert "headless" in props
        assert "max_steps" in props
        assert "wait_for_completion" in props

    def test_get_task_status_params(self):
        tool = mcp._tool_manager._tools["get_task_status"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "task_id" in props

    def test_extract_page_data_params(self):
        tool = mcp._tool_manager._tools["extract_page_data"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "url" in props
        assert "question" in props

    def test_take_screenshot_params(self):
        tool = mcp._tool_manager._tools["take_screenshot"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "url" in props
        assert "full_page" in props

    def test_run_workflow_params(self):
        tool = mcp._tool_manager._tools["run_workflow"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "workflow_name" in props

    def test_list_tools_no_params(self):
        """list_browser_tasks / list_workflows / list_templates 无必填参数。"""
        for name in ["list_browser_tasks", "list_workflows", "list_templates"]:
            tool = mcp._tool_manager._tools[name]
            required = tool.parameters.get("required", [])
            assert len(required) == 0, f"{name} 不应有必填参数"
