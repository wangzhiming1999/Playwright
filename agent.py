"""
Playwright + GPT 通用网页操作 Agent
用法: python agent.py
"""

import asyncio
import base64
import json
import os
import platform
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK，打印 emoji/中文易报错，统一用 UTF-8
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _safe_print(msg: str) -> None:
    """避免 Windows GBK 下 print emoji/特殊字符报 UnicodeEncodeError"""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((msg + "\n").encode(enc, errors="replace"))
        sys.stdout.buffer.flush()

from dotenv import load_dotenv
from openai import OpenAI
from playwright.async_api import async_playwright, Page
from page_annotator import annotate_page, get_element_coords

load_dotenv()

def _get_client():
    proxy = os.getenv("USE_PROXY") and "http://127.0.0.1:7897"
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        http_client=__import__("httpx").Client(proxy=proxy) if proxy else None,
    )

# ── 工具定义（GPT 可调用的操作） ──────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "打开一个 URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的 URL，如 https://example.com"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "点击页面上的元素。优先用截图中的元素编号（index），也可以用可见文字（text）。不要猜 selector。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中元素的编号（红色数字标签），优先使用"},
                    "text": {"type": "string", "description": "元素的可见文字，当不确定 index 时使用"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "在输入框中输入文字。优先用截图中的元素编号（index）直接定位，比 description 更准确。密码框必须设 is_password: true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中输入框的编号（红色数字标签），优先使用，比 description 更准确"},
                    "description": {"type": "string", "description": "输入框的描述，如'邮箱输入框'、'密码框'、'搜索框'，当不确定 index 时使用"},
                    "text": {"type": "string", "description": "要输入的内容"},
                    "press_enter": {"type": "boolean", "description": "输入后是否按 Enter"},
                    "is_password": {"type": "boolean", "description": "是否为密码，设为 true 时日志中不显示内容"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_credentials",
            "description": "从环境变量获取某站点的登录账号和密码，用于登录流程。站点 key 示例：felo_ai 对应 FELO_AI_EMAIL、FELO_AI_PASSWORD",
            "parameters": {
                "type": "object",
                "properties": {
                    "site_key": {"type": "string", "description": "站点标识，如 felo_ai、github，对应环境变量 FELO_AI_EMAIL/FELO_AI_PASSWORD、GITHUB_EMAIL/GITHUB_PASSWORD"},
                },
                "required": ["site_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "滚动页面",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up"], "description": "滚动方向"},
                    "amount": {"type": "integer", "description": "滚动像素数，默认 500"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "等待页面内容稳定。提交搜索/AI生成任务后，必须用 wait_for_content_change=true 等待内容真正生成完毕，再截图。AI生成内容可能需要30-120秒，务必设置足够大的 timeout。",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "固定等待秒数，默认 2。内容变化场景请用 wait_for_content_change 代替"},
                    "selector": {"type": "string", "description": "等待某个元素出现（可选）"},
                    "wait_for_content_change": {"type": "boolean", "description": "等待页面主体内容开始变化并稳定（搜索结果加载、AI生成内容完成后用）。会先等内容开始出现，再等内容停止变化。"},
                    "timeout": {"type": "number", "description": "wait_for_content_change 的最长等待秒数，默认60。AI生成任务建议设为120。"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "截图并保存，任务完成时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "保存的文件名，如 result.png"},
                    "full_page": {"type": "boolean", "description": "是否截全页，默认 false"},
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_html",
            "description": "获取当前页面的 HTML 源码或某个元素的 outerHTML，用于分析页面结构、查找正确的 selector",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "可选，获取某个元素的 HTML；不填则返回整个 body 的 innerHTML"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "按下键盘按键，如 Enter、Tab、Escape 等",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "按键名称，如 Enter、Tab、Escape、ArrowDown"},
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "任务已完成，退出循环",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "任务完成的简短说明"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "当你缺少必要信息无法继续时，暂停并向用户提问。"
                "适用场景：需要登录但没有账号密码、任务描述不清楚、遇到验证码、"
                "需要用户做选择（如多个搜索结果）、需要确认敏感操作。"
                "不要用于可以自己判断的情况。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "向用户提出的问题，要具体说明缺少什么信息"},
                    "reason": {"type": "string", "description": "为什么需要这个信息，当前卡在哪一步"},
                },
                "required": ["question", "reason"],
            },
        },
    },
]


# ── 执行器 ────────────────────────────────────────────────────────────────────

async def _wait_for_navigation(page, timeout: int = 8000):
    """
    点击后等待页面稳定。
    策略：先等 domcontentloaded（快），再尝试 networkidle（有长轮询的页面会超时，忽略）。
    比直接用 networkidle 更可靠，不会被 WebSocket/SSE 卡住。
    """
    await asyncio.sleep(0.8)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass  # 有长轮询/SSE 的页面永远不会 idle，忽略超时


async def _wait_for_content_stable(page, log_fn=None, max_secs: int = 120, active_requests: set = None) -> str:
    """
    等待页面内容生成完成。
    双重判断：
    1. 网络层：监听活跃请求数（由外部传入），请求全部结束后再等 2 秒确认
    2. 内容层：innerText 长度连续 3 秒不变作为兜底
    如果页面本来就静止（无网络请求、无内容变化），快速返回。
    """
    async def _log(msg):
        if log_fn:
            await log_fn(msg)

    if active_requests is None:
        active_requests = set()

    await asyncio.sleep(1.0)  # 等提交后的请求开始发出

    initial_len = await page.evaluate("() => document.body.innerText.length")
    active = len(active_requests)
    await _log(f"  [wait_stable] 开始监听，初始长度={initial_len}，活跃请求={active}")

    poll = 0.5
    max_polls = int(max_secs / poll)
    prev_len = initial_len
    content_stable_count = 0
    network_idle_count = 0
    total = 0

    for _ in range(max_polls):
        await asyncio.sleep(poll)
        total += 1
        curr_len = await page.evaluate("() => document.body.innerText.length")
        delta = abs(curr_len - prev_len)
        active = len(active_requests)

        # 内容稳定计数
        if delta < 20:
            content_stable_count += 1
        else:
            content_stable_count = 0

        # 网络空闲计数
        if active == 0:
            network_idle_count += 1
        else:
            network_idle_count = 0

        if total % 10 == 0:
            await _log(f"  [wait_stable] {total*poll:.0f}s: 长度={curr_len} delta={delta} 活跃请求={active}")

        prev_len = curr_len

        # 网络空闲 2 秒 且 内容稳定 3 秒 → 完成
        if network_idle_count >= 4 and content_stable_count >= 6:
            # 如果从头到尾都没有变化，说明页面本来就是静态的
            if curr_len == initial_len and total <= 10:
                await _log("  [wait_stable] 页面静态，快速返回")
                return "页面静态，无需等待"
            await _log(f"  [wait_stable] ✓ 完成，长度={curr_len}，耗时{total*poll:.1f}s")
            return f"内容已稳定，长度={curr_len}"

    await _log(f"  [wait_stable] ⚠ 超时({max_secs}s)，长度={curr_len}")
    return f"等待超时，长度={curr_len}"


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

    async def screenshot_base64(self, quality: int = 92) -> str:
        """截当前页面，返回 base64 字符串供 GPT 分析"""
        data = await self.page.screenshot(type="jpeg", quality=quality)
        return base64.b64encode(data).decode()

    async def _log(self, msg: str):
        _safe_print(msg)
        if self._log_fn:
            await self._log_fn(msg)

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
                    await asyncio.sleep(0.4)
                    await self._log("  [弹窗] selector 关闭成功")
                    return
            except Exception:
                pass

        # fallback：AI 视觉判断
        try:
            img = await self.screenshot_base64(quality=70)
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
            data = json.loads(resp.choices[0].message.content)
            if data.get("has_overlay") and data.get("x") is not None and data.get("y") is not None:
                await self._log(f"  [弹窗] AI识别: {data.get('reasoning', '')} → ({data['x']}, {data['y']})")
                try:
                    await self.page.mouse.click(data["x"], data["y"])
                    await asyncio.sleep(0.5)
                    await self._log("  [弹窗] AI 关闭成功")
                except Exception as e:
                    await self._log(f"  [弹窗] AI 关闭失败: {e}")
        except Exception:
            pass

    async def _ai_validate(self, prompt: str) -> bool:
        """视觉验证：截图 + GPT 判断页面状态"""
        img = await self.screenshot_base64()
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
            data = json.loads(resp.choices[0].message.content)
            await self._log(f"  [AI验证] {data.get('reason', '')} → {data.get('result')}")
            return bool(data.get("result"))
        except Exception as e:
            await self._log(f"  [AI验证失败] {e}")
            return False

    async def _ai_act(self, prompt: str, input_text: str = None) -> str:
        """视觉操作：截图 + GPT 决定如何操作（基于坐标，不依赖 selector）"""
        img = await self.screenshot_base64()

        # 视口 CSS 像素尺寸（mouse.click 用的是这个坐标系）
        viewport = self.page.viewport_size
        width, height = viewport['width'], viewport['height']

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
            result = json.loads(resp.choices[0].message.content)
            await self._log(f"  [AI决策] {result.get('reasoning', '')} → {result.get('action')} at ({result.get('x')}, {result.get('y')})")

            action = result.get("action")
            x = result.get("x")
            y = result.get("y")
            text = result.get("text") or input_text

            if action == "click" and x is not None and y is not None:
                await self.page.mouse.click(x, y)
                await asyncio.sleep(0.5)
                return f"AI执行: 点击坐标 ({x}, {y})"

            elif action == "type" and x is not None and y is not None and text:
                # 先点击输入框聚焦
                await self.page.mouse.click(x, y)
                await asyncio.sleep(0.3)
                # 清空并输入
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Delete")
                await self.page.keyboard.type(text, delay=30)
                await asyncio.sleep(0.5)
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
                await page.goto(args["url"], wait_until="domcontentloaded", timeout=30000)
                await _wait_for_navigation(page)
                return f"已打开 {args['url']}"

            elif tool_name == "click":
                index = args.get("index")
                text = args.get("text")

                # 优先用 data-skyvern-id 精确定位（annotate_page 已打好标记）
                if index is not None:
                    try:
                        el_info = await get_element_coords(page, index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            await self._log(f"  [skyvern-id点击] #{index} → ({x}, {y}) tag={el_info['tag']}")
                            await page.mouse.click(x, y)
                            await _wait_for_navigation(page)
                            return "点击成功"
                        else:
                            await self._log(f"  skyvern-id #{index} 不存在或不可见，fallback 到文字")
                            if not text:
                                return f"操作失败: index={index} 不存在且未提供 text，请重新截图后用新的 index 重试"
                    except Exception as e:
                        await self._log(f"  index点击失败: {e}")
                        if not text:
                            return f"操作失败: {e}"

                # fallback：用文字定位
                if text:
                    try:
                        await page.get_by_text(text, exact=False).first.click(force=True, timeout=10000)
                        await _wait_for_navigation(page)
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

                # 优先路径：用 data-skyvern-id 精确定位，零歧义
                if annotation_index is not None:
                    try:
                        el_info = await get_element_coords(page, annotation_index)
                        if el_info:
                            x, y = el_info["x"], el_info["y"]
                            await self._log(f"  [skyvern-id输入] #{annotation_index} → ({x}, {y}) tag={el_info['tag']}")
                            await page.mouse.click(x, y)
                            await asyncio.sleep(0.4)
                            await page.keyboard.press("Control+a")
                            await page.keyboard.press("Delete")
                            await asyncio.sleep(0.2)
                            await page.keyboard.type(args["text"], delay=50)
                            await asyncio.sleep(0.4)
                            if args.get("press_enter"):
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(1.0)
                            if args.get("is_password"):
                                return "已输入密码"
                            return f"已输入: {args['text']}"
                        else:
                            await self._log(f"  skyvern-id #{annotation_index} 不存在，fallback 到 description")
                    except Exception as e:
                        await self._log(f"  index定位失败: {e}，fallback 到 description")

                # fallback：用 description + DOM 列表 + GPT 匹配
                description = args.get("description", "")
                if not description:
                    return "需要提供 index 或 description 来定位输入框"

                inputs_info = await page.evaluate("""() => {
                    const inputs = Array.from(document.querySelectorAll('input, textarea'));
                    return inputs
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < window.innerHeight;
                        })
                        .map(el => {
                            const r = el.getBoundingClientRect();
                            return {
                                type: el.type || 'text',
                                placeholder: el.placeholder || '',
                                name: el.name || '',
                                id: el.id || '',
                                label: el.getAttribute('aria-label') || '',
                                x: Math.round(r.left + r.width / 2),
                                y: Math.round(r.top + r.height / 2),
                            };
                        });
                }""")

                if not inputs_info:
                    return "页面上没有找到可见的输入框"

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
                result = json.loads(resp.choices[0].message.content)
                idx = int(result.get("index", 0))
                await self._log(f"  [DOM定位] {result.get('reasoning', '')} → index={idx}")

                if idx >= len(inputs_info):
                    idx = 0
                target = inputs_info[idx]
                x, y = target["x"], target["y"]
                await self._log(f"  [真实坐标] ({x}, {y}) type={target['type']} placeholder={target['placeholder']}")

                try:
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.4)
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Delete")
                    await asyncio.sleep(0.2)
                    await page.keyboard.type(args["text"], delay=50)
                    await asyncio.sleep(0.4)
                    if args.get("press_enter"):
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(1.0)
                    if args.get("is_password"):
                        return "已输入密码"
                    return f"已输入: {args['text']}"
                except Exception as e:
                    await self._log(f"  输入失败: {e}")
                    return f"输入失败: {e}"

            elif tool_name == "scroll":
                amount = args.get("amount", 500)
                direction = 1 if args["direction"] == "down" else -1
                await page.evaluate(f"window.scrollBy(0, {direction * amount})")
                return f"已向{args['direction']}滚动 {amount}px"

            elif tool_name == "wait":
                if args.get("wait_for_content_change"):
                    timeout_secs = args.get("timeout", 120)
                    await self._log(f"  [智能等待] 等待内容稳定（最多 {timeout_secs}s）...")
                    result_msg = await _wait_for_content_stable(page, log_fn=self._log, max_secs=timeout_secs, active_requests=self._active_requests)
                    return result_msg
                elif args.get("selector"):
                    seconds = args.get("seconds", 10)
                    await page.wait_for_selector(args["selector"], timeout=seconds * 1000)
                    return "元素已出现"
                else:
                    await asyncio.sleep(args.get("seconds", 2))
                    return "等待完成"

            elif tool_name == "screenshot":
                path = self.screenshots_dir / args["filename"]
                full = args.get("full_page", False)
                await page.screenshot(path=str(path), full_page=full)
                await self._log(f"  ✓ 截图已保存: {path}")
                if self._screenshot_callback and self._task_id:
                    try:
                        await self._screenshot_callback(self._task_id, args["filename"])
                    except Exception:
                        pass
                return f"截图保存至 {path}"

            elif tool_name == "get_page_html":
                selector = args.get("selector")
                if selector:
                    html = await page.eval_on_selector(selector, "el => el.outerHTML")
                    return f"元素 HTML（已截取前2000字符）:\n{html[:2000]}"
                else:
                    html = await page.evaluate("() => document.body.innerHTML")
                    return f"页面 body HTML（已截取前3000字符）:\n{html[:3000]}"

            elif tool_name == "press_key":
                await page.keyboard.press(args["key"])
                return f"已按下 {args['key']}"

            elif tool_name == "done":
                await _wait_for_navigation(page)
                final_path = self.screenshots_dir / "final_result.png"
                await page.screenshot(path=str(final_path), full_page=True)
                await self._log(f"  ✓ 最终截图: {final_path.name}")
                if self._screenshot_callback and self._task_id:
                    try:
                        await self._screenshot_callback(self._task_id, final_path.name)
                    except Exception:
                        pass
                return "__DONE__"

            elif tool_name == "ask_user":
                # 返回特殊标记，主循环负责暂停并等待用户回答
                return f"__ASK_USER__:{args['question']}::{args.get('reason', '')}"

        except Exception as e:
            return f"操作失败: {e}"

        return "未知操作"


# ── 任务分解 ──────────────────────────────────────────────────────────────────

def _decompose_task(client, task: str) -> list[dict]:
    """
    执行前把用户任务拆成有序步骤列表。
    每个步骤包含：
      - step: 步骤序号
      - action: 要做什么（简短描述，可能需要多个工具调用才能完成）
      - expected: 这个步骤全部完成后，页面的最终状态（粗粒度，不描述中间状态）
      - done_signal: 判断这步完成的关键特征（页面上能看到什么）
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
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
        data = json.loads(resp.choices[0].message.content)
        steps = data if isinstance(data, list) else data.get("steps", [])
        return steps
    except Exception:
        return []


# ── 预期验证 ──────────────────────────────────────────────────────────────────

async def _verify_step(client, page, expected: str, done_signal: str) -> tuple[bool, str]:
    """
    操作后截图，让 GPT 判断是否符合预期。
    返回 (是否成功, 实际观察到的情况描述)
    """
    try:
        data = await page.screenshot(type="jpeg", quality=70)
        img_b64 = base64.b64encode(data).decode()
        resp = client.chat.completions.create(
            model="gpt-4o",
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
        result = json.loads(resp.choices[0].message.content)
        return result.get("success", False), result.get("observation", ""), result.get("mismatch", "")
    except Exception as e:
        return False, "", str(e)


# ── 上下文压缩 ────────────────────────────────────────────────────────────────

def _compress_messages(messages: list, client, max_history: int = 16) -> list:
    """
    消息超出限制时，把中间的历史压缩成一条摘要，保留：
    - messages[0]: system prompt
    - messages[1]: 原始任务
    - 一条压缩摘要（assistant role）
    - 最近 max_history 条消息
    这样 GPT 不会忘记之前做了什么，同时不会撑爆 context。
    """
    if len(messages) <= max_history + 2:
        return messages

    # 要压缩的中间段（去掉 system + task + 最近 max_history 条）
    to_compress = messages[2: -max_history]
    if not to_compress:
        return messages

    # 提取文本内容用于摘要（跳过图片）
    history_text = []
    for m in to_compress:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            # 多模态消息，只取文本部分
            text_parts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts)
        if isinstance(content, str) and content.strip():
            history_text.append(f"[{role}] {content[:200]}")

    if not history_text:
        return messages[:2] + messages[-max_history:]

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    "以下是网页操作的历史记录，请用 2-4 句话总结已完成的操作和当前状态：\n\n"
                    + "\n".join(history_text[-30:])  # 最多取 30 条避免超 token
                ),
            }],
            max_tokens=200,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception:
        summary = f"已执行 {len(to_compress)//2} 步操作"

    summary_msg = {
        "role": "assistant",
        "content": f"[历史摘要] {summary}",
    }
    return messages[:2] + [summary_msg] + messages[-max_history:]


# ── 智能重试分析 ──────────────────────────────────────────────────────────────

def _analyze_failure(client, tool_name: str, tool_args: dict, error_result: str) -> str:
    """
    操作失败时，用 GPT 分析失败原因并给出下一步建议，
    注入到下一轮的 tool result 里，引导 GPT 换策略。
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
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
        return resp.choices[0].message.content.strip()
    except Exception:
        return ""


# ── GPT 决策循环 ──────────────────────────────────────────────────────────────

async def _find_chrome_user_data_dir() -> str | None:
    """自动检测系统上 Chrome/Edge 的 User Data 目录"""
    system = platform.system()
    home = Path.home()
    candidates = []
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates = [
            local / "Google/Chrome/User Data",
            local / "Microsoft/Edge/User Data",
            local / "Google/Chrome Beta/User Data",
        ]
    elif system == "Darwin":
        candidates = [
            home / "Library/Application Support/Google/Chrome",
            home / "Library/Application Support/Microsoft Edge",
        ]
    else:  # Linux
        candidates = [
            home / ".config/google-chrome",
            home / ".config/microsoft-edge",
            home / ".config/chromium",
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


async def run_agent(
    task: str,
    headless: bool = False,
    task_id: str = None,
    log_callback=None,
    cookies_path: str = "cookies.json",
    screenshots_dir: str = "screenshots",
    ask_user_callback=None,      # async (task_id, question, reason) -> str
    screenshot_callback=None,    # async (task_id, filename) -> None
    browser_mode: str = "builtin",  # "builtin" | "user_chrome" | "cdp"
    cdp_url: str = "http://localhost:9222",  # browser_mode="cdp" 时使用
    chrome_profile: str = None,  # browser_mode="user_chrome" 时指定 profile 名，默认 "Default"
):
    screenshots_dir = Path(screenshots_dir)

    async with async_playwright() as pw:
        # ── 三种浏览器模式 ────────────────────────────────────────────────────
        browser = None
        context = None

        if browser_mode == "cdp":
            # 连接用户正在运行的 Chrome（需要用 --remote-debugging-port=9222 启动）
            _safe_print(f"  [CDP] 连接 {cdp_url} ...")
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            # 复用已有的第一个 context（继承所有登录态）
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")

        elif browser_mode == "user_chrome":
            # 用用户的 Chrome Profile 启动，继承所有登录态
            user_data_dir = await _find_chrome_user_data_dir()
            if not user_data_dir:
                _safe_print("  [user_chrome] 未找到 Chrome Profile，降级为 builtin 模式")
                browser_mode = "builtin"
            else:
                profile = chrome_profile or "Default"
                _safe_print(f"  [user_chrome] 使用 Chrome Profile: {user_data_dir} / {profile}")
                # launch_persistent_context 直接返回 context，不返回 browser
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",          # 用系统安装的 Chrome，不是 Playwright 内置的
                    headless=False,            # 用户 Profile 模式必须有头
                    args=["--profile-directory=" + profile],
                    viewport={"width": 1280, "height": 800},
                    locale="zh-CN",
                    proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
                )

        if browser_mode == "builtin":
            # 默认模式：启动内置 Chromium
            browser = await pw.chromium.launch(
                headless=headless,
                proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

        # cookies_file 所有模式都需要定义（builtin 模式读写，其他模式只在需要时写）
        cookies_file = Path(cookies_path)

        # builtin 模式才需要手动加载 cookies
        if browser_mode == "builtin":
            if cookies_file.exists():
                cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
                _safe_print("✓ 已加载登录态")

            # 注入环境变量中的站点 token
            _felo_token = os.environ.get("FELO_AI_TOKEN", "").strip()
            if _felo_token:
                await context.add_cookies([{
                    "name": "felo-user-token",
                    "value": _felo_token,
                    "domain": "felo.ai",
                    "path": "/",
                }])
                _safe_print("✓ 已注入 felo-user-token")

        page = await context.new_page()

        async def _log(msg: str):
            _safe_print(msg)
            if log_callback and task_id:
                await log_callback(task_id, msg)

        client = _get_client()
        agent = BrowserAgent(page, screenshots_dir, log_fn=_log, client=client, screenshot_callback=screenshot_callback, task_id=task_id)

        # 多 tab 支持：监听新页面，自动切换到最新打开的 tab
        async def _on_new_page(new_page):
            await new_page.wait_for_load_state("domcontentloaded")
            agent.page = new_page
            await _log(f"  [新标签页] 已切换到: {new_page.url}")

        context.on("page", lambda p: asyncio.ensure_future(_on_new_page(p)))

        # 如果任务中包含明文账号密码，直接注入到 system prompt，避免 GPT 调用 get_credentials
        task_for_gpt = task
        _cred_hint = ""
        _email_match = re.search(r'账号[是为：:]\s*(\S+)', task)
        _pwd_match = re.search(r'密码[是为：:]\s*(\S+)', task)
        if _email_match and _pwd_match:
            _cred_hint = (
                f"\n用户已提供登录凭证：邮箱={_email_match.group(1)}，密码已知。"
                "直接用 type_text 填写，不需要调用 get_credentials。"
                "密码框必须设 is_password: true。"
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个网页操作助手。每次我会给你当前页面的截图，用视觉理解页面，调用工具完成用户任务。\n"
                    "核心原则：看截图判断当前状态，再决定下一步操作。\n"
                    "基本规则：\n"
                    "1. 每次只调用一个工具\n"
                    "2. 操作元素优先用截图中的红色 index 编号，比文字更准确\n"
                    "3. 操作失败时换个方式重试，不要直接 done 放弃——除非连续5次都失败\n"
                    "4. 任务全部完成后先截图，再调用 done\n"
                    "5. 遇到登录页面，继续完成登录，不要放弃\n"
                    "等待规则（重要）：\n"
                    "  - 点击提交/搜索/发送按钮后，如果任务要求等待生成结果，必须调用 wait(wait_for_content_change=true, timeout=120)\n"
                    "  - wait 会自动等待内容开始出现，再等内容停止变化，完成后再截图\n"
                    "  - 普通页面跳转（登录、导航）不需要调用 wait，系统已自动处理\n"
                    "登录规则：\n"
                    "  - 看到邮箱框填邮箱，看到密码框填密码，看到按钮就点\n"
                    "  - 两步登录（先邮箱后密码）：点继续后等新截图再填密码\n"
                    "  - 没有凭证时调用 get_credentials(site_key) 获取"
                    + _cred_hint
                ),
            },
            {
                "role": "user",
                "content": f"任务：{task_for_gpt}",
            },
        ]

        await _log(f"\n🚀 开始执行任务: {task}\n")
        max_steps = 35
        fail_count = 0

        # ── 任务分解 ──────────────────────────────────────────────────────────
        await _log("  [任务分解] 正在拆解任务步骤...")
        task_steps = _decompose_task(client, task)
        if task_steps:
            await _log(f"  [任务分解] 共 {len(task_steps)} 步：")
            for s in task_steps:
                await _log(f"    步骤{s['step']}: {s['action']}")
                await _log(f"           预期: {s['expected']}")
        else:
            await _log("  [任务分解] 分解失败，使用自由模式执行")

        # 把任务步骤列表格式化成提示文字，注入到每步的 user message 里
        steps_hint = ""
        if task_steps:
            steps_hint = "【任务步骤参考】\n" + "\n".join(
                f"  {s['step']}. {s['action']}（完成标志：{s['done_signal']}）"
                for s in task_steps
            ) + "\n按顺序完成以上步骤，每步完成后再进行下一步。\n"

        # 全程监听网络请求，供 _wait_for_content_stable 使用
        active_requests: set[str] = set()

        def _on_request(req):
            if req.resource_type in ("fetch", "xhr", "websocket"):
                active_requests.add(req.url)

        def _on_response(resp):
            active_requests.discard(resp.url)

        def _on_request_failed(req):
            active_requests.discard(req.url)

        agent.page.on("request", _on_request)
        agent.page.on("response", _on_response)
        agent.page.on("requestfailed", _on_request_failed)
        agent._active_requests = active_requests  # 注入到 agent，供 wait 工具使用

        for step in range(max_steps):
            # 每步都检查弹窗，确保不被遮挡
            await agent.dismiss_overlay()

            # 用标注截图：给所有可交互元素打红框+编号
            img_b64, elements = await annotate_page(agent.page)
            elements_summary = json.dumps(elements, ensure_ascii=False)

            # 保存标注截图，方便调试，并实时推送给前端
            debug_path = screenshots_dir / f"step_{step+1:02d}_annotated.jpg"
            debug_path.write_bytes(base64.b64decode(img_b64))
            await _log(f"  [截图] {debug_path.name}")
            if screenshot_callback and task_id:
                try:
                    await screenshot_callback(task_id, debug_path.name)
                except Exception:
                    pass

            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"第{step+1}步，当前页面截图（红框+编号标注了所有可交互元素）：\n"
                            f"{steps_hint}"
                            f"元素列表: {elements_summary}\n"
                            "根据截图判断当前状态，调用一个工具推进任务。"
                            "操作时用元素的 index 编号，不要猜 selector 或坐标。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}},
                ],
            })

            # 上下文压缩：超过 24 条时压缩中间历史为摘要，保留最近 16 条
            if len(messages) > 24:
                messages = _compress_messages(messages, client, max_history=16)
                await _log(f"  [上下文] 已压缩历史，当前 {len(messages)} 条消息")

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=TOOLS,
                tool_choice="required",
                max_tokens=1000,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                await _log("GPT 没有返回工具调用，结束")
                break

            tool_call = msg.tool_calls[0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            await _log(f"\n>>> step={step+1} tool={tool_name} args={json.dumps(tool_args, ensure_ascii=False)}")

            # done/screenshot 前强制等待内容稳定（主循环层面兜底，日志走主循环的 _log）
            if tool_name in ("done", "screenshot"):
                await _log("  [wait_stable] 执行前等待内容稳定...")
                wait_result = await _wait_for_content_stable(agent.page, log_fn=_log, max_secs=120, active_requests=active_requests)
                await _log(f"  [wait_stable] 结果: {wait_result}")

            result = await agent.execute(tool_name, tool_args)
            await _log(f"  result: {str(result)[:200]}")

            # navigate 后自动检查弹窗
            if tool_name == "navigate":
                await agent.dismiss_overlay()

            # click 成功后保存 cookies（不做 AI 验证，让 GPT 从下一步截图自己判断）
            if tool_name == "click" and not result.startswith("操作失败"):
                try:
                    cookies = await context.cookies()
                    cookies_file.write_text(
                        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass

            if result == "__DONE__":
                summary = tool_args.get("summary", "任务完成")
                await _log(f"\n✅ {summary}")
                for tc in msg.tool_calls[1:]:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                break

            # ── 处理 ask_user：暂停并等待用户回答 ──────────────────────────
            if result.startswith("__ASK_USER__:"):
                parts = result.split("::", 1)
                question = parts[0].replace("__ASK_USER__:", "")
                reason = parts[1] if len(parts) > 1 else ""

                await _log(f"\n❓ [等待用户输入] {question}")
                if reason:
                    await _log(f"   原因: {reason}")

                if ask_user_callback:
                    try:
                        user_answer = await ask_user_callback(task_id, question, reason)
                        await _log(f"   用户回答: {user_answer}")
                        result = f"用户回答: {user_answer}"
                    except Exception as e:
                        await _log(f"   ✗ 获取用户回答失败: {e}")
                        result = "用户未回答，任务终止"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        })
                        for tc in msg.tool_calls[1:]:
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                        break
                else:
                    await _log("   ✗ 未配置 ask_user_callback，任务终止")
                    result = "无法获取用户输入，任务终止"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
                    for tc in msg.tool_calls[1:]:
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                    break

            # 失败计数 + 智能重试分析
            is_failure = (
                result.startswith("操作失败") or
                result.startswith("AI操作失败") or
                result.startswith("AI 定位失败") or
                result.startswith("输入失败")
            )
            if is_failure:
                fail_count += 1
                await _log(f"  [失败计数] {fail_count}/5 — {result[:80]}")
                advice = _analyze_failure(client, tool_name, tool_args, result)
                if advice:
                    result += f"\n[建议] {advice}"
                    await _log(f"  [重试建议] {advice}")
            else:
                if fail_count > 0:
                    await _log(f"  [失败计数] 已重置（上次={fail_count}）")
                fail_count = 0

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

            # 补齐其余 tool_call 的 response
            for tc in msg.tool_calls[1:]:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})

            if fail_count >= 5:
                await _log("\n⚠️  连续5次失败，终止任务")
                break
        else:
            await _log("\n⚠️  达到最大步数限制")

        # builtin 模式保存 cookies；user_chrome/cdp 模式不需要（浏览器本身保存）
        if browser_mode == "builtin":
            try:
                cookies = await context.cookies()
                cookies_file = Path(cookies_path)
                cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                await _log(f"✓ 登录态已保存至 {cookies_path}")
            except Exception:
                pass

        # 关闭浏览器：CDP 模式不关闭（用户还在用），其他模式关闭
        if browser_mode == "cdp":
            await _log("  [CDP] 保持浏览器运行，不关闭")
        elif browser_mode == "user_chrome":
            await context.close()
        elif browser:  # builtin 模式
            await browser.close()


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Playwright + GPT 网页操作 Agent ===")
    print("输入任务描述，例如：")
    print("  打开 https://news.ycombinator.com 截图保存为 hn.png")
    print("  搜索 'playwright python' 并截图")
    print()

    task = input("请输入任务: ").strip()
    if not task:
        task = "打开 https://example.com，截图保存为 result.png"

    headless_input = input("是否无头模式运行？(y/N): ").strip().lower()
    headless = headless_input == "y"

    asyncio.run(run_agent(task, headless=headless))
