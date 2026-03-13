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
            "description": "点击页面上的元素，用 CSS selector 或可见文字定位",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "text": {"type": "string", "description": "元素的可见文字（与 selector 二选一）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "在输入框中输入文字。密码框请设置 is_password: true，避免日志泄露",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "text": {"type": "string", "description": "要输入的内容"},
                    "clear_first": {"type": "boolean", "description": "输入前是否先清空，默认 true"},
                    "press_enter": {"type": "boolean", "description": "输入后是否按 Enter，用于触发两步登录等场景"},
                    "is_password": {"type": "boolean", "description": "是否为密码，设为 true 时日志中不显示内容"},
                },
                "required": ["selector", "text"],
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

    async def screenshot_base64(self) -> str:
        """截当前页面，返回 base64 字符串供 GPT 分析"""
        data = await self.page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(data).decode()

    async def _log(self, msg: str):
        _safe_print(msg)
        if self._log_fn:
            await self._log_fn(msg)

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
                if args.get("text"):
                    await page.get_by_text(args["text"], exact=False).first.click(force=True, timeout=10000)
                else:
                    await page.click(args["selector"], force=True, timeout=10000)
                await page.wait_for_load_state("domcontentloaded")
                return "点击成功"

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
                clear = args.get("clear_first", True)
                if clear:
                    await page.fill(args["selector"], "")
                await page.type(args["selector"], args["text"], delay=20)
                if args.get("press_enter"):
                    await page.keyboard.press("Enter")
                if args.get("is_password"):
                    return "已输入密码"
                return f"已输入: {args['text']}"

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

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个网页操作助手。每次我会给你当前页面的截图，"
                    "你需要调用工具完成用户任务。\n"
                    "规则：\n"
                    "1. 每次只调用一个工具\n"
                    "2. 操作前先观察截图，确认元素存在再操作\n"
                    "3. 任务完成后先截图，再调用 done\n"
                    "4. 如果连续3次操作失败，调用 done 并说明原因\n"
                    "登录流程：若用户要求打开某站点并登录，先 navigate 打开该站，找到登录入口并点击；"
                    "再调用 get_credentials(site_key) 获取账号密码（site_key 用下划线，如 felo.ai 用 felo_ai），"
                    "根据返回的 JSON 用 type_text 填写邮箱和密码（密码框必须设 is_password: true），最后点击登录/提交。"
                ),
            },
            {
                "role": "user",
                "content": f"任务：{task}",
            },
        ]

        await _log(f"\n🚀 开始执行任务: {task}\n")
        max_steps = 35
        fail_count = 0
        client = _get_client()

        for step in range(max_steps):
            img_b64 = await agent.screenshot_base64()
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": f"第{step+1}步，当前页面截图："},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}},
                ],
            })

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

            if result == "__DONE__":
                summary = tool_args.get("summary", "任务完成")
                await _log(f"\n✅ {summary}")
                # 补齐其余 tool_call 的 response，避免 OpenAI 报错
                for tc in msg.tool_calls[1:]:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                break

            if "失败" in result:
                fail_count += 1
            else:
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
