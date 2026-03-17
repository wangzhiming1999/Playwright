"""
E2E 测试运行器。

调用 run_agent() 执行真实场景，收集成功率、耗时、token 消耗等指标。
"""

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable


@dataclass
class E2EScenario:
    """一个 E2E 测试场景。"""
    name: str
    task: str
    max_steps: int = 15
    timeout_seconds: int = 180
    success_check: Callable[[dict], bool] | None = None  # 自定义成功判断


@dataclass
class E2EResult:
    """一个 E2E 测试的结果。"""
    scenario_name: str
    success: bool = False
    steps: int = 0
    duration_seconds: float = 0.0
    token_usage: dict = field(default_factory=dict)
    error: str = ""


class E2ERunner:
    """E2E 测试运行器，顺序执行场景并生成报告。"""

    def __init__(self, screenshots_base: str = "screenshots/e2e", headless: bool = True):
        self.screenshots_base = Path(screenshots_base)
        self.headless = headless

    async def run_scenario(self, scenario: E2EScenario) -> E2EResult:
        """执行单个场景。"""
        from agent.runner import run_agent

        result = E2EResult(scenario_name=scenario.name)
        screenshots_dir = self.screenshots_base / scenario.name

        # 清理上次的截图
        if screenshots_dir.exists():
            shutil.rmtree(screenshots_dir, ignore_errors=True)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        try:
            agent_result = await asyncio.wait_for(
                run_agent(
                    task=scenario.task,
                    headless=self.headless,
                    task_id=f"e2e_{scenario.name}",
                    screenshots_dir=str(screenshots_dir),
                ),
                timeout=scenario.timeout_seconds,
            )
            result.success = agent_result.get("success", False)
            result.steps = agent_result.get("steps", 0)
            result.token_usage = agent_result.get("cost", {})

            # 自定义成功判断
            if scenario.success_check and not scenario.success_check(agent_result):
                result.success = False
                result.error = "自定义成功检查未通过"

        except asyncio.TimeoutError:
            result.error = f"超时 ({scenario.timeout_seconds}s)"
        except Exception as e:
            result.error = str(e)[:500]

        result.duration_seconds = round(time.monotonic() - start, 1)
        return result

    async def run_all(self, scenarios: list[E2EScenario]) -> list[E2EResult]:
        """顺序执行所有场景。"""
        results = []
        for i, scenario in enumerate(scenarios, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(scenarios)}] 运行场景: {scenario.name}")
            print(f"  任务: {scenario.task}")
            print(f"{'='*60}")

            result = await self.run_scenario(scenario)
            results.append(result)

            status = "✅ 成功" if result.success else f"❌ 失败: {result.error or '未知'}"
            print(f"  结果: {status} ({result.duration_seconds}s, {result.steps} 步)")

        return results

    @staticmethod
    def generate_report(results: list[E2EResult]) -> dict:
        """生成 JSON 报告。"""
        total = len(results)
        passed = sum(1 for r in results if r.success)
        failed = total - passed
        avg_duration = sum(r.duration_seconds for r in results) / total if total else 0
        avg_steps = sum(r.steps for r in results) / total if total else 0

        report = {
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "success_rate": round(passed / total, 2) if total else 0,
                "avg_duration_seconds": round(avg_duration, 1),
                "avg_steps": round(avg_steps, 1),
            },
            "results": [asdict(r) for r in results],
        }
        return report

    @staticmethod
    def print_report(report: dict):
        """打印报告摘要。"""
        s = report["summary"]
        print(f"\n{'='*60}")
        print(f"E2E 测试报告")
        print(f"{'='*60}")
        print(f"  总计: {s['total']}  通过: {s['passed']}  失败: {s['failed']}")
        print(f"  成功率: {s['success_rate']*100:.0f}%")
        print(f"  平均耗时: {s['avg_duration_seconds']}s  平均步数: {s['avg_steps']}")
        print()
        for r in report["results"]:
            icon = "✅" if r["success"] else "❌"
            err = f" — {r['error']}" if r["error"] else ""
            print(f"  {icon} {r['scenario_name']}: {r['duration_seconds']}s, {r['steps']} 步{err}")
        print()
