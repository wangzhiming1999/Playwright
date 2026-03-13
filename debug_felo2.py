"""
调试脚本2：加载 cookies，测试登录后的输入框操作
"""
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
EMAIL = os.getenv("FELO_AI_EMAIL")
PASSWORD = os.getenv("FELO_AI_PASSWORD")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")

        # 加载已有 cookies
        cookies_files = list(Path(".").glob("cookies_*.json"))
        if cookies_files:
            latest = max(cookies_files, key=lambda f: f.stat().st_mtime)
            print(f"加载 cookies: {latest}")
            cookies = json.loads(latest.read_text())
            await context.add_cookies(cookies)
        else:
            print("没有找到 cookies 文件，走完整登录流程")

        page = await context.new_page()

        print("=== 打开 felo.ai ===")
        await page.goto("https://felo.ai/search", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        print("URL:", page.url)
        print("标题:", await page.title())
        await page.screenshot(path="screenshots/d2_1_home.png")

        # 判断是否已登录
        if "/search" in page.url or "/chat" in page.url:
            print("已登录，直接在搜索页")
        else:
            print("未登录，需要登录")
            # force click 登录
            await page.locator("text=登录").first.click(force=True)
            await asyncio.sleep(2)
            await page.screenshot(path="screenshots/d2_2_login.png")

            # 输入邮箱
            await page.locator("input[type='email']").first.fill(EMAIL)
            print("邮箱已输入")
            await page.keyboard.press("Enter")
            await asyncio.sleep(2)
            await page.screenshot(path="screenshots/d2_3_after_email.png")

            # 检查密码框
            pwd = await page.query_selector("input[type='password']")
            if pwd:
                print("密码框出现了")
                await pwd.fill(PASSWORD)
                await page.locator("button[type='submit']").first.click(force=True)
                await asyncio.sleep(3)
            else:
                print("密码框没出现，可能直接登录了")

            await page.screenshot(path="screenshots/d2_4_after_login.png")
            print("登录后 URL:", page.url)

        # 找输入框
        print("\n=== 查找对话输入框 ===")
        await asyncio.sleep(2)

        # 试各种 selector
        for sel in [
            "textarea.m-0",
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
            "textarea[placeholder]",
        ]:
            els = await page.query_selector_all(sel)
            if els:
                print(f"[OK] {sel} -> {len(els)} 个")
                for el in els[:2]:
                    ph = await el.get_attribute("placeholder")
                    cls = (await el.get_attribute("class") or "")[:80]
                    visible = await el.is_visible()
                    print(f"  placeholder={ph!r} visible={visible}")
                    print(f"  class={cls!r}")
            else:
                print(f"[NO] {sel} -> 无")

        await page.screenshot(path="screenshots/d2_5_search_page.png")

        # 尝试输入
        print("\n=== 尝试输入文字 ===")
        try:
            ta = page.locator("textarea.m-0").first
            await ta.click()
            await asyncio.sleep(0.5)
            await ta.fill("测试输入")
            print("输入成功！")
            await page.screenshot(path="screenshots/d2_6_typed.png")
            # 清空
            await ta.fill("")
        except Exception as e:
            print(f"输入失败: {e}")
            # 试备用
            try:
                ta = page.locator("textarea").first
                await ta.click()
                await ta.fill("测试输入")
                print("备用 selector 输入成功")
                await page.screenshot(path="screenshots/d2_6_typed.png")
                await ta.fill("")
            except Exception as e2:
                print(f"备用也失败: {e2}")

        print("\n所有截图已保存到 screenshots/")
        await browser.close()

asyncio.run(main())
