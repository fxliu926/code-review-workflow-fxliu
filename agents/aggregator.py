"""并行节点的合并函数。

并行分支各自拿到一份状态副本，跑完由引擎把「改动过的字段」覆盖回主状态。
但有些字段是**跨分支派生**的 —— 比如问题列表要累加、问题计数和是否放行
要看完所有分支才能算。这些业务含义引擎不知道，交给配置里指定的 merge 函数。

为什么不在引擎里直接把列表"追加"：那样上一轮的问题会残留到下一轮
（分支清空了自己的列表，却删不掉主状态里的旧条目），判定就永远翻不了身。
所以这里明确地从各分支的输出重新收集。
"""
from agents.reviewer import BLOCKING_LEVELS, count_by_level, render_review_markdown


def _dedupe(issues: list) -> list:
    """按 (等级, 描述) 去重，避免两个分支报同一件事。"""
    seen = set()
    result = []
    for item in issues:
        key = (item.get("level"), item.get("description"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def merge_issues(state: dict, branch_states: dict = None) -> dict:
    """汇总各分支报出的问题，重算等级计数、放行判定与审查报告。

    branch_states：{分支 id: {"state": 分支状态, "ok": 是否成功, "error": 错误}}
    不传时退化为直接使用当前 state 里已有的 issues（非并行场景也能复用）。
    """
    if branch_states:
        collected = []
        for branch_id, outcome in branch_states.items():
            if not outcome.get("ok"):
                continue
            collected.extend(outcome.get("state", {}).get("issues", []))
        issues = _dedupe(collected)
    else:
        issues = _dedupe(state.get("issues", []))

    counts = count_by_level(issues)
    state["issues"] = issues
    state["issue_counts"] = counts
    state["passed"] = sum(counts[level] for level in BLOCKING_LEVELS) == 0
    state["review"] = render_review_markdown({
        "summary": state.get("summary", ""),
        "issues": issues,
    })

    # 每轮都留一份问题快照。PSP 要追溯"缺陷何时发现、何时修复"，
    # 只看最后一轮的结果推不出来 —— 得留着每一轮的历史。
    state.setdefault("defect_log", []).append({
        "round": state.get("round"),
        "issues": list(issues),
    })
    return state
