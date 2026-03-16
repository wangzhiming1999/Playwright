import asyncio
import sys

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


async def _wait_for_page_ready(page, log_fn=None, timeout_ms: int = 15000, check_network: bool = True, active_requests: set = None) -> str:
    """
    统一的页面就绪等待函数，替代所有硬编码 sleep。

    等待策略（按顺序）：
    1. 等待执行上下文可用（页面导航完成）
    2. 等待 DOM 加载完成（domcontentloaded）
    3. 如果 check_network=True，等待网络请求结束
    4. 等待页面内容稳定（innerText 不再变化）

    智能判断：
    - 如果有活跃网络请求，说明页面在加载，耐心等
    - 如果内容在持续变化，说明在渲染，耐心等
    - 只有网络空闲 + 内容稳定同时满足才返回

    返回：状态描述字符串
    """
    async def _log(msg):
        if log_fn:
            await log_fn(msg)

    start_time = asyncio.get_event_loop().time()
    poll_interval = 0.1  # 100ms 轮询

    # 1. 等待执行上下文可用（页面导航完成）
    max_polls = int(timeout_ms / 100)
    for i in range(max_polls):
        try:
            await page.evaluate("() => document.readyState")
            break
        except Exception:
            if i % 10 == 0:
                await _log(f"  [wait] 等待页面上下文恢复... ({i*0.1:.1f}s)")
            await asyncio.sleep(poll_interval)
    else:
        return f"超时：页面上下文未恢复 ({timeout_ms}ms)"

    # 2. 等待 DOM 加载
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    # 3+4. 同时监测网络和内容，双条件满足才返回
    try:
        prev_len = await page.evaluate("() => document.body?.innerText?.length || 0")
    except Exception:
        prev_len = 0

    network_idle_count = 0   # 网络空闲连续计数
    content_stable_count = 0  # 内容稳定连续计数
    has_seen_activity = False  # 是否观察到过网络活动或内容变化

    remaining_ms = timeout_ms - int((asyncio.get_event_loop().time() - start_time) * 1000)
    max_checks = int(remaining_ms / 100)

    for i in range(max_checks):
        await asyncio.sleep(poll_interval)

        # 检查网络
        active = len(active_requests) if active_requests is not None else 0
        if active == 0:
            network_idle_count += 1
        else:
            network_idle_count = 0
            has_seen_activity = True

        # 检查内容
        try:
            curr_len = await page.evaluate("() => document.body?.innerText?.length || 0")
        except Exception:
            # 页面正在导航，重置一切
            curr_len = prev_len
            network_idle_count = 0
            content_stable_count = 0
            has_seen_activity = True
            continue

        delta = abs(curr_len - prev_len)
        if delta < 10:
            content_stable_count += 1
        else:
            content_stable_count = 0
            has_seen_activity = True

        prev_len = curr_len

        # 日志（每 2 秒打一次）
        if i > 0 and i % 20 == 0:
            elapsed = asyncio.get_event_loop().time() - start_time
            await _log(f"  [wait] {elapsed:.1f}s: 内容长度={curr_len} delta={delta} 活跃请求={active} 网络空闲={network_idle_count} 内容稳定={content_stable_count}")

        # 判断就绪条件
        # 如果从未观察到活动（页面本来就是静态的），快速返回
        if not has_seen_activity and content_stable_count >= 3 and network_idle_count >= 3:
            elapsed = asyncio.get_event_loop().time() - start_time
            return f"页面就绪 ({elapsed:.1f}s)"

        # 如果观察到过活动，需要更严格的稳定条件：
        # 网络空闲 >= 1.5 秒 且 内容稳定 >= 2 秒
        if has_seen_activity and network_idle_count >= 15 and content_stable_count >= 20:
            elapsed = asyncio.get_event_loop().time() - start_time
            return f"页面就绪 ({elapsed:.1f}s)"

    elapsed = asyncio.get_event_loop().time() - start_time
    return f"页面基本就绪 ({elapsed:.1f}s，内容可能仍在变化)"
