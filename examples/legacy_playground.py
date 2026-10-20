"""[已废弃] 最早的硬编码版本：手动串起三个 LLM 调用，没有流程配置也没有复用。

保留下来是为了留存迭代痕迹 —— 正式入口是根目录的 main.py（YAML 配置 + 引擎）与 ui/app.py。
运行方式：python examples/legacy_playground.py
"""
from agents.llm_client import LLMClient

client = LLMClient()

# 共享状态：用一个 dict 表示
state = {"requirement": "用 Python 写一个除法函数"}

# 1. 编码 Agent
print("=== 1. 编码 ===")
state["code"] = client.chat([
    {"role": "user", "content": f"请根据以下需求写 Python 代码：\n{state['requirement']}"}
])
print(state["code"])

# 2. 审查 Agent
print("\n=== 2. 审查 ===")
state["review"] = client.chat([
    {"role": "user", "content": f"请审查以下代码，指出问题：\n{state['code']}"}
])
print(state["review"])

# 3. 修复 Agent
print("\n=== 3. 修复 ===")
state["fixed"] = client.chat([
    {"role": "user", "content": f"根据审查意见修复以下代码：\n{state['code']}\n\n审查意见：\n{state['review']}"}
])
print(state["fixed"])

# 查看最终状态
print("\n=== 最终状态 ===")
print(f"需求：{state['requirement']}")
print(f"原始代码：{state['code'][:80]}...")
print(f"修复后代码：{state['fixed'][:80]}...")