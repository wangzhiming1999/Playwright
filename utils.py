"""
Shared utilities: multi-backend LLM router, retry wrapper, URL validation.
Supports OpenAI and Anthropic backends, switchable via LLM_BACKEND env var.
"""

import json
import logging
import os
import re as _re
import time
from typing import Callable, TypeVar
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Backend config ───────────────────────────────────────────────────────────

_MODELS = {
    "openai": {"default": "gpt-4o", "mini": "gpt-4o-mini"},
    "anthropic": {"default": "claude-sonnet-4-20250514", "mini": "claude-haiku-4-5-20251001"},
}


def get_backend() -> str:
    """Return current LLM backend: 'openai' or 'anthropic'."""
    return os.getenv("LLM_BACKEND", "openai").lower().strip()


def get_default_model() -> str:
    return _MODELS[get_backend()]["default"]


def get_mini_model() -> str:
    return _MODELS[get_backend()]["mini"]


def _resolve_backend(model: str) -> str:
    """Determine backend from model name. Falls back to LLM_BACKEND env."""
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gpt"):
        return "openai"
    return get_backend()


def _resolve_model(model: str) -> tuple[str, str]:
    """Resolve model alias and determine backend. Returns (actual_model, backend)."""
    if model == "default" or not model:
        backend = get_backend()
        return _MODELS[backend]["default"], backend
    if model == "mini":
        backend = get_backend()
        return _MODELS[backend]["mini"], backend
    backend = _resolve_backend(model)
    return model, backend


# ── Client factories (cached) ───────────────────────────────────────────────

_openai_client = None
_anthropic_client = None


def get_openai_client():
    """Create or return cached OpenAI client with optional proxy."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    import httpx
    from openai import OpenAI

    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    http_client = httpx.Client(proxy=proxy, timeout=120.0) if proxy else httpx.Client(timeout=120.0)
    _openai_client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        http_client=http_client,
    )
    return _openai_client


def get_anthropic_client():
    """Create or return cached Anthropic client with optional proxy."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client

    import httpx
    from anthropic import Anthropic

    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    http_client = httpx.Client(proxy=proxy, timeout=120.0) if proxy else httpx.Client(timeout=120.0)
    _anthropic_client = Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        base_url="https://api.anthropic.com",
        http_client=http_client,
    )
    return _anthropic_client


# ── Response wrapper (unified format) ────────────────────────────────────────

class _Choice:
    def __init__(self, message):
        self.message = message

class _Message:
    def __init__(self, role, content, tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []

class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _Function(name, arguments)
        self.type = "function"

class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

class _WrappedResponse:
    """Unified response wrapper — both backends produce this."""
    def __init__(self, choices):
        self.choices = choices

    @classmethod
    def from_openai(cls, resp):
        """Wrap native OpenAI response."""
        choices = []
        for c in resp.choices:
            m = c.message
            tool_calls = None
            if m.tool_calls:
                tool_calls = [
                    _ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                    for tc in m.tool_calls
                ]
            choices.append(_Choice(_Message(
                role=m.role,
                content=m.content,
                tool_calls=tool_calls,
            )))
        return cls(choices)

    @classmethod
    def from_anthropic(cls, resp):
        """Wrap native Anthropic response."""
        content_text = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(_ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=json.dumps(block.input, ensure_ascii=False),
                ))
        msg = _Message(
            role=resp.role,
            content=content_text or None,
            tool_calls=tool_calls if tool_calls else None,
        )
        return cls([_Choice(msg)])


# ── Anthropic format converters ──────────────────────────────────────────────

def _convert_image_block(block: dict) -> dict:
    """Convert OpenAI image_url block → Anthropic image block."""
    url = block.get("image_url", {}).get("url", "")
    m = _re.match(r"data:(image/\w+);base64,(.+)", url, _re.DOTALL)
    if m:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _convert_content(content) -> list:
    """Convert OpenAI message content → Anthropic content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        result = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                result.append(block)
            elif block.get("type") == "image_url":
                result.append(_convert_image_block(block))
            else:
                result.append(block)
        return result
    return [{"type": "text", "text": str(content)}]


def _convert_tools(openai_tools: list) -> list:
    """Convert OpenAI tools format → Anthropic tools format."""
    return [
        {
            "name": t.get("function", {}).get("name", ""),
            "description": t.get("function", {}).get("description", ""),
            "input_schema": t.get("function", {}).get("parameters", {"type": "object", "properties": {}}),
        }
        for t in openai_tools
    ]


# ── OpenAI backend ───────────────────────────────────────────────────────────

def _call_openai(messages: list, model: str, max_tokens: int,
                 tools: list = None, tool_choice: str = None,
                 response_format: dict = None, **kwargs) -> _WrappedResponse:
    """Call OpenAI API directly."""
    client = get_openai_client()
    api_kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        api_kwargs["tools"] = tools
    if tool_choice:
        api_kwargs["tool_choice"] = tool_choice
    if response_format:
        api_kwargs["response_format"] = response_format

    resp = client.chat.completions.create(**api_kwargs)
    return _WrappedResponse.from_openai(resp)


# ── Anthropic backend ────────────────────────────────────────────────────────

def _call_anthropic(messages: list, model: str, max_tokens: int,
                    tools: list = None, tool_choice: str = None,
                    response_format: dict = None, **kwargs) -> _WrappedResponse:
    """Call Anthropic API with OpenAI-style params converted."""
    client = get_anthropic_client()

    # Extract system message + convert roles
    system_text = ""
    converted = []
    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            content = m.get("content", "")
            system_text += (content if isinstance(content, str) else str(content)) + "\n"
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }],
            })
        elif role == "assistant":
            content = m.get("content", "")
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content if isinstance(content, str) else str(content)})
            for tc in m.get("tool_calls", []):
                func = tc.get("function", tc) if isinstance(tc, dict) else tc
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                func_name = func.get("name", "") if isinstance(func, dict) else getattr(func, "name", "")
                func_args = func.get("arguments", "{}") if isinstance(func, dict) else getattr(func, "arguments", "{}")
                try:
                    input_obj = json.loads(func_args) if isinstance(func_args, str) else func_args
                except (json.JSONDecodeError, TypeError):
                    input_obj = {}
                blocks.append({"type": "tool_use", "id": tc_id, "name": func_name, "input": input_obj})
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({
                "role": "user",
                "content": _convert_content(m.get("content", "")),
            })

    # Merge consecutive same-role messages (Anthropic requires alternating)
    merged = []
    for msg in converted:
        if merged and merged[-1]["role"] == msg["role"]:
            prev = merged[-1]["content"]
            curr = msg["content"]
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}]
            if isinstance(curr, str):
                curr = [{"type": "text", "text": curr}]
            merged[-1]["content"] = prev + curr
        else:
            merged.append(msg)

    # response_format → prompt injection
    if response_format and response_format.get("type") == "json_object":
        system_text += "\nIMPORTANT: You must respond with valid JSON only. No markdown, no explanation, just the JSON object.\n"

    api_kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": merged,
    }
    if system_text.strip():
        api_kwargs["system"] = system_text.strip()
    if tools:
        api_kwargs["tools"] = _convert_tools(tools)
        if tool_choice == "required":
            api_kwargs["tool_choice"] = {"type": "any"}
        elif tool_choice == "auto":
            api_kwargs["tool_choice"] = {"type": "auto"}

    resp = client.messages.create(**api_kwargs)
    return _WrappedResponse.from_anthropic(resp)


# ── Unified LLM call (public API) ───────────────────────────────────────────

def llm_chat(messages: list, model: str = None, max_tokens: int = 1000,
             tools: list = None, tool_choice: str = None,
             response_format: dict = None, **kwargs) -> _WrappedResponse:
    """
    Unified LLM call — routes to OpenAI or Anthropic based on model name / env.

    Model resolution:
      - None / "default" → current backend's default model
      - "mini"           → current backend's mini model
      - "gpt-*"          → OpenAI
      - "claude-*"       → Anthropic
      - other            → current backend (LLM_BACKEND env)

    Returns OpenAI-compatible response wrapper regardless of backend.
    """
    actual_model, backend = _resolve_model(model or "default")
    call_fn = _call_openai if backend == "openai" else _call_anthropic

    last_exc = None
    for attempt in range(3):
        try:
            return call_fn(
                messages=messages, model=actual_model, max_tokens=max_tokens,
                tools=tools, tool_choice=tool_choice,
                response_format=response_format, **kwargs,
            )
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if "authentication" in err_str or ("invalid" in err_str and "key" in err_str):
                raise
            if "invalid_request" in err_str or "bad_request" in err_str:
                raise
            delay = 2.0 * (2 ** attempt)
            logger.warning("%s API error (attempt %d/3), retrying in %.1fs: %s",
                           backend, attempt + 1, delay, e)
            time.sleep(delay)

    raise RuntimeError(f"{backend} call failed after 3 attempts: {last_exc}") from last_exc


# ── Legacy retry wrapper (kept for agent_legacy.py) ──────────────────────────

T = TypeVar("T")

def llm_call(fn: Callable[..., T], *args, max_retries: int = 3, base_delay: float = 2.0, **kwargs) -> T:
    """Call fn(*args, **kwargs) with exponential backoff. Legacy compat."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            delay = base_delay * (2 ** attempt)
            logger.warning("LLM call error (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_retries, delay, e)
            time.sleep(delay)
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_exc}") from last_exc


# ── URL validation ───────────────────────────────────────────────────────────

def validate_url(url: str) -> tuple[bool, str]:
    """Validate that a URL is safe to explore. Returns (is_valid, error_message)."""
    if not url or not url.strip():
        return False, "URL 不能为空"

    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL 格式无效"

    if parsed.scheme not in ("http", "https"):
        return False, f"不支持的协议 '{parsed.scheme}'，请使用 http 或 https"
    if not parsed.netloc:
        return False, "URL 缺少域名"

    host = parsed.hostname or ""
    blocked = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    if host in blocked or host.endswith(".local"):
        return False, f"不允许访问本地地址: {host}"

    import re
    private = re.compile(r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.)")
    if private.match(host):
        return False, f"不允许访问私有 IP 地址: {host}"

    return True, ""
