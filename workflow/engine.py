"""
WorkflowEngine — 工作流执行引擎。

职责：
  - 按顺序执行 blocks（支持 next_block_label 跳转）
  - 重试失败的 block（max_retries + continue_on_failure）
  - 持久化运行状态到 SQLite
  - 通过回调推送日志和截图
"""

from __future__ import annotations

import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from .models import BlockDef
from .context import WorkflowContext
from .blocks import execute_block
from .parser import parse_workflow, _parse_blocks
from .db import save_workflow_run, load_workflow_run


class WorkflowEngine:
    def __init__(
        self,
        workflow: dict,
        parameters: dict[str, Any] | None = None,
        page=None,
        agent=None,
        log_callback: Callable[[str, str], Awaitable[None]] | None = None,
        screenshot_callback: Callable[[str, str], Awaitable[None]] | None = None,
        ask_user_callback: Callable | None = None,
    ):
        self.workflow = workflow
        self.run_id = uuid.uuid4().hex[:12]
        self.parameters = parameters or {}
        self.page = page
        self.agent = agent
        self.log_callback = log_callback
        self.screenshot_callback = screenshot_callback
        self.ask_user_callback = ask_user_callback

        # 解析 blocks（可能是 dict 列表，需要转成 BlockDef）
        raw_blocks = workflow.get("blocks", [])
        if raw_blocks and isinstance(raw_blocks[0], dict):
            self.blocks = _parse_blocks(raw_blocks)
        else:
            self.blocks = raw_blocks

        # 运行状态
        self._status = "pending"
        self._block_results: dict[str, Any] = {}
        self._current_block: str | None = None
        self._logs: list[str] = []
        self._error: str | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None

    # ── 公开 API ──────────────────────────────────────────────────────────────

    async def run(self) -> dict:
        """执行整个工作流，返回运行结果。"""
        self._status = "running"
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._persist()

        ctx = WorkflowContext(
            workflow_id=self.workflow.get("id", ""),
            run_id=self.run_id,
            parameters=self.parameters,
            page=self.page,
            agent=self.agent,
            log_callback=self.log_callback,
            screenshot_callback=self.screenshot_callback,
            ask_user_callback=self.ask_user_callback,
        )

        await self._log(f"▶ 工作流开始: {self.workflow.get('title', self.workflow.get('id', ''))}")
        await self._log(f"  参数: {self.parameters}")

        # 构建 label → index 映射，支持 next_block_label 跳转
        label_index = {b.label: i for i, b in enumerate(self.blocks)}

        i = 0
        while i < len(self.blocks):
            block = self.blocks[i]
            self._current_block = block.label
            self._persist()

            await self._log(f"\n── Block [{i+1}/{len(self.blocks)}] {block.label} ({block.block_type}) ──")

            result = None
            last_error = None

            for attempt in range(1 + block.max_retries):
                try:
                    if attempt > 0:
                        await self._log(f"  [重试] 第 {attempt} 次重试")
                    result = await execute_block(block, ctx)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    await self._log(f"  ✗ 执行失败 (attempt {attempt+1}): {e}")
                    if attempt < block.max_retries:
                        await self._log(f"  将在下次重试...")

            if last_error is not None:
                error_msg = f"Block '{block.label}' 失败: {last_error}"
                await self._log(f"  ✗ {error_msg}")

                if block.continue_on_failure:
                    await self._log(f"  ↳ continue_on_failure=true，继续执行")
                    result = {"error": str(last_error)}
                else:
                    self._error = error_msg
                    self._status = "failed"
                    self._finished_at = datetime.now(timezone.utc).isoformat()
                    self._persist()
                    await self._log(f"\n✗ 工作流失败: {error_msg}")
                    return self._build_result()

            # 存储 block 输出
            ctx.set_output(block.label, result)
            self._block_results[block.label] = _safe_serialize(result)
            await self._log(f"  ✓ 完成 → {str(result)[:120]}")

            # 跳转逻辑
            if block.next_block_label:
                next_idx = label_index.get(block.next_block_label)
                if next_idx is not None:
                    await self._log(f"  ↳ 跳转到: {block.next_block_label}")
                    i = next_idx
                    continue
                else:
                    await self._log(f"  ⚠ next_block_label '{block.next_block_label}' 不存在，顺序执行")

            i += 1

        self._status = "completed"
        self._finished_at = datetime.now(timezone.utc).isoformat()
        self._persist()
        await self._log(f"\n✓ 工作流完成")

        return self._build_result()

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _log(self, msg: str):
        self._logs.append(msg)
        if self.log_callback:
            try:
                await self.log_callback(self.run_id, msg)
            except Exception:
                pass

    def _persist(self):
        """持久化当前运行状态到数据库。"""
        try:
            save_workflow_run({
                "id": self.run_id,
                "workflow_id": self.workflow.get("id", ""),
                "status": self._status,
                "parameters": self.parameters,
                "block_results": self._block_results,
                "current_block": self._current_block,
                "logs": self._logs[-200:],  # 只保留最近 200 条
                "error": self._error,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
            })
        except Exception:
            pass  # 持久化失败不影响执行

    def _build_result(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow.get("id", ""),
            "status": self._status,
            "block_results": self._block_results,
            "error": self._error,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
        }


def _safe_serialize(obj: Any) -> Any:
    """确保结果可 JSON 序列化。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    return str(obj)
