"""
Agent 记忆系统 — 跨任务记忆提取、存储、检索。

记忆类型：
  - site:    站点特征（登录流程、弹窗处理、页面结构）
  - pattern: 成功操作模式（可复用的操作序列）
  - failure: 失败经验（错误原因和解决方案）
"""

import json
import logging
import re
import uuid
from urllib.parse import urlparse

from db import (
    save_memory, load_memories, update_memory_hit,
)
from utils import llm_chat

logger = logging.getLogger(__name__)


def _extract_domain(text: str) -> str:
    """从文本中提取域名。"""
    url_match = re.search(r'https?://([^\s/]+)', text)
    if url_match:
        return url_match.group(1).lower().split(":")[0]
    return ""


def _extract_domain_from_logs(logs: list[str]) -> str:
    """从日志中提取主要操作域名（第一个 navigate 的目标）。"""
    for log in logs:
        m = re.search(r'navigate\(\{.*?"url":\s*"(https?://[^"]+)"', log)
        if m:
            try:
                return urlparse(m.group(1)).netloc.lower()
            except Exception:
                pass
    return ""


# ── 分词 ────────────────────────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "个", "上", "也", "到", "说", "要", "去", "你",
    "会", "着", "没", "那", "这",
})


def _tokenize(text: str) -> set[str]:
    """分词：英文按单词，中文按 bigram + 单字（带停用词过滤）。"""
    text = text.lower()
    tokens = set()
    # 英文/数字词（长度 >= 2）
    for w in re.findall(r'[a-z][a-z0-9_]+', text):
        tokens.add(w)
    # 中文：提取连续中文片段，对每个片段做 bigram + 单字
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        for ch in segment:
            if ch not in _STOP_WORDS:
                tokens.add(ch)
        for i in range(len(segment) - 1):
            tokens.add(segment[i:i + 2])
    return tokens


_FAIL_KEYWORDS = frozenset({"失败", "错误", "报错", "error", "fail", "bug", "问题", "异常"})


class MemoryManager:
    """Agent 记忆管理器：提取、保存、检索跨任务记忆。"""

    # --- PLACEHOLDER_MEMORY_METHODS ---

    def extract_memories(
        self,
        task_id: str,
        task: str,
        logs: list[str],
        success: bool,
        domain: str = "",
    ) -> list[dict]:
        """
        任务完成后从日志中提取记忆。
        用 mini LLM 分析日志，提取结构化记忆。JSON 解析失败自动重试 1 次。
        """
        if not logs:
            return []

        if not domain:
            domain = _extract_domain(task) or _extract_domain_from_logs(logs)

        filtered_logs = [
            l for l in logs
            if not l.startswith("__PROGRESS__")
            and "step_" not in l
            and "_annotated" not in l
        ][-40:]

        if not filtered_logs:
            return []

        log_text = "\n".join(filtered_logs)

        prompt = (
            f"你是一个浏览器自动化 Agent 的记忆提取器。\n"
            f"分析以下任务执行日志，提取有价值的经验记忆。\n\n"
            f"任务: {task}\n"
            f"域名: {domain or '未知'}\n"
            f"结果: {'成功' if success else '失败'}\n\n"
            f"日志（最后40条）:\n{log_text}\n\n"
            f"请提取记忆，返回 JSON:\n"
            f'{{\n'
            f'  "site_memories": [\n'
            f'    {{"title": "简短标题", "content": {{"login_flow": [], "common_popups": [], "page_hints": ""}}}}\n'
            f'  ],\n'
            f'  "pattern_memories": [\n'
            f'    {{"title": "简短标题", "content": {{"action_sequence": [], "context": "", "tips": ""}}}}\n'
            f'  ],\n'
            f'  "failure_memories": [\n'
            f'    {{"title": "简短标题", "content": {{"error_type": "", "scenario": "", "root_cause": "", "solution": ""}}}}\n'
            f'  ]\n'
            f'}}\n\n'
            f"规���:\n"
            f"1. 只提取真正有价值的经验，不要凑数\n"
            f"2. 成功任务重点提取 site 和 pattern 记忆\n"
            f"3. 失败任务重点提取 failure 记忆\n"
            f"4. 如果没有值得记录的经验，返回空数组\n"
            f"5. title 要简洁明确，如「GitHub 登录流程」「Bing 搜索模式」"
        )

        # --- PLACEHOLDER_EXTRACT_CONT ---

        data = None
        for attempt in range(2):
            try:
                resp = llm_chat(
                    model="mini",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=800,
                )
                if not resp.choices:
                    return []
                raw = resp.choices[0].message.content or ""
                if not raw.strip():
                    return []
                data = json.loads(raw)
                break
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("记忆提取 JSON 解析失败，重试中...")
                    continue
                logger.error("记忆提取 JSON 解析重试失败")
                return []
            except Exception as e:
                logger.error(f"记忆提取 LLM 调用失败: {e}")
                return []

        if not data:
            return []

        memories = []
        for mem_type, key in [("site", "site_memories"), ("pattern", "pattern_memories"), ("failure", "failure_memories")]:
            for mem in data.get(key, []):
                if mem.get("title"):
                    memories.append({
                        "id": uuid.uuid4().hex[:12],
                        "memory_type": mem_type,
                        "domain": domain,
                        "title": mem["title"],
                        "content": mem.get("content", {}),
                        "source_task_id": task_id,
                    })

        return memories

    # --- PLACEHOLDER_SAVE_RETRIEVE ---

    def save_memories(self, memories: list[dict]) -> list[str]:
        """批量保存记忆，按 (type, domain, title) 去重，已存在则合并更新。"""
        existing = load_memories()
        existing_map = {
            (m["memory_type"], m["domain"], m["title"]): m
            for m in existing
        }

        saved_ids = []
        for mem in memories:
            key = (mem["memory_type"], mem.get("domain", ""), mem["title"])
            if key in existing_map:
                old = existing_map[key]
                if isinstance(old.get("content"), dict) and isinstance(mem.get("content"), dict):
                    old["content"] = {**old["content"], **mem["content"]}
                else:
                    old["content"] = mem.get("content", old["content"])
                old["source_task_id"] = mem.get("source_task_id", old.get("source_task_id", ""))
                save_memory(old)
                saved_ids.append(old["id"])
            else:
                save_memory(mem)
                saved_ids.append(mem["id"])
                existing_map[key] = mem

        return saved_ids

    def retrieve_relevant(
        self,
        task: str,
        domain: str = "",
        max_results: int = 5,
    ) -> list[dict]:
        """检索与当前任务相关的记忆。策略：域名匹配 + bigram 关键词重叠。"""
        all_memories = load_memories()
        if not all_memories:
            return []

        if not domain:
            domain = _extract_domain(task)

        task_words = _tokenize(task)

        scored = []
        for mem in all_memories:
            score = self._score_memory(mem, task_words, domain)
            if score > 0:
                scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [m for _, m in scored[:max_results]]

        for m in results:
            update_memory_hit(m["id"])

        return results

    # --- PLACEHOLDER_SCORE_FORMAT ---

    def _score_memory(self, mem: dict, task_words: set, domain: str) -> float:
        """评分：域名匹配 + bigram 关键词重叠 + 条件加分。"""
        score = 0.0

        mem_domain = mem.get("domain", "")
        if domain and mem_domain:
            if domain == mem_domain:
                score += 10.0
            elif domain.endswith("." + mem_domain) or mem_domain.endswith("." + domain):
                score += 5.0

        mem_text = f"{mem.get('title', '')} {json.dumps(mem.get('content', {}), ensure_ascii=False)}"
        mem_words = _tokenize(mem_text)
        overlap = task_words & mem_words
        for w in overlap:
            score += 1.0 if len(w) >= 2 else 0.3

        score += min(mem.get("hit_count", 0) * 0.1, 2.0)

        if mem.get("memory_type") == "failure" and task_words & _FAIL_KEYWORDS:
            score += 2.0

        return score


def format_memories_for_prompt(memories: list[dict], max_chars: int = 2000) -> str:
    """将记忆格式化为可注入 prompt 的文本，总长度不超过 max_chars。"""
    if not memories:
        return ""

    type_labels = {"site": "站点经验", "pattern": "操作模式", "failure": "失败教训"}
    header = "## 历史经验（来自之前的任务）"
    lines = [header]
    total_len = len(header)

    for idx, mem in enumerate(memories):
        label = type_labels.get(mem["memory_type"], mem["memory_type"])
        content = mem.get("content", {})
        if isinstance(content, dict):
            content_str = json.dumps(content, ensure_ascii=False)
        else:
            content_str = str(content)
        if len(content_str) > 200:
            content_str = content_str[:200] + "..."
        domain_tag = f"[{mem.get('domain', '')}] " if mem.get("domain") else ""
        line = f"- 【{label}】{domain_tag}{mem['title']}: {content_str}"

        if total_len + len(line) + 1 > max_chars:
            remaining = len(memories) - idx
            lines.append(f"- ...（还有 {remaining} 条记忆被截断）")
            break
        lines.append(line)
        total_len += len(line) + 1

    return "\n".join(lines)
