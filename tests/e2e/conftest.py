"""
E2E 测试 conftest — 网络检测、超时控制。
"""

import asyncio
import socket

import pytest


def _has_internet() -> bool:
    """快速检测是否有网络连接。"""
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
        return True
    except OSError:
        return False


requires_internet = pytest.mark.skipif(
    not _has_internet(),
    reason="需要网络连接才能运行 E2E 测试",
)


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
