"""
E2E 框架单元测试 — 测试 E2ERunner / E2EScenario / E2EResult 的逻辑，不需要真实网络。
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from tests.e2e.e2e_runner import E2EScenario, E2EResult, E2ERunner
from tests.e2e.scenarios import BASIC_SCENARIOS, REAL_SCENARIOS, SCENARIOS


# ── E2EScenario 数据结构测试 ──────────────────────────────────────────

class TestE2EScenario:
    def test_default_fields(self):
        s = E2EScenario(name="test", task="do something")
        assert s.max_steps == 15
        assert s.timeout_seconds == 180
        assert s.category == "general"
        assert s.difficulty == "basic"
        assert s.tags == []
        assert s.expected_result == ""
        assert s.success_check is None

    def test_custom_fields(self):
        check_fn = lambda r: r.get("success")
        s = E2EScenario(
            name="custom",
            task="custom task",
            max_steps=20,
            timeout_seconds=300,
            category="search",
            difficulty="advanced",
            tags=["real_site", "needs_network"],
            expected_result="search results",
            success_check=check_fn,
        )
        assert s.category == "search"
        assert s.difficulty == "advanced"
        assert "real_site" in s.tags
        assert s.success_check({"success": True})
        assert not s.success_check({"success": False})


# ── E2EResult 数据结构测试 ──────────────────────────────────────────

class TestE2EResult:
    def test_default_fields(self):
        r = E2EResult(scenario_name="test")
        assert r.success is False
        assert r.steps == 0
        assert r.duration_seconds == 0.0
        assert r.token_usage == {}
        assert r.error == ""
        assert r.category == ""
        assert r.difficulty == ""
        assert r.logs == []

    def test_logs_collection(self):
        r = E2EResult(scenario_name="test", logs=["log1", "log2"])
        assert len(r.logs) == 2


# ── 场景定义完整性测试 ──────────────────────────────────────────

class TestScenarioDefinitions:
    def test_basic_scenarios_count(self):
        assert len(BASIC_SCENARIOS) >= 9

    def test_real_scenarios_count(self):
        assert len(REAL_SCENARIOS) >= 15

    def test_all_scenarios_combined(self):
        assert len(SCENARIOS) == len(BASIC_SCENARIOS) + len(REAL_SCENARIOS)

    def test_unique_names(self):
        names = [s.name for s in SCENARIOS]
        assert len(names) == len(set(names)), f"重复场景名: {[n for n in names if names.count(n) > 1]}"

    def test_all_have_category(self):
        for s in SCENARIOS:
            assert s.category, f"场景 {s.name} 缺少 category"

    def test_all_have_difficulty(self):
        for s in SCENARIOS:
            assert s.difficulty in ("basic", "intermediate", "advanced"), \
                f"场景 {s.name} 的 difficulty 无效: {s.difficulty}"

    def test_basic_scenarios_tagged(self):
        for s in BASIC_SCENARIOS:
            assert "mock_site" in s.tags, f"基础场景 {s.name} 应有 mock_site 标签"

    def test_real_scenarios_tagged(self):
        for s in REAL_SCENARIOS:
            assert "real_site" in s.tags, f"真实场景 {s.name} 应有 real_site 标签"

    def test_categories_coverage(self):
        categories = {s.category for s in SCENARIOS}
        expected = {"navigation", "search", "form", "extract", "multi_step", "login"}
        assert expected.issubset(categories), f"缺少分类: {expected - categories}"

    def test_difficulty_coverage(self):
        difficulties = {s.difficulty for s in SCENARIOS}
        assert "basic" in difficulties
        assert "intermediate" in difficulties
        assert "advanced" in difficulties

    def test_max_steps_reasonable(self):
        for s in SCENARIOS:
            assert 3 <= s.max_steps <= 50, f"场景 {s.name} 的 max_steps={s.max_steps} 不合理"

    def test_timeout_reasonable(self):
        for s in SCENARIOS:
            assert 30 <= s.timeout_seconds <= 600, f"场景 {s.name} 的 timeout={s.timeout_seconds} 不合理"


# ── E2ERunner 逻辑测试 ──────────────────────────────────────────

class TestE2ERunner:
    def test_init(self):
        runner = E2ERunner(screenshots_base="test_screenshots", headless=False)
        assert runner.screenshots_base == Path("test_screenshots")
        assert runner.headless is False

    def test_init_defaults(self):
        runner = E2ERunner()
        assert runner.screenshots_base == Path("screenshots/e2e")
        assert runner.headless is True


class TestE2ERunnerScenarioExecution:
    """测试 run_scenario 的各种情况（mock run_agent）。"""

    @pytest.mark.asyncio
    async def test_successful_scenario(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        mock_result = {"success": True, "steps": 3, "cost": {"total_cost_usd": 0.01}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            scenario = E2EScenario(name="test_ok", task="test task", timeout_seconds=10)
            result = await runner.run_scenario(scenario)

        assert result.success is True
        assert result.steps == 3
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_failed_scenario(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        mock_result = {"success": False, "steps": 5, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            scenario = E2EScenario(name="test_fail", task="test task", timeout_seconds=10)
            result = await runner.run_scenario(scenario)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_timeout_scenario(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        async def slow_agent(**kwargs):
            await asyncio.sleep(100)
            return {"success": True}

        with patch("agent.runner.run_agent", side_effect=slow_agent):
            scenario = E2EScenario(name="test_timeout", task="slow task", timeout_seconds=1)
            result = await runner.run_scenario(scenario)

        assert result.success is False
        assert "超时" in result.error

    @pytest.mark.asyncio
    async def test_exception_scenario(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        with patch("agent.runner.run_agent", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            scenario = E2EScenario(name="test_error", task="error task", timeout_seconds=10)
            result = await runner.run_scenario(scenario)

        assert result.success is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_custom_success_check_override(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        # Agent 说成功了，但自定义检查不通过
        mock_result = {"success": True, "steps": 2, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            scenario = E2EScenario(
                name="test_check",
                task="test",
                timeout_seconds=10,
                success_check=lambda r: False,  # 总是失败
            )
            result = await runner.run_scenario(scenario)

        assert result.success is False
        assert "自定义成功检查未通过" in result.error

    @pytest.mark.asyncio
    async def test_logs_collected(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        async def mock_agent(**kwargs):
            cb = kwargs.get("log_callback")
            if cb:
                await cb("e2e_test_logs", "step 1")
                await cb("e2e_test_logs", "step 2")
            return {"success": True, "steps": 2, "cost": {}}

        with patch("agent.runner.run_agent", side_effect=mock_agent):
            scenario = E2EScenario(name="test_logs", task="test", timeout_seconds=10)
            result = await runner.run_scenario(scenario)

        assert len(result.logs) == 2
        assert "step 1" in result.logs

    @pytest.mark.asyncio
    async def test_category_and_difficulty_in_result(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        mock_result = {"success": True, "steps": 1, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            scenario = E2EScenario(
                name="test_meta",
                task="test",
                timeout_seconds=10,
                category="search",
                difficulty="advanced",
            )
            result = await runner.run_scenario(scenario)

        assert result.category == "search"
        assert result.difficulty == "advanced"


class TestE2ERunnerFiltering:
    """测试按分类/难度/标签过滤运行。"""

    @pytest.mark.asyncio
    async def test_run_by_category(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        scenarios = [
            E2EScenario(name="s1", task="t1", category="search", timeout_seconds=5),
            E2EScenario(name="s2", task="t2", category="form", timeout_seconds=5),
            E2EScenario(name="s3", task="t3", category="search", timeout_seconds=5),
        ]

        mock_result = {"success": True, "steps": 1, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            results = await runner.run_by_category(scenarios, "search")

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_run_by_difficulty(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        scenarios = [
            E2EScenario(name="s1", task="t1", difficulty="basic", timeout_seconds=5),
            E2EScenario(name="s2", task="t2", difficulty="advanced", timeout_seconds=5),
        ]

        mock_result = {"success": True, "steps": 1, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            results = await runner.run_by_difficulty(scenarios, "advanced")

        assert len(results) == 1
        assert results[0].scenario_name == "s2"

    @pytest.mark.asyncio
    async def test_run_by_tags(self, tmp_path):
        runner = E2ERunner(screenshots_base=str(tmp_path / "screenshots"))

        scenarios = [
            E2EScenario(name="s1", task="t1", tags=["real_site"], timeout_seconds=5),
            E2EScenario(name="s2", task="t2", tags=["mock_site"], timeout_seconds=5),
            E2EScenario(name="s3", task="t3", tags=["real_site", "needs_network"], timeout_seconds=5),
        ]

        mock_result = {"success": True, "steps": 1, "cost": {}}
        with patch("agent.runner.run_agent", new_callable=AsyncMock, return_value=mock_result):
            results = await runner.run_by_tags(scenarios, ["real_site"])

        assert len(results) == 2


# ── 报告生成测试 ──────────────────────────────────────────

class TestE2EReport:
    def _make_results(self):
        return [
            E2EResult(scenario_name="s1", success=True, steps=3, duration_seconds=10.0,
                       category="search", difficulty="basic"),
            E2EResult(scenario_name="s2", success=False, steps=5, duration_seconds=20.0,
                       error="timeout", category="search", difficulty="intermediate"),
            E2EResult(scenario_name="s3", success=True, steps=2, duration_seconds=5.0,
                       category="form", difficulty="basic"),
        ]

    def test_generate_report_summary(self):
        results = self._make_results()
        report = E2ERunner.generate_report(results)

        assert report["summary"]["total"] == 3
        assert report["summary"]["passed"] == 2
        assert report["summary"]["failed"] == 1
        assert report["summary"]["success_rate"] == 0.67

    def test_generate_report_by_category(self):
        results = self._make_results()
        report = E2ERunner.generate_report(results)

        assert "search" in report["by_category"]
        assert report["by_category"]["search"]["total"] == 2
        assert report["by_category"]["search"]["passed"] == 1
        assert report["by_category"]["form"]["total"] == 1
        assert report["by_category"]["form"]["passed"] == 1

    def test_generate_report_by_difficulty(self):
        results = self._make_results()
        report = E2ERunner.generate_report(results)

        assert report["by_difficulty"]["basic"]["total"] == 2
        assert report["by_difficulty"]["basic"]["passed"] == 2
        assert report["by_difficulty"]["intermediate"]["total"] == 1
        assert report["by_difficulty"]["intermediate"]["passed"] == 0

    def test_generate_report_empty(self):
        report = E2ERunner.generate_report([])
        assert report["summary"]["total"] == 0
        assert report["summary"]["success_rate"] == 0

    def test_save_report(self, tmp_path):
        results = self._make_results()
        report = E2ERunner.generate_report(results)
        path = str(tmp_path / "report.json")
        E2ERunner.save_report(report, path)

        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        assert loaded["summary"]["total"] == 3

    def test_print_report_no_crash(self, capsys):
        results = self._make_results()
        report = E2ERunner.generate_report(results)
        E2ERunner.print_report(report)
        captured = capsys.readouterr()
        assert "E2E 测试报告" in captured.out
        assert "67%" in captured.out
        assert "按分类" in captured.out
        assert "按难度" in captured.out
