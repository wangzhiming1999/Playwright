import asyncio
import base64
import json
import os
from pathlib import Path

from playwright.async_api import Page
from page_annotator import get_element_coords, get_last_elements

from .page_utils import _safe_print, _wait_for_page_ready
from .llm_helpers import robust_json_loads
from .action_registry import is_custom_action, execute_custom_action
from utils import llm_chat as _llm_chat


class BrowserAgent:
    def __init__(self, page: Page, screenshots_dir: Path, log_fn=None, screenshot_callback=None, task_id=None):
        self.page = page
        self.screenshots_dir = screenshots_dir
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._log_fn = log_fn  # async callable(msg) or None
        self._screenshot_callback = screenshot_callback
        self._task_id = task_id
        self._active_requests: set = set()  # 由外部主循环注入，供 wait 工具使用
        self._nav_baseline_ms: float | None = None  # 首次导航耗时基准（ms），用于自适应超时
        self._active_frame = None  # switch_iframe 设置的当前 frame

    def get_active_page(self):
        """返回当前活跃的页面或 iframe frame，供标注和截图使用。"""
        return self._active_frame if self._active_frame else self.page

    async def screenshot_base64(self, quality: int = 92, full_page: bool = False) -> str:
        """截当前页面，返回 base64 字符串供 GPT 分析"""
        try:
            data = await self.page.screenshot(type="jpeg", quality=quality, full_page=full_page)
            return base64.b64encode(data).decode()
        except Exception as e:
            await self._log(f"  ⚠ 截图失败: {e}")
            return ""

    async def _safe_evaluate(self, expression: str, timeout_ms: int = 5000, default=None):
        """带超时的 page.evaluate，防止页面卡死时挂起"""
        try:
            return await asyncio.wait_for(
                self.page.evaluate(expression),
                timeout=timeout_ms / 1000
            )
        except asyncio.TimeoutError:
            await self._log(f"  ⚠ evaluate 超时 ({timeout_ms}ms): {expression[:80]}")
            return default
        except Exception as e:
            await self._log(f"  ⚠ evaluate 失败: {e}")
            return default

    async def _log(self, msg: str):
        _safe_print(msg)
        if self._log_fn:
            await self._log_fn(msg)

    def _validate_index(self, index: int) -> str | None:
        """校验 index 是否在有效范围内，返回错误消息或 None。"""
        elements = get_last_elements()
        if not elements:
            return None  # 没有元素列表时不校验
        max_idx = max(el.get("index", 0) for el in elements)
        if index < 0 or index > max_idx:
            return (
                f"操作失败: index={index} 超出有效范围 0~{max_idx}。"
                "请查看截图中的蓝色编号，使用有效的 index 重试。"
            )
        return None

    async def _click_and_wait(self, x: int, y: int, check_navigation: bool = True) -> str:
        """
        点击坐标并等待页面就绪。
        如果 check_navigation=True，检测 URL 变化并等待内容稳定。
        """
        url_before = self.page.url if check_navigation else None
        await self.page.mouse.click(x, y)

        result = await _wait_for_page_ready(
            self.page,
            log_fn=self._log,
            timeout_ms=15000,
            check_network=True,
            active_requests=self._active_requests
        )

        if check_navigation and url_before and self.page.url != url_before:
            await self._log(f"  [导航] URL 变化: {url_before} → {self.page.url}")

        return result

    async def _type_into_focused(self, text: str, press_enter: bool = False, is_password: bool = False) -> str:
        """
        在当前焦点元素输入文字（假设已经聚焦）。
        清空现有内容，输入新内容，可选按 Enter。
        如果按 Enter 触发导航，自动等待页面就绪。
        输入后验证是否成功写入（密码框跳过验证）。
        """
        # 清空现有内容
        await self.page.keyboard.press("Control+a")
        await self.page.keyboard.press("Delete")

        # 输入新内容
        await self.page.keyboard.type(text, delay=50)

        # 输入验证（密码框无法读取值，跳过）
        if not is_password and not press_enter:
            try:
                actual_value = await self._safe_evaluate(
                    "() => document.activeElement?.value || ''",
                    timeout_ms=2000, default=""
                )
                if actual_value and text not in actual_value and actual_value not in text:
                    await self._log(f"  ⚠ 输入验证失败: 期望含 '{text[:20]}', 实际 '{actual_value[:20]}'，尝试 fill")
                    # 重试：用 fill 方法（直接设置 value）
                    try:
                        focused = await self.page.evaluate_handle("() => document.activeElement")
                        el = focused.as_element()
                        if el:
                            await el.fill(text)
                            return f"已输入(fill): {text}"
                    except Exception:
                        pass  # fill 也失败，继续原始流程
            except Exception:
                pass

        # 可选：按 Enter 提交
        if press_enter:
            url_before = self.page.url
            await self.page.keyboard.press("Enter")

            # 等待页面就绪
            await _wait_for_page_ready(
                self.page,
                log_fn=self._log,
                timeout_ms=30000,  # Enter 提交可能触发长时间加载
                check_network=True,
                active_requests=self._active_requests
            )

            if self.page.url != url_before:
                await self._log(f"  [导航] Enter 后 URL 变化: {url_before} → {self.page.url}")

        if is_password:
            return "已输入密码"
        return f"已输入: {text}"

    async def dismiss_overlay(self):
        """每步操作前调用，自动关闭弹窗/遮罩/cookie 横幅。先试 selector，失败用 AI 视觉。"""
        # 常见关闭按钮 selector
        selectors = [
            "button[aria-label*='close' i]",
            "button[aria-label*='dismiss' i]",
            "[class*='modal'] button[class*='close' i]",
            "[class*='overlay'] button[class*='close' i]",
            "[class*='dialog'] button[class*='close' i]",
            "[role='dialog'] button[class*='close' i]",
            "[role='alertdialog'] button[class*='close' i]",
            "[class*='popup'] button[class*='close' i]",
            "[class*='cookie'] button",
            "[id*='cookie'] button",
            "[class*='banner'] button",
            "[class*='toast'] button[class*='close' i]",
            "[data-testid*='close' i]",
            "[data-dismiss]",
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=300):
                    await el.click(timeout=500)
                    await _wait_for_page_ready(self.page, log_fn=self._log, timeout_ms=3000, check_network=False)
                    await self._log("  [弹窗] selector 关闭成功")
                    return
            except Exception:
                pass  # selector 不存在或不可见，继续尝试下一个
        try:
            img = await self.screenshot_base64(quality=70)
            if not img:
                return
            resp = _llm_chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "页面上是否有【非登录表单的】弹窗、遮罩层或 cookie 横幅阻挡操作？\n"
                                "注意：登录框、注册框、搜索框不算弹窗，不要关闭它们。\n"
                                "只有明显的广告弹窗、cookie 提示、订阅弹窗、欢迎引导才需要关闭。\n"
                                '返回 JSON: {"has_overlay": true/false, "x": 关闭按钮X坐标或null, "y": 关闭按钮Y坐标或null, "reasoning": "简短说明"}'
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "high"}},
                    ],
                }],
                response_format={"type": "json_object"},
                max_tokens=150,
            )
            if not resp.choices:
                return
            data = robust_json_loads(resp.choices[0].message.content)
            if data.get("has_overlay") and data.get("x") is not None and data.get("y") is not None:
                await self._log(f"  [弹窗] AI识别: {data.get('reasoning', '')} → ({data['x']}, {data['y']})")
                try:
                    await self.page.mouse.click(data["x"], data["y"])
                    await _wait_for_page_ready(self.page, log_fn=self._log, timeout_ms=3000, check_network=False)
                    await self._log("  [弹窗] AI 关闭成功")
                except Exception as e:
                    await self._log(f"  [弹窗] AI 关闭失败: {e}")
        except Exception as e:
            await self._log(f"  [弹窗] AI fallback 失败: {e}")

    async def quick_dismiss(self) -> bool:
        """轻量弹窗检测+关闭：只做 JS 检测 + selector 匹配，不调 AI。耗时 <100ms。
        返回 True 表示关闭了弹窗。"""
        try:
            has_overlay = await self._safe_evaluate("""() => {
                const d = document.querySelector('[role="dialog"]:not([aria-hidden="true"])');
                const a = document.querySelector('[role="alertdialog"]:not([aria-hidden="true"])');
                const m = document.querySelector('[class*="modal"]:not([style*="display: none"])');
                const p = document.querySelector('[class*="popup"]:not([style*="display: none"])');
                const el = d || a || m || p;
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }""", timeout_ms=500, default=False)
            if not has_overlay:
                return False
        except Exception:
            return False

        quick_selectors = [
            "[role='dialog'] button[aria-label*='close' i]",
            "[role='alertdialog'] button[aria-label*='close' i]",
            "[class*='modal'] button[aria-label*='close' i]",
            "[class*='popup'] button[class*='close' i]",
            "[class*='modal'] button[class*='close' i]",
            "[class*='toast'] button[class*='close' i]",
            "[data-dismiss]",
        ]
        for sel in quick_selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=200):
                    await el.click(timeout=500)
                    await self._log("  [弹窗] quick_dismiss 关闭成功")
                    return True
            except Exception:
                pass
        return False

    async def _detect_form_errors(self) -> str:
        """检测页面上的表单验证错误，返回错误描述或空字符串。"""
        try:
            errors = await self._safe_evaluate("""() => {
                const results = [];
                // aria-invalid 标记
                document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
                    const label = el.getAttribute('aria-label') || el.name || el.id || el.type || '';
                    const msg = el.getAttribute('aria-errormessage');
                    const msgEl = msg ? document.getElementById(msg) : null;
                    const errText = msgEl ? msgEl.textContent.trim() : '';
                    results.push({ field: label, error: errText });
                });
                // 常见错误 class
                document.querySelectorAll('.error-message, .field-error, .invalid-feedback, [class*="error-msg"], [role="alert"]').forEach(el => {
                    const text = el.textContent.trim();
                    if (text && text.length < 200) {
                        results.push({ field: '', error: text });
                    }
                });
                return results.slice(0, 5);
            }""", timeout_ms=2000, default=[])
            if not errors:
                return ""
            parts = []
            for e in errors:
                field = e.get("field", "")
                err = e.get("error", "")
                if field and err:
                    parts.append(f"{err}({field})")
                elif err:
                    parts.append(err)
                elif field:
                    parts.append(f"{field}: 验证失败")
            if parts:
                return "⚠️ 检测到表单错误: " + ", ".join(parts)
            return ""
        except Exception:
            return ""

    async def _ai_validate(self, prompt: str) -> bool:
        """视觉验证：截图 + GPT 判断页面状态"""
        img = await self.screenshot_base64()
        if not img:
            await self._log("  [AI验证] 截图失败，跳过验证")
            return False
        try:
            resp = _llm_chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{prompt}\n返回 JSON: {{\"result\": true/false, \"reason\": \"简短说明\"}}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "low"}},
                    ],
                }],
                response_format={"type": "json_object"},
                max_tokens=150,
            )
            if not resp.choices:
                return False
            data = robust_json_loads(resp.choices[0].message.content)
            await self._log(f"  [AI验证] {data.get('reason', '')} → {data.get('result')}")
            return bool(data.get("result"))
        except Exception as e:
            await self._log(f"  [AI验证失败] {e}")
            return False

    async def _ai_act(self, prompt: str, input_text: str = None) -> str:
        """视觉操作：截图 + GPT 决定如何操作（基于坐标，不依赖 selector）"""
        img = await self.screenshot_base64()
        if not img:
            return "AI操作失败: 截图失败"

        # 视口 CSS 像素尺寸（mouse.click 用的是这个坐标系）
        viewport = self.page.viewport_size
        if not viewport:
            return "AI操作失败: 无法获取视口尺寸"
        width, height = viewport.get('width', 1920), viewport.get('height', 1080)

        try:
            task_desc = prompt
            if input_text:
                task_desc += f"\n要输入的内容: {input_text}"

            resp = _llm_chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"任务: {task_desc}\n"
                                f"浏览器视口: {width}x{height} CSS像素\n"
                                f"截图可能经过缩放，但坐标必须对应视口 CSS 像素（X: 0~{width}, Y: 0~{height}）\n"
                                "找到目标元素中心位置，返回 JSON:\n"
                                '{"action": "click"|"type", '
                                f'"x": X坐标(0~{width}), '
                                f'"y": Y坐标(0~{height}), '
                                '"text": "要输入的文字(action=type时填写，否则null)", '
                                '"reasoning": "说明元素在截图中的位置"}'
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "high"}},
                    ],
                }],
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            if not resp.choices:
                return "AI操作失败: 空响应"
            try:
                result = robust_json_loads(resp.choices[0].message.content)
            except (json.JSONDecodeError, ValueError) as e:
                return f"AI操作失败: JSON 解析错误 — {e}"
            await self._log(f"  [AI决策] {result.get('reasoning', '')} → {result.get('action')} at ({result.get('x')}, {result.get('y')})")

            action = result.get("action")
            x = result.get("x")
            y = result.get("y")
            text = result.get("text") or input_text

            if action == "click" and x is not None and y is not None:
                await self._click_and_wait(x, y)
                return f"AI执行: 点击坐标 ({x}, {y})"

            elif action == "type" and x is not None and y is not None and text:
                await self._click_and_wait(x, y, check_navigation=False)
                await self._type_into_focused(text)
                return f"AI执行: 在 ({x}, {y}) 输入文字"

            else:
                return f"AI 返回了无效的操作: {result}"

        except Exception as e:
            await self._log(f"  [AI操作失败] {e}")
            return f"AI操作失败: {e}"

    async def execute(self, tool_name: str, args: dict) -> str:
        """执行 GPT 决定的操作，返回结果描述"""
        page = self.page
        # 日志中不输出密码；get_credentials 不输出凭证内容
        log_args = dict(args)
        if tool_name == "type_text" and args.get("is_password"):
            log_args = {**args, "text": "***"}
        if tool_name == "get_credentials":
            await self._log(f"  → [get_credentials] site_key={args.get('site_key', '')}")
        else:
            await self._log(f"  → [{tool_name}] {log_args}")

        try:
            if tool_name == "navigate":
                url = args.get("url", "")
                if not url:
                    return "操作失败: url 参数不能为空"
                max_nav_retries = 3
                # 自适应超时：首次 30s，后续根据基准动态调整（上限 60s）
                nav_timeout = 30000
                if self._nav_baseline_ms is not None:
                    nav_timeout = min(60000, max(30000, int(self._nav_baseline_ms * 2)))
                for nav_attempt in range(max_nav_retries):
                    try:
                        _t0 = asyncio.get_event_loop().time()
                        await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
                        _elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000
                        self._active_requests.clear()  # 清除旧页面的残留请求
                        await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=15000, check_network=True, active_requests=self._active_requests)
                        # 记录首次导航耗时作为基准
                        if self._nav_baseline_ms is None:
                            self._nav_baseline_ms = _elapsed_ms
                            await self._log(f"  [导航基准] {_elapsed_ms:.0f}ms")
                        return f"已打开 {url}"
                    except Exception as e:
                        if nav_attempt < max_nav_retries - 1:
                            await self._log(f"  ⚠ 导航失败 (尝试 {nav_attempt+1}/{max_nav_retries}): {e}，1秒后重试...")
                            await asyncio.sleep(1)
                        else:
                            # 降级：只等待 commit（不等 domcontentloaded），适用于超慢页面
                            try:
                                await page.goto(url, wait_until="commit", timeout=15000)
                                self._active_requests.clear()
                                await asyncio.sleep(1)
                                return f"已打开 {url}（页面可能仍在加载）"
                            except Exception:
                                pass
                            return f"操作失败: 导航到 {url} 失败（已重试{max_nav_retries}次）— {e}"

            elif tool_name == "click":
                index = args.get("index")
                text = args.get("text")

                # 优先用回退链定位（skyvern-id → CSS → XPath → 缓存坐标）
                if index is not None:
                    idx_err = self._validate_index(index)
                    if idx_err:
                        return idx_err
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            method = el_info.get("method", "skyvern-id")
                            await self._log(f"  [点击] #{index} → ({x}, {y}) tag={el_info.get('tag','')} method={method}")

                            # 遮挡检测：3级递进恢复策略
                            is_covered = await self._safe_evaluate(
                                f"""() => {{
                                    const el = document.querySelector('[data-skyvern-id="{index}"]');
                                    if (!el) return {{covered: false, sticky: false}};
                                    const r = el.getBoundingClientRect();
                                    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                                    const top = document.elementFromPoint(cx, cy);
                                    const isCovered = top !== el && !el.contains(top) && !(top && top.contains(el));
                                    if (!isCovered) return {{covered: false, sticky: false}};
                                    let isSticky = false;
                                    try {{
                                        const s = window.getComputedStyle(top);
                                        isSticky = s.position === 'fixed' || s.position === 'sticky';
                                    }} catch(e) {{}}
                                    return {{covered: true, sticky: isSticky}};
                                }}""",
                                default={"covered": False, "sticky": False},
                            )
                            _cover_info = is_covered if isinstance(is_covered, dict) else {"covered": is_covered, "sticky": False}
                            if _cover_info.get("covered"):
                                # 第1级：scrollIntoView(center)
                                _scroll_block = "'nearest'" if _cover_info.get("sticky") else "'center'"
                                await self._log(f"  [点击] #{index} 被遮挡{'(sticky)' if _cover_info.get('sticky') else ''}，尝试滚动")
                                await self._safe_evaluate(
                                    f"""() => {{
                                        const el = document.querySelector('[data-skyvern-id="{index}"]');
                                        if (el) el.scrollIntoView({{block: {_scroll_block}, behavior: 'instant'}});
                                    }}"""
                                )
                                await asyncio.sleep(0.3)

                                # 检查是否仍被遮挡
                                _still_covered = await self._safe_evaluate(
                                    f"""() => {{
                                        const el = document.querySelector('[data-skyvern-id="{index}"]');
                                        if (!el) return true;
                                        const r = el.getBoundingClientRect();
                                        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                                        const top = document.elementFromPoint(cx, cy);
                                        return top !== el && !el.contains(top) && !(top && top.contains(el));
                                    }}""",
                                    default=False,
                                )
                                if _still_covered:
                                    # 第2级：偏移滚动（避开 sticky header/footer）
                                    await self._log(f"  [点击] #{index} 仍被遮挡，尝试偏移滚动")
                                    await self._safe_evaluate(
                                        f"""() => {{
                                            const el = document.querySelector('[data-skyvern-id="{index}"]');
                                            if (el) {{
                                                el.scrollIntoView({{block: 'nearest', behavior: 'instant'}});
                                                window.scrollBy(0, -100);
                                            }}
                                        }}"""
                                    )
                                    await asyncio.sleep(0.3)

                                    # 再次检查
                                    _still_covered2 = await self._safe_evaluate(
                                        f"""() => {{
                                            const el = document.querySelector('[data-skyvern-id="{index}"]');
                                            if (!el) return true;
                                            const r = el.getBoundingClientRect();
                                            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                                            const top = document.elementFromPoint(cx, cy);
                                            return top !== el && !el.contains(top) && !(top && top.contains(el));
                                        }}""",
                                        default=False,
                                    )
                                    if _still_covered2:
                                        # 第3级：force click（JS 直接触发 click 事件）
                                        await self._log(f"  [点击] #{index} 仍被遮挡，使用 force click")
                                        await self._safe_evaluate(
                                            f"""() => {{
                                                const el = document.querySelector('[data-skyvern-id="{index}"]');
                                                if (el) el.click();
                                            }}"""
                                        )
                                        form_err = await self._detect_form_errors()
                                        return f"点击成功(force)。{form_err}" if form_err else "点击成功(force)"

                                el_info = await get_element_coords(page, index)
                                if el_info:
                                    x, y = el_info["x"], el_info["y"]

                            await self._click_and_wait(x, y)
                            form_err = await self._detect_form_errors()
                            return f"点击成功。{form_err}" if form_err else "点击成功"
                        else:
                            await self._log(f"  #{index} 回退链全部失败，fallback 到文字")
                            if not text:
                                return f"操作失败: index={index} 定位失败且未提供 text，请重新截图后用新的 index 重试"
                    except Exception as e:
                        await self._log(f"  index点击失败: {e}")
                        if not text:
                            return f"操作失败: {e}"

                # fallback：用文字定位
                if text:
                    try:
                        el = page.get_by_text(text, exact=False).first
                        bbox = await el.bounding_box(timeout=10000)
                        if bbox:
                            x = int(bbox["x"] + bbox["width"] / 2)
                            y = int(bbox["y"] + bbox["height"] / 2)
                            await self._click_and_wait(x, y)
                        else:
                            await el.click(force=True, timeout=10000)
                            await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=15000, check_network=True, active_requests=self._active_requests)
                        form_err = await self._detect_form_errors()
                        return f"点击成功。{form_err}" if form_err else "点击成功"
                    except Exception as e:
                        await self._log(f"  文字点击失败 ({e})，尝试 AI 视觉...")
                        return await self._ai_act(f"点击 {text}")

                return "操作失败: 需要提供 index 或 text 参数"

            elif tool_name == "get_credentials":
                site_key = (args.get("site_key") or "").strip().upper().replace("-", "_")
                if not site_key:
                    return "site_key 不能为空"
                prefix = site_key  # 如 FELO_AI -> FELO_AI_EMAIL, FELO_AI_PASSWORD
                email_var = f"{prefix}_EMAIL"
                password_var = f"{prefix}_PASSWORD"
                email = os.environ.get(email_var, "").strip()
                password = os.environ.get(password_var, "").strip()
                if not email or not password:
                    return f"未配置登录凭证：请设置环境变量 {email_var} 和 {password_var}"
                await self._log("  ✓ 已获取该站点登录凭证（密码已脱敏）")
                return json.dumps({"email": email, "password": password}, ensure_ascii=False)

            elif tool_name == "type_text":
                annotation_index = args.get("index")
                text_to_type = args.get("text", "")
                press_enter = args.get("press_enter", False)
                is_password = args.get("is_password", False)

                if not text_to_type:
                    return "操作失败: text 参数不能为空"

                # 优先路径：用回退链定位（skyvern-id → CSS → XPath → 缓存坐标）
                if annotation_index is not None:
                    idx_err = self._validate_index(annotation_index)
                    if idx_err:
                        return idx_err
                    try:
                        el_info = await get_element_coords(page, annotation_index)
                        if el_info:
                            tag = el_info.get("tag", "").lower()
                            method = el_info.get("method", "skyvern-id")
                            x, y = el_info["x"], el_info["y"]

                            # 如果目标不是可输入元素，先点击检查焦点
                            if tag not in ("input", "textarea", "div", "span"):
                                await self._log(f"  ⚠ index #{annotation_index} 是 {tag}（method={method}），尝试点击后检查焦点")
                                await self._click_and_wait(x, y, check_navigation=False)
                                focused_tag = await page.evaluate("() => document.activeElement?.tagName?.toLowerCase() || ''")
                                focused_editable = await page.evaluate("() => document.activeElement?.isContentEditable || false")
                                if focused_tag in ("input", "textarea") or focused_editable:
                                    await self._log(f"  ✓ 点击后焦点落在 {focused_tag} 上，继续输入")
                                    return await self._type_into_focused(text_to_type, press_enter, is_password)
                                else:
                                    await self._log(f"  ⚠ 点击后焦点在 {focused_tag}，不是输入框，fallback 到 DOM 扫描")
                            else:
                                await self._log(f"  [输入] #{annotation_index} → ({x}, {y}) tag={tag} method={method}")
                                await self._click_and_wait(x, y, check_navigation=False)
                                return await self._type_into_focused(text_to_type, press_enter, is_password)
                        else:
                            await self._log(f"  #{annotation_index} 回退链全部失败，fallback 到 DOM 扫描")
                    except Exception as e:
                        await self._log(f"  index定位失败: {e}，fallback 到 DOM 扫描")

                # fallback：用 description + DOM 列表 + GPT 匹配
                description = args.get("description", "") or f"用于输入 '{text_to_type}' 的搜索框或输入框"

                try:
                    inputs_info = await page.evaluate("""() => {
                        const inputs = Array.from(document.querySelectorAll(
                            'input, textarea, [contenteditable="true"], [contenteditable=""]'
                        ));
                        return inputs
                            .filter(el => {
                                const r = el.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < window.innerHeight;
                            })
                            .map(el => {
                                const r = el.getBoundingClientRect();
                                return {
                                    type: el.type || el.tagName.toLowerCase(),
                                    placeholder: el.placeholder || el.getAttribute('data-placeholder') || '',
                                    name: el.name || '',
                                    id: el.id || '',
                                    label: el.getAttribute('aria-label') || el.getAttribute('aria-placeholder') || '',
                                    x: Math.round(r.left + r.width / 2),
                                    y: Math.round(r.top + r.height / 2),
                                };
                            });
                    }""")
                except Exception as e:
                    return f"操作失败: 获取输入框列表失败 — {e}"

                if not inputs_info:
                    await self._log("  [DOM] 未找到输入框，fallback 到 AI 视觉")
                    action_desc = f"点击搜索框并输入 '{text_to_type}'"
                    if press_enter:
                        action_desc += "，然后按 Enter 提交"
                    return await self._ai_act(action_desc)

                await self._log(f"  [DOM] 找到 {len(inputs_info)} 个输入框")

                resp = _llm_chat(
                    model="mini",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"需要找到: {description}\n"
                            f"页面输入框列表:\n{json.dumps(inputs_info, ensure_ascii=False)}\n"
                            "选择最匹配的输入框索引，返回 JSON:\n"
                            '{"index": 索引(0开始), "reasoning": "原因"}'
                        ),
                    }],
                    response_format={"type": "json_object"},
                    max_tokens=100,
                )
                if not resp.choices:
                    return "操作失败: AI 定位输入框失败（空响应）"
                try:
                    result = robust_json_loads(resp.choices[0].message.content)
                except (json.JSONDecodeError, ValueError) as e:
                    return f"操作失败: AI 返回无效 JSON — {e}"
                try:
                    idx = int(result.get("index", 0))
                except (ValueError, TypeError):
                    idx = 0
                await self._log(f"  [DOM定位] {result.get('reasoning', '')} → index={idx}")

                if idx < 0 or idx >= len(inputs_info):
                    idx = 0
                target = inputs_info[idx]
                x = target.get("x", 0)
                y = target.get("y", 0)
                await self._log(f"  [真实坐标] ({x}, {y}) type={target.get('type','')} placeholder={target.get('placeholder','')}")

                try:
                    await self._click_and_wait(x, y, check_navigation=False)
                    return await self._type_into_focused(text_to_type, press_enter, is_password)
                except Exception as e:
                    await self._log(f"  输入失败: {e}")
                    return f"输入失败: {e}"

            elif tool_name == "scroll":
                try:
                    amount = max(0, min(int(float(args.get("amount", 500))), 5000))
                except (ValueError, TypeError):
                    amount = 500
                direction_str = args.get("direction", "down")

                # 支持滚动到顶部/底部
                if direction_str == "top":
                    try:
                        await page.evaluate("() => window.scrollTo(0, 0)")
                        return "已滚动到页面顶部"
                    except Exception as e:
                        return f"操作失败: 滚动失败 — {e}"
                elif direction_str == "bottom":
                    try:
                        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                        return "已滚动到页面底部"
                    except Exception as e:
                        return f"操作失败: 滚动失败 — {e}"

                direction = 1 if direction_str == "down" else -1
                try:
                    # 记录滚动前状态
                    scroll_before = await self._safe_evaluate("() => window.scrollY", default=0)
                    content_before = await self._safe_evaluate(
                        "() => ({ len: (document.body.innerText || '').length, children: document.body.querySelectorAll('*').length })",
                        default={"len": 0, "children": 0}
                    )
                    await page.evaluate("(px) => window.scrollBy(0, px)", direction * amount)
                    await asyncio.sleep(0.3)
                    scroll_after = await self._safe_evaluate("() => window.scrollY", default=0)

                    if scroll_before == scroll_after:
                        # 到达边界，但检查是否有新内容在加载
                        boundary = "底部" if direction_str == "down" else "顶部"
                        for _ in range(6):  # 最多再等 3s (6 × 0.5s)
                            await asyncio.sleep(0.5)
                            content_after = await self._safe_evaluate(
                                "() => ({ len: (document.body.innerText || '').length, children: document.body.querySelectorAll('*').length })",
                                default={"len": 0, "children": 0}
                            )
                            has_spinner = await self._safe_evaluate(
                                "() => !!(document.querySelector('.loading, .spinner, [class*=\"skeleton\"], [aria-busy=\"true\"]'))",
                                default=False
                            )
                            if content_after.get("len", 0) != content_before.get("len", 0) or \
                               content_after.get("children", 0) != content_before.get("children", 0):
                                content_before = content_after
                                continue  # 内容还在变化，继续等
                            if has_spinner:
                                continue  # 有 loading 指示器，继续等
                            break  # 内容稳定且无 spinner
                        else:
                            return f"已到达页面{boundary}，但内容仍在加载中，建议稍后再检查"
                        # 再次检查是否有新内容
                        content_final = await self._safe_evaluate(
                            "() => (document.body.innerText || '').length",
                            default=0
                        )
                        if content_final > content_before.get("len", 0):
                            return f"已到达页面{boundary}，新内容已加载完成"
                        return f"已到达页面{boundary}，无更多内容"
                    return f"已向{direction_str}滚动 {amount}px"
                except Exception as e:
                    return f"操作失败: 滚动失败 — {e}"

            elif tool_name == "wait":
                if args.get("wait_for_content_change"):
                    timeout_secs = args.get("timeout", 120)
                    await self._log(f"  [智能等待] 等待内容稳定（最多 {timeout_secs}s）...")
                    result_msg = await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=timeout_secs * 1000, check_network=True, active_requests=self._active_requests)
                    return result_msg
                elif args.get("selector"):
                    seconds = args.get("seconds", 10)
                    selector = args.get("selector", "")
                    try:
                        await page.wait_for_selector(selector, timeout=seconds * 1000)
                        return "元素已出现"
                    except Exception as e:
                        return f"操作失败: 等待元素 {selector} 超时 — {e}"
                else:
                    seconds = args.get("seconds", 2)
                    await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=seconds * 1000, check_network=True, active_requests=self._active_requests)
                    return "等待完成"

            elif tool_name == "wait_for_text":
                text_to_find = args.get("text", "")
                timeout_secs = args.get("timeout", 15)
                if not text_to_find:
                    return "操作失败: text 参数不能为空"
                try:
                    await page.get_by_text(text_to_find, exact=False).first.wait_for(
                        state="visible", timeout=timeout_secs * 1000
                    )
                    return f"Text '{text_to_find}' appeared on page"
                except Exception:
                    return f"Timeout: text '{text_to_find}' did not appear within {timeout_secs}s"

            elif tool_name == "screenshot":
                filename = args.get("filename", "screenshot.png")
                # 防止路径遍历攻击
                if ".." in filename or filename.startswith("/") or "\\" in filename:
                    return "操作失败: 文件名不合法"
                path = self.screenshots_dir / filename
                full = args.get("full_page", False)
                try:
                    await page.screenshot(path=str(path), full_page=full)
                    await self._log(f"  ✓ 截图已保存: {path}")
                    if self._screenshot_callback and self._task_id:
                        try:
                            await self._screenshot_callback(self._task_id, filename)
                        except Exception as e:
                            await self._log(f"  ⚠ 截图回调失败: {e}")
                    return f"截图保存至 {path}。如果任务要求的截图已完成，请立即调用 done 结束任务。"
                except Exception as e:
                    return f"操作失败: 截图失败 — {e}"

            elif tool_name == "get_page_html":
                selector = args.get("selector")
                try:
                    if selector:
                        html = await page.eval_on_selector(selector, "el => el.outerHTML")
                        return f"元素 HTML（已截取前2000字符）:\n{html[:2000]}"
                    else:
                        html = await page.evaluate("() => document.body.innerHTML")
                        return f"页面 body HTML（已截取前3000字符）:\n{html[:3000]}"
                except Exception as e:
                    return f"操作失败: 获取 HTML 失败 — {e}"

            elif tool_name == "press_key":
                key = args.get("key", "")
                modifiers = args.get("modifiers", [])
                if not key:
                    return "操作失败: key 参数不能为空"
                valid_modifiers = {"Control", "Shift", "Alt", "Meta"}
                modifiers = [m for m in modifiers if m in valid_modifiers]
                try:
                    for mod in modifiers:
                        await page.keyboard.down(mod)
                    await page.keyboard.press(key)
                    for mod in reversed(modifiers):
                        await page.keyboard.up(mod)
                    if modifiers:
                        combo = "+".join(modifiers + [key])
                        return f"已按下组合键 {combo}"
                    return f"已按下 {key}"
                except Exception as e:
                    for mod in reversed(modifiers):
                        try:
                            await page.keyboard.up(mod)
                        except Exception:
                            pass
                    return f"操作失败: 按键 {key} 失败 — {e}"

            elif tool_name == "extract":
                # 轻量信息提取：用 mini 模型从页面 HTML 中提取信息，不需要截图
                question = args.get("question", "")
                if not question:
                    return "操作失败: question 参数不能为空"
                try:
                    # 提取页面文本内容（限制长度避免 token 爆炸）
                    page_text = await self._safe_evaluate(
                        "() => document.body.innerText.substring(0, 5000)",
                        timeout_ms=5000,
                        default=""
                    )
                    if not page_text:
                        return "提取失败: 页面内容为空"

                    resp = _llm_chat(
                        model="mini",
                        messages=[{
                            "role": "user",
                            "content": (
                                f"页面内容：\n{page_text}\n\n"
                                f"问题：{question}\n\n"
                                "请根据页面内容回答问题。如果页面中没有相关信息，说明'未找到'。简洁回答。"
                            ),
                        }],
                        max_tokens=300,
                    )
                    if resp.choices:
                        answer = resp.choices[0].message.content.strip()
                        return f"提取结果: {answer}"
                    return "提取失败: API 返回空"
                except Exception as e:
                    return f"提取失败: {e}"

            elif tool_name == "done":
                # 主循环已在 done 前调用了 _wait_for_page_ready
                # 直接截图，避免重复等待导致超时
                final_path = self.screenshots_dir / "final_result.png"
                try:
                    # 增加重试机制，防止页面正在跳转时截图失败
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            await page.screenshot(path=str(final_path), full_page=True, timeout=10000)
                            await self._log(f"  ✓ 最终截图: {final_path.name}")
                            if self._screenshot_callback and self._task_id:
                                try:
                                    await self._screenshot_callback(self._task_id, final_path.name)
                                except Exception as e:
                                    await self._log(f"  ⚠ 截图回调失败: {e}")
                            break
                        except Exception as e:
                            if attempt < max_retries - 1:
                                await self._log(f"  ⚠ 最终截图失败 (尝试 {attempt+1}/{max_retries}): {e}，1秒后重试...")
                                await asyncio.sleep(1)
                            else:
                                await self._log(f"  ⚠ 最终截图失败 (已重试{max_retries}次): {e}")
                except Exception as e:
                    await self._log(f"  ⚠ 最终截图异常: {e}")

                # 长页面分段截图，确保用户能看到完整内容
                try:
                    scroll_height = await self._safe_evaluate("() => document.body.scrollHeight", default=0)
                    vp_h = page.viewport_size.get("height", 1080) if page.viewport_size else 1080
                    if scroll_height and scroll_height > vp_h * 1.5:
                        parts_count = min(int(scroll_height / vp_h) + 1, 5)
                        await self._log(f"  📄 长页面检测: {scroll_height}px，分 {parts_count} 段截图")
                        for i in range(parts_count):
                            y = i * vp_h
                            await self._safe_evaluate(f"window.scrollTo(0, {y})")
                            await asyncio.sleep(0.3)
                            part_path = self.screenshots_dir / f"final_part_{i+1}.png"
                            try:
                                await page.screenshot(path=str(part_path), timeout=10000)
                                if self._screenshot_callback and self._task_id:
                                    try:
                                        await self._screenshot_callback(self._task_id, part_path.name)
                                    except Exception:
                                        pass
                            except Exception as e:
                                await self._log(f"  ⚠ 分段截图 {i+1} 失败: {e}")
                        await self._safe_evaluate("window.scrollTo(0, 0)")
                except Exception as e:
                    await self._log(f"  ⚠ 分段截图异常: {e}")

                return "__DONE__"

            elif tool_name == "hover":
                index = args.get("index")
                text = args.get("text")

                if index is not None:
                    idx_err = self._validate_index(index)
                    if idx_err:
                        return idx_err
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            method = el_info.get("method", "skyvern-id")
                            await self._log(f"  [悬停] #{index} → ({x}, {y}) method={method}")
                            await page.mouse.move(x, y)
                            await asyncio.sleep(0.5)
                            return f"已悬停在 #{index} ({x}, {y})"
                        elif not text:
                            return f"操作失败: index={index} 定位失败且未提供 text"
                    except Exception as e:
                        if not text:
                            return f"操作失败: {e}"

                if text:
                    try:
                        el = page.get_by_text(text, exact=False).first
                        bbox = await el.bounding_box(timeout=10000)
                        if bbox:
                            x = int(bbox["x"] + bbox["width"] / 2)
                            y = int(bbox["y"] + bbox["height"] / 2)
                            await page.mouse.move(x, y)
                            await asyncio.sleep(0.5)
                            return f"已悬停在 '{text}'"
                        else:
                            await el.hover(timeout=10000)
                            return f"已悬停在 '{text}'"
                    except Exception as e:
                        return f"操作失败: hover '{text}' 失败 — {e}"

                return "操作失败: 需要提供 index 或 text 参数"

            elif tool_name == "right_click":
                index = args.get("index")
                text = args.get("text")

                if index is not None:
                    idx_err = self._validate_index(index)
                    if idx_err:
                        return idx_err
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            method = el_info.get("method", "skyvern-id")
                            await self._log(f"  [右键] #{index} → ({x}, {y}) method={method}")
                            await page.mouse.click(x, y, button="right")
                            await asyncio.sleep(0.3)
                            return f"右键点击成功 #{index} ({x}, {y})"
                        elif not text:
                            return f"操作失败: index={index} 定位失败且未提供 text"
                    except Exception as e:
                        if not text:
                            return f"操作失败: {e}"

                if text:
                    try:
                        el = page.get_by_text(text, exact=False).first
                        bbox = await el.bounding_box(timeout=10000)
                        if bbox:
                            x = int(bbox["x"] + bbox["width"] / 2)
                            y = int(bbox["y"] + bbox["height"] / 2)
                            await page.mouse.click(x, y, button="right")
                            await asyncio.sleep(0.3)
                            return f"右键点击成功 '{text}'"
                        else:
                            return f"操作失败: 元素 '{text}' 无法获取位置"
                    except Exception as e:
                        return f"操作失败: right_click '{text}' 失败 — {e}"

                return "操作失败: 需要提供 index 或 text 参数"

            elif tool_name == "switch_iframe":
                iframe_index = args.get("index", 0)
                if iframe_index == 0:
                    # 回到主页面
                    self._active_frame = None
                    await self._log("  [iframe] 已切换回主页面")
                    return "已切换回主页面"
                try:
                    el_info = await get_element_coords(page, iframe_index)
                    if not el_info:
                        return f"操作失败: index={iframe_index} 定位失败"
                    # 尝试通过 skyvern-id 或坐标找到 iframe 元素
                    iframe_el = None
                    sid = el_info.get("skyvern_id")
                    if sid:
                        iframe_el = await page.query_selector(f"[skyvern-id='{sid}']")
                    if not iframe_el:
                        # 通过坐标找最近的 iframe
                        x, y = el_info["x"], el_info["y"]
                        iframe_el = await page.evaluate_handle(
                            f"document.elementFromPoint({x}, {y})"
                        )
                        iframe_el = iframe_el.as_element()
                    if not iframe_el:
                        return f"操作失败: 无法定位 iframe 元素 #{iframe_index}"
                    frame = await iframe_el.content_frame()
                    if not frame:
                        return f"操作失败: 元素 #{iframe_index} 不是 iframe"
                    self._active_frame = frame
                    await self._log(f"  [iframe] 已切换到 iframe #{iframe_index}, url={frame.url[:80]}")
                    return f"已切换到 iframe #{iframe_index}"
                except Exception as e:
                    return f"操作失败: switch_iframe 失败 — {e}"

            elif tool_name == "select_option":
                index = args.get("index")
                value = args.get("value", "")

                if index is None:
                    return "操作失败: 需要提供 index 参数"
                idx_err = self._validate_index(index)
                if idx_err:
                    return idx_err

                try:
                    el_info = await get_element_coords(page, index)
                    if not el_info:
                        return f"操作失败: index={index} 定位失败"

                    method = el_info.get("method", "skyvern-id")
                    await self._log(f"  [选择] #{index} method={method}")

                    selected = False

                    # 尝试用 Playwright 的 select_option（优先用 css_selector 回退）
                    selector = f'[data-skyvern-id="{index}"]'
                    try:
                        await page.select_option(selector, value=value, timeout=5000)
                        selected = True
                    except Exception:
                        pass

                    # fallback: 用 label 匹配
                    if not selected:
                        try:
                            await page.select_option(selector, label=value, timeout=5000)
                            selected = True
                        except Exception:
                            pass

                    # fallback: 点击 select 后点击选项文字
                    if not selected:
                        x, y = el_info["x"], el_info["y"]
                        await self._click_and_wait(x, y, check_navigation=False)
                        await asyncio.sleep(0.3)
                        try:
                            option_el = page.get_by_text(value, exact=False).first
                            await option_el.click(timeout=5000)
                            selected = True
                        except Exception as e:
                            return f"操作失败: 选择 '{value}' 失败 — {e}"

                    if selected:
                        # 等待 DOM 稳定（级联选择场景：选择后其他下拉框选项会动态更新）
                        await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=3000, check_network=False)
                        return f"已选择 '{value}'（页面可能有联动更新，请观察后再操作下一个选择框）"

                except Exception as e:
                    return f"操作失败: select_option 失败 — {e}"

            elif tool_name == "switch_tab":
                tab_index = args.get("tab_index")
                url_contains = args.get("url_contains", "")

                pages = page.context.pages
                if not pages:
                    return "操作失败: 没有可用的标签页"

                target = None
                if url_contains:
                    for p in pages:
                        if url_contains.lower() in p.url.lower():
                            target = p
                            break
                    if not target:
                        return f"操作失败: 未找到 URL 包含 '{url_contains}' 的标签页"
                elif tab_index is not None:
                    if 0 <= tab_index < len(pages):
                        target = pages[tab_index]
                    else:
                        return f"操作失败: tab_index={tab_index} 超出范围 (共 {len(pages)} 个标签页)"
                else:
                    return "操作失败: 需要提供 tab_index 或 url_contains"

                await target.bring_to_front()
                self.page = target
                await _wait_for_page_ready(target, log_fn=self._log, timeout_ms=5000, check_network=False)
                return f"已切换到标签页: {target.url}"

            elif tool_name == "ask_user":
                question = args.get("question", "")
                reason = args.get("reason", "")
                if not question:
                    return "操作失败: question 参数不能为空"
                return f"__ASK_USER__:{question}::{reason}"

            elif tool_name == "upload_file":
                file_path = args.get("file_path", "")
                index = args.get("index")

                if not file_path:
                    return "操作失败: file_path 参数不能为空"

                # 验证文件存在
                from pathlib import Path
                file_obj = Path(file_path)
                if not file_obj.exists():
                    return f"操作失败: 文件不存在 — {file_path}"
                if not file_obj.is_file():
                    return f"操作失败: 路径不是文件 — {file_path}"

                # 定位文件上传元素
                if index is not None:
                    try:
                        el_info = await get_element_coords(page, index)
                        if not el_info:
                            return f"操作失败: index={index} 定位失败"
                        method = el_info.get("method", "skyvern-id")
                        await self._log(f"  [上传] #{index} method={method} file={file_obj.name}")
                    except Exception as e:
                        return f"操作失败: 定位上传元素失败 — {e}"

                # 使用 Playwright 的 set_input_files
                try:
                    selector = f'[data-skyvern-id="{index}"]' if index is not None else 'input[type="file"]'
                    await page.set_input_files(selector, str(file_obj.absolute()), timeout=10000)
                    await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=5000, check_network=True, active_requests=self._active_requests)
                    return f"已上传文件: {file_obj.name}"
                except Exception as e:
                    return f"操作失败: 上传文件失败 — {e}"

            elif tool_name == "download_file":
                index = args.get("index")
                text = args.get("text")
                timeout_sec = args.get("timeout", 30)

                # 启动下载监听
                download_info = {"download": None, "path": None}

                async def handle_download(download):
                    download_info["download"] = download
                    try:
                        # 等待下载完成
                        path = await download.path()
                        download_info["path"] = str(path)
                        await self._log(f"  [下载] 完成: {download.suggested_filename} → {path}")
                    except Exception as e:
                        await self._log(f"  [下载] 失败: {e}")

                page.once("download", handle_download)

                # 点击下载按钮
                if index is not None:
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            method = el_info.get("method", "skyvern-id")
                            await self._log(f"  [下载] 点击 #{index} method={method}")
                            await page.mouse.click(x, y)
                        elif not text:
                            return f"操作失败: index={index} 定位失败且未提供 text"
                    except Exception as e:
                        if not text:
                            return f"操作失败: {e}"

                if text and not download_info["download"]:
                    try:
                        el = page.get_by_text(text, exact=False).first
                        await el.click(timeout=10000)
                    except Exception as e:
                        return f"操作失败: 点击下载按钮失败 — {e}"

                # 等待下载完成
                try:
                    for _ in range(timeout_sec * 2):  # 每 0.5 秒检查一次
                        if download_info["path"]:
                            return f"下载完成: {download_info['path']}"
                        await asyncio.sleep(0.5)
                    return f"操作失败: 下载超时（{timeout_sec}秒）"
                except Exception as e:
                    return f"操作失败: 等待下载失败 — {e}"

            elif tool_name == "drag_drop":
                from_index = args.get("from_index")
                to_index = args.get("to_index")
                to_x = args.get("to_x")
                to_y = args.get("to_y")

                if from_index is None:
                    return "操作失败: 需要提供 from_index 参数"

                # 定位起点
                try:
                    from_info = await get_element_coords(page, from_index)
                    if not from_info:
                        return f"操作失败: from_index={from_index} 定位失败"
                    from_x, from_y = from_info["x"], from_info["y"]
                except Exception as e:
                    return f"操作失败: 定位起点失败 — {e}"

                # 定位终点
                if to_index is not None:
                    try:
                        to_info = await get_element_coords(page, to_index)
                        if not to_info:
                            return f"操作失败: to_index={to_index} 定位失败"
                        to_x, to_y = to_info["x"], to_info["y"]
                    except Exception as e:
                        return f"操作失败: 定位终点失败 — {e}"
                elif to_x is None or to_y is None:
                    return "操作失败: 需要提供 to_index 或 (to_x, to_y)"

                # 执行拖拽
                try:
                    await self._log(f"  [拖拽] ({from_x}, {from_y}) → ({to_x}, {to_y})")
                    await page.mouse.move(from_x, from_y)
                    await page.mouse.down()
                    await asyncio.sleep(0.1)
                    await page.mouse.move(to_x, to_y, steps=10)
                    await asyncio.sleep(0.1)
                    await page.mouse.up()
                    await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=3000, check_network=False)
                    return f"拖拽完成: ({from_x}, {from_y}) → ({to_x}, {to_y})"
                except Exception as e:
                    return f"操作失败: 拖拽失败 — {e}"

            elif tool_name == "solve_captcha":
                input_index = args.get("input_index")
                captcha_index = args.get("captcha_index")

                if input_index is None:
                    return "操作失败: 需要提供 input_index 参数"

                # 截取验证码图片区域（如果指定了 captcha_index）
                captcha_img = None
                if captcha_index is not None:
                    try:
                        el_info = await get_element_coords(page, captcha_index)
                        if el_info:
                            # 截取验证码元素区域
                            selector = f'[data-skyvern-id="{captcha_index}"]'
                            try:
                                el = page.locator(selector)
                                screenshot_bytes = await el.screenshot(type="jpeg", quality=90, timeout=5000)
                                captcha_img = base64.b64encode(screenshot_bytes).decode()
                                await self._log(f"  [验证码] 已截取元素 #{captcha_index} 区域")
                            except Exception:
                                await self._log(f"  [验证码] 元素截图失败，使用全页截图")
                    except Exception as e:
                        await self._log(f"  [验证码] 定位验证码元素失败: {e}")

                # 如果没有截取到局部图，用全页截图
                if not captcha_img:
                    captcha_img = await self.screenshot_base64(quality=90)
                    if not captcha_img:
                        return "操作失败: 截图失败"

                # 用 AI 识别验证码
                try:
                    resp = _llm_chat(
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "图片中有一个验证码（CAPTCHA），请识别其中的文字或数字。\n"
                                        "只返回验证码内容本身，不要加任何解释。\n"
                                        "如果看不清或无法识别，返回 UNKNOWN。"
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{captcha_img}", "detail": "high"}},
                            ],
                        }],
                        max_tokens=50,
                    )
                    if not resp.choices:
                        return "操作失败: AI 识别验证码失败（空响应）"
                    captcha_text = resp.choices[0].message.content.strip()
                    await self._log(f"  [验证码] AI 识别结果: {captcha_text}")

                    if captcha_text == "UNKNOWN" or not captcha_text:
                        return "操作失败: 无法识别验证码，请用 ask_user 让用户手动输入"

                    # 点击输入框并输入验证码
                    el_info = await get_element_coords(page, input_index)
                    if not el_info:
                        return f"操作失败: input_index={input_index} 定位失败"
                    x, y = el_info["x"], el_info["y"]
                    await self._click_and_wait(x, y, check_navigation=False)
                    await self._type_into_focused(captcha_text)
                    return f"已输入验证码: {captcha_text}"

                except Exception as e:
                    return f"操作失败: 验证码识别失败 — {e}"

            elif tool_name == "get_totp_code":
                site_key = (args.get("site_key") or "").strip().upper().replace("-", "_")
                if not site_key:
                    return "操作失败: site_key 不能为空"

                secret_var = f"{site_key}_TOTP_SECRET"
                secret = os.environ.get(secret_var, "").strip()
                if not secret:
                    return f"未配置 TOTP 密钥：请设置环境变量 {secret_var}"

                try:
                    import pyotp
                    totp = pyotp.TOTP(secret)
                    code = totp.now()
                    await self._log(f"  [TOTP] 已生成 {site_key} 验证码")
                    return json.dumps({"code": code}, ensure_ascii=False)
                except Exception as e:
                    return f"操作失败: TOTP 生成失败 — {e}"

            elif tool_name == "find_element":
                description = args.get("description", "")
                element_type = args.get("element_type", "any")
                if not description:
                    return "操作失败: description 参数不能为空"

                img = await self.screenshot_base64(quality=90)
                if not img:
                    return "操作失败: 截图失败"

                viewport = self.page.viewport_size
                if not viewport:
                    return "操作失败: 无法获取视口尺寸"
                width, height = viewport.get('width', 1920), viewport.get('height', 1080)

                type_hint = ""
                if element_type == "image":
                    type_hint = "目标是一张图片（img 标签或 background-image）。"
                elif element_type == "text":
                    type_hint = "目标是一段文字内容。"
                elif element_type == "button":
                    type_hint = "目标是一个按钮或可点击元素。"
                elif element_type == "link":
                    type_hint = "目标是一个链接。"

                try:
                    resp = _llm_chat(
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"在截图中找到以下元素：{description}\n"
                                        f"{type_hint}\n"
                                        f"浏览器视口: {width}x{height} CSS像素\n"
                                        "仔细观察截图，找到目标元素的中心位置。\n"
                                        "如果找到了，返回坐标；如果截图中没有这个元素，返回 found=false。\n"
                                        '返回 JSON: {"found": true/false, '
                                        f'"x": X坐标(0~{width}), '
                                        f'"y": Y坐标(0~{height}), '
                                        '"reasoning": "描述元素在截图中的位置和外观"}'
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "high"}},
                            ],
                        }],
                        response_format={"type": "json_object"},
                        max_tokens=300,
                    )
                    if not resp.choices:
                        return "操作失败: AI 视觉定位失败（空响应）"
                    result = robust_json_loads(resp.choices[0].message.content)
                    found = result.get("found", False)
                    reasoning = result.get("reasoning", "")
                    await self._log(f"  [find_element] {'找到' if found else '未找到'}: {reasoning}")

                    if not found:
                        return f"未找到元素: {description}。建议：向下滚动页面后重试，或检查描述是否准确。"

                    x, y = result.get("x", 0), result.get("y", 0)

                    # 尝试获取该坐标处的元素信息
                    try:
                        el_info = await self.page.evaluate(f"""(coords) => {{
                            const el = document.elementFromPoint(coords.x, coords.y);
                            if (!el) return null;
                            const tag = el.tagName.toLowerCase();
                            const info = {{
                                tag: tag,
                                src: el.src || el.currentSrc || '',
                                alt: el.alt || '',
                                href: el.href || '',
                                text: (el.textContent || '').trim().substring(0, 100),
                            }};
                            // 检查 background-image
                            if (!info.src) {{
                                try {{
                                    const bg = window.getComputedStyle(el).backgroundImage;
                                    if (bg && bg !== 'none' && bg.startsWith('url(')) {{
                                        info.src = bg.slice(5, -2).replace(/['"]/g, '');
                                        info.tag = 'div(bg-image)';
                                    }}
                                }} catch(e) {{}}
                            }}
                            // 检查父元素是否是链接
                            const parent = el.closest('a');
                            if (parent) info.href = parent.href || '';
                            return info;
                        }}""", {"x": x, "y": y})
                    except Exception:
                        el_info = None

                    info_str = f"坐标: ({x}, {y})"
                    if el_info:
                        if el_info.get("src"):
                            info_str += f", src={el_info['src'][:200]}"
                        if el_info.get("alt"):
                            info_str += f", alt={el_info['alt']}"
                        if el_info.get("href"):
                            info_str += f", href={el_info['href'][:200]}"
                        info_str += f", tag={el_info.get('tag', '')}"

                    return f"找到元素: {description}。{info_str}。你可以用 click(index=...) 点击它，或用 save_element/download_url 下载。"

                except Exception as e:
                    return f"操作失败: 视觉定位失败 — {e}"

            elif tool_name == "save_element":
                index = args.get("index")
                filename = args.get("filename", "saved_element.png")

                if index is None:
                    return "操作失败: 需要提供 index 参数"
                if ".." in filename or filename.startswith("/") or "\\" in filename:
                    return "操作失败: 文件名不合法"

                save_path = self.screenshots_dir / filename

                try:
                    el_info = await get_element_coords(page, index)
                    if not el_info:
                        return f"操作失败: index={index} 定位失败"

                    # 获取元素的 src（图片 URL）
                    src = await page.evaluate(f"""() => {{
                        const el = document.querySelector('[data-skyvern-id="{index}"]');
                        if (!el) return null;
                        const tag = el.tagName.toLowerCase();
                        if (tag === 'img') return el.src || el.currentSrc || null;
                        if (tag === 'video') return el.poster || el.src || null;
                        // background-image
                        try {{
                            const bg = window.getComputedStyle(el).backgroundImage;
                            if (bg && bg !== 'none' && bg.startsWith('url(')) {{
                                return bg.slice(5, -2).replace(/['"]/g, '');
                            }}
                        }} catch(e) {{}}
                        return null;
                    }}""")

                    if src:
                        # 有 URL，通过浏览器上下文下载（继承 cookies）
                        await self._log(f"  [save_element] 下载图片: {src[:100]}")
                        try:
                            resp = await page.context.request.get(src)
                            if resp.ok:
                                body = await resp.body()
                                save_path.write_bytes(body)
                                await self._log(f"  ✓ 已保存: {save_path} ({len(body)} bytes)")
                                if self._screenshot_callback and self._task_id:
                                    try:
                                        await self._screenshot_callback(self._task_id, filename)
                                    except Exception:
                                        pass
                                return f"已保存元素到 {save_path}（{len(body)} bytes）"
                            else:
                                await self._log(f"  ⚠ HTTP 下载失败: {resp.status}，fallback 到元素截图")
                        except Exception as e:
                            await self._log(f"  ⚠ 下载失败: {e}，fallback 到元素截图")

                    # fallback: 对元素区域截图
                    selector = f'[data-skyvern-id="{index}"]'
                    try:
                        el = page.locator(selector)
                        screenshot_bytes = await el.screenshot(type="png", timeout=10000)
                        save_path.write_bytes(screenshot_bytes)
                        await self._log(f"  ✓ 元素截图已保存: {save_path} ({len(screenshot_bytes)} bytes)")
                        if self._screenshot_callback and self._task_id:
                            try:
                                await self._screenshot_callback(self._task_id, filename)
                            except Exception:
                                pass
                        return f"已截图保存元素到 {save_path}（{len(screenshot_bytes)} bytes）"
                    except Exception as e:
                        return f"操作失败: 元素截图失败 — {e}"

                except Exception as e:
                    return f"操作失败: save_element 失败 — {e}"

            elif tool_name == "download_url":
                url = args.get("url", "")
                filename = args.get("filename", "download")

                if not url:
                    return "操作失败: url 参数不能为空"
                if ".." in filename or filename.startswith("/") or "\\" in filename:
                    return "操作失败: 文件名不合法"

                save_path = self.screenshots_dir / filename

                try:
                    await self._log(f"  [download_url] 下载: {url[:150]}")
                    resp = await page.context.request.get(url)
                    if resp.ok:
                        body = await resp.body()
                        save_path.write_bytes(body)
                        await self._log(f"  ✓ 已下载: {save_path} ({len(body)} bytes)")
                        if self._screenshot_callback and self._task_id:
                            try:
                                await self._screenshot_callback(self._task_id, filename)
                            except Exception:
                                pass
                        return f"已下载到 {save_path}（{len(body)} bytes）"
                    else:
                        return f"操作失败: HTTP {resp.status} — {url[:100]}"
                except Exception as e:
                    return f"操作失败: 下载失败 — {e}"

            elif tool_name == "scroll_to_text":
                text = args.get("text", "")
                if not text:
                    return "操作失败: text 参数不能为空"

                try:
                    # 用 JS 在整个文档中搜索文字并滚动到它
                    found = await page.evaluate(f"""(searchText) => {{
                        // 递归搜索所有文本节点
                        const walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT, null
                        );
                        let node;
                        while (node = walker.nextNode()) {{
                            if (node.textContent && node.textContent.includes(searchText)) {{
                                const el = node.parentElement;
                                if (el) {{
                                    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                                    return {{
                                        found: true,
                                        tag: el.tagName.toLowerCase(),
                                        text: el.textContent.trim().substring(0, 100),
                                    }};
                                }}
                            }}
                        }}
                        return {{ found: false }};
                    }}""", text)

                    if found and found.get("found"):
                        await asyncio.sleep(0.5)  # 等待平滑滚动完成
                        await self._log(f"  [scroll_to_text] 找到并滚动到: {found.get('text', '')[:50]}")
                        return f"已滚动到包含 '{text}' 的元素（{found.get('tag', '')}）"
                    else:
                        return f"未找到包含 '{text}' 的文字。建议：检查文字是否准确，或页面可能需要先加载更多内容。"

                except Exception as e:
                    return f"操作失败: scroll_to_text 失败 — {e}"

            # ── 日期选择器工具 ──────────────────────────────────────────
            elif tool_name == "set_date":
                index = args.get("index")
                date_str = args.get("date", "")
                if index is None:
                    return "操作失败: 需要提供 index 参数"
                if not date_str:
                    return "操作失败: 需要提供 date 参数��格式 YYYY-MM-DD）"
                idx_err = self._validate_index(index)
                if idx_err:
                    return idx_err

                try:
                    el_info = await get_element_coords(page, index)
                    if not el_info:
                        return f"操作失败: index={index} 定位失败"

                    selector = f'[data-skyvern-id="{index}"]'

                    # 方法1: 直接设置 value + 触发事件（适用于 input[type="date"] 和大部分组件）
                    try:
                        set_ok = await page.evaluate(f"""(sel) => {{
                            const el = document.querySelector(sel);
                            if (!el) return false;
                            // 尝试用 native input setter 绕过 React/Vue 的受控组件
                            const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            )?.set;
                            if (nativeSetter) {{
                                nativeSetter.call(el, '{date_str}');
                            }} else {{
                                el.value = '{date_str}';
                            }}
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return true;
                        }}""", selector)
                        if set_ok:
                            await asyncio.sleep(0.3)
                            return f"已设置日期 '{date_str}'"
                    except Exception:
                        pass

                    # 方法2: 点击 + 清空 + 键盘输入
                    x, y = el_info["x"], el_info["y"]
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.2)
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(date_str, delay=50)
                    await page.keyboard.press("Escape")  # 关闭可能弹出的日期选择器面板
                    await asyncio.sleep(0.3)
                    return f"已输入日期 '{date_str}'"

                except Exception as e:
                    return f"操作失败: set_date 失败 — {e}"

            # ── 站点理解工具 ──────────────────────────────────────────
            elif tool_name == "analyze_current_page":
                context_hint = args.get("context", "")
                try:
                    # 检查缓存
                    cached = getattr(self, '_site_analysis', None)
                    if cached and cached.get("analyzed_url") == page.url:
                        result = cached
                    else:
                        page_html = await self._safe_evaluate(
                            "() => document.documentElement.outerHTML",
                            timeout_ms=10000,
                            default=""
                        )
                        if not page_html:
                            return "分析失败: 无法获取页面 HTML"

                        screenshot_b64 = None
                        try:
                            raw = await page.screenshot(type="jpeg", quality=40)
                            screenshot_b64 = base64.b64encode(raw).decode()
                        except Exception:
                            pass

                        from site_understanding import analyze_site as _analyze_site
                        result = _analyze_site(
                            url=page.url,
                            html=page_html,
                            screenshot_b64=screenshot_b64,
                            product_context=context_hint,
                        )
                        self._site_analysis = result  # 缓存

                    # 格式化结果
                    parts = []
                    parts.append(f"站点: {result.get('site_name', '未知')} ({result.get('site_category', '未知')})")
                    if result.get("needs_login"):
                        parts.append("需要登录")
                    features = result.get("key_features_visible", [])
                    if features:
                        parts.append(f"可见功能: {', '.join(features[:5])}")
                    entry_points = result.get("entry_points", [])
                    if entry_points:
                        top = sorted(entry_points, key=lambda x: -x.get("priority", 0))[:5]
                        for ep in top:
                            parts.append(f"  入口: {ep.get('label', '')} → {ep.get('path', '')} (优先级{ep.get('priority', 0)})")
                    strategy = result.get("exploration_strategy", "")
                    if strategy:
                        parts.append(f"建议策略: {strategy}")
                    return "站点分析结果:\n" + "\n".join(parts)
                except Exception as e:
                    return f"分析失败: {e}"

            # ── 自定义 Action fallback ──────────────────────────────────
            elif is_custom_action(tool_name):
                return await execute_custom_action(
                    tool_name,
                    args,
                    page=page,
                    agent=self,
                    log_fn=self._log,
                )

        except Exception as e:
            return f"操作失败: {e}"

        return "未知操作"
