"""
动态计划管理器：跟踪任务步骤状态，支持 LLM 动态更新计划。

LLM 通过 msg.content 中的 [PLAN_UPDATE]...[/PLAN_UPDATE] 块报告计划变更，
PlanManager 解析并维护每步状态（pending/current/done/skipped）。
停滞时递进提醒，第 7 步调用 _verify_step() 验证。
"""

import json
import re
from dataclasses import dataclass, field

from .page_utils import _safe_print


@dataclass
class PlanStep:
    index: int          # 1-based 步骤号
    action: str         # 要做什么
    done_signal: str    # 完成标志
    expected: str       # 预期页面状态
    status: str = "pending"  # pending | current | done | skipped


_PLAN_UPDATE_RE = re.compile(
    r'\[PLAN_UPDATE\]\s*(.*?)\s*\[/PLAN_UPDATE\]',
    re.DOTALL
)

# 停滞提醒阈值
_SOFT_NUDGE = 4
_REPLAN_NUDGE = 7
_FORCE_REPLAN = 10


class PlanManager:
    """
    封装任务计划的全部状态和逻辑。
    主循环只需调用 3 个方法：process_llm_content / format_hint / check_stall。
    """

    def __init__(self, task_steps: list[dict] | None = None):
        self._steps: list[PlanStep] = []
        self._steps_since_progress = 0
        self._next_index = 1  # 下一个可用的步骤号
        self._last_note = ""

        if task_steps:
            for s in task_steps:
                step = PlanStep(
                    index=s.get("step", self._next_index),
                    action=s.get("action", ""),
                    done_signal=s.get("done_signal", ""),
                    expected=s.get("expected", ""),
                    status="pending",
                )
                self._steps.append(step)
                self._next_index = max(self._next_index, step.index) + 1

            # 第一步设为 current
            if self._steps:
                self._steps[0].status = "current"

    @property
    def has_plan(self) -> bool:
        return len(self._steps) > 0

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self._steps if s.status == "done")

    def get_current_step(self) -> PlanStep | None:
        for s in self._steps:
            if s.status == "current":
                return s
        return None

    def _find_step(self, index: int) -> PlanStep | None:
        for s in self._steps:
            if s.index == index:
                return s
        return None

    def process_llm_content(self, content: str | None) -> bool:
        """
        从 LLM 的 msg.content 中提取 [PLAN_UPDATE] 块并应用变更。
        返回 True 表示计划有变化。
        """
        if not content:
            return False

        match = _PLAN_UPDATE_RE.search(content)
        if not match:
            return False

        try:
            update = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError) as e:
            _safe_print(f"  [计划更新] JSON 解析失败: {e}")
            return False

        if not isinstance(update, dict):
            return False

        changed = False

        # 记录备注
        note = update.get("note", "")
        if note:
            self._last_note = note

        # 标记完成
        for idx in update.get("completed", []):
            step = self._find_step(idx)
            if step and step.status != "done":
                step.status = "done"
                changed = True

        # 标记跳过
        for idx in update.get("skip", []):
            step = self._find_step(idx)
            if step and step.status in ("pending", "current"):
                step.status = "skipped"
                changed = True

        # 设置当前步骤
        new_current = update.get("current")
        if new_current is not None:
            # 把之前的 current 自动标记为 done（如果还没标记）
            for s in self._steps:
                if s.status == "current" and s.index != new_current:
                    s.status = "done"
                    changed = True
            target = self._find_step(new_current)
            if target and target.status != "current":
                target.status = "current"
                changed = True

        # 插入新步骤
        add_after = update.get("add_after")
        new_steps = update.get("new_steps", [])
        if add_after is not None and new_steps:
            # 找到插入位置
            insert_pos = len(self._steps)  # 默认追加到末尾
            for i, s in enumerate(self._steps):
                if s.index == add_after:
                    insert_pos = i + 1
                    break

            for j, action_text in enumerate(new_steps):
                new_step = PlanStep(
                    index=self._next_index,
                    action=action_text,
                    done_signal="",
                    expected="",
                    status="pending",
                )
                self._steps.insert(insert_pos + j, new_step)
                self._next_index += 1
                changed = True

        # 如果没有 current 步骤了，自动推进到下一个 pending
        if not self.get_current_step():
            for s in self._steps:
                if s.status == "pending":
                    s.status = "current"
                    changed = True
                    break

        if changed:
            self._steps_since_progress = 0

        return changed

    def format_hint(self) -> str:
        """渲染带状态标记的计划文本，注入到 LLM 上下文。"""
        if not self._steps:
            return ""

        _STATUS_ICONS = {
            "done": "✅",
            "current": "👉",
            "pending": "⏳",
            "skipped": "⏭️",
        }

        lines = ["【任务计划】"]
        for s in self._steps:
            icon = _STATUS_ICONS.get(s.status, "  ")
            suffix = ""
            if s.status == "current":
                suffix = "  ← 当前"
            elif s.status == "skipped":
                suffix = " [已跳过]"
            signal = f"（完成标志：{s.done_signal}）" if s.done_signal else ""
            lines.append(f"  {icon} {s.index}. {s.action}{signal}{suffix}")

        current = self.get_current_step()
        if current:
            lines.append(f"当前在步骤 {current.index}，已完成 {self.completed_count}/{self.total_steps} 步。")
        else:
            lines.append(f"已完成 {self.completed_count}/{self.total_steps} 步。")

        lines.append(
            "如果当前步骤已完成或需要调整计划，在回复文本中加入 [PLAN_UPDATE]...[/PLAN_UPDATE] 块。\n"
        )
        return "\n".join(lines) + "\n"

    def check_stall(self, iteration: int) -> str | None:
        """
        每步调用，检测计划停滞。
        返回提醒消息或 None。
        """
        if not self._steps:
            return None

        self._steps_since_progress += 1
        current = self.get_current_step()

        if self._steps_since_progress >= _FORCE_REPLAN:
            step_desc = f"步骤 {current.index}（{current.action}）" if current else "当前步骤"
            return (
                f"⚠️ 已在{step_desc}停滞 {self._steps_since_progress} 步，你必须立即更新计划。"
                "请在回复文本中加入 [PLAN_UPDATE] 块：跳过当前步骤、修改计划、或添加中间步骤。"
                "不能继续重复同样的操作。"
            )

        if self._steps_since_progress >= _REPLAN_NUDGE:
            step_desc = f"步骤 {current.index}（{current.action}）" if current else "当前步骤"
            return (
                f"⚠️ 已在{step_desc}停滞 {self._steps_since_progress} 步，没有进展。"
                "请考虑：1) 用 [PLAN_UPDATE] 跳过此步骤 2) 添加中间步骤分解难点 3) 换一种方式完成。"
            )

        if self._steps_since_progress >= _SOFT_NUDGE:
            step_desc = f"步骤 {current.index}" if current else "当前步骤"
            return (
                f"提示：你已在{step_desc}执行了 {self._steps_since_progress} 步操作。"
                "如果已完成，请用 [PLAN_UPDATE] 报告进度。"
            )

        return None

    def to_log_dict(self) -> dict:
        """返回可序列化的计划状态，用于日志。"""
        return {
            "steps": [
                {"index": s.index, "action": s.action[:30], "status": s.status}
                for s in self._steps
            ],
            "completed": self.completed_count,
            "total": self.total_steps,
            "stall": self._steps_since_progress,
            "note": self._last_note,
        }
