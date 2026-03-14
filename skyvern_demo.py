"""
Skyvern-style Demo
对比两种浏览器自动化方式：
  A) 传统 Playwright：依赖 CSS selector
  B) AI-native：用 GPT-4o 视觉理解页面，自然语言驱动操作

运行: python skyvern_demo.py
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from openai import OpenAI
from playwright.async_api import async_playwright, Page
from utils import get_openai_client

load_dotenv()


# ── AI-native page helpers (Skyvern-style interface) ──────────────────────────

def _get_client():
    return get_openai_client()


async def _screenshot_b64(page: Page) -> str:
    data = await page.screenshot(type="jpeg", quality=70)
    return base64.b64encode(data).decode()


async def ai_extract(page: Page, prompt: str, schema: dict = None) -> dict:
    """
    Skyvern-style page.extract()
    截图 + 让 GPT 从页面提取结构化数据
    """
    img = await _screenshot_b64(page)
    schema_hint = f"\n返回 JSON，字段: {list(schema.keys())}" if schema else "\n返回 JSON"
    client = _get_client()
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": f"{prompt}{schema_hint}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "low"}},
            ],
        }],
        response_format={"type": "json_object"},
        max_tokens=500,
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"raw": resp.choices[0].message.content}


async def ai_act(page: Page, prompt: str) -> str:
    """
    Skyvern-style page.act()
    截图 + 让 GPT 决定点哪里/输什么，返回 JS 操作
    """
    img = await _screenshot_b64(page)
    client = _get_client()
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"任务: {prompt}\n"
                        "分析截图，返回 JSON:\n"
                        '{"action": "click"|"type"|"press", "selector": "CSS selector 或 null", '
                        '"text": "要输入的文字或按键名，不需要则 null", '
                        '"reasoning": "简短说明"}'
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}", "detail": "high"}},
            ],
        }],
        response_format={"type": "json_object"},
        max_tokens=300,
    )
    result = json.loads(resp.choices[0].message.content)
    print(f"    AI 决策: {result.get('reasoning', '')} → {result.get('action')} {result.get('selector') or result.get('text', '')}")

    action = result.get("action")
    selector = result.get("selector")
    text = result.get("text")

    try:
        if action == "click" and selector:
            await page.click(selector, timeout=8000)
        elif action == "type" and selector and text:
            await page.fill(selector, text)
        elif action == "press" and text:
            await page.keyboard.press(text)
        await asyncio.sleep(1)
        return f"执行: {action}"
    except Exception as e:
        return f"执行失败: {e}"


async def ai_validate(page: Page, prompt: str) -> bool:
    """
    Skyvern-style page.validate()
    截图 + 让 GPT 判断页面状态，返回 True/False
    """
    img = await _screenshot_b64(page)
    client = _get_client()
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
    print(f"    AI 验证: {data.get('reason', '')} → {data.get('result')}")
    return bool(data.get("result"))


# ── Demo A: 传统 Playwright ────────────────────────────────────────────────────

async def demo_traditional(out_dir: Path):
    print("\n" + "="*55)
    print("A) 传统 Playwright（CSS selector）")
    print("="*55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        print("1. 打开 HackerNews...")
        await page.goto("https://news.ycombinator.com", timeout=30000)

        print("2. 用 selector 抓取前5条标题...")
        # 必须知道 HN 的 DOM 结构
        titles = await page.eval_on_selector_all(
            ".athing .titleline > a",
            "els => els.slice(0, 5).map(e => e.textContent.trim())"
        )
        for i, t in enumerate(titles, 1):
            print(f"   {i}. {t[:60]}")

        print("3. 点击第一条...")
        await page.locator(".athing .titleline > a").first.click()
        await asyncio.sleep(2)

        await page.screenshot(path=str(out_dir / "A_traditional.png"))
        print(f"   截图: A_traditional.png")

        await browser.close()
    print("✓ 传统方式完成")


# ── Demo B: AI-native（Skyvern 风格）─────────────────────────────────────────

async def demo_ai_native(out_dir: Path):
    print("\n" + "="*55)
    print("B) AI-native（Skyvern 风格，自然语言驱动）")
    print("="*55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        print("1. 打开 HackerNews...")
        await page.goto("https://news.ycombinator.com", timeout=30000)

        # ai_extract: 不需要知道 selector，直接问 AI
        print("2. AI 提取前5条标题（无需 selector）...")
        data = await ai_extract(
            page,
            "列出页面上前5条新闻的标题",
            schema={"titles": "list of strings"}
        )
        titles = data.get("titles", [])
        for i, t in enumerate(titles[:5], 1):
            print(f"   {i}. {str(t)[:60]}")

        # ai_act: 用自然语言描述要做什么
        print("3. AI 点击第一条新闻（自然语言指令）...")
        await ai_act(page, "点击页面上第一条新闻的标题链接")
        await asyncio.sleep(2)

        # ai_validate: 验证操作结果
        print("4. AI 验证是否成功跳转...")
        ok = await ai_validate(page, "页面是否已经离开 HackerNews 首页，跳转到了一篇文章或外部网站？")
        print(f"   跳转成功: {ok}")

        await page.screenshot(path=str(out_dir / "B_ai_native.png"))
        print(f"   截图: B_ai_native.png")

        await browser.close()
    print("✓ AI-native 方式完成")


# ── Demo C: 混合模式（推荐用法）──────────────────────────────────────────────

async def demo_hybrid(out_dir: Path):
    print("\n" + "="*55)
    print("C) 混合模式（稳定部分用 selector，动态部分用 AI）")
    print("="*55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        print("1. 打开 HackerNews...")
        await page.goto("https://news.ycombinator.com", timeout=30000)

        # 稳定的导航用 selector（快、省钱）
        print("2. 用 selector 点击 'new' 标签（稳定 selector）...")
        try:
            await page.click("a[href='newest']", timeout=5000)
            await asyncio.sleep(1.5)
            print("   ✓ selector 成功")
        except Exception:
            # selector 失效时 fallback 到 AI
            print("   selector 失效，fallback 到 AI...")
            await ai_act(page, "点击页面顶部导航中的 'new' 链接")
            await asyncio.sleep(1.5)

        # 动态内容用 AI 提取（不需要分析 DOM）
        print("3. AI 提取最新一条新闻的标题和分数...")
        data = await ai_extract(
            page,
            "提取页面第一条新闻的标题和点赞分数",
            schema={"title": "string", "score": "string or null"}
        )
        print(f"   标题: {data.get('title', 'N/A')[:60]}")
        print(f"   分数: {data.get('score', 'N/A')}")

        await page.screenshot(path=str(out_dir / "C_hybrid.png"))
        print(f"   截图: C_hybrid.png")

        await browser.close()
    print("✓ 混合模式完成")


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def main():
    out_dir = Path("screenshots/demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Playwright vs Skyvern-style AI-native 对比 Demo")
    print("截图保存至: screenshots/demo/")

    await demo_traditional(out_dir)
    await demo_ai_native(out_dir)
    await demo_hybrid(out_dir)

    print("\n" + "="*55)
    print("总结")
    print("="*55)
    print("A 传统:  快、省钱，但 selector 脆，页面改版就挂")
    print("B AI:    慢、贵，但不依赖 DOM，自然语言驱动")
    print("C 混合:  推荐 — 稳定路径用 selector，动态/易变部分用 AI")
    print()
    print("对你项目的建议:")
    print("  explorer.py  → 继续用 selector（BFS 爬取，速度优先）")
    print("  agent.py     → 登录/弹窗关闭 可以加 AI fallback")
    print("  _close_popups → 可以换成 ai_validate + ai_act 更鲁棒")


if __name__ == "__main__":
    asyncio.run(main())
