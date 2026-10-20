"""工具模块：Agent 可调用的工具（代码执行）。

代码审查 Agent 除了静态解析（ast）之外，还需要「执行代码」这个工具，
用来复现运行时 Bug（除零、越界、NameError、类型错误等）。
"""
import os
import subprocess
import sys


class CodeRunner:
    """代码执行工具：在子进程里运行 Python 代码并捕获输出。"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def run(self, code: str, preamble: str = "") -> str:
        """运行代码，返回 stdout / stderr / 退出码。

        preamble 是「前置代码」：想测试被审查代码里的某个函数时，
        可先拼接整段源码，让子进程里能直接调用这些定义。
        """
        full_code = code
        if preamble:
            full_code = preamble.rstrip() + "\n\n" + code

        # 强制子进程用 UTF-8 输出，避免 Windows 中文乱码
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

        try:
            proc = subprocess.run(
                [sys.executable, "-c", full_code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"运行超时（>{self.timeout}s），已强制终止。可能存在死循环。"
        except Exception as e:  # noqa: BLE001 - 工具调用要兜底所有异常
            return f"代码执行失败：{e}"

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode == 0:
            return f"运行成功（退出码 0）\nstdout:\n{stdout or '(无输出)'}"
        return f"运行失败（退出码 {proc.returncode}）\nstderr:\n{stderr or '(无错误信息)'}"
