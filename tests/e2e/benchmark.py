"""
评测基准系统 — 运行 E2E 场景，生成基线报告，支持历史对比和回归检测。

用法：
  python -m tests.e2e.benchmark                    # 运行全部场景
  python -m tests.e2e.benchmark --category search  # 只跑搜索类
  python -m tests.e2e.benchmark --difficulty basic  # 只跑基础难度
  python -m tests.e2e.benchmark --compare           # 与上次基线对比
  python -m tests.e2e.benchmark --tags real_site    # 只跑真实网站
"""

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .e2e_runner import E2ERunner, E2EResult
from .scenarios import SCENARIOS, BASIC_SCENARIOS, REAL_SCENARIOS


BENCHMARK_DIR = Path("benchmarks")
LATEST_BASELINE = BENCHMARK_DIR / "latest.json"


@dataclass
class RegressionItem:
    """一个回归项。"""
    scenario: str
    change_type: str   # "regression" | "improvement" | "new_failure" | "fixed"
    detail: str = ""
    baseline_value: str = ""
    current_value: str = ""


@dataclass
class BenchmarkReport:
    """评测报告，含基线对比。"""
    timestamp: str = ""
    git_commit: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    success_rate: float = 0.0
    avg_duration: float = 0.0
    avg_steps: float = 0.0
    total_cost_usd: float = 0.0
    by_category: dict = field(default_factory=dict)
    by_difficulty: dict = field(default_factory=dict)
    results: list[dict] = field(default_factory=dict)
    regressions: list[dict] = field(default_factory=list)


def _get_git_commit() -> str:
    """获取当前 git commit hash。"""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def compare_with_baseline(
    current_results: list[E2EResult],
    baseline: dict,
) -> list[RegressionItem]:
    """
    将当前结果与基线对比，找出回归项。

    回归类型：
    - regression: 之前通过现在失败
    - improvement: 之前失败现在通过
    - new_failure: 新场景失败
    - duration_regression: 耗时翻倍以上
    """
    regressions = []
    baseline_results = {r["scenario_name"]: r for r in baseline.get("results", [])}

    for result in current_results:
        name = result.scenario_name
        prev = baseline_results.get(name)

        if prev is None:
            # 新场景
            if not result.success:
                regressions.append(RegressionItem(
                    scenario=name,
                    change_type="new_failure",
                    detail=f"新场景失败: {result.error or '未知'}",
                    current_value=f"failed ({result.error or ''})",
                ))
            continue

        # 成功/失败变化
        if prev["success"] and not result.success:
            regressions.append(RegressionItem(
                scenario=name,
                change_type="regression",
                detail=f"之前通过，现在失败: {result.error or '未知'}",
                baseline_value="passed",
                current_value=f"failed ({result.error or ''})",
            ))
        elif not prev["success"] and result.success:
            regressions.append(RegressionItem(
                scenario=name,
                change_type="improvement",
                detail="之前失败，现在通过",
                baseline_value=f"failed ({prev.get('error', '')})",
                current_value="passed",
            ))

        # 耗时回归（翻倍以上）
        prev_dur = prev.get("duration_seconds", 0)
        if prev_dur > 0 and result.duration_seconds > prev_dur * 2 and result.duration_seconds > 10:
            regressions.append(RegressionItem(
                scenario=name,
                change_type="duration_regression",
                detail=f"耗时从 {prev_dur}s 增加到 {result.duration_seconds}s",
                baseline_value=f"{prev_dur}s",
                current_value=f"{result.duration_seconds}s",
            ))

    return regressions


def generate_benchmark_report(
    results: list[E2EResult],
    baseline: dict | None = None,
) -> dict:
    """生成完整的评测报告。"""
    e2e_report = E2ERunner.generate_report(results)

    # 计算总成本
    total_cost = 0.0
    for r in results:
        cost = r.token_usage
        if isinstance(cost, dict):
            total_cost += cost.get("total_cost_usd", 0)

    report = {
        "timestamp": datetime.now().isoformat(),
        "git_commit": _get_git_commit(),
        **e2e_report["summary"],
        "total_cost_usd": round(total_cost, 4),
        "by_category": e2e_report.get("by_category", {}),
        "by_difficulty": e2e_report.get("by_difficulty", {}),
        "results": e2e_report["results"],
        "regressions": [],
    }

    # 基线对比
    if baseline:
        regressions = compare_with_baseline(results, baseline)
        report["regressions"] = [asdict(r) for r in regressions]
        report["baseline_commit"] = baseline.get("git_commit", "unknown")
        report["baseline_timestamp"] = baseline.get("timestamp", "")

    return report


def print_benchmark_report(report: dict):
    """打印评测报告。"""
    print(f"\n{'='*70}")
    print(f"  评测基准报告  |  {report.get('timestamp', '')[:19]}  |  commit: {report.get('git_commit', '?')}")
    print(f"{'='*70}")

    s_rate = report.get("success_rate", 0) * 100
    print(f"\n  总计: {report['total']}  通过: {report['passed']}  失败: {report['failed']}  成功率: {s_rate:.0f}%")
    print(f"  平均耗时: {report.get('avg_duration_seconds', 0)}s  平均步数: {report.get('avg_steps', 0)}")
    if report.get("total_cost_usd"):
        print(f"  总成本: ${report['total_cost_usd']:.4f}")

    # 分类热力图
    by_cat = report.get("by_category", {})
    if by_cat:
        print(f"\n  {'分类':<16} {'通过率':>8}  {'详情':>10}")
        print(f"  {'-'*40}")
        for cat, stats in sorted(by_cat.items()):
            rate = stats["passed"] / stats["total"] * 100 if stats["total"] else 0
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            print(f"  {cat:<16} {rate:>6.0f}%  {bar} {stats['passed']}/{stats['total']}")

    # 难度热力图
    by_diff = report.get("by_difficulty", {})
    if by_diff:
        print(f"\n  {'难度':<16} {'通过率':>8}  {'详情':>10}")
        print(f"  {'-'*40}")
        for diff in ["basic", "intermediate", "advanced"]:
            stats = by_diff.get(diff, {"total": 0, "passed": 0})
            if stats["total"] == 0:
                continue
            rate = stats["passed"] / stats["total"] * 100
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            print(f"  {diff:<16} {rate:>6.0f}%  {bar} {stats['passed']}/{stats['total']}")

    # 回归项
    regressions = report.get("regressions", [])
    if regressions:
        print(f"\n  {'⚠ 回归检测':}")
        print(f"  {'-'*40}")
        for r in regressions:
            icon = {"regression": "🔴", "improvement": "🟢", "new_failure": "🟡", "duration_regression": "🟠"}.get(r["change_type"], "⚪")
            print(f"  {icon} [{r['change_type']}] {r['scenario']}: {r['detail']}")

        reg_count = sum(1 for r in regressions if r["change_type"] in ("regression", "new_failure"))
        imp_count = sum(1 for r in regressions if r["change_type"] == "improvement")
        if reg_count:
            print(f"\n  🔴 {reg_count} 个回归")
        if imp_count:
            print(f"  🟢 {imp_count} 个改进")
    elif report.get("baseline_commit"):
        print(f"\n  ✅ 无回归（对比基线 {report['baseline_commit']}）")

    # 失败详情
    failed = [r for r in report.get("results", []) if not r["success"]]
    if failed:
        print(f"\n  失败场景详情:")
        print(f"  {'-'*40}")
        for r in failed:
            print(f"  ❌ {r['scenario_name']}: {r.get('error', '未知')}")

    print(f"\n{'='*70}\n")


def save_baseline(report: dict):
    """保存报告为基线。"""
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    # 保存为 latest
    LATEST_BASELINE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 同时保存带时间戳的历史版本
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    commit = report.get("git_commit", "unknown")
    history_path = BENCHMARK_DIR / f"benchmark_{ts}_{commit}.json"
    history_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return str(history_path)


def load_baseline() -> dict | None:
    """加载最新基线。"""
    if LATEST_BASELINE.exists():
        try:
            return json.loads(LATEST_BASELINE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_baselines() -> list[dict]:
    """列出所有历史基线。"""
    if not BENCHMARK_DIR.exists():
        return []
    baselines = []
    for f in sorted(BENCHMARK_DIR.glob("benchmark_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            baselines.append({
                "file": f.name,
                "timestamp": data.get("timestamp", ""),
                "git_commit": data.get("git_commit", ""),
                "success_rate": data.get("success_rate", 0),
                "total": data.get("total", 0),
                "passed": data.get("passed", 0),
            })
        except Exception:
            pass
    return baselines


async def run_benchmark(
    scenarios: list | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    tags: list[str] | None = None,
    headless: bool = True,
    compare: bool = True,
    save: bool = True,
) -> dict:
    """
    运行评测基准。

    Args:
        scenarios: 自定义场景列表（默认全部）
        category: 只跑指定分类
        difficulty: 只跑指定难度
        tags: 只跑包含指定标签的场景
        headless: 无头模式
        compare: 是否与基线对比
        save: 是否保存为新基线

    Returns:
        评测报告 dict
    """
    all_scenarios = scenarios or SCENARIOS

    # 过滤
    if category:
        all_scenarios = [s for s in all_scenarios if s.category == category]
    if difficulty:
        all_scenarios = [s for s in all_scenarios if s.difficulty == difficulty]
    if tags:
        all_scenarios = [s for s in all_scenarios if any(t in s.tags for t in tags)]

    if not all_scenarios:
        print("没有匹配的场景")
        return {}

    runner = E2ERunner(headless=headless)
    results = await runner.run_all(all_scenarios)

    # 加载基线
    baseline = load_baseline() if compare else None

    # 生成报告
    report = generate_benchmark_report(results, baseline)
    print_benchmark_report(report)

    # 保存
    if save:
        path = save_baseline(report)
        print(f"  基线已保存: {path}")

    return report


# ── CLI 入口 ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent 评测基准")
    parser.add_argument("--category", type=str, help="只跑指定分类")
    parser.add_argument("--difficulty", type=str, help="只跑指定难度")
    parser.add_argument("--tags", type=str, nargs="+", help="只跑包含指定标签的场景")
    parser.add_argument("--no-compare", action="store_true", help="不与基线对比")
    parser.add_argument("--no-save", action="store_true", help="不保存为新基线")
    parser.add_argument("--headful", action="store_true", help="有头模式（可视化）")
    parser.add_argument("--basic-only", action="store_true", help="只跑基础场景")
    parser.add_argument("--real-only", action="store_true", help="只跑真实网站场景")
    parser.add_argument("--list-baselines", action="store_true", help="列出历史基线")
    args = parser.parse_args()

    if args.list_baselines:
        baselines = list_baselines()
        if not baselines:
            print("没有历史基线")
        else:
            print(f"\n历史基线 ({len(baselines)} 个):")
            for b in baselines:
                rate = b["success_rate"] * 100
                print(f"  {b['file']}  commit={b['git_commit']}  {b['passed']}/{b['total']} ({rate:.0f}%)")
        return

    scenarios = None
    if args.basic_only:
        scenarios = BASIC_SCENARIOS
    elif args.real_only:
        scenarios = REAL_SCENARIOS

    asyncio.run(run_benchmark(
        scenarios=scenarios,
        category=args.category,
        difficulty=args.difficulty,
        tags=args.tags,
        headless=not args.headful,
        compare=not args.no_compare,
        save=not args.no_save,
    ))


if __name__ == "__main__":
    main()
