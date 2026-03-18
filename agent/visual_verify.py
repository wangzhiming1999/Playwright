"""
视觉验证模块 — Action 执行后自动检测操作是否生效。

核心机制：
1. 执行前快照页面指纹（URL + DOM hash + 滚动位置 + 输入框值）
2. 执行后对比指纹，判断页面是否发生预期变化
3. 对于 click/type_text 等关键操作，无变化时自动提示 LLM 重试
"""

import hashlib
from dataclasses import dataclass, field


@dataclass
class PageSnapshot:
    """页面状态快照。"""
    url: str = ""
    title: str = ""
    body_text_len: int = 0
    child_count: int = 0
    scroll_y: int = 0
    focused_tag: str = ""       # 当前聚焦元素的 tagName
    focused_value: str = ""     # 当前聚焦元素的 value（输入框）
    visible_text_hash: str = "" # 可见文本前 2000 字符的 hash

    def fingerprint(self) -> str:
        """生成指纹字符串用于快速比较。"""
        return f"{self.url}|{self.title}|{self.body_text_len}|{self.child_count}|{self.scroll_y}"


@dataclass
class VerifyResult:
    """验证结果。"""
    changed: bool = True          # 页面是否发生变化
    change_type: str = ""         # 变化类型：url/content/scroll/input/none
    details: str = ""             # 变化详情
    should_retry: bool = False    # 是否建议重试
    nudge: str = ""               # 注入给 LLM 的提示


# 哪些工具执行后预期页面会变化
EXPECTS_CHANGE = {
    "click": "content",       # 点击后预期内容或 URL 变化
    "type_text": "input",     # 输入后预期输入框值变化
    "navigate": "url",        # 导航后预期 URL 变化
    "select_option": "input", # 选择后预期值变化
    "press_key": "content",   # 按键后可能内容变化
    "scroll": "scroll",       # 滚动后预期滚动位置变化
    "set_date": "input",      # 日期设置后预期值变化
}

# 不需要验证的工具
SKIP_VERIFY = {
    "screenshot", "get_page_html", "extract", "done", "ask_user", "wait",
    "get_credentials", "get_totp_code", "analyze_current_page",
    "find_element", "solve_captcha",
}


async def take_snapshot(page) -> PageSnapshot:
    """捕获当前页面状态快照。"""
    snap = PageSnapshot()
    try:
        snap.url = page.url or ""
    except Exception:
        pass

    try:
        data = await page.evaluate("""() => {
            const body = document.body;
            const active = document.activeElement;
            const text = (body.innerText || '').substring(0, 2000);
            return {
                title: document.title || '',
                bodyTextLen: (body.innerText || '').length,
                childCount: body.children.length,
                scrollY: window.scrollY || 0,
                focusedTag: active ? active.tagName : '',
                focusedValue: (active && active.value !== undefined) ? String(active.value) : '',
                visibleText: text,
            };
        }""")
        snap.title = data.get("title", "")
        snap.body_text_len = data.get("bodyTextLen", 0)
        snap.child_count = data.get("childCount", 0)
        snap.scroll_y = data.get("scrollY", 0)
        snap.focused_tag = data.get("focusedTag", "")
        snap.focused_value = data.get("focusedValue", "")
        visible = data.get("visibleText", "")
        snap.visible_text_hash = hashlib.md5(visible.encode("utf-8", errors="ignore")).hexdigest()
    except Exception:
        pass

    return snap


def verify_action(
    tool_name: str,
    tool_args: dict,
    before: PageSnapshot,
    after: PageSnapshot,
    action_result: str,
) -> VerifyResult:
    """
    对比 action 前后的页面快照，判断操作是否生效。

    返回 VerifyResult，包含是否变化、变化类型、是否建议重试。
    """
    result = VerifyResult()

    # 跳过不需要验证的工具
    if tool_name in SKIP_VERIFY:
        result.changed = True
        result.change_type = "skip"
        return result

    # action 本身已经报错了，不需要再验证
    if action_result.startswith("操作失败") or action_result.startswith("AI操作失败"):
        result.changed = False
        result.change_type = "error"
        result.details = "action 本身已报错"
        return result

    # URL 变化
    url_changed = before.url != after.url
    if url_changed:
        result.changed = True
        result.change_type = "url"
        result.details = f"{before.url} → {after.url}"
        return result

    # 内容变化（文本 hash 或长度或子元素数）
    content_changed = (
        before.visible_text_hash != after.visible_text_hash
        or abs(before.body_text_len - after.body_text_len) > 10
        or abs(before.child_count - after.child_count) > 2
    )

    # 滚动变化
    scroll_changed = abs(before.scroll_y - after.scroll_y) > 50

    # 输入框值变化
    input_changed = before.focused_value != after.focused_value

    # 标题变化
    title_changed = before.title != after.title

    # 综合判断
    any_change = url_changed or content_changed or scroll_changed or input_changed or title_changed

    if any_change:
        result.changed = True
        changes = []
        if content_changed:
            changes.append("content")
        if scroll_changed:
            changes.append("scroll")
        if input_changed:
            changes.append("input")
        if title_changed:
            changes.append("title")
        result.change_type = "+".join(changes)
        return result

    # 没有任何变化 — 根据工具类型判断是否需要重试
    expected = EXPECTS_CHANGE.get(tool_name)
    if not expected:
        # 不在预期变化列表中的工具，不变化也正常
        result.changed = True
        result.change_type = "none_expected"
        return result

    # 预期有变化但没变化 — 操作可能失效
    result.changed = False
    result.change_type = "none"
    result.should_retry = True

    if tool_name == "click":
        text = tool_args.get("text", "")
        index = tool_args.get("index", "?")
        result.details = f"点击 index={index} text='{text}' 后页面无变化"
        result.nudge = (
            f"⚠️ 操作验证：点击后页面没有发生任何变化，操作可能未生效。"
            f"建议：1) 确认 index 是否正确 2) 尝试滚动到元素可见区域后重试 "
            f"3) 用 find_element 重新定位 4) 检查是否有遮挡弹窗"
        )
    elif tool_name == "type_text":
        result.details = "输入后输入框值未变化"
        result.nudge = (
            "⚠️ 操作验证：输入后输入框的值没有变化，输入可能未生效。"
            "建议：1) 先点击输入框获取焦点 2) 确认 index 指向的是正确的输入框 "
            "3) 尝试先清空再输入"
        )
    elif tool_name == "scroll":
        result.details = "滚动后页面位置未变化"
        result.nudge = "⚠️ 操作验证：滚动后页面位置没有变化，可能已到达页面边界。"
        result.should_retry = False  # 滚动到底不需要重试
    elif tool_name == "select_option":
        result.details = "选择后值未变化"
        result.nudge = (
            "⚠️ 操作验证：选择操作后值没有变化。"
            "建议：确认 index 指向的是 select 元素，value 值是否正确。"
        )
    elif tool_name == "set_date":
        result.details = "日期设置后值未变化"
        result.nudge = (
            "⚠️ 操作验证：日期设置后输入框值没有变化。"
            "建议：确认 index 指向的是日期输入框，日期格式是否为 YYYY-MM-DD。"
        )
    else:
        result.details = f"{tool_name} 执行后页面无变化"
        result.nudge = f"⚠️ 操作验证：{tool_name} 执行后页面没有发生预期变化，请检查操作是否正确。"

    return result


class ActionVerifier:
    """
    Action 验证器 — 跟踪连续无效操作，递进提醒。

    连续 2 次无效：温和提醒
    连续 4 次无效：强烈建议换策略
    连续 6 次无效：建议放弃当前子目标
    """

    def __init__(self):
        self._consecutive_no_change = 0
        self._total_no_change = 0

    def record(self, verify_result: VerifyResult) -> str | None:
        """
        记录验证结果，返回递进提醒（如果需要）。
        """
        if verify_result.changed or not verify_result.should_retry:
            self._consecutive_no_change = 0
            return None

        self._consecutive_no_change += 1
        self._total_no_change += 1

        if self._consecutive_no_change >= 6:
            self._consecutive_no_change = 0  # 重置，避免无限累积
            return (
                "🚨 连续 6 次操作未生效，当前方法可能不适用。"
                "建议：1) 换一个完全不同的策略 2) 用 get_page_html 分析页面结构 "
                "3) 如果子目标无法完成，跳过它继续下一步"
            )
        elif self._consecutive_no_change >= 4:
            return (
                "⚠️ 连续 4 次操作未生效，请换一种方式。"
                "尝试：用 find_element 视觉定位、用 get_page_html 查看 HTML、"
                "或者滚动页面让目标元素进入视口。"
            )
        elif self._consecutive_no_change >= 2:
            return "💡 连续 2 次操作未生效，建议检查元素是否可见、是否被遮挡。"

        return None

    @property
    def stats(self) -> dict:
        return {
            "consecutive_no_change": self._consecutive_no_change,
            "total_no_change": self._total_no_change,
        }

    def reset(self):
        self._consecutive_no_change = 0
        self._total_no_change = 0
