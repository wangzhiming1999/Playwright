"""
循环检测器：检测 agent 是否陷入重复行为循环。

借鉴 browser-use 的 ActionLoopDetector 设计：
- 滑动窗口记录最近 N 步 action
- Hash 去重检测重复 action
- 页面指纹停滞检测
- 递进式提醒（5/8/12 次重复时给出不同强度的 nudge）
"""

import hashlib
from collections import Counter


class ActionLoopDetector:
    """
    检测 agent 是否陷入行为循环。

    两种检测维度：
    1. Action 重复：同样的 tool+args 反复出现
    2. 页面停滞：页面指纹（URL + innerText 长度）长时间不变
    """

    def __init__(self, window_size: int = 20):
        self._window_size = window_size
        self._action_hashes: list[str] = []       # 滑动窗口内的 action hash
        self._action_history: list[dict] = []      # 完整 action 记录（用于日志）
        self._page_fingerprints: list[str] = []    # 页面指纹历史
        self._nudge_thresholds = [4, 6, 9]        # 递进提醒阈值（更早干预）
        self._last_nudge_count = 0                 # 上次提醒时的重复次数

    def _hash_action(self, tool_name: str, tool_args: dict, result: str = "") -> str:
        """将 action + result 前缀转为 hash，用于去重比较。"""
        result_prefix = result[:50] if result else ""
        key = f"{tool_name}:{sorted(tool_args.items())}:{result_prefix}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def record_action(self, tool_name: str, tool_args: dict, result: str = ""):
        """记录一次 action（含执行结果前缀）。"""
        h = self._hash_action(tool_name, tool_args, result)
        self._action_hashes.append(h)
        self._action_history.append({"tool": tool_name, "args": tool_args})

        # 保持滑动窗口大小
        if len(self._action_hashes) > self._window_size:
            self._action_hashes.pop(0)
            self._action_history.pop(0)

    def record_page_fingerprint(self, url: str, content_length: int):
        """记录页面指纹（URL + 内容长度）。"""
        fp = f"{url}|{content_length}"
        self._page_fingerprints.append(fp)
        if len(self._page_fingerprints) > self._window_size:
            self._page_fingerprints.pop(0)

    def check_loop(self) -> tuple[bool, str]:
        """
        检测是否存在循环行为。

        返回: (是否循环, 提醒消息)
        - 提醒消息为空字符串表示无循环
        - 提醒消息非空时应注入到 LLM 上下文中
        """
        if len(self._action_hashes) < 4:
            return False, ""

        # ── 检测1：action hash 重复频率 ──
        counter = Counter(self._action_hashes)
        most_common_hash, most_common_count = counter.most_common(1)[0]

        # 找到对应的 action 信息用于提示
        repeated_action = None
        for i, h in enumerate(self._action_hashes):
            if h == most_common_hash:
                repeated_action = self._action_history[i]
                break

        # 递进式提醒
        nudge = ""
        if most_common_count >= self._nudge_thresholds[0] and most_common_count > self._last_nudge_count:
            self._last_nudge_count = most_common_count
            action_desc = f"{repeated_action['tool']}({repeated_action['args']})" if repeated_action else "同一操作"

            if most_common_count >= self._nudge_thresholds[2]:
                # 12+ 次：强烈警告，建议放弃当前路径
                nudge = (
                    f"⚠️ 严重循环：你已经重复执行 '{action_desc}' {most_common_count} 次。"
                    "这个方法明显不可行。请彻底改变策略："
                    "1) 尝试完全不同的操作路径 "
                    "2) 用 scroll 探索页面其他区域 "
                    "3) 如果任务核心目标已部分完成，考虑 done 结束"
                )
            elif most_common_count >= self._nudge_thresholds[1]:
                # 8+ 次：中度警告
                nudge = (
                    f"⚠️ 检测到循环：'{action_desc}' 已重复 {most_common_count} 次。"
                    "当前方法可能无效，请尝试不同的操作方式。"
                    "比如：换个元素、滚动页面、用 find_element 重新定位、或换一种交互方式。"
                )
            else:
                # 5+ 次：轻度提醒
                nudge = (
                    f"提示：'{action_desc}' 已重复 {most_common_count} 次，"
                    "如果没有进展，请考虑换一种方式。"
                )

        # ── 检测2：页面指纹停滞 ──
        if not nudge and len(self._page_fingerprints) >= 6:
            recent = self._page_fingerprints[-6:]
            if len(set(recent)) == 1:
                nudge = (
                    "提示：页面状态已连续 6 步没有变化，你的操作可能没有产生效果。"
                    "请尝试：1) 滚动页面 2) 点击不同的元素 3) 导航到其他页面"
                )

        return bool(nudge), nudge

    def reset(self):
        """重置检测器（任务成功完成时调用）。"""
        self._action_hashes.clear()
        self._action_history.clear()
        self._page_fingerprints.clear()
        self._last_nudge_count = 0
