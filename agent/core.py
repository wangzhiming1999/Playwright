import asyncio
import base64
import json
import os
from pathlib import Path

from playwright.async_api import Page
from page_annotator import get_element_coords

from .page_utils import _safe_print, _wait_for_page_ready
from .llm_helpers import robust_json_loads


def _get_client():
    from utils import get_openai_client
    return get_openai_client()


class BrowserAgent:
    def __init__(self, page: Page, screenshots_dir: Path, log_fn=None, client=None, screenshot_callback=None, task_id=None):
        self.page = page
        self.screenshots_dir = screenshots_dir
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._log_fn = log_fn  # async callable(msg) or None
        self._client = client  # shared OpenAI client
        self._screenshot_callback = screenshot_callback
        self._task_id = task_id
        self._active_requests: set = set()  # 由外部主循环注入，供 wait 工具使用

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
        """
        # 清空现有内容
        await self.page.keyboard.press("Control+a")
        await self.page.keyboard.press("Delete")

        # 输入新内容
        await self.page.keyboard.type(text, delay=50)

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
            "[class*='cookie'] button",
            "[id*='cookie'] button",
            "[class*='banner'] button",
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
            client = self._client or _get_client()
            resp = client.chat.completions.create(
                model="gpt-4o",
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

    async def _ai_validate(self, prompt: str) -> bool:
        """视觉验证：截图 + GPT 判断页面状态"""
        img = await self.screenshot_base64()
        if not img:
            await self._log("  [AI验证] 截图失败，跳过验证")
            return False
        client = self._client or _get_client()
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
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
        width, height = viewport.get('width', 1280), viewport.get('height', 800)

        client = self._client or _get_client()
        try:
            task_desc = prompt
            if input_text:
                task_desc += f"\n要输入的内容: {input_text}"

            resp = client.chat.completions.create(
                model="gpt-4o",
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
                for nav_attempt in range(max_nav_retries):
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        self._active_requests.clear()  # 清除旧页面的残留请求
                        await _wait_for_page_ready(page, log_fn=self._log, timeout_ms=15000, check_network=True, active_requests=self._active_requests)
                        return f"已打开 {url}"
                    except Exception as e:
                        if nav_attempt < max_nav_retries - 1:
                            await self._log(f"  ⚠ 导航失败 (尝试 {nav_attempt+1}/{max_nav_retries}): {e}，1秒后重试...")
                            await asyncio.sleep(1)
                        else:
                            return f"操作失败: 导航到 {url} 失败（已重试{max_nav_retries}次）— {e}"

            elif tool_name == "click":
                index = args.get("index")
                text = args.get("text")

                # 优先用回退链定位（skyvern-id → CSS → XPath → 缓存坐标）
                if index is not None:
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            method = el_info.get("method", "skyvern-id")
                            await self._log(f"  [点击] #{index} → ({x}, {y}) tag={el_info.get('tag','')} method={method}")
                            await self._click_and_wait(x, y)
                            return "点击成功"
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
                        return "点击成功"
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

                client = self._client or _get_client()
                resp = client.chat.completions.create(
                    model="gpt-4o",
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
                direction = 1 if direction_str == "down" else -1
                try:
                    await page.evaluate("(px) => window.scrollBy(0, px)", direction * amount)
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
                if not key:
                    return "操作失败: key 参数不能为空"
                try:
                    await page.keyboard.press(key)
                    return f"已按下 {key}"
                except Exception as e:
                    return f"操作失败: 按键 {key} 失败 — {e}"

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

            elif tool_name == "select_option":
                index = args.get("index")
                value = args.get("value", "")

                if index is None:
                    return "操作失败: 需要提供 index 参数"

                try:
                    el_info = await get_element_coords(page, index)
                    if not el_info:
                        return f"操作失败: index={index} 定位失败"

                    method = el_info.get("method", "skyvern-id")
                    await self._log(f"  [选择] #{index} method={method}")

                    # 尝试用 Playwright 的 select_option（优先用 css_selector 回退）
                    selector = f'[data-skyvern-id="{index}"]'
                    try:
                        await page.select_option(selector, value=value, timeout=5000)
                        return f"已选择 '{value}'"
                    except Exception:
                        pass

                    # fallback: 用 label 匹配
                    try:
                        await page.select_option(selector, label=value, timeout=5000)
                        return f"已选择 '{value}'"
                    except Exception:
                        pass

                    # fallback: 点击 select 后点击选项文字
                    x, y = el_info["x"], el_info["y"]
                    await self._click_and_wait(x, y, check_navigation=False)
                    await asyncio.sleep(0.3)
                    try:
                        option_el = page.get_by_text(value, exact=False).first
                        await option_el.click(timeout=5000)
                        return f"已选择 '{value}'"
                    except Exception as e:
                        return f"操作失败: 选择 '{value}' 失败 — {e}"

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

        except Exception as e:
            return f"操作失败: {e}"

        return "未知操作"
