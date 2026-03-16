"""
Exploration Agent
- Takes a URL + product context
- Uses site_understanding to analyze homepage
- BFS crawls candidate feature pages (depth-limited)
- Screenshots each worthy page
- Returns structured exploration result
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext

load_dotenv()

from site_understanding import analyze_site
from utils import llm_chat


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((msg + "\n").encode(enc, errors="replace"))
        sys.stdout.buffer.flush()


def _same_origin(base: str, target: str) -> bool:
    try:
        b = urlparse(base)
        t = urlparse(target)
        return b.netloc == t.netloc
    except Exception:
        return False


def _normalize_url(base: str, href: str) -> str | None:
    try:
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            return None
        full = urljoin(base, href)
        parsed = urlparse(full)
        # Drop fragment
        return parsed._replace(fragment="").geturl()
    except Exception:
        return None


async def _screenshot_b64(page: Page, quality: int = 60) -> str:
    data = await page.screenshot(type="jpeg", quality=quality)
    return base64.b64encode(data).decode()


async def _get_html(page: Page) -> str:
    try:
        return await page.evaluate("() => document.documentElement.outerHTML")
    except Exception:
        return ""


async def _close_popups(page: Page):
    """先用 selector 快速尝试，失败则 fallback 到 AI 视觉识别。"""
    selectors = [
        "button[aria-label*='close' i]",
        "button[aria-label*='dismiss' i]",
        "[class*='cookie'] button",
        "[id*='cookie'] button",
        "[class*='modal'] button[class*='close' i]",
        "[class*='banner'] button",
    ]
    closed = False
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=500):
                await el.click(timeout=500)
                await asyncio.sleep(0.3)
                closed = True
        except Exception:
            pass

    if not closed:
        # fallback：AI 视觉判断是否有弹窗
        try:
            data = await page.screenshot(type="jpeg", quality=50)
            img_b64 = base64.b64encode(data).decode()
            resp = llm_chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": '页面上是否有 cookie 横幅、弹窗或遮罩层需要关闭？返回 JSON: {"has_popup": true/false, "selector": "关闭按钮的 CSS selector 或 null"}'},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "low"}},
                    ],
                }],
                response_format={"type": "json_object"},
                max_tokens=100,
            )
            result = json.loads(resp.choices[0].message.content)
            if result.get("has_popup") and result.get("selector"):
                try:
                    await page.click(result["selector"], timeout=3000)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    _safe_print(f"popup click error: {e}")
        except Exception as e:
            _safe_print(f"popup close fallback error: {e}")


# ── Main exploration runner ───────────────────────────────────────────────────

async def run_exploration(
    url: str,
    product_context: str = "",
    screenshots_dir: str = "screenshots/explore",
    cookies_path: str | None = None,
    max_pages: int = 12,
    max_depth: int = 2,
    min_page_score: float = 5.0,
    headless: bool = True,
    log_fn=None,
) -> dict:
    """
    Full exploration pipeline.
    Returns:
      {
        "site_understanding": {...},
        "visited_pages": [...],
        "screenshots": [...],   # list of {filename, url, title, score, page_type}
      }
    """
    out_dir = Path(screenshots_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    async def log(msg: str):
        _safe_print(msg)
        if log_fn:
            await log_fn(msg)

    visited_urls: set[str] = set()
    screenshots: list[dict] = []
    visited_pages: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
        )
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )

        # Load cookies if provided
        if cookies_path:
            cp = Path(cookies_path)
            if cp.exists():
                cookies = json.loads(cp.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
                await log("✓ 已加载登录态")

        page = await context.new_page()

        # ── Step 1: Load homepage and analyze ────────────────────────────────
        await log(f"\n🌐 打开首页: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            await _close_popups(page)
        except Exception as e:
            await log(f"  ✗ 首页加载失败: {e}")
            await browser.close()
            return {"error": str(e), "site_understanding": {}, "visited_pages": [], "screenshots": []}

        homepage_html = await _get_html(page)
        homepage_b64 = await _screenshot_b64(page)

        await log("  → 分析网站结构...")
        site_info = analyze_site(url, homepage_html, homepage_b64, product_context)
        await log(f"  ✓ 网站类型: {site_info.get('site_category')} | {site_info.get('site_name')}")
        await log(f"  ✓ 发现候选页面: {len(site_info.get('candidate_feature_pages', []))} 个")

        # Screenshot homepage
        home_shot = out_dir / "00_homepage.png"
        await page.screenshot(path=str(home_shot), full_page=False)
        screenshots.append({
            "filename": home_shot.name,
            "url": url,
            "title": site_info.get("site_name", "Homepage"),
            "score": 7.0,
            "page_type": "landing",
            "source": "homepage",
        })
        visited_urls.add(url)

        # ── Step 2: Build exploration queue from site understanding ───────────
        # Priority order: candidate_feature_pages (sorted by score) + entry_points
        queue: list[tuple[str, int]] = []  # (url, depth)

        candidates = sorted(
            site_info.get("candidate_feature_pages", []),
            key=lambda x: x.get("marketing_score", 0),
            reverse=True,
        )
        for c in candidates:
            path = c.get("path", "")
            if not path:
                continue
            full = _normalize_url(url, path)
            if full and _same_origin(url, full) and full not in visited_urls:
                queue.append((full, 1))

        # Also add entry_points not already in queue
        queued_urls = {u for u, _ in queue}
        for ep in sorted(site_info.get("entry_points", []), key=lambda x: -x.get("priority", 0)):
            path = ep.get("path", "")
            if not path:
                continue
            full = _normalize_url(url, path)
            if full and _same_origin(url, full) and full not in visited_urls and full not in queued_urls:
                queue.append((full, 1))
                queued_urls.add(full)

        await log(f"  → 探索队列: {len(queue)} 个页面")

        # ── Step 3: BFS exploration ───────────────────────────────────────────
        page_counter = 1
        while queue and len(visited_urls) < max_pages:
            target_url, depth = queue.pop(0)
            if target_url in visited_urls:
                continue
            visited_urls.add(target_url)

            await log(f"\n  [{len(visited_urls)}/{max_pages}] 访问: {target_url}")
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(1.5)
                await _close_popups(page)
            except Exception as e:
                await log(f"    ✗ 加载失败: {e}")
                continue

            title = await page.title()

            # 不再用 AI 判断是否截图，每页都直接保存，由用户自己筛选
            filename = f"{page_counter:02d}_page.png"
            shot_path = out_dir / filename
            await page.screenshot(path=str(shot_path), full_page=False)
            screenshots.append({
                "filename": filename,
                "url": target_url,
                "title": title,
                "score": None,
                "page_type": "page",
                "source": "exploration",
            })
            visited_pages.append({
                "url": target_url,
                "title": title,
                "score": None,
                "page_type": "page",
                "worth_screenshot": True,
            })
            page_counter += 1
            await log(f"    ✓ 截图保存: {filename}")

            # Enqueue sub-pages if depth allows
            if depth < max_depth:
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.getAttribute('href'))",
                )
                for href in links[:40]:
                    full = _normalize_url(target_url, href)
                    if (
                        full
                        and _same_origin(url, full)
                        and full not in visited_urls
                        and full not in {u for u, _ in queue}
                    ):
                        queue.append((full, depth + 1))

        await log(f"\n✅ 探索完成: 访问 {len(visited_urls)} 页，截图 {len(screenshots)} 张")
        await browser.close()

    return {
        "site_understanding": site_info,
        "visited_pages": visited_pages,
        "screenshots": screenshots,
    }
