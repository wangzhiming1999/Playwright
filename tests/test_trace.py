"""
Agent 可观测性追踪模块单元测试 — 测试 StepTrace / TaskTrace / TraceCollector。
"""

import json
import pytest
from pathlib import Path

from agent.trace import StepTrace, TaskTrace, TraceCollector


# ── StepTrace 测试 ──────────────────────────────────────────

class TestStepTrace:
    def test_default_fields(self):
        s = StepTrace()
        assert s.step == 0
        assert s.tool_name == ""
        assert s.verify_changed is True
        assert s.nudges == []
        assert s.events == []

    def test_custom_fields(self):
        s = StepTrace(step=3, tool_name="click", tool_args={"index": 5},
                       result="ok", input_mode="screenshot", elements_count=42)
        assert s.step == 3
        assert s.tool_name == "click"
        assert s.tool_args["index"] == 5
        assert s.elements_count == 42


# ── TaskTrace 测试 ──────────────────────────────────────────

class TestTaskTrace:
    def _trace(self):
        t = TaskTrace(task_id="t1", task="test task", started_at=1000.0, finished_at=1030.0,
                       success=True, reason="done", total_steps=3, total_cost_usd=0.05)
        t.steps = [
            StepTrace(step=0, tool_name="navigate", result="ok"),
            StepTrace(step=1, tool_name="click", result="ok"),
            StepTrace(step=2, tool_name="click", result="操作失败: 元素不存在",
                       result_is_error=True, verify_changed=False, verify_type="none"),
        ]
        return t

    def test_duration(self):
        t = self._trace()
        assert t.duration_seconds == 30.0

    def test_duration_no_finish(self):
        t = TaskTrace(started_at=1000.0)
        assert t.duration_seconds == 0.0

    def test_tool_usage(self):
        t = self._trace()
        usage = t.tool_usage
        assert usage["click"] == 2
        assert usage["navigate"] == 1

    def test_error_steps(self):
        t = self._trace()
        errors = t.error_steps
        assert len(errors) == 1
        assert errors[0].step == 2

    def test_verify_failures(self):
        t = self._trace()
        failures = t.verify_failures
        assert len(failures) == 1
        assert failures[0].step == 2

    def test_summary(self):
        t = self._trace()
        s = t.summary()
        assert s["task_id"] == "t1"
        assert s["success"] is True
        assert s["total_steps"] == 3
        assert s["error_count"] == 1
        assert s["verify_failure_count"] == 1
        assert s["tool_usage"]["click"] == 2

    def test_empty_trace(self):
        t = TaskTrace()
        assert t.duration_seconds == 0.0
        assert t.tool_usage == {}
        assert t.error_steps == []
        assert t.verify_failures == []


# ── TraceCollector 测试 ──────────────────────────────────────

class TestTraceCollector:
    def test_init(self):
        tc = TraceCollector(task_id="t1", task="test")
        assert tc.trace.task_id == "t1"
        assert tc.trace.task == "test"
        assert tc.trace.started_at > 0

    def test_begin_end_step(self):
        tc = TraceCollector()
        tc.begin_step(0, page_url="https://a.com", page_title="Page A")
        tc.set_input_mode("screenshot", elements_count=10)
        tc.set_tool_call("click", {"index": 3}, action_count=1)
        tc.set_result("ok", is_error=False, duration_ms=150.0)
        tc.set_verify(True, "content")
        tc.end_step()

        assert len(tc.trace.steps) == 1
        step = tc.trace.steps[0]
        assert step.step == 0
        assert step.page_url == "https://a.com"
        assert step.input_mode == "screenshot"
        assert step.elements_count == 10
        assert step.tool_name == "click"
        assert step.tool_args == {"index": 3}
        assert step.result == "ok"
        assert step.verify_changed is True
        assert step.duration_ms == 150.0

    def test_multiple_steps(self):
        tc = TraceCollector()
        for i in range(5):
            tc.begin_step(i)
            tc.set_tool_call(f"tool_{i}", {})
            tc.set_result("ok")
            tc.end_step()
        assert len(tc.trace.steps) == 5

    def test_set_page_change(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.set_page_change("https://a.com", "https://b.com", True)
        tc.end_step()
        assert tc.trace.steps[0].url_before == "https://a.com"
        assert tc.trace.steps[0].url_after == "https://b.com"
        assert tc.trace.steps[0].page_changed is True

    def test_set_llm_usage(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.set_llm_usage(input_tokens=500, output_tokens=100, cached_tokens=200,
                          cost_usd=0.01, model="gpt-4o")
        tc.end_step()
        step = tc.trace.steps[0]
        assert step.input_tokens == 500
        assert step.output_tokens == 100
        assert step.cached_tokens == 200
        assert step.cost_usd == 0.01
        assert step.model == "gpt-4o"

    def test_add_nudge(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.add_nudge("循环检测提醒")
        tc.add_nudge("停滞检测提醒")
        tc.end_step()
        assert len(tc.trace.steps[0].nudges) == 2

    def test_add_event(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.add_event("CAPTCHA_DETECTED")
        tc.end_step()
        assert "CAPTCHA_DETECTED" in tc.trace.steps[0].events

    def test_finish(self):
        tc = TraceCollector(task_id="t1", task="test")
        tc.begin_step(0)
        tc.set_tool_call("done", {"summary": "完成"})
        tc.end_step()
        tc.finish(success=True, reason="完成", total_steps=1, total_cost_usd=0.02)

        assert tc.trace.success is True
        assert tc.trace.reason == "完成"
        assert tc.trace.total_steps == 1
        assert tc.trace.total_cost_usd == 0.02
        assert tc.trace.finished_at >= tc.trace.started_at

    def test_no_step_operations_safe(self):
        """没有 begin_step 时调用 set_* 不崩溃。"""
        tc = TraceCollector()
        tc.set_input_mode("dom")
        tc.set_tool_call("click", {})
        tc.set_result("ok")
        tc.set_verify(True)
        tc.set_page_change("a", "b", True)
        tc.set_llm_usage(100, 50)
        tc.add_nudge("test")
        tc.add_event("test")
        tc.end_step()
        assert len(tc.trace.steps) == 0  # 没有 begin_step，不会有步骤

    def test_result_truncated(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.set_result("x" * 1000)
        tc.end_step()
        assert len(tc.trace.steps[0].result) == 500

    def test_multi_action(self):
        tc = TraceCollector()
        tc.begin_step(0)
        tc.set_tool_call("click", {"index": 1}, action_count=3)
        tc.end_step()
        assert tc.trace.steps[0].is_multi_action is True
        assert tc.trace.steps[0].action_count == 3


# ── 序列化测试 ──────────────────────────────────────────

class TestTraceSerialization:
    def _collector(self):
        tc = TraceCollector(task_id="t1", task="test")
        tc.begin_step(0, page_url="https://a.com")
        tc.set_tool_call("click", {"index": 1})
        tc.set_result("ok")
        tc.end_step()
        tc.finish(success=True, reason="done", total_steps=1)
        return tc

    def test_to_dict(self):
        tc = self._collector()
        d = tc.to_dict()
        assert d["task_id"] == "t1"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["tool_name"] == "click"

    def test_to_json(self):
        tc = self._collector()
        j = tc.to_json()
        data = json.loads(j)
        assert data["task_id"] == "t1"

    def test_save_and_load(self, tmp_path):
        tc = self._collector()
        path = tmp_path / "trace.json"
        tc.save(path)

        loaded = TraceCollector.load(path)
        assert loaded.task_id == "t1"
        assert loaded.success is True
        assert len(loaded.steps) == 1
        assert loaded.steps[0].tool_name == "click"

    def test_save_creates_dirs(self, tmp_path):
        tc = self._collector()
        path = tmp_path / "sub" / "dir" / "trace.json"
        tc.save(path)
        assert path.exists()

    def test_json_roundtrip(self):
        tc = self._collector()
        j = tc.to_json()
        data = json.loads(j)
        # 验证所有字段都可序列化
        assert isinstance(data, dict)
        assert isinstance(data["steps"], list)
        assert isinstance(data["steps"][0]["tool_args"], dict)
