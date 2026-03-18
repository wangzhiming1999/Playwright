"""
错误分类与自适应恢复策略。

将失败按类型独立计数，不同类型有不同的恢复策略和容忍阈值。
替代原来的全局 5 次一刀切逻辑。
"""

from enum import Enum


class FailureType(Enum):
    ELEMENT_NOT_FOUND = "element_not_found"
    PAGE_NOT_LOADED = "page_not_loaded"
    LLM_INVALID = "llm_invalid"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


# 每种失败类型的最大容忍次数（+2 补偿渐进衰减）
_MAX_FAILURES = {
    FailureType.ELEMENT_NOT_FOUND: 10,  # 元素找不到可以多试几次（滚动、刷新）
    FailureType.PAGE_NOT_LOADED: 6,     # 页面加载失败
    FailureType.LLM_INVALID: 7,        # LLM 返回无效
    FailureType.NETWORK_ERROR: 5,       # 网络错误容忍度低
    FailureType.UNKNOWN: 7,
}

# 每种失败类型的恢复建议（注入到 GPT 的 tool result 中）
_RECOVERY_HINTS = {
    FailureType.ELEMENT_NOT_FOUND: (
        "元素未找到。建议：1) 用 scroll(direction='down') 滚动页面查找 "
        "2) 用 find_element 通过视觉描述定位 3) 刷新页面重试"
    ),
    FailureType.PAGE_NOT_LOADED: (
        "页面未正确加载。建议：1) 调用 wait(timeout=10) 等待 "
        "2) 用 navigate 重新打开页面"
    ),
    FailureType.LLM_INVALID: "LLM 返回格式无效，请重新调用工具。",
    FailureType.NETWORK_ERROR: "网络错误，稍后重试。",
    FailureType.UNKNOWN: "",
}


def classify_failure(tool_name: str, result: str) -> FailureType:
    """根据工具名和错误信息分类失败类型。"""
    r = result.lower()

    # 元素未找到
    if any(kw in r for kw in ("未找到", "not found", "找不到元素", "no element", "index", "selector")):
        return FailureType.ELEMENT_NOT_FOUND

    # 页面未加载
    if any(kw in r for kw in ("timeout", "超时", "page crashed", "navigation", "net::", "err_")):
        return FailureType.PAGE_NOT_LOADED

    # 网络错误
    if any(kw in r for kw in ("connection", "network", "dns", "ssl", "refused")):
        return FailureType.NETWORK_ERROR

    # LLM 无效返回
    if any(kw in r for kw in ("json", "解析失败", "parse", "invalid")):
        return FailureType.LLM_INVALID

    return FailureType.UNKNOWN


class FailureTracker:
    """
    按类型独立追踪失败次数。
    成功操作会重置对应类型的计数器。
    """

    def __init__(self):
        self._counts: dict[FailureType, int] = {ft: 0 for ft in FailureType}
        self._total_consecutive = 0

    def record_failure(self, tool_name: str, result: str) -> tuple[FailureType, int, str]:
        """
        记录一次失败。
        返回: (失败类型, 该类型累计次数, 恢复建议)
        """
        ft = classify_failure(tool_name, result)
        self._counts[ft] += 1
        self._total_consecutive += 1
        hint = _RECOVERY_HINTS.get(ft, "")
        return ft, self._counts[ft], hint

    def record_success(self):
        """成功操作：计数器减半（而非清零），避免过于乐观。连续失败计数仍清零。"""
        for ft in FailureType:
            self._counts[ft] = self._counts[ft] // 2
        self._total_consecutive = 0

    def should_abort(self) -> tuple[bool, str]:
        """
        判断是否应该终止任务。
        返回: (是否终止, 原因)
        """
        # 任何单一类型超过阈值
        for ft, count in self._counts.items():
            max_allowed = _MAX_FAILURES.get(ft, 5)
            if count >= max_allowed:
                return True, f"{ft.value} 连续失败 {count} 次（上限 {max_allowed}）"

        # 总连续失败超过 10 次（跨类型）
        if self._total_consecutive >= 10:
            return True, f"总连续失败 {self._total_consecutive} 次"

        return False, ""

    @property
    def total_consecutive(self) -> int:
        return self._total_consecutive
