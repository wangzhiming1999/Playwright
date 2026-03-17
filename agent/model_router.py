"""
智能模型路由：根据步骤复杂度自动选择模型。

策略：
- DOM 模式 + 简单操作 → mini 模型（gpt-4o-mini / haiku），成本降 80%
- 截图模式 / 复杂操作 → 默认模型（gpt-4o / sonnet），保证质量
- 连续失败时自动升级到默认模型

简单操作判定：
- 上一步成功 + 非首步 + DOM 模式 + 无验证码/弹窗
"""


def select_model_tier(
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
        return "default"

    # 首步：需要理解任务 + 页面，用默认模型
    if step == 0:
        return "default"

    # 验证码/弹窗：需要复杂判断
    if has_captcha or has_dialog:
        return "default"

    # 连续失败 >= 2：升级模型避免死循环
    if consecutive_failures >= 2:
        return "default"

    # 上一步失败：用默认模型分析问题
    if last_failed:
        return "default"

    # 复杂工具操作后：需要理解结果
    if last_tool in ("find_element", "get_page_html", "solve_captcha", "extract"):
        return "default"

    # 其余情况：DOM 模式 + 简单操作 → mini 模型
    return "mini"
