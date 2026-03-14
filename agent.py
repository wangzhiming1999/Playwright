"""
Playwright + GPT 通用网页操作 Agent
用法: python agent.py
"""

import asyncio
import base64
import json
import os
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
from page_annotator import annotate_page

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
            "description": "在输入框中输入文字。必须用 description 描述输入框（如'邮箱输入框'、'密码框'），系统会用 AI 视觉自动定位。不要猜测 selector。密码框必须设 is_password: true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "输入框的描述，如'邮箱输入框'、'密码框'、'搜索框'"},
                    "text": {"type": "string", "description": "要输入的内容"},
                    "press_enter": {"type": "boolean", "description": "输入后是否按 Enter"},
                    "is_password": {"type": "boolean", "description": "是否为密码，设为 true 时日志中不显示内容"},
                },
                "required": ["description", "text"],
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
            "description": "等待页面加载或某个元素出现",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "等待秒数，默认 2"},
                    "selector": {"type": "string", "description": "等待某个元素出现（可选）"},
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
]


# ── 执行器 ────────────────────────────────────────────────────────────────────

class BrowserAgent:
    def __init__(self, page: Page, screenshots_dir: Path, log_fn=None):
        self.page = page
        self.screenshots_dir = screenshots_dir
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._log_fn = log_fn  # async callable(msg) or None

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
            client = _get_client()
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
        client = _get_client()
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

        client = _get_client()
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
                return f"已打开 {args['url']}"

            elif tool_name == "click":
                index = args.get("index")
                text = args.get("text")

                # 优先用 index：从 DOM 获取真实坐标
                if index is not None:
                    try:
                        elements_info = await page.evaluate("""() => {
                            const selectors = [
                                'input:not([type="hidden"])', 'textarea', 'button',
                                'a[href]', 'select', '[role="button"]', '[onclick]',
                            ];
                            const seen = new Set();
                            const all = [];
                            selectors.forEach(sel => {
                                document.querySelectorAll(sel).forEach(el => {
                                    if (!seen.has(el)) { seen.add(el); all.push(el); }
                                });
                            });
                            return all
                                .filter(el => {
                                    const r = el.getBoundingClientRect();
                                    return r.width > 0 && r.height > 0 &&
                                           r.top >= 0 && r.top < window.innerHeight;
                                })
                                .map(el => {
                                    const r = el.getBoundingClientRect();
                                    return {
                                        x: Math.round(r.left + r.width / 2),
                                        y: Math.round(r.top + r.height / 2),
                                    };
                                });
                        }""")
                        if index < len(elements_info):
                            x, y = elements_info[index]["x"], elements_info[index]["y"]
                            await self._log(f"  [index点击] #{index} → ({x}, {y})")
                            await page.mouse.click(x, y)
                            await asyncio.sleep(1)
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                            except Exception:
                                pass
                            return "点击成功"
                    except Exception as e:
                        await self._log(f"  index点击失败: {e}")

                # fallback：用文字定位
                if text:
                    try:
                        await page.get_by_text(text, exact=False).first.click(force=True, timeout=10000)
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                        return "点击成功"
                    except Exception as e:
                        await self._log(f"  文字点击失败 ({e})，尝试 AI 视觉...")
                        return await self._ai_act(f"点击 {text}")

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
                description = args.get("description", "")
                if not description:
                    return "需要提供 description 描述输入框"

                # 用 JS 获取页面所有可见 input 的真实 DOM 坐标，100% 准确
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

                await self._log(f"  [DOM] 找到 {len(inputs_info)} 个输入框: {inputs_info}")

                # GPT 根据描述选择正确的 input（给真实坐标，不靠猜）
                client = _get_client()
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
                    await asyncio.sleep(0.5)  # 等待聚焦
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Delete")
                    await asyncio.sleep(0.2)
                    await page.keyboard.type(args["text"], delay=50)
                    await asyncio.sleep(0.5)  # 等待输入完成
                    if args.get("press_enter"):
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(1.0)  # 等待提交响应
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
                seconds = args.get("seconds", 2)
                if args.get("selector"):
                    await page.wait_for_selector(args["selector"], timeout=seconds * 1000)
                else:
                    await asyncio.sleep(seconds)
                return f"等待完成"

            elif tool_name == "screenshot":
                path = self.screenshots_dir / args["filename"]
                full = args.get("full_page", False)
                await page.screenshot(path=str(path), full_page=full)
                await self._log(f"  ✓ 截图已保存: {path}")
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
                return "__DONE__"

        except Exception as e:
            return f"操作失败: {e}"

        return "未知操作"


# ── GPT 决策循环 ──────────────────────────────────────────────────────────────

async def run_agent(
    task: str,
    headless: bool = False,
    task_id: str = None,
    log_callback=None,
    cookies_path: str = "cookies.json",
    screenshots_dir: str = "screenshots",
):
    screenshots_dir = Path(screenshots_dir)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        # 复用登录态：如果存在 cookies 文件则加载
        cookies_file = Path(cookies_path)
        if cookies_file.exists():
            cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
            await context.add_cookies(cookies)
            _safe_print("✓ 已加载登录态")

        # 注入环境变量中的站点 token（如 FELO_AI_TOKEN）
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

        agent = BrowserAgent(page, screenshots_dir, log_fn=_log)

        # 如果任务中包含明文账号密码，直接注入到 system prompt，避免 GPT 调用 get_credentials
        task_for_gpt = task
        inline_creds = None
        import re as _re
        _cred_hint = ""
        _email_match = _re.search(r'账号[是为：:]\s*(\S+)', task)
        _pwd_match = _re.search(r'密码[是为：:]\s*(\S+)', task)
        if _email_match and _pwd_match:
            inline_creds = {"email": _email_match.group(1), "password": _pwd_match.group(1)}
            _cred_hint = (
                f"\n用户已提供登录凭证：邮箱={inline_creds['email']}，密码已知。"
                "直接用 type_text 填写，不需要调用 get_credentials。"
                "密码框必须设 is_password: true。"
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个网页操作助手。每次我会给你当前页面的截图，用视觉理解页面，调用工具完成用户任务。\n"
                    "核心原则：优先用视觉理解页面，每步操作前仔细看截图确认当前状态。\n"
                    "规则：\n"
                    "1. 每次只调用一个工具\n"
                    "2. type_text 必须用 description 描述输入框，不要用 selector\n"
                    "3. click 优先用 text 参数描述按钮文字\n"
                    "4. 任务完成后先截图，再调用 done\n"
                    "5. 如果连续3次操作失败，调用 done 并说明原因\n"
                    "6. 遇到 401、403、需要登录等情况，不要放弃，继续执行登录流程\n"
                    "登录流程（重要）：\n"
                    "  - 每次操作后观察截图，判断当前在哪一步\n"
                    "  - 有些网站是两步登录：先输邮箱点继续，再输密码点登录\n"
                    "  - 有些网站是一步登录：邮箱和密码在同一页面\n"
                    "  - 看到邮箱框就填邮箱，看到密码框就填密码，看到登录/继续按钮就点击\n"
                    "  - 不要提前填写还没出现的输入框\n"
                    "  - 点击继续/下一步后，等待页面变化再操作\n"
                    "  - 若用户未提供凭证则先调用 get_credentials(site_key) 获取"
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
        client = _get_client()

        for step in range(max_steps):
            # 只在 navigate 后或操作失败后才检查弹窗
            if step == 0 or fail_count > 0:
                await agent.dismiss_overlay()

            # 用标注截图：给所有可交互元素打红框+编号
            img_b64, elements = await annotate_page(agent.page)
            elements_summary = json.dumps(elements, ensure_ascii=False)

            # 保存标注截图，方便调试
            debug_path = screenshots_dir / f"step_{step+1:02d}_annotated.jpg"
            import base64 as _b64
            debug_path.write_bytes(_b64.b64decode(img_b64))
            await _log(f"  [截图] {debug_path.name}")

            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"第{step+1}步，当前页面截图（红框+编号标注了所有可交互元素）：\n"
                            f"元素列表: {elements_summary}\n"
                            "操作时用元素的 index 编号，不要猜 selector 或坐标。\n"
                            "type_text 用 description 描述输入框，click 用 text 描述按钮文字。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}},
                ],
            })

            # 只保留最近 10 轮对话，避免 context 爆炸
            if len(messages) > 22:
                messages = messages[:2] + messages[-20:]

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

            result = await agent.execute(tool_name, tool_args)

            # navigate 后自动检查弹窗
            if tool_name == "navigate":
                await asyncio.sleep(1.5)
                await agent.dismiss_overlay()

            # 只在提交表单后验证（不在点击登录入口时验证）
            submit_keywords = ["submit", "sign in", "提交", "确认", "confirm"]
            is_submit = tool_name == "click" and any(kw in str(tool_args).lower() for kw in submit_keywords)
            if is_submit:
                await asyncio.sleep(3.5)
                is_success = await agent._ai_validate(
                    "表单提交是否成功？如果页面跳转到了新页面、或出现了成功提示则为成功。"
                    "如果页面还在原来的表单页面且有错误提示（如密码错误、账号不存在）则为失败。"
                    "注意：跳转到登录表单页面不算失败。"
                )
                if is_success:
                    result += " | AI验证: 提交成功"
                    cookies = await context.cookies()
                    cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                    await _log("  ✓ 登录态已保存")
                else:
                    result += " | AI验证: 提交可能失败，有错误提示"
                    fail_count += 1

            if result == "__DONE__":
                summary = tool_args.get("summary", "任务完成")
                await _log(f"\n✅ {summary}")
                # 补齐其余 tool_call 的 response，避免 OpenAI 报错
                for tc in msg.tool_calls[1:]:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                break

            # 只有操作本身报错才计入失败，AI验证失败不重复计数
            if result.startswith("操作失败") or result.startswith("AI操作失败") or result.startswith("AI 定位失败"):
                fail_count += 1
            elif not result.endswith("提交可能失败，有错误提示"):
                fail_count = 0

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

            # 补齐其余 tool_call 的 response（只执行第一个，其余跳过）
            for tc in msg.tool_calls[1:]:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})

            if fail_count >= 3:
                await _log("\n⚠️  连续3次失败，终止任务")
                break
        else:
            await _log("\n⚠️  达到最大步数限制")

        # 保存最终 cookies
        cookies = await context.cookies()
        cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        await _log(f"✓ 登录态已保存至 {cookies_path}")

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
