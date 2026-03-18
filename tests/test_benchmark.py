"""
评测基准系统单元测试 — 测试基线对比、报告生成、回归检测逻辑。
"""

import json
import pytest
from pathlib import Path
from dataclasses import asdict

from tests.e2e.e2e_runner import E2EResult
from tests.e2e.benchmark import (
    compare_with_baseline, generate_benchmark_report,
    save_baseline, load_baseline, list_baselines,
    print_benchmark_report, RegressionItem,
    BENCHMARK_DIR, LATEST_BASELINE,
)


# ── 回归检测测试 ──────────────────────────────────────────

class TestCompareWithBaseline:
    def _result(self, name, success=True, duration=10.0, error="", steps=3):
        return E2EResult(
            scenario_name=name, success=success,
            duration_seconds=duration, error=error, steps=steps,
        )

    def test_no_regression_identical(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
        ]}
        current = [self._result("s1", success=True, duration=10.0)]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 0

    def test_regression_pass_to_fail(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
        ]}
        current = [self._result("s1", success=False, error="timeout")]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 1
        assert regs[0].change_type == "regression"
        assert "timeout" in regs[0].detail

    def test_improvement_fail_to_pass(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": False, "error": "old error", "duration_seconds": 10.0},
        ]}
        current = [self._result("s1", success=True)]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 1
        assert regs[0].change_type == "improvement"

    def test_new_failure(self):
        baseline = {"results": []}
        current = [self._result("new_scenario", success=False, error="boom")]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 1
        assert regs[0].change_type == "new_failure"

    def test_new_success_no_regression(self):
        baseline = {"results": []}
        current = [self._result("new_scenario", success=True)]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 0

    def test_duration_regression(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
        ]}
        current = [self._result("s1", success=True, duration=25.0)]
        regs = compare_with_baseline(current, baseline)
        assert any(r.change_type == "duration_regression" for r in regs)

    def test_duration_small_increase_ok(self):
        """耗时增加不到 2 倍不算回归。"""
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
        ]}
        current = [self._result("s1", success=True, duration=15.0)]
        regs = compare_with_baseline(current, baseline)
        assert not any(r.change_type == "duration_regression" for r in regs)

    def test_duration_regression_ignores_short(self):
        """短耗时（<10s）翻倍不算回归。"""
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 3.0},
        ]}
        current = [self._result("s1", success=True, duration=8.0)]
        regs = compare_with_baseline(current, baseline)
        assert not any(r.change_type == "duration_regression" for r in regs)

    def test_multiple_regressions(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
            {"scenario_name": "s2", "success": True, "duration_seconds": 5.0},
            {"scenario_name": "s3", "success": False, "error": "old", "duration_seconds": 20.0},
        ]}
        current = [
            self._result("s1", success=False, error="fail"),  # regression
            self._result("s2", success=True, duration=5.0),   # no change
            self._result("s3", success=True),                  # improvement
        ]
        regs = compare_with_baseline(current, baseline)
        types = {r.change_type for r in regs}
        assert "regression" in types
        assert "improvement" in types
        assert len(regs) == 2

    def test_empty_baseline(self):
        baseline = {}
        current = [self._result("s1", success=True)]
        regs = compare_with_baseline(current, baseline)
        assert len(regs) == 0

    def test_empty_current(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
        ]}
        regs = compare_with_baseline([], baseline)
        assert len(regs) == 0


# ── 报告生成测试 ──────────────────────────────────────────

class TestGenerateBenchmarkReport:
    def _results(self):
        return [
            E2EResult(scenario_name="s1", success=True, steps=3, duration_seconds=10.0,
                       category="search", difficulty="basic",
                       token_usage={"total_cost_usd": 0.01}),
            E2EResult(scenario_name="s2", success=False, steps=5, duration_seconds=20.0,
                       error="timeout", category="form", difficulty="intermediate",
                       token_usage={"total_cost_usd": 0.02}),
            E2EResult(scenario_name="s3", success=True, steps=2, duration_seconds=5.0,
                       category="search", difficulty="basic",
                       token_usage={"total_cost_usd": 0.005}),
        ]

    def test_basic_fields(self):
        report = generate_benchmark_report(self._results())
        assert report["total"] == 3
        assert report["passed"] == 2
        assert report["failed"] == 1
        assert report["success_rate"] == 0.67
        assert "timestamp" in report
        assert "git_commit" in report

    def test_total_cost(self):
        report = generate_benchmark_report(self._results())
        assert report["total_cost_usd"] == pytest.approx(0.035, abs=0.001)

    def test_by_category(self):
        report = generate_benchmark_report(self._results())
        assert report["by_category"]["search"]["passed"] == 2
        assert report["by_category"]["form"]["passed"] == 0

    def test_by_difficulty(self):
        report = generate_benchmark_report(self._results())
        assert report["by_difficulty"]["basic"]["passed"] == 2
        assert report["by_difficulty"]["intermediate"]["passed"] == 0

    def test_with_baseline_comparison(self):
        baseline = {"results": [
            {"scenario_name": "s1", "success": True, "duration_seconds": 10.0},
            {"scenario_name": "s2", "success": True, "duration_seconds": 10.0},  # was passing
        ]}
        report = generate_benchmark_report(self._results(), baseline)
        assert len(report["regressions"]) >= 1
        assert "baseline_commit" in report

    def test_without_baseline(self):
        report = generate_benchmark_report(self._results())
        assert report["regressions"] == []

    def test_empty_results(self):
        report = generate_benchmark_report([])
        assert report["total"] == 0
        assert report["success_rate"] == 0


# ── 基线存储测试 ──────────────────────────────────────────

class TestBaselineStorage:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tests.e2e.benchmark.BENCHMARK_DIR", tmp_path)
        monkeypatch.setattr("tests.e2e.benchmark.LATEST_BASELINE", tmp_path / "latest.json")

        report = {"total": 5, "passed": 4, "success_rate": 0.8, "git_commit": "abc123", "timestamp": "2026-01-01"}
        save_baseline(report)

        loaded = load_baseline()
        assert loaded is not None
        assert loaded["total"] == 5
        assert loaded["git_commit"] == "abc123"

    def test_load_no_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tests.e2e.benchmark.LATEST_BASELINE", tmp_path / "nonexistent.json")
        assert load_baseline() is None

    def test_history_saved(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tests.e2e.benchmark.BENCHMARK_DIR", tmp_path)
        monkeypatch.setattr("tests.e2e.benchmark.LATEST_BASELINE", tmp_path / "latest.json")

        report = {"total": 3, "passed": 2, "success_rate": 0.67, "git_commit": "def456", "timestamp": "2026-01-01"}
        save_baseline(report)

        history = list(tmp_path.glob("benchmark_*.json"))
        assert len(history) == 1
        assert "def456" in history[0].name

    def test_list_baselines(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tests.e2e.benchmark.BENCHMARK_DIR", tmp_path)
        monkeypatch.setattr("tests.e2e.benchmark.LATEST_BASELINE", tmp_path / "latest.json")

        for i in range(3):
            report = {"total": 10, "passed": 8 + i, "success_rate": (8 + i) / 10,
                       "git_commit": f"commit{i}", "timestamp": f"2026-01-0{i+1}"}
            (tmp_path / f"benchmark_2026010{i}_000000_commit{i}.json").write_text(
                json.dumps(report), encoding="utf-8"
            )

        baselines = list_baselines()
        assert len(baselines) == 3
        assert all("git_commit" in b for b in baselines)

    def test_list_baselines_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tests.e2e.benchmark.BENCHMARK_DIR", tmp_path)
        assert list_baselines() == []


# ── 报告打印测试 ──────────────────────────────────────────

class TestPrintReport:
    def test_print_no_crash(self, capsys):
        report = {
            "timestamp": "2026-03-18T12:00:00",
            "git_commit": "abc123",
            "total": 3, "passed": 2, "failed": 1,
            "success_rate": 0.67,
            "avg_duration_seconds": 12.0,
            "avg_steps": 4.0,
            "total_cost_usd": 0.05,
            "by_category": {"search": {"total": 2, "passed": 2}, "form": {"total": 1, "passed": 0}},
            "by_difficulty": {"basic": {"total": 2, "passed": 2}, "intermediate": {"total": 1, "passed": 0}},
            "results": [
                {"scenario_name": "s1", "success": True},
                {"scenario_name": "s2", "success": True},
                {"scenario_name": "s3", "success": False, "error": "timeout"},
            ],
            "regressions": [],
        }
        print_benchmark_report(report)
        out = capsys.readouterr().out
        assert "评测基准报告" in out
        assert "abc123" in out
        assert "67%" in out

    def test_print_with_regressions(self, capsys):
        report = {
            "timestamp": "2026-03-18T12:00:00",
            "git_commit": "abc123",
            "total": 2, "passed": 1, "failed": 1,
            "success_rate": 0.5,
            "avg_duration_seconds": 10.0,
            "avg_steps": 3.0,
            "total_cost_usd": 0.0,
            "by_category": {},
            "by_difficulty": {},
            "results": [
                {"scenario_name": "s1", "success": True},
                {"scenario_name": "s2", "success": False, "error": "fail"},
            ],
            "regressions": [
                {"scenario": "s2", "change_type": "regression", "detail": "之前通过现在失败",
                 "baseline_value": "passed", "current_value": "failed"},
            ],
            "baseline_commit": "old123",
        }
        print_benchmark_report(report)
        out = capsys.readouterr().out
        assert "回归检测" in out
        assert "regression" in out

    def test_print_no_regressions_with_baseline(self, capsys):
        report = {
            "timestamp": "2026-03-18T12:00:00",
            "git_commit": "abc123",
            "total": 1, "passed": 1, "failed": 0,
            "success_rate": 1.0,
            "avg_duration_seconds": 5.0,
            "avg_steps": 2.0,
            "total_cost_usd": 0.0,
            "by_category": {},
            "by_difficulty": {},
            "results": [{"scenario_name": "s1", "success": True}],
            "regressions": [],
            "baseline_commit": "old123",
        }
        print_benchmark_report(report)
        out = capsys.readouterr().out
        assert "无回归" in out


# ── RegressionItem 测试 ──────────────────────────────────

class TestRegressionItem:
    def test_fields(self):
        r = RegressionItem(scenario="s1", change_type="regression", detail="broke")
        assert r.scenario == "s1"
        assert r.baseline_value == ""

    def test_asdict(self):
        r = RegressionItem(scenario="s1", change_type="improvement", detail="fixed",
                           baseline_value="failed", current_value="passed")
        d = asdict(r)
        assert d["change_type"] == "improvement"
        assert d["current_value"] == "passed"
