"""
Tests for agent/action_registry.py — @action decorator, schema, execution.
"""

import pytest
import asyncio

from agent.action_registry import (
    action,
    _build_schema,
    get_custom_tools,
    is_custom_action,
    execute_custom_action,
    unregister,
    clear_registry,
    get_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


# ── _build_schema ────────────────────────────────────────────────────────────


class TestBuildSchema:
    def test_simplified_to_openai(self):
        params = {
            "url": {"type": "string", "description": "Target URL", "required": True},
            "count": {"type": "integer", "description": "Number", "default": 1},
        }
        schema = _build_schema(params)
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "count" in schema["properties"]
        assert "url" in schema["required"]
        assert "count" not in schema.get("required", [])

    def test_passthrough_standard_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = _build_schema(schema)
        assert result is schema

    def test_shorthand_type_string(self):
        params = {"name": "string"}
        schema = _build_schema(params)
        assert schema["properties"]["name"]["type"] == "string"

    def test_enum_preserved(self):
        params = {"mode": {"type": "string", "enum": ["fast", "slow"]}}
        schema = _build_schema(params)
        assert schema["properties"]["mode"]["enum"] == ["fast", "slow"]

    def test_empty_params(self):
        schema = _build_schema({})
        assert schema == {"type": "object", "properties": {}}


# ── @action decorator ───────────────────────────────────────────────────────


class TestActionDecorator:
    def test_registers_action(self):
        @action(name="test_action", description="A test")
        async def handler(**ctx):
            return "ok"

        assert is_custom_action("test_action")
        reg = get_registry()
        assert reg["test_action"].description == "A test"

    def test_overwrites_existing(self):
        @action(name="dup", description="first")
        async def h1(**ctx):
            return "1"

        @action(name="dup", description="second")
        async def h2(**ctx):
            return "2"

        assert get_registry()["dup"].description == "second"

    def test_returns_original_function(self):
        @action(name="ret_test", description="test")
        async def my_fn(**ctx):
            return "hello"

        assert asyncio.iscoroutinefunction(my_fn)


# ── get_custom_tools ─────────────────────────────────────────────────────────


class TestGetCustomTools:
    def test_returns_function_calling_format(self):
        @action(name="tool1", description="desc1", parameters={"x": {"type": "string"}})
        async def h(**ctx):
            return ""

        tools = get_custom_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "tool1"
        assert tools[0]["function"]["description"] == "desc1"

    def test_empty_registry(self):
        assert get_custom_tools() == []


# ── is_custom_action ─────────────────────────────────────────────────────────


class TestIsCustomAction:
    def test_registered(self):
        @action(name="exists", description="x")
        async def h(**ctx):
            return ""
        assert is_custom_action("exists") is True

    def test_not_registered(self):
        assert is_custom_action("nonexistent") is False


# ── execute_custom_action ────────────────────────────────────────────────────


class TestExecuteCustomAction:
    @pytest.fixture
    def _register_echo(self):
        @action(
            name="echo",
            description="Echo back",
            parameters={"msg": {"type": "string", "description": "message", "required": True}},
        )
        async def echo(msg: str, **ctx) -> str:
            return f"echo: {msg}"

    @pytest.mark.asyncio
    async def test_basic_execution(self, _register_echo):
        result = await execute_custom_action("echo", {"msg": "hello"})
        assert result == "echo: hello"

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await execute_custom_action("no_such_action", {})
        assert "未知" in result

    @pytest.mark.asyncio
    async def test_missing_required_param(self, _register_echo):
        result = await execute_custom_action("echo", {})
        assert "缺少必需参数" in result

    @pytest.mark.asyncio
    async def test_type_conversion_int(self):
        @action(
            name="int_test",
            description="test",
            parameters={"count": {"type": "integer", "description": "n", "required": True}},
        )
        async def h(count: int, **ctx):
            return str(count * 2)

        result = await execute_custom_action("int_test", {"count": "5"})
        assert result == "10"

    @pytest.mark.asyncio
    async def test_type_conversion_bool(self):
        @action(
            name="bool_test",
            description="test",
            parameters={"flag": {"type": "boolean", "description": "f"}},
        )
        async def h(flag: bool = False, **ctx):
            return str(flag)

        result = await execute_custom_action("bool_test", {"flag": "true"})
        assert result == "True"

    @pytest.mark.asyncio
    async def test_domain_filter_blocks(self):
        @action(
            name="restricted",
            description="test",
            allowed_domains=["example.com"],
        )
        async def h(**ctx):
            return "ok"

        class FakePage:
            url = "https://other-site.com/page"

        result = await execute_custom_action("restricted", {}, page=FakePage())
        assert "仅允许" in result

    @pytest.mark.asyncio
    async def test_domain_filter_allows(self):
        @action(
            name="allowed",
            description="test",
            allowed_domains=["example.com"],
        )
        async def h(**ctx):
            return "ok"

        class FakePage:
            url = "https://sub.example.com/page"

        result = await execute_custom_action("allowed", {}, page=FakePage())
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        @action(name="fail_action", description="test")
        async def h(**ctx):
            raise RuntimeError("boom")

        result = await execute_custom_action("fail_action", {})
        assert "执行异常" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_none_return_becomes_string(self):
        @action(name="none_ret", description="test")
        async def h(**ctx):
            return None

        result = await execute_custom_action("none_ret", {})
        assert result == "操作完成"


# ── unregister / clear ───────────────────────────────────────────────────────


class TestUnregister:
    def test_unregister_existing(self):
        @action(name="to_remove", description="x")
        async def h(**ctx):
            return ""
        assert unregister("to_remove") is True
        assert is_custom_action("to_remove") is False

    def test_unregister_nonexistent(self):
        assert unregister("nope") is False
