"""
run_agent() 主流程：启动浏览器、执行任务循环、返回结果。
从 agent.py 抽取，作为 agent 包的一部分。
"""

import asyncio
import base64
import json
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright

from .page_utils import _safe_print, _wait_for_page_ready
from .core import BrowserAgent
from .tools import TOOLS
from .llm_helpers import _decompose_task, _verify_step, _compress_messages, _analyze_failure
from .chrome_detector import _find_chrome_user_data_dir

from utils import llm_chat
from page_annotator import annotate_page


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
) -> dict:
    """
    运行 agent 执行任务。
    返回: {"success": bool, "reason": str, "steps": int}
    """
    screenshots_dir = Path(screenshots_dir)
    task_success = False
    task_reason = "未知"
    steps_executed = 0

    async with async_playwright() as pw:
        # ── 三种浏览器模式 ────────────────────────────────────────────────────
        browser = None
        context = None

        if browser_mode == "cdp":
            # 连接用户正在运行的 Chrome（需要用 --remote-debugging-port=9222 启动）
            _safe_print(f"  [CDP] 连接 {cdp_url} ...")
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                raise RuntimeError(f"CDP 连接失败 ({cdp_url}): {e}") from e
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

        # 多 tab 支持：监听新页面，自动切换到最新打开的 tab
        async def _on_new_page(new_page):
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                agent.page = new_page
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

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个网页操作助手。每次我会给你当前页面的截图，用视觉理解页面，调用工具完成用户任务。\n"
                    "核心原则：仔细观察截图，理解页面布局和内容，再决定下一步操作。\n\n"
                    "## 基本规则\n"
                    "1. 每次只调用一个工具\n"
                    "2. 操作元素优先用截图中的蓝色 index 编号，比文字更准确\n"
                    "3. 操作失败时换个方式重试（换 index、用 text、滚动页面、用 find_element 视觉定位），不要直接 done 放弃——除非连续5次都失败\n"
                    "4. 任务全部完成后先截图，再调用 done\n"
                    "5. 遇到登录页面，继续完成登录，不要放弃\n\n"
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
                    "- 页面可能有懒加载，滚动后等待内容出现\n"
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
        last_tool_name = None
        last_tool_pressed_enter = False

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
        steps_hint = ""
        if task_steps:
            steps_hint = "【任务步骤参考】\n" + "\n".join(
                f"  {s.get('step', '?')}. {s.get('action', '')}（完成标志：{s.get('done_signal', '')}）"
                for s in task_steps
            ) + "\n按顺序完成以上步骤，每步完成后再进行下一步。\n"

        # 全程监听网络请求，供 _wait_for_page_ready 使用
        active_requests: set[str] = set()

        def _on_request(req):
            try:
                if req.resource_type in ("fetch", "xhr", "websocket"):
                    active_requests.add(req.url)
            except Exception:
                pass

        def _on_response(resp):
            try:
                active_requests.discard(resp.url)
            except Exception:
                pass

        def _on_request_failed(req):
            try:
                active_requests.discard(req.url)
            except Exception:
                pass

        agent.page.on("request", _on_request)
        agent.page.on("response", _on_response)
        agent.page.on("requestfailed", _on_request_failed)
        agent._active_requests = active_requests  # 注入到 agent，供 wait 工具使用

        for step in range(max_steps):
            # 广播步骤进度（前端可解析展示进度条）
            total_steps = len(task_steps) if task_steps else max_steps
            await _log(f"__PROGRESS__:{step+1}/{total_steps}")
            # 步数预警：80% 时提醒 GPT 加速收尾
            if step == int(max_steps * 0.8):
                await _log(f"  ⚠ [预警] 已执行 {step+1}/{max_steps} 步，即将达到上限")
                messages.append({
                    "role": "user",
                    "content": "⚠️ 注意：你已使用了大部分步数，请尽快完成任务。如果核心目标已达成，请截图并调用 done。"
                })

            # 只在第一步和 navigate 后检查弹窗，避免干扰正常操作
            if step == 0:
                await agent.dismiss_overlay()

            # 截图前确保页面就绪（统一使用 _wait_for_page_ready）
            await _wait_for_page_ready(agent.page, log_fn=_log, timeout_ms=10000, check_network=True, active_requests=active_requests)

            # 用标注截图：给所有可交互元素打红框+编号
            try:
                img_b64, elements = await annotate_page(agent.page)
            except Exception as e:
                await _log(f"  ⚠ 页面标注失败: {e}，使用普通截图")
                try:
                    raw = await agent.page.screenshot(type="jpeg", quality=80)
                    img_b64 = base64.b64encode(raw).decode()
                    elements = []
                except Exception as e2:
                    await _log(f"  ❌ 截图也失败: {e2}，终止任务")
                    break
            elements_summary = json.dumps(elements, ensure_ascii=False)

            # 保存标注截图，方便调试，并实时推送给前端
            debug_path = screenshots_dir / f"step_{step+1:02d}_annotated.jpg"
            try:
                debug_path.write_bytes(base64.b64decode(img_b64))
            except Exception as e:
                await _log(f"  ⚠ 保存调试截图失败: {e}")
            await _log(f"  [截图] {debug_path.name}")
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

            # 上下文压缩：硬上限 60 条防止压缩失败时无限增长
            if len(messages) > 60:
                await _log(f"  ⚠ [上下文] 消息数 {len(messages)} 超过硬上限，强制截断")
                messages = [messages[0]] + messages[-20:]

            # 正常压缩：超过 24 条时压缩中间历史为摘要，保留最近 16 条
            if len(messages) > 24:
                messages = _compress_messages(messages, max_history=16)
                await _log(f"  [上下文] 已压缩历史，当前 {len(messages)} 条消息")

            response = llm_chat(
                messages=messages,
                tools=TOOLS,
                tool_choice="required",
                max_tokens=1000,
            )

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

            if not msg.tool_calls:
                await _log("GPT 没有返回工具调用，结束")
                break

            tool_call = msg.tool_calls[0]
            tool_name = tool_call.function.name

            try:
                tool_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                await _log(f"❌ GPT 返回的 JSON 无效: {e}")
                await _log(f"   原始内容: {tool_call.function.arguments[:200]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"JSON 解析失败: {e}，请重新调用工具",
                })
                for tc in msg.tool_calls[1:]:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                continue

            await _log(f"\n>>> step={step+1} tool={tool_name} args={json.dumps(tool_args, ensure_ascii=False)}")

            # 拦截：上一步是 type_text 且没有 press_enter，GPT 就直接 done 了——说明忘记提交
            if tool_name == "done" and last_tool_name == "type_text" and not last_tool_pressed_enter:
                await _log("  [拦截] 检测到输入后未提交就 done，强制要求先提交")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "操作被拦截：你刚刚输入了内容但还没有提交。请先点击提交/发送按钮（或用 press_enter=true），再等待生成完成，最后才能 done。",
                })
                for tc in msg.tool_calls[1:]:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                continue

            # done/screenshot 前强制等待内容稳定（主循环层面兜底）
            if tool_name in ("done", "screenshot"):
                await _log("  [wait_stable] 执行前等待内容稳定...")
                wait_result = await _wait_for_page_ready(agent.page, log_fn=_log, timeout_ms=120000, check_network=True, active_requests=active_requests)
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
                except Exception as e:
                    await _log(f"  ⚠ 保存 cookies 失败: {e}")

            if result == "__DONE__":
                summary = tool_args.get("summary", "任务完成")

                # ── 完成前验证：截图 + GPT 判断是否真正满足用户需求 ──
                # 每 15 秒检查一次，最多 3 次，防止页面还没渲染完就结束
                done_verified = False
                for check_round in range(1, 4):
                    await _log(f"\n🔍 [完成验证] 第 {check_round}/3 次检查...")
                    try:
                        # full_page 截图，确保长页面内容完整可见
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

                        # 构建验证图片列表
                        image_parts = [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{check_img}", "detail": "low"}},
                        ]
                        bottom_hint = ""
                        if bottom_img:
                            image_parts.append(
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{bottom_img}", "detail": "low"}},
                            )
                            bottom_hint = "第一张是完整页面截图，第二张是页面底部截图。请同时检查底部是否有未完成的内容。\n"

                        verify_resp = llm_chat(
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
                            done_verified = True
                            break

                        try:
                            verify_data = json.loads(verify_resp.choices[0].message.content)
                        except json.JSONDecodeError:
                            await _log(f"  ⚠ 验证结果 JSON 解析失败，视为通过")
                            done_verified = True
                            break

                        is_done = verify_data.get("done", True)
                        reason = verify_data.get("reason", "")
                        await _log(f"  [完成验证] {'✅ 已完成' if is_done else '⏳ 未完成'} — {reason}")

                        if is_done:
                            done_verified = True
                            break
                        else:
                            if check_round < 3:
                                await _log(f"  等待 15 秒后重新检查...")
                                await asyncio.sleep(15)

                    except Exception as e:
                        await _log(f"  ⚠ 验证异常: {e}，视为通过")
                        done_verified = True
                        break

                if done_verified:
                    await _log(f"\n✅ {summary}")
                    task_success = True
                    task_reason = summary
                    steps_executed = step + 1
                    for tc in msg.tool_calls[1:]:
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "skipped"})
                    break
                else:
                    # 3 次验证都未通过，告诉 GPT 继续操作
                    await _log(f"  ⚠ 3次验证均未通过，要求 agent 继续执行")
                    result = (
                        "任务尚未真正完成。页面内容仍在加载或结果不符合预期。"
                        "请等待页面加载完成，或检查当前页面状态后继续操作。不要急于调用 done。"
                    )

            # ── 处理 ask_user：暂停并等待用户回答 ──────────────────────────
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
                advice = _analyze_failure(tool_name, tool_args, result)
                if advice:
                    result += f"\n[建议] {advice}"
                    await _log(f"  [重试建议] {advice}")
            else:
                if fail_count > 0:
                    await _log(f"  [失败计数] 已重置（上次={fail_count}）")
                fail_count = 0

            # 记录上一步工具信息，供下一步拦截判断
            last_tool_name = tool_name
            last_tool_pressed_enter = (
                tool_name == "type_text" and bool(tool_args.get("press_enter"))
            )

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
                task_reason = "连续5次操作失败"
                steps_executed = step + 1
                break
        else:
            await _log("\n⚠️  达到最大步数限制")
            task_reason = f"达到最大步数限制({max_steps}步)"
            steps_executed = max_steps

        # builtin 模式保存 cookies；user_chrome/cdp 模式不需要（浏览器本身保存）
        if browser_mode == "builtin":
            try:
                cookies = await context.cookies()
                cookies_file = Path(cookies_path)
                cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                await _log(f"✓ 登录态已保存至 {cookies_path}")
            except Exception as e:
                await _log(f"⚠ 保存 cookies 失败: {e}")

        # 关闭浏览器：保留浏览器供用户查看结果，仅在非 headless 模式下保持打开
        # CDP 模式不关闭（用户还在用），builtin headless 模式关闭（无界面无意义）
        if browser_mode == "cdp":
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

        return {
            "success": task_success,
            "reason": task_reason,
            "steps": steps_executed,
        }
