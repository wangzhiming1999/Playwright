"""
RecordingConverter — 将录制的操作序列转换为 workflow YAML。

转换策略：
  1. 按 URL 变化将操作分组
  2. 每组生成一个 workflow block（navigation 或 task）
  3. 自动检测可参数化的值（搜索词、URL、密码）
  4. 用 mini LLM 为每组操作生成自然语言描述
"""

import json
import re
from urllib.parse import urlparse, parse_qs, urlencode

import yaml

from utils import llm_chat


class RecordingConverter:
    """将录制的操作序列转换为 workflow YAML。"""

    def __init__(self, recording: dict):
        self.recording = recording
        self.actions: list[dict] = recording.get("actions", [])

    def to_workflow_yaml(self, params: list[dict] = None, title: str = "") -> str:
        """
        将操作序列转为 workflow YAML。
        params: 用户自定义参数列表（覆盖自动检测）
        """
        if not self.actions:
            return yaml.dump({"title": title or "空录制", "blocks": []}, allow_unicode=True)

        detected_params = params if params else self._detect_parameters()
        groups = self._group_actions_by_page()
        blocks = []

        for i, group in enumerate(groups):
            block = self._actions_to_block(group, i, detected_params)
            if block:
                blocks.append(block)

        # 构建 workflow 定义
        wf = {
            "title": title or self.recording.get("title", "") or "录制工作流",
            "description": f"从录制自动生成，共 {len(self.actions)} 步操作",
            "parameters": [
                {
                    "key": p["key"],
                    "type": p.get("type", "string"),
                    "description": p.get("description", ""),
                    "required": True,
                    "default": p.get("default_value", ""),
                }
                for p in detected_params
            ],
            "blocks": blocks,
        }

        return yaml.dump(wf, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def _group_actions_by_page(self) -> list[list[dict]]:
        """按 URL 变化将操作分组。"""
        if not self.actions:
            return []

        groups = []
        current_group = []
        current_url = ""

        for action in self.actions:
            action_url = action.get("url", "")
            # URL 变化（忽略 hash）
            if action.get("type") == "navigate" or (
                action_url and current_url and
                self._normalize_url(action_url) != self._normalize_url(current_url)
            ):
                if current_group:
                    groups.append(current_group)
                current_group = [action]
                current_url = action_url
            else:
                current_group.append(action)
                if not current_url:
                    current_url = action_url

        if current_group:
            groups.append(current_group)

        return groups

    def _actions_to_block(self, actions: list[dict], index: int, params: list[dict]) -> dict | None:
        """将一组操作转为 workflow block。"""
        if not actions:
            return None

        first = actions[0]

        # 纯导航
        if len(actions) == 1 and first.get("type") == "navigate":
            url = first.get("url", "")
            # 参数化 URL
            for p in params:
                if p.get("type") == "url" and p.get("default_value") == url:
                    url = "{{ " + p["key"] + " }}"
                    break
            return {
                "block_type": "navigation",
                "label": f"step_{index + 1}_navigate",
                "url": url,
            }

        # 复合操作 → task block
        description = self._describe_actions(actions, params)
        return {
            "block_type": "task",
            "label": f"step_{index + 1}",
            "task": description,
        }

    def _describe_actions(self, actions: list[dict], params: list[dict]) -> str:
        """为一组操作生成自然语言描述。"""
        parts = []
        for a in actions:
            atype = a.get("type", "")
            text = a.get("text", "")

            # 参数化文本
            for p in params:
                if p.get("default_value") and text == p["default_value"]:
                    text = "{{ " + p["key"] + " }}"
                    break

            if atype == "click":
                parts.append(f"点击「{text or a.get('selector', '元素')}」")
            elif atype == "type_text":
                input_type = a.get("input_type", "")
                if input_type == "password":
                    parts.append(f"在密码框中输入密码")
                else:
                    parts.append(f"输入「{text}」")
            elif atype == "press_key":
                key = a.get("meta", {}).get("key", "")
                parts.append(f"按 {key}")
            elif atype == "scroll":
                direction = a.get("meta", {}).get("direction", "down")
                parts.append(f"向{'下' if direction == 'down' else '上'}滚动")
            elif atype == "select_option":
                parts.append(f"选择「{text}」")
            elif atype == "navigate":
                parts.append(f"导航到 {a.get('url', '')}")

        if not parts:
            return "执行操作"

        # 简单拼接（不调 LLM，保持快速）
        return "，".join(parts[:10])

    def _detect_parameters(self) -> list[dict]:
        """自动检测可参数化的值。"""
        params = []
        seen_keys = set()

        # 敏感字段检测规则（优先级从高到低）
        _FIELD_RULES = [
            ("password", "密码", lambda a, t: a.get("input_type") == "password"),
            ("email", "邮箱地址", lambda a, t: a.get("input_type") == "email" or "@" in t),
            ("username", "用户名", lambda a, t: any(
                k in (a.get("selector", "") + " " + a.get("name", "")).lower()
                for k in ("user", "account", "login", "username")
            )),
            ("phone", "手机号", lambda a, t: (
                a.get("input_type") == "tel"
                or any(k in a.get("selector", "").lower() for k in ("phone", "mobile", "tel"))
            )),
            ("search_query", "搜索关键词", lambda a, t: (
                "search" in a.get("selector", "").lower()
                or "搜索" in t
                or a.get("input_type") == "search"
            )),
        ]

        for a in self.actions:
            atype = a.get("type", "")
            text = a.get("text", "")

            if atype == "type_text" and text:
                key, desc = None, None
                for rule_key, rule_desc, rule_fn in _FIELD_RULES:
                    if rule_fn(a, text):
                        key, desc = rule_key, rule_desc
                        break
                if not key:
                    key = f"input_{len(params) + 1}"
                    desc = "输入内容"

                if key not in seen_keys:
                    seen_keys.add(key)
                    params.append({
                        "key": key,
                        "type": "string",
                        "description": desc,
                        "default_value": text,
                    })

        # 起始 URL 参数化
        start_url = self.recording.get("start_url", "")
        if start_url and start_url != "about:blank" and "start_url" not in seen_keys:
            params.append({
                "key": "start_url",
                "type": "url",
                "description": "起始页面 URL",
                "default_value": start_url,
            })

        return params

    _IGNORE_PARAMS = frozenset({
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "fbclid", "gclid", "ref", "page", "p", "offset", "_t", "timestamp",
    })

    @staticmethod
    def _normalize_url(url: str) -> str:
        """规范化 URL（去 hash + 过滤追踪/分页参数）用于比较。"""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            filtered = {k: v for k, v in params.items()
                        if k.lower() not in RecordingConverter._IGNORE_PARAMS}
            clean_query = urlencode(filtered, doseq=True) if filtered else ""
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return f"{base}?{clean_query}" if clean_query else base
        except Exception:
            return url
