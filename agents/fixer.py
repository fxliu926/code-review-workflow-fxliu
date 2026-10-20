from agents.llm_client import LLMClient
from agents.prompt import strip_code_block

client = LLMClient()


def fixer_agent(state: dict) -> dict:
    """根据审查意见修复代码，并把修复结果写回共享状态。

    关键约定：修复结果必须写回 `state["code"]`。
    如果只写进 `state["fixed"]`，下一轮 reviewer 读到的仍是未修复的初版代码，
    「审查 → 修复 → 再审」的回炉循环就形同虚设。

    同时把每一版代码快照追加到 `state["versions"]`，供 trace 与最终报告回溯。
    """
    original = state.get("code", "")
    review = state.get("review", "")

    # 测试环节跑出来的失败用例也要带上 —— 审查意见是"读代码读出来的问题"，
    # 测试失败是"真跑一遍炸出来的问题"，两者互补，修的时候都得看。
    failure = (state.get("test_failure") or "").strip()
    if failure:
        review = f"{review}\n\n## 单元测试失败的用例\n{failure}"

    fixed = strip_code_block(client.chat([
        {"role": "user", "content": f"根据审查意见修复以下代码：\n{original}\n\n审查意见：\n{review}"}
    ]))

    state["fixed"] = fixed      # 保留旧字段，兼容已有读取方
    state["code"] = fixed       # 关键：回写，让下一轮审查的是修好的版本
    state.setdefault("versions", []).append({
        "round": state.get("round", 0),
        "source": "fixer",
        "code": fixed,
    })
    return state