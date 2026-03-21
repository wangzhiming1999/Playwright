"""
Shared utilities: multi-backend LLM router, retry wrapper, URL validation.
Supports OpenAI, Anthropic, and litellm (Gemini, local models, etc.) backends.

Backend selection:
  - LLM_BACKEND=openai    → direct OpenAI SDK
  - LLM_BACKEND=anthropic → direct Anthropic SDK
  - LLM_BACKEND=litellm   → litellm router (supports 100+ models)

Model override:
  - LLM_MODEL=gpt-4o              → override default model
  - LLM_MINI_MODEL=gpt-4o-mini    → override mini model
  - VISION_MODEL=gpt-4o-mini      → override vision/screenshot model (always uses OpenAI)
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

_BUILTIN_MODELS = {
    "openai": {"default": "gpt-4o", "mini": "gpt-4o-mini"},
    "anthropic": {"default": "claude-sonnet-4-20250514", "mini": "claude-haiku-4-5-20251001"},
    "litellm": {"default": "gpt-4o", "mini": "gpt-4o-mini"},
}


def get_backend() -> str:
    """Return current LLM backend: 'openai', 'anthropic', or 'litellm'."""
    return os.getenv("LLM_BACKEND", "openai").lower().strip()


def get_default_model() -> str:
    env_model = os.getenv("LLM_MODEL", "").strip()
    if env_model:
        return env_model
    return _BUILTIN_MODELS.get(get_backend(), _BUILTIN_MODELS["openai"])["default"]


def get_mini_model() -> str:
    env_model = os.getenv("LLM_MINI_MODEL", "").strip()
    if env_model:
        return env_model
    return _BUILTIN_MODELS.get(get_backend(), _BUILTIN_MODELS["openai"])["mini"]


def get_vision_model() -> str:
    """Return the model used for screenshot/vision tasks. Always OpenAI-compatible."""
    return os.getenv("VISION_MODEL", "gpt-4o-mini").strip()


def _resolve_backend(model: str) -> str:
    """Determine backend from model name. Falls back to LLM_BACKEND env."""
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gpt"):
        return "openai"
    backend = get_backend()
    # 非 openai/anthropic 前缀的模型，如果 backend 不是 litellm，自动走 litellm
    if backend not in ("openai", "anthropic") or (
        not model.startswith("gpt") and not model.startswith("claude")
    ):
        if backend == "litellm":
            return "litellm"
    return backend


def _resolve_model(model: str) -> tuple[str, str]:
    """Resolve model alias and determine backend. Returns (actual_model, backend)."""
    if model == "default" or not model:
        actual = get_default_model()
        return actual, _resolve_backend(actual)
    if model == "mini":
        actual = get_mini_model()
        return actual, _resolve_backend(actual)
    return model, _resolve_backend(model)


# ── Client factories (cached) ───────────────────────────────────────────────

import threading
_thread_local = threading.local()


def get_openai_client():
    """Create or return thread-local cached OpenAI client with optional proxy."""
    if getattr(_thread_local, "openai_client", None) is not None:
        return _thread_local.openai_client

    import httpx
    from openai import OpenAI

    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    http_client = httpx.Client(proxy=proxy, timeout=120.0) if proxy else httpx.Client(timeout=120.0)
    _thread_local.openai_client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        http_client=http_client,
    )
    return _thread_local.openai_client


def get_anthropic_client():
    """Create or return thread-local cached Anthropic client with optional proxy."""
    if getattr(_thread_local, "anthropic_client", None) is not None:
        return _thread_local.anthropic_client

    import httpx
    from anthropic import Anthropic

    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    http_client = httpx.Client(proxy=proxy, timeout=120.0) if proxy else httpx.Client(timeout=120.0)
    _thread_local.anthropic_client = Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        base_url="https://api.anthropic.com",
        http_client=http_client,
    )
    return _thread_local.anthropic_client


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
    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage or {}

    @classmethod
    def from_openai(cls, resp):
        """Wrap native OpenAI response."""
        usage = {}
        if hasattr(resp, 'usage') and resp.usage:
            cached = 0
            if hasattr(resp.usage, 'prompt_tokens_details') and resp.usage.prompt_tokens_details:
                cached = getattr(resp.usage.prompt_tokens_details, 'cached_tokens', 0) or 0
            usage = {
                "input_tokens": resp.usage.prompt_tokens or 0,
                "output_tokens": resp.usage.completion_tokens or 0,
                "cached_tokens": cached,
            }
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
        return cls(choices, usage=usage)

    @classmethod
    def from_anthropic(cls, resp):
        """Wrap native Anthropic response."""
        usage = {}
        if hasattr(resp, 'usage') and resp.usage:
            input_tokens = getattr(resp.usage, 'input_tokens', 0) or 0
            output_tokens = getattr(resp.usage, 'output_tokens', 0) or 0
            cached = getattr(resp.usage, 'cache_read_input_tokens', 0) or 0
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached,
            }
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
        # Strip markdown code fences that Claude sometimes wraps JSON in
        if content_text:
            stripped = content_text.strip()
            if stripped.startswith("```"):
                stripped = _re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
                stripped = _re.sub(r"\n?```$", "", stripped.rstrip())
                content_text = stripped.strip()
        msg = _Message(
            role=resp.role,
            content=content_text or None,
            tool_calls=tool_calls if tool_calls else None,
        )
        return cls([_Choice(msg)], usage=usage)


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


def _strip_images(messages: list) -> list:
    """Remove image_url blocks from messages for non-vision models."""
    stripped = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text_only = [b for b in content if isinstance(b, dict) and b.get("type") != "image_url"]
            if text_only:
                stripped.append({**m, "content": text_only})
            # skip messages that were image-only
        else:
            stripped.append(m)
    return stripped


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


# ── litellm backend ─────────────────────────────────────────────────────────

def _call_litellm(messages: list, model: str, max_tokens: int,
                  tools: list = None, tool_choice: str = None,
                  response_format: dict = None, **kwargs) -> _WrappedResponse:
    """
    Call any model via litellm (Gemini, Ollama, Azure, etc.).
    litellm uses OpenAI-compatible format, so we wrap the response the same way.
    """
    import litellm

    # litellm respects env vars: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.
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

    resp = litellm.completion(**api_kwargs)
    return _WrappedResponse.from_openai(resp)


# ── Model capabilities detection ────────────────────────────────────────────

# Known vision-capable model prefixes
_VISION_MODELS = {
    "gpt-4o", "gpt-4-turbo", "gpt-4-vision",
    "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini-2", "gemini-1.5", "gemini-pro-vision",
}

# Known models that support function calling / tool use
_TOOL_MODELS = {
    "gpt-4o", "gpt-4-turbo", "gpt-4o-mini", "gpt-3.5-turbo",
    "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini-2", "gemini-1.5",
}


def get_model_capabilities(model: str) -> dict:
    """
    Detect model capabilities based on model name.
    Returns: {"vision": bool, "tools": bool, "max_tokens": int}
    """
    m = model.lower()

    has_vision = any(m.startswith(prefix) for prefix in _VISION_MODELS)
    has_tools = any(m.startswith(prefix) for prefix in _TOOL_MODELS)

    # Rough max context estimates
    if "gpt-4o" in m:
        max_ctx = 128000
    elif "claude" in m:
        max_ctx = 200000
    elif "gemini" in m:
        max_ctx = 1000000
    else:
        max_ctx = 8000  # conservative default

    return {
        "vision": has_vision,
        "tools": has_tools,
        "max_tokens": max_ctx,
    }


# ── Unified LLM call (public API) ───────────────────────────────────────────

def llm_chat(messages: list, model: str = None, max_tokens: int = 1000,
             tools: list = None, tool_choice: str = None,
             response_format: dict = None, **kwargs) -> _WrappedResponse:
    """
    Unified LLM call — routes to OpenAI, Anthropic, or litellm.

    Model resolution:
      - None / "default" → LLM_MODEL env or backend's default
      - "mini"           → LLM_MINI_MODEL env or backend's mini
      - "gpt-*"          → OpenAI direct
      - "claude-*"       → Anthropic direct
      - other            → litellm (if LLM_BACKEND=litellm) or current backend

    Returns OpenAI-compatible response wrapper regardless of backend.
    """
    actual_model, backend = _resolve_model(model or "default")

    # Auto-strip images for non-vision models
    caps = get_model_capabilities(actual_model)
    if not caps["vision"] and tools is None:
        # Remove image content from messages to avoid errors
        messages = _strip_images(messages)

    _backends = {
        "openai": _call_openai,
        "anthropic": _call_anthropic,
        "litellm": _call_litellm,
    }
    call_fn = _backends.get(backend, _call_openai)

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
            # Fatal errors: don't retry auth or bad request
            err_type = type(e).__name__
            if err_type in ("AuthenticationError", "PermissionDeniedError"):
                raise
            if err_type == "BadRequestError":
                raise
            last_exc = e
            # Unknown errors (not API errors): retry once then raise original
            is_api_error = err_type in (
                "RateLimitError", "APIConnectionError", "APITimeoutError",
                "InternalServerError", "APIStatusError",
            )
            if not is_api_error and attempt >= 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("LLM call error (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_retries, delay, e)
            time.sleep(delay)
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_exc}") from last_exc


# ── Vision-specific LLM call (always OpenAI, for screenshot analysis) ────────

def llm_chat_vision(messages: list, max_tokens: int = 1000,
                    tools: list = None, tool_choice: str = None,
                    response_format: dict = None, **kwargs) -> _WrappedResponse:
    """
    LLM call specifically for vision/screenshot tasks.
    Always uses OpenAI backend with VISION_MODEL (default: gpt-4o-mini).
    This allows using a different model for image recognition while keeping
    the main reasoning model (e.g., Anthropic Claude) for text-only steps.
    """
    vision_model = get_vision_model()
    return _call_openai(
        messages=messages,
        model=vision_model,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        **kwargs,
    )


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
