"""本地测试 agent：独立事件循环 + 指定任务"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from agent import run_agent

async def main():
    Path("screenshots/test").mkdir(parents=True, exist_ok=True)
    await run_agent(
        "打开 https://felo.ai 截图保存为 a.png",
        headless=True,
        screenshots_dir="screenshots/test",
    )
    print("Agent 执行完成")

if __name__ == "__main__":
    asyncio.run(main())
