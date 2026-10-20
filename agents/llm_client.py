"""LLM 调用模块：封装 OpenAI SDK，支持重试和错误处理。"""
import os
import time

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


NO_KEY_HINT = (
    "未设置 OPENAI_API_KEY。请复制 .env.example 为 .env 并填入密钥；"
    "只想验证流程编排可以跑 `python main.py --mock`（不需要密钥）。"
)


class LLMClient:
    """封装 LLM 调用。

    **密钥改成延迟校验**：原先在构造时就检查，而各 Agent 模块在导入阶段就会
    构造 client，导致没配密钥的机器上一 import 就崩 —— 测试套件在收集阶段
    就挂、离线演示也跑不起来。现在构造只记下配置，真正发起请求时才校验，
    并给出可操作的提示。
    """

    def __init__(self, model: str = "deepseek-chat", max_retries: int = 3):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.model = model
        self.max_retries = max_retries
        self._client = None

    @property
    def client(self):
        """延迟创建底层客户端，避免导入阶段就因为缺少密钥而失败。"""
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def chat(self, messages: list) -> str:
        """调用 LLM，返回文本。失败时重试。"""
        # 缺密钥不是"网络抖动"，不该进重试循环白等 7 秒
        if not self.api_key:
            raise ValueError(NO_KEY_HINT)

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.2,
                )
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                print(f"[重试 {attempt + 1}/{self.max_retries}] {e}，{wait}s 后重试")
                time.sleep(wait)
        raise RuntimeError(f"LLM 调用失败：{last_error}")
