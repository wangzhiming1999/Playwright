"""
Shared utilities: OpenAI retry wrapper, URL validation, shared OpenAI client factory.
"""

import asyncio
import functools
import logging
import os
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)


# ── Shared OpenAI client factory ──────────────────────────────────────────────

def get_openai_client():
    """
    Create an OpenAI client with optional proxy support and a 60s timeout.
    Reads OPENAI_API_KEY and USE_PROXY from environment.
    """
    import httpx
    from openai import OpenAI

    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    http_client = httpx.Client(proxy=proxy, timeout=60.0) if proxy else httpx.Client(timeout=60.0)
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        http_client=http_client,
    )

T = TypeVar("T")

# ── OpenAI retry wrapper ──────────────────────────────────────────────────────

# Import lazily to avoid hard dependency at module load
def _openai_errors():
    from openai import (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        AuthenticationError,
        BadRequestError,
    )
    return {
        "retryable": (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError),
        "fatal": (AuthenticationError, BadRequestError),
    }


def llm_call(fn: Callable[..., T], *args, max_retries: int = 3, base_delay: float = 2.0, **kwargs) -> T:
    """
    Call fn(*args, **kwargs) with exponential backoff on transient OpenAI errors.
    Raises immediately on fatal errors (auth, bad request).
    """
    errors = _openai_errors()
    retryable = errors["retryable"]
    fatal = errors["fatal"]

    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except fatal as e:
            logger.error("Fatal OpenAI error (no retry): %s", e)
            raise
        except retryable as e:
            last_exc = e
            delay = base_delay * (2 ** attempt)
            logger.warning("OpenAI transient error (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_retries, delay, e)
            time.sleep(delay)
        except Exception as e:
            # Unknown error — retry once, then raise
            last_exc = e
            if attempt < 1:
                time.sleep(base_delay)
            else:
                raise

    raise RuntimeError(f"OpenAI call failed after {max_retries} attempts: {last_exc}") from last_exc


# ── URL validation ────────────────────────────────────────────────────────────

from urllib.parse import urlparse


def validate_url(url: str) -> tuple[bool, str]:
    """
    Validate that a URL is safe to explore.
    Returns (is_valid, error_message).
    """
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

    # Block obviously internal/dangerous targets
    host = parsed.hostname or ""
    blocked = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    if host in blocked or host.endswith(".local"):
        return False, f"不允许访问本地地址: {host}"

    # Block private IP ranges (basic check)
    import re
    private = re.compile(
        r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.)"
    )
    if private.match(host):
        return False, f"不允许访问私有 IP 地址: {host}"

    return True, ""
