import os
import platform
from pathlib import Path


async def _find_chrome_user_data_dir() -> str | None:
    """自动检测系统上 Chrome/Edge 的 User Data 目录"""
    system = platform.system()
    home = Path.home()
    candidates = []
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates = [
            local / "Google/Chrome/User Data",
            local / "Microsoft/Edge/User Data",
            local / "Google/Chrome Beta/User Data",
        ]
    elif system == "Darwin":
        candidates = [
            home / "Library/Application Support/Google/Chrome",
            home / "Library/Application Support/Microsoft Edge",
        ]
    else:  # Linux
        candidates = [
            home / ".config/google-chrome",
            home / ".config/microsoft-edge",
            home / ".config/chromium",
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None
