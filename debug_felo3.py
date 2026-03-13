"""
可视化验证：cookie 注入 + 输入框操作
"""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            proxy={"server": "http://127.0.0.1:7897"},
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")

        token = os.environ.get("FELO_AI_TOKEN", "").strip()
        print(f"FELO_AI_TOKEN = {token[:20]}..." if token else "FELO_AI_TOKEN 未设置！")

        if token:
            await context.add_cookies([{
                "name": "felo-user-token",
                "value": token,
                "domain": "felo.ai",
                "path": "/",
            }])
            print("cookie 已注入")

        page = await context.new_page()
        print("打开 felo.ai/search ...")
        await page.goto("https://felo.ai/search", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        print("URL:", page.url)

        # 验证 cookie
        cookies = await context.cookies("https://felo.ai")
        felo_cookie = next((c for c in cookies if c["name"] == "felo-user-token"), None)
        print(f"felo-user-token cookie: {felo_cookie['value'][:20]}..." if felo_cookie else "cookie 不存在！")

        # 找输入框
        ta = await page.query_selector("textarea.m-0")
        if ta:
            print("找到 textarea.m-0，尝试输入...")
            await ta.click()
            await asyncio.sleep(0.5)
            await page.keyboard.type("测试输入 hello", delay=30)
            await asyncio.sleep(1)
            print("输入完成，按 Enter...")
            await page.keyboard.press("Enter")
            await asyncio.sleep(3)
            await page.screenshot(path="screenshots/debug3_result.png")
            print("截图已保存 debug3_result.png")
        else:
            print("未找到 textarea.m-0！")
            # 列出所有 textarea
            all_ta = await page.query_selector_all("textarea")
            print(f"页面共有 {len(all_ta)} 个 textarea")
            for i, el in enumerate(all_ta):
                cls = await el.get_attribute("class")
                print(f"  [{i}] class={cls}")

        print("\n浏览器保持打开，按 Ctrl+C 退出")
        await asyncio.sleep(60)
        await browser.close()

asyncio.run(main())
