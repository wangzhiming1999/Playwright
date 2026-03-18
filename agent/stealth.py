"""
浏览器反检测模块 — 隐藏自动化特征，降低被网站检测和封禁的概率。

功能：
1. Stealth 脚本注入 — 覆盖 navigator.webdriver、plugins、languages 等指纹
2. 指纹随机化 — User-Agent / viewport / WebGL vendor 随机化
3. 代理支持 — HTTP/SOCKS5 代理配置
"""

import random
import os

# ── Stealth JS 脚本 ──────────────────────────────────────────────

STEALTH_JS = """
(() => {
  // 1. 隐藏 webdriver 标志
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // 2. 伪造 plugins（正常浏览器至少有几个插件）
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const plugins = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
      ];
      plugins.length = 3;
      return plugins;
    }
  });

  // 3. 伪造 languages
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

  // 4. 隐藏 Playwright/Puppeteer 注入的全局变量
  delete window.__playwright;
  delete window.__pw_manual;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

  // 5. 修复 chrome.runtime（headless 模式下缺失）
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) window.chrome.runtime = { connect: () => {}, sendMessage: () => {} };

  // 6. 伪造 permissions API
  const originalQuery = window.navigator.permissions?.query;
  if (originalQuery) {
    window.navigator.permissions.query = (params) => {
      if (params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery(params);
    };
  }

  // 7. 隐藏 automation 相关的 CDP 标志
  Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });
})();
"""


# ── 指纹随机化 ──────────────────────────────────────────────

# 常见桌面 User-Agent 池
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# 常见桌面分辨率
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 2560, "height": 1440},
]

# 常见时区
_TIMEZONES = [
    "Asia/Shanghai",
    "Asia/Tokyo",
    "America/New_York",
    "America/Los_Angeles",
    "Europe/London",
]


def random_fingerprint() -> dict:
    """
    生成随机浏览器指纹配置。

    返回可直接传给 browser.new_context() 的参数。
    """
    ua = random.choice(_USER_AGENTS)
    viewport = random.choice(_VIEWPORTS)
    timezone = random.choice(_TIMEZONES)
    locale = "zh-CN" if "Shanghai" in timezone else "en-US"

    return {
        "user_agent": ua,
        "viewport": viewport,
        "locale": locale,
        "timezone_id": timezone,
        "color_scheme": random.choice(["light", "dark", "no-preference"]),
    }


def get_stealth_fingerprint() -> dict:
    """
    获取反检测指纹配置。

    优先使用环境变量覆盖：
    - BROWSER_USER_AGENT: 自定义 UA
    - BROWSER_VIEWPORT: 自定义分辨率（如 "1920x1080"）
    - BROWSER_TIMEZONE: 自定义时区
    - BROWSER_LOCALE: 自定义语言
    - BROWSER_RANDOMIZE: "true" 启用随机化（默认 false）
    """
    if os.getenv("BROWSER_RANDOMIZE", "").lower() == "true":
        fp = random_fingerprint()
    else:
        fp = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
        }

    # 环境变量覆盖
    env_ua = os.getenv("BROWSER_USER_AGENT", "").strip()
    if env_ua:
        fp["user_agent"] = env_ua

    env_vp = os.getenv("BROWSER_VIEWPORT", "").strip()
    if env_vp and "x" in env_vp:
        try:
            w, h = env_vp.split("x")
            fp["viewport"] = {"width": int(w), "height": int(h)}
        except (ValueError, TypeError):
            pass

    env_tz = os.getenv("BROWSER_TIMEZONE", "").strip()
    if env_tz:
        fp["timezone_id"] = env_tz

    env_locale = os.getenv("BROWSER_LOCALE", "").strip()
    if env_locale:
        fp["locale"] = env_locale

    return fp


# ── 代理配置 ──────────────────────────────────────────────

def get_proxy_config() -> dict | None:
    """
    获取代理配置。

    环境变量：
    - BROWSER_PROXY: 代理地址（如 "http://127.0.0.1:7897" 或 "socks5://user:pass@host:port"）
    - USE_PROXY: 旧版兼容，设为任意值启用默认代理 http://127.0.0.1:7897
    """
    proxy_url = os.getenv("BROWSER_PROXY", "").strip()
    if proxy_url:
        config = {"server": proxy_url}
        # 从 URL 中提取认证信息
        if "@" in proxy_url:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            if parsed.username:
                config["username"] = parsed.username
            if parsed.password:
                config["password"] = parsed.password
            # 重建不含认证的 URL
            config["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return config

    # 旧版兼容
    if os.getenv("USE_PROXY"):
        return {"server": "http://127.0.0.1:7897"}

    return None


async def apply_stealth(context) -> None:
    """
    对 BrowserContext 应用反检测措施。

    在 context 创建后、页面打开前调用：
        context = await browser.new_context(**fingerprint)
        await apply_stealth(context)
        page = await context.new_page()
    """
    await context.add_init_script(STEALTH_JS)
