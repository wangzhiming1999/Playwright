"""
Watchdog 事件架构：将浏览器事件（CAPTCHA/下载/崩溃/弹窗/网络空闲）
从主循环中解耦为独立的事件监听器。

设计思路：
- 每种事件类型有独立的 handler，通过 Playwright 事件 API 注册
- 事件触发时写入事件队列，主循环在每步开始时消费队列
- 主循环不再需要在每步手动检查这些状态，降低耦合度
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable, Optional

from playwright.async_api import Page, BrowserContext, Download, Dialog


class EventType(Enum):
    CAPTCHA_DETECTED = "captcha"
    DOWNLOAD_STARTED = "download"
    DOWNLOAD_COMPLETED = "download_completed"
    PAGE_CRASHED = "crash"
    POPUP_APPEARED = "popup"
    DIALOG_APPEARED = "dialog"
    NETWORK_IDLE = "network_idle"
    NEW_TAB = "new_tab"
    CONSOLE_ERROR = "console_error"


@dataclass
class WatchdogEvent:
    type: EventType
    timestamp: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)
    handled: bool = False


class Watchdog:
    """
    浏览器事件监听器。

    用法：
        watchdog = Watchdog(page, context, log_fn=_log)
        await watchdog.start()

        # 主循环中消费事件
        for event in watchdog.drain_events():
            if event.type == EventType.CAPTCHA_DETECTED:
                ...
            elif event.type == EventType.DOWNLOAD_COMPLETED:
                ...

        # 任务结束时停止
        await watchdog.stop()
    """

    def __init__(
        self,
        page: Page,
        context: BrowserContext,
        log_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        downloads_dir: str = "downloads",
    ):
        self.page = page
        self.context = context
        self._log_fn = log_fn
        self._downloads_dir = downloads_dir
        self._events: list[WatchdogEvent] = []
        self._running = False
        self._handlers_registered = False

        # 下载追踪
        self._active_downloads: dict[str, Download] = {}

        # 网络空闲检测
        self._pending_requests: set[str] = set()
        self._last_network_activity: float = time.time()
        self._network_idle_threshold: float = 2.0  # 2秒无网络活动视为空闲

        # CAPTCHA 检测（基于 URL/DOM 关键词）
        self._captcha_keywords = [
            "captcha", "recaptcha", "hcaptcha", "turnstile",
            "challenge", "verify", "人机验证", "验证码",
        ]

        # 控制台错误收集
        self._console_errors: list[str] = []

    async def _log(self, msg: str):
        if self._log_fn:
            await self._log_fn(msg)

    def _emit(self, event_type: EventType, **data):
        """发射事件到队列。"""
        event = WatchdogEvent(type=event_type, data=data)
        self._events.append(event)

    def drain_events(self) -> list[WatchdogEvent]:
        """消费所有未处理的事件，返回后清空队列。"""
        events = [e for e in self._events if not e.handled]
        for e in events:
            e.handled = True
        self._events = [e for e in self._events if not e.handled]
        return events

    def peek_events(self, event_type: EventType = None) -> list[WatchdogEvent]:
        """查看未处理的事件（不消费）。"""
        events = [e for e in self._events if not e.handled]
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events

    def has_event(self, event_type: EventType) -> bool:
        """检查是否有指定类型的未处理事件。"""
        return any(e.type == event_type and not e.handled for e in self._events)

    # ── 事件处理器 ──────────────────────────────────────────────────────

    def _on_download(self, download: Download):
        """下载开始事件。"""
        url = download.url
        suggested = download.suggested_filename
        self._active_downloads[url] = download
        self._emit(EventType.DOWNLOAD_STARTED, url=url, filename=suggested)

        # 异步等待下载完成
        async def _wait_download():
            try:
                path = await download.path()
                self._emit(
                    EventType.DOWNLOAD_COMPLETED,
                    url=url,
                    filename=suggested,
                    path=str(path) if path else None,
                )
                await self._log(f"  [Watchdog] 下载完成: {suggested}")
            except Exception as e:
                await self._log(f"  [Watchdog] 下载失败: {suggested} — {e}")
            finally:
                self._active_downloads.pop(url, None)

        asyncio.ensure_future(_wait_download())

    def _on_dialog(self, dialog: Dialog):
        """浏览器弹窗事件（alert/confirm/prompt/beforeunload）。"""
        self._emit(
            EventType.DIALOG_APPEARED,
            dialog_type=dialog.type,
            message=dialog.message,
            default_value=dialog.default_value,
        )

        # 自动处理：accept alert/confirm，dismiss beforeunload
        async def _handle_dialog():
            try:
                if dialog.type == "beforeunload":
                    await dialog.accept()
                elif dialog.type == "alert":
                    await self._log(f"  [Watchdog] Alert: {dialog.message[:100]}")
                    await dialog.accept()
                elif dialog.type == "confirm":
                    await self._log(f"  [Watchdog] Confirm: {dialog.message[:100]} → 自动确认")
                    await dialog.accept()
                elif dialog.type == "prompt":
                    # prompt 类型不自动处理，留给主循环
                    await self._log(f"  [Watchdog] Prompt: {dialog.message[:100]} → 需要用户输入")
                else:
                    await dialog.dismiss()
            except Exception as e:
                await self._log(f"  [Watchdog] 处理弹窗失败: {e}")

        asyncio.ensure_future(_handle_dialog())

    def _on_crash(self):
        """页面崩溃事件。"""
        self._emit(EventType.PAGE_CRASHED)
        asyncio.ensure_future(self._log("  [Watchdog] ⚠ 页面崩溃！"))

    def _on_new_page(self, new_page: Page):
        """新标签页打开事件。"""
        self._emit(EventType.NEW_TAB, url=new_page.url)

    def _on_request(self, request):
        """网络请求开始。"""
        try:
            if request.resource_type in ("fetch", "xhr", "websocket"):
                self._pending_requests.add(request.url)
                self._last_network_activity = time.time()
        except Exception:
            pass

    def _on_response(self, response):
        """网络响应。"""
        try:
            self._pending_requests.discard(response.url)
            self._last_network_activity = time.time()
        except Exception:
            pass

    def _on_request_failed(self, request):
        """网络请求失败。"""
        try:
            self._pending_requests.discard(request.url)
        except Exception:
            pass

    def _on_console(self, msg):
        """控制台消息（只收集 error 级别）。"""
        if msg.type == "error":
            text = msg.text[:200]
            self._console_errors.append(text)
            # 只在积累到一定数量时发射事件，避免噪音
            if len(self._console_errors) >= 5:
                self._emit(
                    EventType.CONSOLE_ERROR,
                    errors=self._console_errors.copy(),
                    count=len(self._console_errors),
                )
                self._console_errors.clear()

    # ── CAPTCHA 检测（基于 DOM 内容） ──────────────────────────────────

    async def check_captcha(self) -> bool:
        """
        主动检测当前页面是否有 CAPTCHA。
        基于 URL 和 DOM 关键词匹配，不调用 LLM。
        返回 True 表示检测到 CAPTCHA。
        """
        try:
            url = self.page.url.lower()
            if any(kw in url for kw in self._captcha_keywords):
                self._emit(EventType.CAPTCHA_DETECTED, source="url", url=self.page.url)
                return True

            # 检查 iframe src（reCAPTCHA/hCaptcha 通常在 iframe 中）
            captcha_found = await self.page.evaluate("""() => {
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    const src = (iframe.src || '').toLowerCase();
                    if (src.includes('captcha') || src.includes('recaptcha') ||
                        src.includes('hcaptcha') || src.includes('turnstile') ||
                        src.includes('challenge')) {
                        return src;
                    }
                }
                // 检查页面中的验证码相关元素
                const selectors = [
                    '.g-recaptcha', '#recaptcha', '[data-sitekey]',
                    '.h-captcha', '.cf-turnstile',
                    'img[src*="captcha"]', 'img[alt*="验证码"]',
                ];
                for (const sel of selectors) {
                    if (document.querySelector(sel)) return sel;
                }
                return null;
            }""")

            if captcha_found:
                self._emit(EventType.CAPTCHA_DETECTED, source="dom", selector=captcha_found)
                await self._log(f"  [Watchdog] 检测到 CAPTCHA: {captcha_found}")
                return True

        except Exception:
            pass

        return False

    # ── 网络空闲检测 ──────────────────────────────────────────────────

    @property
    def is_network_idle(self) -> bool:
        """当前网络是否空闲（无 pending 请求且超过阈值时间无活动）。"""
        if self._pending_requests:
            return False
        return (time.time() - self._last_network_activity) > self._network_idle_threshold

    @property
    def pending_request_count(self) -> int:
        return len(self._pending_requests)

    # ── 生命周期 ──────────────────────────────────────────────────────

    async def start(self):
        """注册所有事件监听器。"""
        if self._handlers_registered:
            return

        page = self.page
        context = self.context

        # 页面级事件
        page.on("crash", lambda: self._on_crash())
        page.on("download", self._on_download)
        page.on("dialog", self._on_dialog)
        page.on("console", self._on_console)

        # 网络事件
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

        # 上下文级事件（新标签页）
        context.on("page", self._on_new_page)

        self._handlers_registered = True
        self._running = True
        await self._log("  [Watchdog] 事件监听器已启动")

    async def stop(self):
        """停止监听（清理状态，但不移除 Playwright 事件监听器——它们随 page/context 销毁自动清理）。"""
        self._running = False
        self._events.clear()
        self._active_downloads.clear()
        self._pending_requests.clear()
        self._console_errors.clear()

    def get_status(self) -> dict:
        """返回 Watchdog 当前状态摘要。"""
        return {
            "running": self._running,
            "pending_events": len([e for e in self._events if not e.handled]),
            "active_downloads": len(self._active_downloads),
            "pending_requests": len(self._pending_requests),
            "network_idle": self.is_network_idle,
            "console_errors": len(self._console_errors),
        }
