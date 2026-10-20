"""pytest 配置：把项目根目录加入搜索路径，并保证没有密钥也能跑测试。

第二件事是为了解决一个实际问题：每个 Agent 模块在导入时就会构造 LLMClient，
构造函数发现没有 OPENAI_API_KEY 会直接抛错 —— 换一台机器 clone 下来，
整套测试会在**收集阶段**就崩掉（6 个文件 ERROR，一条用例都跑不到）。

测试里 LLM 一律用桩替换，所以这里补的占位值永远不会被真正发出请求；
它只负责让 import 通过。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "crw-placeholder-not-a-real-key"
if not os.getenv("OPENAI_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com/v1"
