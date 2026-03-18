"""
run_agent() 主流程：启动浏览器、执行任务循环、返回结果。
从 agent.py 抽取，作为 agent 包的一部分。
"""

import asyncio
import base64
import json
import os
import re
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from pathlib import Path

from playwright.async_api import async_playwright

from .page_utils import _safe_print, _wait_for_page_ready
from .core import BrowserAgent
from .tools import TOOLS, TERMINATES_SEQUENCE
from .llm_helpers import _decompose_task, _verify_step, _compress_messages, _analyze_failure, trim_elements, estimate_messages_tokens
from .chrome_detector import _find_chrome_user_data_dir
from .error_recovery import FailureTracker
from .circuit_breaker import CircuitBreaker
from .loop_detector import ActionLoopDetector
from .plan_manager import PlanManager
from .watchdog import Watchdog, EventType
from .action_registry import load_custom_actions, get_custom_tools
from .cost_tracker import CostTracker
from .a11y_tree import extract_a11y_tree, get_page_summary, should_use_screenshot
from .model_router import select_model_tier
from .memory import MemoryManager, format_memories_for_prompt, _extract_domain
from .visual_verify import take_snapshot, verify_action, ActionVerifier, SKIP_VERIFY

from utils import llm_chat
from page_annotator import annotate_page


async def _verify_done(agent, task: str, summary: str, _log, llm_chat_fn) -> bool:
    """
    完成前验证：截图 + GPT 判断是否真正满足用户需求。
    每 15 秒检查一次，最多 3 次，防止页面还没渲染完就结束。
    返回 True 表示验证通过。
    """
    for check_round in range(1, 4):
        await _log(f"\n🔍 [完成验证] 第 {check_round}/3 次检查...")
        try:
            check_img = await agent.screenshot_base64(quality=75, full_page=True)
            if not check_img:
                await _log(f"  ⚠ 截图为空，跳过本轮验证")
                if check_round < 3:
                    await asyncio.sleep(15)
                continue

            # 额外截一张底部 viewport 截图，检测底部是否有 loading
            bottom_img = None
            try:
                scroll_h = await agent.page.evaluate("() => document.body.scrollHeight")
                vp_h = agent.page.viewport_size.get("height", 1080) if agent.page.viewport_size else 1080
                if scroll_h > vp_h * 1.2:
                    await agent.page.evaluate(f"window.scrollTo(0, {scroll_h})")
                    await asyncio.sleep(0.5)
                    bottom_img = await agent.screenshot_base64(quality=60)
                    await agent.page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

            image_parts = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{check_img}", "detail": "low"}},
            ]
            bottom_hint = ""
            if bottom_img:
                image_parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{bottom_img}", "detail": "low"}},
                )
                bottom_hint = "第一张是完整页面截图，第二张是页面底部截图。请同时检查底部是否有未完成的内容。\n"

            verify_resp = llm_chat_fn(
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"用户任务：{task}\n"
                                f"Agent 认为已完成：{summary}\n\n"
                                f"{bottom_hint}"
                                "请观察截图，判断任务是否真正完成。注意：\n"
                                "1. 如果页面有 loading/spinner/骨架屏，说明内容还在加载，未完成\n"
                                "2. 如果是 AI 生成类任务，检查内容是否已经完整输出（不是只有开头几个字）\n"
                                "3. 如果页面显示错误信息，说明任务失败\n"
                                "4. 如果页面内容与任务目标明显不符，说明未完成\n"
                                "5. 检查页面底部是否有 '加载更多'、spinner、或未完成的内容区块\n"
                                "6. 如果是长内容页面，检查内容是否在中间截断（如只有标题没有正文）\n\n"
                                '返回 JSON：{"done": true/false, "reason": "1句话说明判断依据"}'
                            ),
                        },
                    ] + image_parts,
                }],
                response_format={"type": "json_object"},
                max_tokens=150,
            )
            if not verify_resp.choices:
                await _log(f"  ⚠ 验证 API 返回空，视为通过")
                return True

            try:
                verify_data = json.loads(verify_resp.choices[0].message.content)
            except json.JSONDecodeError:
                await _log(f"  ⚠ 验证结果 JSON 解析失败，视为通过")
                return True

            is_done = verify_data.get("done", True)
            reason = verify_data.get("reason", "")
            await _log(f"  [完成验证] {'✅ 已完成' if is_done else '⏳ 未完成'} — {reason}")

            if is_done:
                return True
            else:
                if check_round < 3:
                    await _log(f"  等待 15 秒后重新检查...")
                    await asyncio.sleep(15)

        except Exception as e:
            await _log(f"  ⚠ 验证异常: {e}，视为通过")
            return True

    return False


def _normalize_url(url: str) -> str:
    """Normalize URL for comparison: strip fragment, sort query params."""
    try:
        parsed = urlparse(url)
        query = urlencode(sorted(parse_qs(parsed.query, keep_blank_values=True).items()), doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ''))
    except Exception:
        return url


def _format_site_understanding(analysis: dict) -> str:
    """Format site analysis result as a prompt hint for LLM."""
    if not analysis or analysis.get("site_category") == "unknown":
        return ""

    parts = ["## 站点理解（自动分析结果）"]
    parts.append(f"- 站点: {analysis.get('site_name', '未知')} ({analysis.get('site_category', '未知')})")

    if analysis.get("needs_login"):
        parts.append("- 需要登录才能使用核心功能")

    features = analysis.get("key_features_visible", [])
    if features:
        parts.append(f"- 可见功能: {', '.join(features[:5])}")

    strategy = analysis.get("exploration_strategy", "")
    if strategy:
        parts.append(f"- 建议策略: {strategy}")

    entry_points = analysis.get("entry_points", [])
    if entry_points:
        top_entries = sorted(entry_points, key=lambda x: -x.get("priority", 0))[:3]
        entries_str = ", ".join(f"{e.get('label', '')}({e.get('path', '')})" for e in top_entries)
        parts.append(f"- 关键入口: {entries_str}")

    return "\n".join(parts)


async def run_agent(
    task: str,
    headless: bool = False,
    task_id: str = None,
    log_callback=None,
    cookies_path: str = "data/cookies/cookies.json",
    screenshots_dir: str = "screenshots",
    ask_user_callback=None,      # async (task_id, question, reason) -> str
    screenshot_callback=None,    # async (task_id, filename) -> None
    browser_mode: str = "builtin",  # "builtin" | "user_chrome" | "cdp"
    cdp_url: str = "http://localhost:9222",  # browser_mode="cdp" 时使用
    chrome_profile: str = None,  # browser_mode="user_chrome" 时指定 profile 名，默认 "Default"
    pool_browser=None,           # 从 BrowserPool 注入的浏览器实例
    pool_context=None,           # 从 BrowserPool 注入的 BrowserContext
    site_understanding: bool = True,  # 首步自动分析站点结构
    max_steps: int = 35,         # 最大执行步数
) -> dict:
    """
    运行 agent 执行任务。
    返回: {"success": bool, "reason": str, "steps": int}
    """
    screenshots_dir = Path(screenshots_dir)
    task_success = False
    task_reason = "未知"
    steps_executed = 0

    # ── 浏览器池模式：跳过 Playwright 启动，直接使用注入的实例 ──
    _using_pool = pool_browser is not None and pool_context is not None
    _pw_cm = None  # Playwright context manager（非池模式时需要）

    if _using_pool:
        pw = None
        browser = pool_browser
        context = pool_context
        browser_mode = "pool"  # 标记为池模式
    else:
        _pw_cm = async_playwright()
        pw = await _pw_cm.start()
        browser = None
        context = None

        if browser_mode == "cdp":
            _safe_print(f"  [CDP] 连接 {cdp_url} ...")
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                await _pw_cm.__aexit__(type(e), e, e.__traceback__)
                raise RuntimeError(f"CDP 连接失败 ({cdp_url}): {e}") from e
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")

        elif browser_mode == "user_chrome":
            user_data_dir = await _find_chrome_user_data_dir()
            if not user_data_dir:
                _safe_print("  [user_chrome] 未找到 Chrome Profile，降级为 builtin 模式")
                browser_mode = "builtin"
            else:
                profile = chrome_profile or "Default"
                _safe_print(f"  [user_chrome] 使用 Chrome Profile: {user_data_dir} / {profile}")
                try:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        channel="chrome",
                        headless=False,
                        args=["--profile-directory=" + profile],
                        viewport={"width": 1920, "height": 1080},
                        locale="zh-CN",
                        proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
                    )
                except Exception as e:
                    _safe_print(f"  [user_chrome] 启动失败: {e}")
                    _safe_print("  [user_chrome] Chrome 可能正在运行，降级为 builtin 模式")
                    browser_mode = "builtin"

        if browser_mode == "builtin":
            browser = await pw.chromium.launch(
                headless=headless,
                proxy={"server": "http://127.0.0.1:7897"} if os.environ.get("USE_PROXY") else None,
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )

    try:

        # cookies_file 所有模式都需要定义（builtin 模式读写，其他模式只在需要时写）
        cookies_file = Path(cookies_path)
        cookies_file.parent.mkdir(parents=True, exist_ok=True)

        # builtin 模式才需要手动加载 cookies
        if browser_mode == "builtin":
            if cookies_file.exists():
                try:
                    cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
                    if isinstance(cookies, list):
                        await context.add_cookies(cookies)
                        _safe_print("✓ 已加载登录态")
                    else:
                        _safe_print("⚠ cookies 文件格式错误，跳过加载")
                except (json.JSONDecodeError, Exception) as e:
                    _safe_print(f"⚠ 加载 cookies 失败: {e}，继续执行")

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

        agent = BrowserAgent(page, screenshots_dir, log_fn=_log, screenshot_callback=screenshot_callback, task_id=task_id)

        # ── Watchdog 事件架构 ──────────────────────────────────────────
        watchdog = Watchdog(page, context, log_fn=_log, downloads_dir=str(screenshots_dir))
        await watchdog.start()

        # 多 tab 支持：监听新页面，自动切换到最新打开的 tab
        async def _on_new_page(new_page):
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                agent.page = new_page
                # 更新 watchdog 的 page 引用
                watchdog.page = new_page
                await _log(f"  [新标签页] 已切换到: {new_page.url}")
            except Exception as e:
                await _log(f"  [新标签页] 切换失败: {e}")

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

        # ── Prompt 静态/动态拆分 ──────────────────────────────────────────
        # 静态部分放在 system message 开头，利用 prompt caching 降低成本
        # 动态部分（任务、凭证提示）放在 user message
        _SYSTEM_PROMPT_STATIC = (
            "你是一个网页操作助手。系统会给你当前页面的信息（截图或 Accessibility Tree），你需要理解页面状态并调用工具完成用户任务。\n"
            "核心原则：仔细分析页面信息，理解页面布局和内容，再决定下一步操作。\n\n"
            "## 输入模式\n"
            "系统会根据场景自动选择两种模式之一：\n"
            "- 截图模式：提供标注截图（蓝框+编号），适合需要视觉理解的场景\n"
            "- DOM模式：提供 Accessibility Tree 文本 + 元素列表，更轻量高效\n"
            "两种模式下都可以用 index 编号操作元素。如果 DOM 模式下需要视觉信息，调用 screenshot 工具。\n\n"
            "## 基本规则\n"
            "1. 你可以一次返回多个工具调用（批量执行），系统会按顺序执行，遇到页面跳转自动中断剩余操作\n"
            "2. 适合批量的场景：连续填写多个表单字段、先输入再按回车、先滚动再点击等不涉及页面跳转的连续操作\n"
            "3. 不适合批量的场景：需要观察页面变化后再决定下一步的操作（如点击后需要看新页面）\n"
            "4. 操作元素优先用截图中的蓝色 index 编号，比文字更准确\n"
            "5. 操作失败时换个方式重试（换 index、用 text、滚动页面、用 find_element 视觉定位），不要直接 done 放弃——除非连续5次都失败\n"
            "6. 任务全部完成后先截图，再调用 done\n"
            "7. 遇到登录页面，继续完成登录，不要放弃\n\n"
            "## 查找和定位策略（重要）\n"
            "当任务要求找到页面上的特定内容（图片、文字、按钮等）时：\n"
            "1. 先仔细观察当前截图，看目标是否已经在可视区域内\n"
            "2. 如果目标不在当前视口，用 scroll(direction='down') 向下滚动，每次滚动后观察新截图\n"
            "3. 如果元素列表中没有目标元素的 index（比如图片、非交互元素），用 find_element 工具通过视觉描述定位\n"
            "4. 找到目标后，根据任务需求决定操作：点击、下载、截图等\n"
            "5. 不要在没有看到目标的情况下就放弃，先滚动整个页面搜索\n\n"
            "## 下载策略\n"
            "下载文件/图片时，按优先级尝试：\n"
            "1. 如果页面有明确的下载按钮/链接，用 click 或 download_file 点击它\n"
            "2. 如果是图片，用 find_element 定位图片，然后用 save_element 保存图片到本地\n"
            "3. 如果以上都不行，用 get_page_html 获取页面源码，找到图片/文件的 URL，然后用 download_url 直接下载\n"
            "4. 不要尝试右键另存为（浏览器自动化不支持原生右键菜单）\n\n"
            "## 等待规则\n"
            "- 点击提交/搜索/发送按钮后，如果任务要求等待生成结果，必须调用 wait(wait_for_content_change=true, timeout=120)\n"
            "- wait 会自动等待内容开始出现，再等内容停止变化，完成后再截图\n"
            "- 普通页面跳转（登录、导航）不需要调用 wait，系统已自动处理\n\n"
            "## 提交规则\n"
            "- 在输入框输入内容后，必须点击提交/发送/搜索按钮，或者用 press_enter=true 提交，不能直接 done\n"
            "- 提交后才能等待生成结果\n\n"
            "## 登录规则\n"
            "- 看到邮箱框填邮箱，看到密码框填密码，看到按钮就点\n"
            "- 两步登录（先邮箱后密码）：点继续后等新截图再填密码\n"
            "- 没有凭证时调用 get_credentials(site_key) 获取\n\n"
            "## 滚动搜索策略\n"
            "当需要在页面中找到特定内容时：\n"
            "- 先观察当前视口，如果没找到就向下滚动\n"
            "- 每次滚动后仔细观察新截图中是否出现了目标\n"
            "- 如果滚动到底部还没找到，尝试回到顶部用 find_element 搜索\n"
            "- 页面可能有懒加载，滚动后等待内容出现\n\n"
            "## 计划管理（可选）\n"
            "系统会显示任务计划和每步状态（✅已完成/👉当前/⏳待做/⏭️已跳过）。\n"
            "当你完成了某个步骤、需要跳过步骤、或发现需要新增步骤时，在回复的文本部分加入：\n"
            "[PLAN_UPDATE]\n"
            '{"completed": [1], "current": 2}\n'
            "[/PLAN_UPDATE]\n"
            "支持的字段（都是可选的）：completed(已完成步骤号列表), current(当前步骤号), "
            "skip(跳过步骤号列表), add_after(在哪步后插入)+new_steps(新步骤描述列表), note(备注)\n"
            "不需要每步都更新，只在计划状态变化时包含。\n"
        )

        messages = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT_STATIC,  # 纯静态，利用 prompt caching
            },
            {
                "role": "user",
                "content": f"任务：{task_for_gpt}" + (_cred_hint if _cred_hint else ""),
            },
        ]

        await _log(f"\n🚀 开始执行任务: {task}\n")

        # ── 记忆注入：检索相关历史经验 ──────────────────────────────────
        _memory_mgr = MemoryManager()
        _task_domain = _extract_domain(task)
        try:
            _relevant_memories = _memory_mgr.retrieve_relevant(task, domain=_task_domain, max_results=5)
            if _relevant_memories:
                _memory_text = format_memories_for_prompt(_relevant_memories)
                messages[1]["content"] += f"\n\n{_memory_text}"
                await _log(f"  [记忆] 已注入 {len(_relevant_memories)} 条相关经验")
        except Exception as e:
            await _log(f"  ⚠ [记忆] 检索失败: {e}")

        # ── 加载自定义 Actions ──────────────────────────────────────────
        custom_count = load_custom_actions("custom_actions")
        custom_tools = get_custom_tools()
        all_tools = TOOLS + custom_tools  # 合并内置 + 自定义工具
        if custom_count > 0:
            await _log(f"  [自定义 Action] 已加载 {custom_count} 个自定义工具")

        # 环境变量覆盖 max_steps
        _env_max_steps = os.environ.get("AGENT_MAX_STEPS")
        if _env_max_steps:
            try:
                max_steps = max(10, min(int(_env_max_steps), 200))
            except (ValueError, TypeError):
                pass
        fail_count = 0
        last_tool_name = None
        last_tool_pressed_enter = False
        _last_content_hash = None  # 用于截图复用：检测 DOM 是否变化
        _pending_nudges: list[str] = []  # 缓存 nudge 消息，下一轮截图时注入（避免打断 tool_result 顺序）
        cost_tracker = CostTracker()  # 成本追踪
        action_verifier = ActionVerifier()  # 视觉验证器
        _consecutive_dom_steps = 0  # 连续使用 DOM 模式的步数（用于按需截图判断）
        _dom_tokens_saved = 0  # 累计节省的 token 数

        # ── 智能错误恢复 + 熔断器 + 循环检测 ──────────────────────────────────
        failure_tracker = FailureTracker()
        llm_breaker = CircuitBreaker("llm_api", failure_threshold=3, cooldown=30.0,
                                     log_fn=lambda msg: asyncio.ensure_future(_log(msg)))
        loop_detector = ActionLoopDetector(window_size=20)

        # ── 任务分解 ──────────────────────────────────────────────
        await _log("  [任务分解] 正在拆解任务步骤...")
        task_steps = _decompose_task(task)
        if task_steps:
            await _log(f"  [任务分解] 共 {len(task_steps)} 步：")
            for s in task_steps:
                await _log(f"    步骤{s.get('step', '?')}: {s.get('action', '')}")
                await _log(f"           预期: {s.get('expected', '')}")
        else:
            await _log("  [任务分解] 分解失败，使用自由模式执行")

        # 把任务步骤列表格式化成提示文字，注入到每步的 user message 里
        plan_manager = PlanManager(task_steps)

        # 网络请求追踪：复用 Watchdog 的 _pending_requests（不再手动注册事件）
        active_requests = watchdog._pending_requests
        agent._active_requests = active_requests  # 注入到 agent，供 wait 工具使用

        for step in range(max_steps):
            # ── 消费 Watchdog 事件 ──────────────────────────────────────
            for evt in watchdog.drain_events():
                if evt.type == EventType.PAGE_CRASHED:
                    await _log("  ⚠ [Watchdog] 页面崩溃，尝试恢复...")
                    try:
                        page = await context.new_page()
                        agent.page = page
                        watchdog.page = page
                        await _log("  [Watchdog] 已创建新页面")
                    except Exception as e:
                        await _log(f"  ❌ [Watchdog] 恢复失败: {e}，终止任务")
                        task_reason = "页面崩溃且恢复失败"
                        steps_executed = step
                        break
                elif evt.type == EventType.CAPTCHA_DETECTED:
                    _pending_nudges.append(
                        f"⚠️ 系统检测到验证码（{evt.data.get('source', 'unknown')}）。"
                        "请调用 solve_captcha 尝试自动识别，失败则用 ask_user 请求人工协助。"
                    )
                elif evt.type == EventType.DOWNLOAD_COMPLETED:
                    _pending_nudges.append(
                        f"📥 下载完成: {evt.data.get('filename', '未知文件')} → {evt.data.get('path', '未知路径')}"
                    )
                elif evt.type == EventType.CONSOLE_ERROR:
                    error_count = evt.data.get('count', 0)
                    if error_count >= 10:
                        _pending_nudges.append(
                            f"⚠️ 页面控制台出现 {error_count} 个错误，页面可能存在问题。"
                        )
            else:
                # for-else: 只有 break 时不执行这里（崩溃恢复失败时 break）
                pass

            # 广播步骤进度（基于计划完成度，而非循环计数）
            if plan_manager.has_plan:
                await _log(f"__PROGRESS__:{plan_manager.completed_count}/{plan_manager.total_steps}")
            else:
                await _log(f"__PROGRESS__:{step+1}/{max_steps}")
            # 步数预警：剩余 5 步时提醒 GPT 加速收尾
            _remaining = max_steps - step - 1
            if _remaining == 5:
                await _log(f"  ⚠ [预警] 已执行 {step+1}/{max_steps} 步，仅剩 {_remaining} 步")
                _pending_nudges.append(f"⚠️ 注意：仅剩 {_remaining} 步可用，请尽快完成任务。如果核心目标已达成，请截图并调用 done。")

            # 只在第一步用完整 dismiss_overlay（含 AI fallback），后续步骤用轻量 quick_dismiss
            if step == 0:
                await agent.dismiss_overlay()
                # 首步主动检测 CAPTCHA
                await watchdog.check_captcha()
            else:
                # 每步轻量弹窗检测（<100ms，不调 AI）
                await agent.quick_dismiss()

            # 截图前确保页面就绪（统一使用 _wait_for_page_ready）
            await _wait_for_page_ready(agent.page, log_fn=_log, timeout_ms=10000, check_network=True, active_requests=active_requests)

            # ── DOM 优先 + 按需截图策略 ──────────────────────────────────
            # 获取页面摘要，判断是否需要截图
            page_summary = await get_page_summary(agent.get_active_page())
            use_screenshot = should_use_screenshot(
                step=step,
                last_tool=last_tool_name,
                page_summary=page_summary,
                consecutive_dom_steps=_consecutive_dom_steps,
            )

            # DOM 变化检测
            try:
                _cur_hash = await agent.get_active_page().evaluate("() => document.body.innerText.length + '|' + document.body.children.length")
            except Exception:
                _cur_hash = None

            _reuse_screenshot = (
                _cur_hash is not None
                and _cur_hash == _last_content_hash
                and step > 0
                and last_tool_name in ("wait", "screenshot")
            )

            # 始终执行标注（需要 data-skyvern-id 供 execute 使用）
            if _reuse_screenshot:
                await _log(f"  [截图复用] DOM 未变化，跳过重新截图")
            else:
                try:
                    img_b64, elements = await annotate_page(agent.get_active_page())
                except Exception as e:
                    await _log(f"  ⚠ 页面标注失败: {e}，使用普通截图")
                    use_screenshot = True  # 标注失败时强制截图
                    try:
                        raw = await agent.page.screenshot(type="jpeg", quality=60, timeout=10000)
                        img_b64 = base64.b64encode(raw).decode()
                        elements = []
                    except Exception as e2:
                        await _log(f"  ❌ 截图也失败: {e2}，终止任务")
                        break
                _last_content_hash = _cur_hash

            # ── 站点理解：首步自动分析 ──────────────────────────────────
            _site_understanding_enabled = site_understanding and os.environ.get("SITE_UNDERSTANDING", "1") != "0"
            if step == 0 and _site_understanding_enabled and not getattr(agent, '_site_analysis', None):
                try:
                    _page_url = agent.page.url
                    if _page_url and _page_url != "about:blank":
                        await _log("  [站点理解] 正在分析目标站点...")
                        _page_html = await agent._safe_evaluate(
                            "() => document.documentElement.outerHTML",
                            timeout_ms=10000,
                            default=""
                        )
                        if _page_html:
                            from site_understanding import analyze_site as _analyze_site
                            _site_analysis = _analyze_site(
                                url=_page_url,
                                html=_page_html,
                                screenshot_b64=img_b64 if use_screenshot else None,
                            )
                            agent._site_analysis = _site_analysis
                            _site_hint = _format_site_understanding(_site_analysis)
                            if _site_hint:
                                messages[0]["content"] += f"\n\n{_site_hint}"
                                await _log(f"  [站点理解] {_site_analysis.get('site_name', '?')} ({_site_analysis.get('site_category', '?')})")
                except Exception as e:
                    await _log(f"  ⚠ [站点理解] 分析失败: {e}")

            elements_summary = trim_elements(elements)

            # 计算有效 index 范围，注入到 prompt 防止 LLM 幻觉
            max_index = max((el.get("index", 0) for el in elements), default=-1) if elements else -1
            index_hint = f"⚠️ 有效 index 范围: 0~{max_index}，不要使用超出此范围的 index。\n" if max_index >= 0 else ""

            # 注入缓存的 nudge（循环检测/停滞检测），避免作为独立 user 消息打断 tool_result 顺序
            nudge_text = ""
            if _pending_nudges:
                nudge_text = "\n".join(_pending_nudges) + "\n"
                _pending_nudges.clear()

            if use_screenshot:
                # ── 截图模式：发送标注截图 + 元素列表（~1100 tokens for image） ──
                _consecutive_dom_steps = 0

                # 保存标注截图，方便调试，并实时推送给前端
                debug_path = screenshots_dir / f"step_{step+1:02d}_annotated.jpg"
                try:
                    debug_path.write_bytes(base64.b64decode(img_b64))
                except Exception as e:
                    await _log(f"  ⚠ 保存调试截图失败: {e}")
                await _log(f"  [截图模式] {debug_path.name}")
                if screenshot_callback and task_id:
                    try:
                        await screenshot_callback(task_id, debug_path.name)
                    except Exception as e:
                        await _log(f"  ⚠ 截图回调失败: {e}")

                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"第{step+1}步，当前页面截图（蓝框+编号标注了所有可交互元素）：\n"
                                f"{nudge_text}"
                                f"{plan_manager.format_hint()}"
                                f"{index_hint}"
                                f"元素列表: {elements_summary}\n"
                                "根据截图判断当前状态，调用工具推进任务（可一次返回多个工具调用）。"
                                "操作时用元素的 index 编号，不要猜 selector 或坐标。"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}},
                    ],
                })
            else:
                # ── DOM 模式：发送 a11y tree + 元素列表（~500-1500 tokens，节省截图 ~1100 tokens） ──
                _consecutive_dom_steps += 1
                _dom_tokens_saved += 1100  # 每次省掉一张截图的 token

                a11y_tree = await extract_a11y_tree(agent.get_active_page())
                await _log(f"  [DOM模式] 第{_consecutive_dom_steps}步（累计节省 ~{_dom_tokens_saved} tokens）")

                messages.append({
                    "role": "user",
                    "content": (
                        f"第{step+1}步，当前页面结构（Accessibility Tree）：\n"
                        f"{nudge_text}"
                        f"{plan_manager.format_hint()}"
                        f"{index_hint}"
                        f"```\n{a11y_tree}\n```\n"
                        f"元素列表: {elements_summary}\n"
                        "根据页面结构判断当前状态，调用工具推进任务（可一次返回多个工具调用）。"
                        "操作时用元素的 index 编号。如果需要视觉信息（如验证码、图片内容），请调用 screenshot 工具获取截图。"
                    ),
                })

            # ── Token 级上下文压缩 ──────────────────────────────────────
            # 硬上限：消息数 > 80 时强制截断（防止压缩失败时无限增长）
            if len(messages) > 80:
                await _log(f"  ⚠ [上下文] 消息数 {len(messages)} 超过硬上限，强制截断")
                messages = [messages[0]] + messages[-20:]

            # Token 级压缩：估算总 token 数，超过预算时智能压缩
            current_tokens = estimate_messages_tokens(messages)
            if current_tokens > 65000:  # 65k 触发压缩（20% 安全边际给 128k 上下文窗口）
                await _log(f"  [上下文] 当前 {current_tokens} tokens，触发压缩...")
                messages = _compress_messages(messages, max_tokens=65000, keep_recent=20)

            # ── 智能模型路由 ──────────────────────────────────────────
            _last_failed = (
                failure_tracker.total_consecutive > 0
            )
            model_tier = select_model_tier(
                use_screenshot=use_screenshot,
                step=step,
                last_tool=last_tool_name,
                last_failed=_last_failed,
                consecutive_failures=failure_tracker.total_consecutive,
                has_captcha=page_summary.get("has_captcha", False),
                has_dialog=page_summary.get("has_dialog", False),
            )
            if model_tier == "mini":
                await _log(f"  [模型路由] mini（DOM模式，简单操作）")

            # LLM 调用（带指数退避重试 + 熔断保护）
            _LLM_RETRY_DELAYS = [1.0, 3.0]  # 2 次重试: 1s, 3s
            response = None
            _llm_last_error = None

            for _retry_idx in range(len(_LLM_RETRY_DELAYS) + 1):  # 0, 1, 2 = 初始 + 2 次重试
                if not llm_breaker.check():
                    await _log("  ⚠ LLM API 熔断中，等待冷却...")
                    await asyncio.sleep(llm_breaker.cooldown)

                try:
                    response = llm_chat(
                        model=model_tier,
                        messages=messages,
                        tools=all_tools,
                        tool_choice="required",
                        max_tokens=2000,  # 增大以支持多 action 返回
                    )
                    llm_breaker.record_success()
                    # 记录 token 消耗
                    if response and hasattr(response, 'usage') and response.usage:
                        from utils import _resolve_model as _rm
                        _actual_model, _ = _rm(model_tier)
                        cost_tracker.record(model=_actual_model, usage=response.usage, purpose="main_loop")
                    break  # 成功
                except Exception as e:
                    _llm_last_error = e
                    if _retry_idx < len(_LLM_RETRY_DELAYS):
                        delay = _LLM_RETRY_DELAYS[_retry_idx]
                        await _log(f"  ⚠ LLM API 调用失败 (重试 {_retry_idx+1}/{len(_LLM_RETRY_DELAYS)}): {e}，{delay}s 后重试...")
                        await asyncio.sleep(delay)
                    else:
                        # 所有重试耗尽，记录熔断器失败
                        llm_breaker.record_failure()
                        await _log(f"  ❌ LLM API 调用失败 (已重试{len(_LLM_RETRY_DELAYS)}次): {e}")
                        if llm_breaker.state.value == "open":
                            await _log(f"  ⚠ LLM API 熔断，等待 {llm_breaker.cooldown}s 后重试")
                            await asyncio.sleep(llm_breaker.cooldown)

            if response is None:
                continue  # 跳到下一步

            if not response.choices:
                await _log("⚠️ LLM API 返回空 choices，终止任务")
                break

            msg = response.choices[0].message
            # 转成 dict 存入 messages，避免 _compress_messages 中 .get() 报错
            msg_dict = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(msg_dict)

            # ── 解析计划更新（从 msg.content 旁路） ──────────────────────
            plan_changed = plan_manager.process_llm_content(msg.content)
            if plan_changed:
                await _log(f"  [计划更新] {json.dumps(plan_manager.to_log_dict(), ensure_ascii=False)}")

            if not msg.tool_calls:
                await _log("GPT 没有返回工具调用，结束")
                break

            # ── 多 Action 批量执行 ──────────────────────────────────────
            # 顺序执行所有 tool_calls，遇到页面跳转/done/ask_user 自动中断剩余队列
            await _log(f"\n>>> step={step+1} 收到 {len(msg.tool_calls)} 个 action")

            url_before = agent.page.url  # 记录执行前的 URL，用于检测页面跳转
            page_changed = False
            should_break_outer = False  # 是否需要跳出外层 for step 循环

            for tc_idx, tool_call in enumerate(msg.tool_calls):
                tool_name = tool_call.function.name

                # 解析参数
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    await _log(f"  [{tc_idx+1}/{len(msg.tool_calls)}] ❌ {tool_name} JSON 解析失败: {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"JSON 解析失败: {e}，请重新调用工具",
                    })
                    # 剩余 action 标记为 skipped
                    for remaining_tc in msg.tool_calls[tc_idx+1:]:
                        messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": "skipped"})
                    break

                await _log(f"  [{tc_idx+1}/{len(msg.tool_calls)}] {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

                # 记录到循环检测器
                loop_detector.record_action(tool_name, tool_args)

                # 拦截：上一步是 type_text 且没有 press_enter，GPT 就直接 done 了——说明忘记提交
                if tool_name == "done" and last_tool_name == "type_text" and not last_tool_pressed_enter:
                    await _log("  [拦截] 检测到输入后未提交就 done，强制要求先提交")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": "操作被拦截：你刚刚输入了内容但还没有提交。请先点击提交/发送按钮（或用 press_enter=true），再等待生成完成，最后才能 done。",
                    })
                    for remaining_tc in msg.tool_calls[tc_idx+1:]:
                        messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": "skipped"})
                    break

                # done/screenshot 前强制等待内容稳定（主循环层面兜底）
                if tool_name in ("done", "screenshot"):
                    await _log("  [wait_stable] 执行前等待内容稳定...")
                    wait_result = await _wait_for_page_ready(agent.page, log_fn=_log, timeout_ms=120000, check_network=True, active_requests=active_requests)
                    await _log(f"  [wait_stable] 结果: {wait_result}")

                # ── 视觉验证：action 前快照 ──────────────────────────────
                _snap_before = None
                if tool_name not in SKIP_VERIFY:
                    try:
                        _snap_before = await take_snapshot(agent.get_active_page())
                    except Exception:
                        pass

                result = await agent.execute(tool_name, tool_args)
                await _log(f"    result: {str(result)[:200]}")

                # ── 视觉验证：action 后对比 ──────────────────────────────
                if _snap_before is not None:
                    try:
                        _snap_after = await take_snapshot(agent.get_active_page())
                        _vr = verify_action(tool_name, tool_args, _snap_before, _snap_after, result)
                        _escalation = action_verifier.record(_vr)
                        if _vr.nudge and not _vr.changed:
                            await _log(f"  [视觉验证] {_vr.details}")
                            _pending_nudges.append(_vr.nudge)
                        if _escalation:
                            await _log(f"  [视觉验证] {_escalation}")
                            _pending_nudges.append(_escalation)
                    except Exception:
                        pass

                # navigate 后自动检查弹窗 + CAPTCHA
                if tool_name == "navigate":
                    await agent.dismiss_overlay()
                    await watchdog.check_captcha()

                # click 成功后保存 cookies
                if tool_name == "click" and not result.startswith("操作失败"):
                    try:
                        cookies = await context.cookies()
                        cookies_file.write_text(
                            json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception as e:
                        await _log(f"  ⚠ 保存 cookies 失败: {e}")

                # ── 处理 __DONE__ ──────────────────────────────────────
                if result == "__DONE__":
                    summary = tool_args.get("summary", "任务完成")
                    done_verified = await _verify_done(agent, task, summary, _log, llm_chat)
                    if done_verified:
                        await _log(f"\n✅ {summary}")
                        task_success = True
                        task_reason = summary
                        steps_executed = step + 1
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                        for remaining_tc in msg.tool_calls[tc_idx+1:]:
                            messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": "skipped"})
                        should_break_outer = True
                        break
                    else:
                        await _log(f"  ⚠ 验证未通过，要求 agent 继续执行")
                        result = (
                            "任务尚未真正完成。页面内容仍在加载或结果不符合预期。"
                            "请等待页面加载完成，或检查当前页面状态后继续操作。不要急于调用 done。"
                        )

                # ── 处理 ask_user ──────────────────────────────────────
                if result.startswith("__ASK_USER__:"):
                    parts = result.split("::", 1)
                    question = parts[0].replace("__ASK_USER__:", "").strip()
                    reason = parts[1].strip() if len(parts) > 1 else ""
                    if not question:
                        question = "需要您的输入"

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
                            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                            for remaining_tc in msg.tool_calls[tc_idx+1:]:
                                messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": "skipped"})
                            should_break_outer = True
                            break
                    else:
                        await _log("   ✗ 未配置 ask_user_callback，任务终止")
                        result = "无法获取用户输入，任务终止"
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                        for remaining_tc in msg.tool_calls[tc_idx+1:]:
                            messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": "skipped"})
                        should_break_outer = True
                        break

                # 失败计数 + 智能重试分析
                is_failure = (
                    result.startswith("操作失败") or
                    result.startswith("AI操作失败") or
                    result.startswith("AI 定位失败") or
                    result.startswith("输入失败")
                )
                if is_failure:
                    ft, ft_count, recovery_hint = failure_tracker.record_failure(tool_name, result)
                    await _log(f"  [失败追踪] {ft.value} #{ft_count} — {result[:80]}")
                    if recovery_hint:
                        result += f"\n[恢复建议] {recovery_hint}"
                        await _log(f"  [恢复建议] {recovery_hint}")
                    advice = _analyze_failure(tool_name, tool_args, result)
                    if advice:
                        result += f"\n[AI建议] {advice}"
                        await _log(f"  [AI建议] {advice}")
                else:
                    if failure_tracker.total_consecutive > 0:
                        await _log(f"  [失败追踪] 已重置（上次连续={failure_tracker.total_consecutive}）")
                    failure_tracker.record_success()

                # 记录上一步工具信息，供下一步拦截判断
                last_tool_name = tool_name
                last_tool_pressed_enter = (
                    tool_name == "type_text" and bool(tool_args.get("press_enter"))
                )

                # 记录本次 action 的 tool result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

                # ── 页面跳转检测：中断剩余 action 队列 ──────────────────
                if tool_name in TERMINATES_SEQUENCE and tc_idx < len(msg.tool_calls) - 1:
                    try:
                        url_after = agent.page.url
                    except Exception:
                        url_after = url_before

                    # URL 规范化比较（去 fragment、排序 query params）
                    url_changed = _normalize_url(url_after) != _normalize_url(url_before)

                    # SPA 检测：URL 不变时检查 DOM 内容 hash
                    dom_changed = False
                    if not url_changed:
                        try:
                            dom_hash = await agent.page.evaluate(
                                "() => document.title + '|' + document.body.innerText.substring(0, 500).length"
                            )
                            if hasattr(agent, '_last_dom_hash') and agent._last_dom_hash is not None and dom_hash != agent._last_dom_hash:
                                dom_changed = True
                            agent._last_dom_hash = dom_hash
                        except Exception:
                            pass

                    if url_changed or dom_changed:
                        page_changed = True
                        skipped_count = len(msg.tool_calls) - tc_idx - 1
                        change_type = "URL 变化" if url_changed else "DOM 内容变化"
                        await _log(f"  [multi_act] {change_type} ({url_before} → {url_after})，跳过剩余 {skipped_count} 个 action")

                        # OAuth/SSO 跨域跳转感知
                        if url_changed:
                            try:
                                domain_before = urlparse(url_before).netloc
                                domain_after = urlparse(url_after).netloc
                                if domain_before and domain_after and domain_before != domain_after:
                                    _pending_nudges.append(
                                        f"⚠️ 页面已跳转到第三方域名 ({domain_after})，"
                                        "这可能是 OAuth/SSO 登录流程。请在此页面完成登录操作，完成后会自动跳回原站。"
                                    )
                            except Exception:
                                pass

                        for remaining_tc in msg.tool_calls[tc_idx+1:]:
                            messages.append({"role": "tool", "tool_call_id": remaining_tc.id, "content": f"skipped: 页面已变化（{change_type}），后续操作取消"})
                        break
                    url_before = url_after  # 更新 URL 基准

            # 如果内层 break 要求跳出外层循环
            if should_break_outer:
                break

            # ── 循环检测：注入 nudge 到下一轮上下文 ──────────────────────
            try:
                _page_url = agent.page.url
                _page_len = await agent.page.evaluate("() => document.body.innerText.length")
            except Exception:
                _page_url = ""
                _page_len = 0
            loop_detector.record_page_fingerprint(_page_url, _page_len)

            is_loop, loop_nudge = loop_detector.check_loop()
            if is_loop:
                await _log(f"  [循环检测] {loop_nudge}")
                _pending_nudges.append(loop_nudge)

            # ── 计划停滞检测 ──────────────────────────────────────────
            stall_nudge = plan_manager.check_stall(step)
            if stall_nudge:
                await _log(f"  [计划停滞] {stall_nudge}")
                _pending_nudges.append(stall_nudge)

            # 检查是否应该终止（基于 FailureTracker 的分类计数）
            should_abort, abort_reason = failure_tracker.should_abort()
            if should_abort:
                await _log(f"\n⚠️  {abort_reason}，终止任务")
                task_reason = abort_reason
                steps_executed = step + 1
                break
        else:
            await _log("\n⚠️  达到最大步数限制")
            task_reason = f"达到最大步数限制({max_steps}步)"
            steps_executed = max_steps

        # 停止 Watchdog
        await watchdog.stop()

        # builtin 模式保存 cookies；user_chrome/cdp 模式不需要（浏览器本身保存）
        if browser_mode == "builtin":
            try:
                cookies = await context.cookies()
                cookies_file = Path(cookies_path)
                cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                await _log(f"✓ 登录态已保存至 {cookies_path}")
            except Exception as e:
                await _log(f"⚠ 保存 cookies 失败: {e}")

        # 关闭浏览器：池模式不关闭（由池管理），其他模式按原逻辑
        if browser_mode == "pool":
            # 池模式：只关闭 page，不关闭 browser/context（由 BrowserPool.release 处理）
            try:
                await page.close()
            except Exception:
                pass
        elif browser_mode == "cdp":
            await _log("  [CDP] 保持浏览器运行，不关闭")
        elif browser_mode == "user_chrome":
            await _log("  [user_chrome] 保持浏览器运行，用户可查看结果")
        elif browser_mode == "builtin" and browser:
            if headless:
                try:
                    await browser.close()
                except Exception as e:
                    await _log(f"⚠ 关闭浏览器失败: {e}")
            else:
                await _log("  🌐 浏览器保持打开，可手动查看结果。关闭浏览器窗口即可释放资源。")

        # 兜底：如果 AI 没调 done 但截图目录里有非调试截图，也算成功
        if not task_success:
            user_screenshots = [
                f for f in screenshots_dir.glob("*.*")
                if f.suffix.lower() in (".png", ".jpg", ".jpeg")
                and not f.stem.endswith("_annotated")
                and not f.stem.startswith("step_")
            ]
            if user_screenshots:
                await _log(f"  [兜底] AI 未调用 done，但发现 {len(user_screenshots)} 张用户截图，标记为成功")
                task_success = True
                if task_reason == "未知":
                    task_reason = "任务已完成（截图已保存）"

        # 输出成本统计
        cost_summary = cost_tracker.summary()
        if cost_summary["total_calls"] > 0:
            await _log(
                f"\n💰 [成本统计] 调用 {cost_summary['total_calls']} 次, "
                f"输入 {cost_summary['total_input_tokens']} tokens, "
                f"输出 {cost_summary['total_output_tokens']} tokens, "
                f"缓存命中 {cost_summary['cache_hit_rate']*100:.0f}%, "
                f"成本 ${cost_summary['total_cost_usd']}"
            )
            if _dom_tokens_saved > 0:
                await _log(f"  🌿 [DOM模式] 节省 ~{_dom_tokens_saved} 截图 tokens（{_dom_tokens_saved // 1100} 步使用 DOM 模式）")

        return {
            "success": task_success,
            "reason": task_reason,
            "steps": steps_executed,
            "cost": cost_summary,
        }
    finally:
        # 非池模式：清理 Playwright 实例
        if _pw_cm:
            try:
                await _pw_cm.stop()
            except Exception:
                pass
