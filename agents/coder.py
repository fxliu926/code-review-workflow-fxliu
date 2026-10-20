from agents.llm_client import LLMClient
from agents.prompt import build_coder_messages, strip_code_block

client = LLMClient()


def coder_agent(state: dict) -> dict:
    """根据需求生成初版代码，作为 `versions` 快照链表的第一项。"""
    requirement = state.get("requirement", "")
    code = strip_code_block(client.chat(build_coder_messages(requirement)))

    state["code"] = code
    state.setdefault("versions", []).append({
        "round": 0,
        "source": "coder",
        "code": code,
    })
    return state