"""LLM 辅助函数：任务分解、步骤验证、上下文压缩、失败分析"""

import base64
import json
import re

from json_repair import repair_json

from .page_utils import _safe_print
from utils import llm_chat


# ── JSON 容错解析 ─────────────────────────────────────────────────────────────

def robust_json_loads(raw: str) -> dict | list:
    """
    多层容错 JSON 解析链：
    1. 标准 json.loads
    2. 从 markdown 代码块提取 JSON
    3. json_repair 容错修复
    4. 截断 JSON 修复（补全缺失的括号）
    任何一层成功即返回，全部失败则抛出 ValueError。
    """
    if not raw or not raw.strip():
        raise ValueError("空字符串")

    raw = raw.strip()

    # 1. 标准解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. 从 markdown ```json ... ``` 代码块提取
    md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. json_repair 容错修复（处理未转义引号、注释、尾逗号等）
    try:
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, (dict, list)):
            return repaired
    except Exception:
        pass

    # 4. 截断 JSON 修复：补全缺失的括号
    trimmed = raw
    if trimmed.startswith('```'):
        trimmed = re.sub(r'^```(?:json)?\s*\n?', '', trimmed)
        trimmed = re.sub(r'\n?\s*```\s*$', '', trimmed)
    open_braces = trimmed.count('{') - trimmed.count('}')
    open_brackets = trimmed.count('[') - trimmed.count(']')
    if open_braces > 0 or open_brackets > 0:
        patched = trimmed + '}' * max(0, open_braces) + ']' * max(0, open_brackets)
        try:
            repaired = repair_json(patched, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
        except Exception:
            pass

    raise ValueError(f"所有 JSON 解析策略均失败，原始内容前200字符: {raw[:200]}")


# ── 经济元素树裁剪 ────────────────────────────────────────────────────────────

_CHARS_PER_TOKEN = 3
_MAX_ELEMENTS_TOKENS = 3000

_INTERACTIVE_TAGS = {"input", "textarea", "button", "select"}

_FULL_FIELDS = ["index", "tag", "type", "text", "placeholder", "name", "id", "href", "aria_label", "src", "alt", "x", "y"]
_COMPACT_FIELDS = ["index", "tag", "type", "text", "placeholder", "aria_label", "src", "alt"]
_MINIMAL_FIELDS = ["index", "tag", "text"]


def trim_elements(elements: list[dict], max_tokens: int = _MAX_ELEMENTS_TOKENS) -> str:
    """
    经济元素树：根据 token 预算逐级裁剪元素列表。
    裁剪策略（逐级降级）：
    1. 完整版 → 2. 精简字段 → 3. 截断文字 → 4. 最小字段 + 过滤
    """
    if not elements:
        return "[]"

    full_json = json.dumps(elements, ensure_ascii=False)
    if len(full_json) / _CHARS_PER_TOKEN <= max_tokens:
        return full_json

    compact = [
        {k: el[k] for k in _COMPACT_FIELDS if k in el and el[k] != ""}
        for el in elements
    ]
    compact_json = json.dumps(compact, ensure_ascii=False)
    if len(compact_json) / _CHARS_PER_TOKEN <= max_tokens:
        return compact_json

    for el in compact:
        if "text" in el and len(el["text"]) > 20:
            el["text"] = el["text"][:20] + "…"
    truncated_json = json.dumps(compact, ensure_ascii=False)
    if len(truncated_json) / _CHARS_PER_TOKEN <= max_tokens:
        return truncated_json

    minimal = []
    for el in elements:
        tag = el.get("tag", "")
        text = el.get("text", "")
        if tag == "a" and not text.strip():
            continue
        entry = {"index": el.get("index", 0), "tag": tag}
        if text:
            entry["text"] = text[:15] + "…" if len(text) > 15 else text
        if el.get("type"):
            entry["type"] = el["type"]
        if el.get("placeholder"):
            entry["placeholder"] = el["placeholder"][:15]
        minimal.append(entry)
    minimal_json = json.dumps(minimal, ensure_ascii=False)
    if len(minimal_json) / _CHARS_PER_TOKEN <= max_tokens:
        return minimal_json

    interactive_only = [el for el in minimal if el.get("tag") in _INTERACTIVE_TAGS]
    return json.dumps(interactive_only, ensure_ascii=False)


# ── 任务分解 ──────────────────────────────────────────────────────────────────

def _decompose_task(task: str) -> list[dict]:
    """
    执行前把用户任务拆成有序步骤列表。
    每个步骤包含：
      - step: 步骤序号
      - action: 要做什么
      - expected: 这个步骤全部完成后，页面的最终状态
      - done_signal: 判断这步完成的关键特征
    """
    try:
        resp = llm_chat(
            messages=[{
                "role": "user",
                "content": (
                    f"用户任务：{task}\n\n"
                    "请把这个任务拆解成有序的操作步骤。\n"
                    "返回 JSON 数组，每个元素格式：\n"
                    '{"step": 1, "action": "打开网址并等待加载", '
                    '"expected": "网站首页已加载，可以看到导航栏和登录入口", '
                    '"done_signal": "看到网站首页内容"}\n\n'
                    "重要规则：\n"
                    "- 每个步骤代表一个完整的阶段，不要拆得太细\n"
                    "- 登录是一个步骤（包含填邮箱、填密码、点登录按钮），不要拆成3步\n"
                    "- expected 描述这个阶段全部完成后的最终页面状态，不描述中间过程\n"
                    "- done_signal 要简单明确，比如'已进入首页'、'已登录显示用户头像'、'搜索结果已显示'\n"
                    "- 步骤数控制在 3-6 步\n"
                    "- 如果任务涉及提交搜索或 AI 生成内容，必须有一个独立步骤：'等待生成完成'，done_signal 为'页面内容不再变化，生成结果完整显示'\n"
                    "- 最后一步必须是截图\n"
                    '返回格式：{"steps": [...]}，steps 是步骤数组。'
                ),
            }],
            response_format={"type": "json_object"},
            max_tokens=600,
        )
        if not resp.choices:
            return []
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        steps = data if isinstance(data, list) else data.get("steps", [])
        return steps if isinstance(steps, list) else []
    except Exception as e:
        _safe_print(f"  [任务分解] 失败: {e}")
        return []


# ── 预期验证 ──────────────────────────────────────────────────────────────────

async def _verify_step(page, expected: str, done_signal: str) -> tuple[bool, str]:
    """
    操作后截图，让 GPT 判断是否符合预期。
    返回 (是否成功, 观察描述, 差距描述)
    """
    try:
        data = await page.screenshot(type="jpeg", quality=70)
        img_b64 = base64.b64encode(data).decode()
        resp = llm_chat(
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"预期结果：{expected}\n"
                            f"完成信号：{done_signal}\n\n"
                            "观察截图，判断操作是否成功达到预期。\n"
                            '返回 JSON：{"success": true/false, "observation": "实际看到了什么（1-2句）", "mismatch": "如果失败，差距在哪里"}'
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "low"}},
                ],
            }],
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        if not resp.choices:
            return False, "", "空响应"
        try:
            result = json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError as e:
            return False, "", f"JSON 解析失败: {e}"
        return result.get("success", False), result.get("observation", ""), result.get("mismatch", "")
    except Exception as e:
        return False, "", str(e)


# ── 上下文压缩 ────────────────────────────────────────────────────────────────

def _compress_messages(messages: list, max_history: int = 16) -> list:
    """
    消息超出限制时，把中间的历史压缩成一条摘要，保留：
    - messages[0]: system prompt
    - messages[1]: 原始任务
    - 一条压缩摘要（assistant role）
    - 最近 max_history 条消息
    """
    if len(messages) <= max_history + 2:
        return messages

    to_compress = messages[2: -max_history]
    if not to_compress:
        return messages

    history_text = []
    for m in to_compress:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            text_parts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts)
        if isinstance(content, str) and content.strip():
            history_text.append(f"[{role}] {content[:200]}")

    if not history_text:
        return messages[:2] + messages[-max_history:]

    try:
        resp = llm_chat(
            model="mini",
            messages=[{
                "role": "user",
                "content": (
                    "以下是网页操作的历史记录，请用 2-4 句话总结已完成的操作和当前状态：\n\n"
                    + "\n".join(history_text[-30:])
                ),
            }],
            max_tokens=200,
        )
        if resp.choices:
            summary = resp.choices[0].message.content.strip()
        else:
            summary = f"已执行 {len(to_compress)//2} 步操作"
    except Exception as e:
        _safe_print(f"  [上下文压缩] 摘要生成失败: {e}")
        summary = f"已执行 {len(to_compress)//2} 步操作"

    summary_msg = {
        "role": "assistant",
        "content": f"[历史摘要] {summary}",
    }
    return messages[:2] + [summary_msg] + messages[-max_history:]


# ── 智能重试分析 ──────────────────────────────────────────────────────────────

def _analyze_failure(tool_name: str, tool_args: dict, error_result: str) -> str:
    """
    操作失败时，用 GPT 分析失败原因并给出下一步建议。
    """
    try:
        resp = llm_chat(
            model="mini",
            messages=[{
                "role": "user",
                "content": (
                    f"网页操作失败了：\n"
                    f"操作: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
                    f"错误: {error_result}\n\n"
                    "请分析失败原因，并给出 1-2 句具体的下一步建议（如：换用其他 index、先滚动页面、等待加载等）。"
                    "直接给建议，不要废话。"
                ),
            }],
            max_tokens=100,
        )
        if resp.choices:
            return resp.choices[0].message.content.strip()
        return ""
    except Exception as e:
        _safe_print(f"  [失败分析] 分析失败: {e}")
        return ""
