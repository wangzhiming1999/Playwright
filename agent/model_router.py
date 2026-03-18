"""
智能模型路由：根据步骤复杂度自动选择模型。

策略：
- DOM 模式 + 简单操作 → mini 模型（gpt-4o-mini / haiku），成本降 80%
- 截图模式 / 复杂操作 → 默认模型（gpt-4o / sonnet），保证质量
- 连续失败时自动升级到默认模型
- mini 历史成功率 < 60% 时全部升级到 default

简单操作判定：
- 上一步成功 + 非首步 + DOM 模式 + 无验证码/弹窗
"""

import os


def is_claude_model(model: str = "") -> bool:
    """判断当前使用的是否是 Claude 模型。"""
    if model and model.startswith("claude"):
        return True
    env_model = os.getenv("LLM_MODEL", "")
    if env_model.startswith("claude"):
        return True
    return os.getenv("LLM_BACKEND", "").lower() == "anthropic"


def get_claude_prompt_hints() -> str:
    """
    Claude 特有的 prompt 优化提示。
    Claude 在以下方面表现更好：
    - 结构化输出（JSON 格式更稳定）
    - 长上下文理解
    - 指令遵循
    """
    return (
        "## 输出格式要求\n"
        "- 严格按照工具定义调用，不要在 content 中输出 JSON\n"
        "- 每次只调用必要的工具，避免冗余操作\n"
        "- 如果需要多步操作，可以一次返回多个 tool_calls\n"
    )


_HARD_TASK_KEYWORDS = ["登录", "login", "购买", "checkout", "支付", "payment", "注册", "signup", "oauth", "认证"]
_EASY_TASK_KEYWORDS = ["截图", "screenshot", "打开", "navigate", "go to", "访问", "提取", "extract", "下载", "download"]


def estimate_task_difficulty(task: str) -> str:
    """
    根据任务关键词预估难度。
    返回: "hard" / "easy" / "medium"
    """
    lower = task.lower()
    if any(kw in lower for kw in _HARD_TASK_KEYWORDS):
        return "hard"
    if any(kw in lower for kw in _EASY_TASK_KEYWORDS):
        return "easy"
    return "medium"


class ModelRouter:
    """
    有状态的模型路由器，维护 mini 模型的历史成功率。
    当 mini 成功率 < 60%（≥5 次采样后）时，全部升级到 default。
    """

    def __init__(self):
        self._mini_success = 0
        self._mini_fail = 0
        self._last_tier = "default"

    @property
    def mini_success_rate(self) -> float:
        """mini 模型历史成功率。"""
        total = self._mini_success + self._mini_fail
        if total == 0:
            return 1.0
        return self._mini_success / total

    @property
    def mini_total_calls(self) -> int:
        return self._mini_success + self._mini_fail

    def select(
        self,
        use_screenshot: bool,
        step: int,
        last_tool: str | None,
        last_failed: bool,
        consecutive_failures: int,
        has_captcha: bool = False,
        has_dialog: bool = False,
    ) -> str:
        """
        返回模型 tier: "default" 或 "mini"。
        传给 llm_chat(model=tier) 即可。
        """
        # 截图模式：需要视觉理解，用默认模型
        if use_screenshot:
            self._last_tier = "default"
            return "default"

        # 首步：需要理解任务 + 页面，用默认模型
        if step == 0:
            self._last_tier = "default"
            return "default"

        # 验证码/弹窗：需要复杂判断
        if has_captcha or has_dialog:
            self._last_tier = "default"
            return "default"

        # 连续失败 >= 2：升级模型避免死循环
        if consecutive_failures >= 2:
            self._last_tier = "default"
            return "default"

        # 上一步失败：用默认模型分析问题
        if last_failed:
            self._last_tier = "default"
            return "default"

        # 复杂工具操作后：需要理解结果
        if last_tool in ("find_element", "get_page_html", "solve_captcha", "extract"):
            self._last_tier = "default"
            return "default"

        # mini 历史成功率 < 60%（≥5 次采样后）→ 全部升级 default
        if self.mini_total_calls >= 5 and self.mini_success_rate < 0.6:
            self._last_tier = "default"
            return "default"

        # 其余情况：DOM 模式 + 简单操作 → mini 模型
        self._last_tier = "mini"
        return "mini"

    def record_result(self, tier: str, success: bool):
        """记录模型调用结果，用于动态调整路由策略。"""
        if tier == "mini":
            if success:
                self._mini_success += 1
            else:
                self._mini_fail += 1

    def stats(self) -> dict:
        """返回路由统计信息。"""
        return {
            "mini_success": self._mini_success,
            "mini_fail": self._mini_fail,
            "mini_rate": round(self.mini_success_rate, 2),
            "last_tier": self._last_tier,
        }


# ── 向后兼容：保留原函数签名 ──────────────────────────────────────
_default_router = ModelRouter()


def select_model_tier(
    use_screenshot: bool,
    step: int,
    last_tool: str | None,
    last_failed: bool,
    consecutive_failures: int,
    has_captcha: bool = False,
    has_dialog: bool = False,
) -> str:
    """向后兼容的纯函数接口，内部使用默认 ModelRouter 实例。"""
    return _default_router.select(
        use_screenshot=use_screenshot,
        step=step,
        last_tool=last_tool,
        last_failed=last_failed,
        consecutive_failures=consecutive_failures,
        has_captcha=has_captcha,
        has_dialog=has_dialog,
    )
