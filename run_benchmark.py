"""
快速运行 E2E benchmark，验证 agent 在真实网站上的成功率。

用法：
  python run_benchmark.py                    # 跑全部 basic 场景
  python run_benchmark.py --real             # 跑真实网站场景
  python run_benchmark.py --all              # 跑所有场景
  python run_benchmark.py --category search  # 只跑搜索类
  python run_benchmark.py --compare          # 与上次基线对比
  python run_benchmark.py --headful          # 有头模式（可视化）
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载 .env（与 app.py 保持一致）
from dotenv import load_dotenv
load_dotenv()

from tests.e2e.benchmark import main

if __name__ == "__main__":
    main()
