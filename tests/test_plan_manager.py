"""
Tests for agent/plan_manager.py — PlanManager pure state management.
"""

from agent.plan_manager import PlanManager, PlanStep


class TestInit:
    def test_empty_init(self):
        pm = PlanManager()
        assert pm.has_plan is False
        assert pm.total_steps == 0
        assert pm.completed_count == 0

    def test_init_with_steps(self):
        steps = [
            {"step": 1, "action": "open page", "done_signal": "page loaded", "expected": "homepage"},
            {"step": 2, "action": "click login", "done_signal": "login form", "expected": "form visible"},
        ]
        pm = PlanManager(steps)
        assert pm.has_plan is True
        assert pm.total_steps == 2
        assert pm.completed_count == 0

    def test_first_step_is_current(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        current = pm.get_current_step()
        assert current is not None
        assert current.status == "current"
        assert current.index == 1


class TestProcessLlmContent:
    def test_no_plan_update_returns_false(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        assert pm.process_llm_content("just some text") is False

    def test_none_content_returns_false(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        assert pm.process_llm_content(None) is False

    def test_completed_marks_step_done(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "click"},
        ])
        content = '[PLAN_UPDATE]{"completed": [1]}[/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is True
        assert pm._steps[0].status == "done"
        assert pm.completed_count == 1

    def test_skip_marks_step_skipped(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "click"},
        ])
        content = '[PLAN_UPDATE]{"skip": [2]}[/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is True
        assert pm._steps[1].status == "skipped"

    def test_current_sets_new_current(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "click"},
        ])
        content = '[PLAN_UPDATE]{"current": 2}[/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is True
        assert pm._steps[0].status == "done"  # old current auto-done
        assert pm._steps[1].status == "current"

    def test_add_after_inserts_new_steps(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "done"},
        ])
        content = '[PLAN_UPDATE]{"add_after": 1, "new_steps": ["scroll down", "find element"]}[/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is True
        assert pm.total_steps == 4
        assert pm._steps[1].action == "scroll down"
        assert pm._steps[2].action == "find element"

    def test_auto_advance_to_next_pending(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "click"},
        ])
        # Complete step 1, no explicit current set
        content = '[PLAN_UPDATE]{"completed": [1]}[/PLAN_UPDATE]'
        pm.process_llm_content(content)
        # Step 1 was current, now done; step 2 should auto-advance
        assert pm._steps[1].status == "current"

    def test_malformed_json_returns_false(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        content = '[PLAN_UPDATE]not valid json[/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is False

    def test_non_dict_update_returns_false(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        content = '[PLAN_UPDATE][1, 2, 3][/PLAN_UPDATE]'
        assert pm.process_llm_content(content) is False

    def test_note_is_recorded(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        content = '[PLAN_UPDATE]{"note": "page was slow"}[/PLAN_UPDATE]'
        pm.process_llm_content(content)
        assert pm._last_note == "page was slow"

    def test_resets_stall_counter_on_change(self):
        pm = PlanManager([{"step": 1, "action": "open"}, {"step": 2, "action": "click"}])
        pm._steps_since_progress = 5
        content = '[PLAN_UPDATE]{"completed": [1]}[/PLAN_UPDATE]'
        pm.process_llm_content(content)
        assert pm._steps_since_progress == 0


class TestFormatHint:
    def test_empty_plan_returns_empty(self):
        pm = PlanManager()
        assert pm.format_hint() == ""

    def test_contains_status_icons(self):
        pm = PlanManager([
            {"step": 1, "action": "open page"},
            {"step": 2, "action": "click button"},
        ])
        hint = pm.format_hint()
        assert "👉" in hint  # current
        assert "⏳" in hint  # pending
        assert "任务计划" in hint

    def test_shows_completed_count(self):
        pm = PlanManager([
            {"step": 1, "action": "open"},
            {"step": 2, "action": "click"},
        ])
        pm.process_llm_content('[PLAN_UPDATE]{"completed": [1]}[/PLAN_UPDATE]')
        hint = pm.format_hint()
        assert "1/2" in hint


class TestCheckStall:
    def test_no_stall_below_threshold(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        for _ in range(3):
            result = pm.check_stall(0)
        assert result is None

    def test_soft_nudge_at_4(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        result = None
        for i in range(4):
            result = pm.check_stall(i)
        assert result is not None
        assert "提示" in result

    def test_replan_nudge_at_7(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        result = None
        for i in range(7):
            result = pm.check_stall(i)
        assert result is not None
        assert "没有进展" in result

    def test_force_replan_at_10(self):
        pm = PlanManager([{"step": 1, "action": "open"}])
        result = None
        for i in range(10):
            result = pm.check_stall(i)
        assert result is not None
        assert "必须立即更新计划" in result

    def test_no_stall_without_plan(self):
        pm = PlanManager()
        assert pm.check_stall(0) is None


class TestToLogDict:
    def test_returns_serializable_dict(self):
        pm = PlanManager([{"step": 1, "action": "open page with a very long description that exceeds thirty chars"}])
        d = pm.to_log_dict()
        assert "steps" in d
        assert "completed" in d
        assert "total" in d
        assert d["total"] == 1
        # action should be truncated to 30 chars
        assert len(d["steps"][0]["action"]) <= 30
