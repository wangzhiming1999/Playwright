"""
调试脚本：抓取 felo.ai 页面结构
"""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
EMAIL = os.getenv("FELO_AI_EMAIL")
PASSWORD = os.getenv("FELO_AI_PASSWORD")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page = await context.new_page()

        print("=== 1. 打开 felo.ai ===")
        await page.goto("https://felo.ai", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.screenshot(path="screenshots/debug_1_home.png")
        print("截图已保存 debug_1_home.png")

        # 检查遮罩
        overlay = await page.query_selector("div[data-state='open'][aria-hidden='true']")
        if overlay:
            print("发现遮罩弹窗，尝试关闭...")
            # 按 Escape 关闭
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
            # 或者点击遮罩外部
            await page.mouse.click(640, 400)
            await asyncio.sleep(1)

        await page.screenshot(path="screenshots/debug_2_overlay.png")
        print("截图已保存 debug_2_overlay.png")

        # 找登录按钮并用 force click 绕过遮罩
        print("\n=== 2. 强制点击登录按钮 ===")
        try:
            login_btn = page.locator("text=登录").first
            await login_btn.click(force=True, timeout=5000)
            print("force click 登录成功")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"force click 失败: {e}")

        await page.screenshot(path="screenshots/debug_3_login_modal.png")
        print("截图已保存 debug_3_login_modal.png")

        # 登录弹窗里的 input
        inputs = await page.query_selector_all("input")
        print(f"\n找到 {len(inputs)} 个 input:")
        for i, el in enumerate(inputs):
            t = await el.get_attribute("type")
            ph = await el.get_attribute("placeholder")
            print(f"  [{i}] type={t} placeholder={ph}")

        # 输入邮箱
        print("\n=== 3. 输入邮箱 ===")
        for sel in ["input[type='email']", "input[placeholder*='邮箱']", "input[placeholder*='email']", "input[placeholder*='Email']"]:
            try:
                el = page.locator(sel).first
                await el.fill(EMAIL, timeout=3000)
                print(f"邮箱输入成功，selector: {sel}")
                break
            except:
                pass

        # 输入密码
        print("=== 4. 输入密码 ===")
        try:
            await page.locator("input[type='password']").first.fill(PASSWORD, timeout=3000)
            print("密码输入成功")
        except Exception as e:
            print(f"密码输入失败: {e}")

        await page.screenshot(path="screenshots/debug_4_filled.png")
        print("截图已保存 debug_4_filled.png")

        # 找提交按钮
        print("\n=== 5. 查找并点击提交按钮 ===")
        buttons = await page.query_selector_all("button")
        for i, el in enumerate(buttons):
            txt = (await el.inner_text()).strip()
            if txt:
                print(f"  [{i}] {repr(txt)}")

        for sel in ["button[type='submit']", "text=登录", "text=确认", "text=继续"]:
            try:
                await page.locator(sel).last.click(timeout=3000)
                print(f"点击提交成功: {sel}")
                break
            except:
                pass

        await asyncio.sleep(4)
        await page.screenshot(path="screenshots/debug_5_after_login.png")
        print("截图已保存 debug_5_after_login.png")
        print("URL:", page.url)

        # 登录后找输入框
        print("\n=== 6. 登录后查找对话输入框 ===")
        for sel in ["textarea", "[contenteditable='true']", "[role='textbox']", "input[type='text']"]:
            els = await page.query_selector_all(sel)
            if els:
                print(f"  {sel} → {len(els)} 个")
                for el in els[:2]:
                    ph = await el.get_attribute("placeholder")
                    cls = await el.get_attribute("class")
                    print(f"    placeholder={ph}")
                    print(f"    class={str(cls)[:100] if cls else None}")

        await page.screenshot(path="screenshots/debug_6_logged_in.png")
        print("截图已保存 debug_6_logged_in.png")

        print("\n完成！按 Enter 关闭...")
        input()
        await browser.close()

asyncio.run(main())
