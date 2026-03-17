"""
Tests for workflow/context.py — WorkflowContext parameter and template resolution.
"""

import pytest
from jinja2.exceptions import UndefinedError

from workflow.context import WorkflowContext


def _ctx(**params):
    return WorkflowContext(workflow_id="wf1", run_id="run1", parameters=params)


class TestOutputStorage:
    def test_set_and_get(self):
        ctx = _ctx()
        ctx.set_output("step1", {"data": 42})
        assert ctx.get_output("step1") == {"data": 42}

    def test_get_missing_returns_none(self):
        ctx = _ctx()
        assert ctx.get_output("nonexistent") is None

    def test_get_all_outputs(self):
        ctx = _ctx()
        ctx.set_output("a", 1)
        ctx.set_output("b", 2)
        outputs = ctx.get_all_outputs()
        assert outputs == {"a_output": 1, "b_output": 2}


class TestResolve:
    def test_simple_template(self):
        ctx = _ctx(url="https://example.com")
        assert ctx.resolve("{{ url }}") == "https://example.com"

    def test_no_template_passthrough(self):
        ctx = _ctx()
        assert ctx.resolve("plain text") == "plain text"

    def test_non_string_passthrough(self):
        ctx = _ctx()
        assert ctx.resolve(42) == 42

    def test_output_reference(self):
        ctx = _ctx()
        ctx.set_output("step1", "hello")
        assert ctx.resolve("{{ step1_output }}") == "hello"

    def test_mixed_template(self):
        ctx = _ctx(base="https://api.com")
        assert ctx.resolve("{{ base }}/users") == "https://api.com/users"

    def test_undefined_variable_raises(self):
        ctx = _ctx()
        with pytest.raises(UndefinedError):
            ctx.resolve("{{ nonexistent }}")


class TestResolveExpression:
    def test_boolean_expression(self):
        ctx = _ctx(price=150)
        result = ctx.resolve_expression("{{ price > 100 }}")
        assert result is True

    def test_list_expression(self):
        ctx = _ctx(items=[1, 2, 3])
        result = ctx.resolve_expression("{{ items }}")
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_non_expression_string(self):
        ctx = _ctx(name="world")
        result = ctx.resolve_expression("hello {{ name }}")
        assert result == "hello world"

    def test_non_string_passthrough(self):
        ctx = _ctx()
        assert ctx.resolve_expression(42) == 42

    def test_output_expression(self):
        ctx = _ctx()
        ctx.set_output("fetch", {"items": [1, 2]})
        result = ctx.resolve_expression("{{ fetch_output }}")
        assert result == {"items": [1, 2]}


class TestResolveDeep:
    def test_nested_dict(self):
        ctx = _ctx(host="api.com", token="abc")
        obj = {"url": "https://{{ host }}/v1", "headers": {"Authorization": "Bearer {{ token }}"}}
        result = ctx.resolve_deep(obj)
        assert result["url"] == "https://api.com/v1"
        assert result["headers"]["Authorization"] == "Bearer abc"

    def test_nested_list(self):
        ctx = _ctx(a="x", b="y")
        result = ctx.resolve_deep(["{{ a }}", "{{ b }}"])
        assert result == ["x", "y"]

    def test_non_string_values_preserved(self):
        ctx = _ctx()
        result = ctx.resolve_deep({"count": 5, "flag": True})
        assert result == {"count": 5, "flag": True}


class TestLoopVariables:
    def test_current_value_and_index(self):
        ctx = _ctx()
        ctx.current_value = "item_a"
        ctx.current_index = 3
        assert ctx.resolve("{{ current_value }}") == "item_a"
        assert ctx.resolve("{{ current_index }}") == "3"
