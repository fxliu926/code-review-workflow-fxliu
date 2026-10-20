"""回归测试：回炉循环必须让每一轮审查的是「最新修复后」的代码。

背景缺陷：fixer 原先只把结果写进 state["fixed"]，而 reviewer 读的是 state["code"]，
导致第 2、3 轮审查的仍是第 1 版原始代码，循环形同虚设。
"""
import pytest
from pathlib import Path

import agents.fixer as fixer_mod
from agents.aggregator import merge_issues
from agents.fixer import fixer_agent
from agents.linter import linter_agent
from workflow.engine import WorkflowEngine

YAML_PATH = Path(__file__).parent.parent / "config" / "workflow.yaml"

def TesterStub(state):
    """桩测试 Agent：直接标记通过，不额外触发回炉。"""
    state["tests_passed"] = True
    return state


# 真实配置里有 linter / merge_issues / tester 这些节点，测试注册表也要带上
EXTRA_NODES = {"linter": linter_agent, "merge_issues": merge_issues,
               "tester": TesterStub}


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 LLMClient.chat，避免测试真实调用 LLM。"""
    def fake_chat(messages):
        return "# v%d fixed\n" % (len(fake_chat.calls) + 1)

    fake_chat.calls = []
    monkeypatch.setattr(fixer_mod.client, "chat", fake_chat)
    return fake_chat


def test_fixer_writes_back_to_code(fake_llm):
    """修复结果必须写回 state["code"]，而不是只有 state["fixed"]。"""
    state = {"code": "# v1\n", "review": "除零未处理", "round": 1}
    out = fixer_agent(state)

    assert out["code"] == "# v1 fixed", "修复结果应写回 code（strip_code_block 会去掉尾换行）"
    assert out["fixed"] == out["code"], "旧字段应与 code 保持一致，避免下游读到两个版本"


def test_fixer_records_version_snapshot(fake_llm):
    """每轮修复都要留一份代码快照，供 trace 与报告回溯。"""
    state = {"code": "# v1\n", "review": "除零未处理", "round": 2}
    out = fixer_agent(state)

    assert len(out["versions"]) == 1
    assert out["versions"][0]["round"] == 2
    assert out["versions"][0]["source"] == "fixer"


def _make_loop_engine(seen):
    """用桩 Agent 组装真实引擎（读真实 workflow.yaml），记录每轮被审查的代码。"""
    def coder(state):
        state["code"] = "# v1\n"
        state.setdefault("versions", []).append({"round": 0, "source": "coder", "code": state["code"]})
        return state

    def reviewer(state):
        seen.append(state["code"])
        # 放行与否由合并函数按 issues 重算，所以桩必须真的写出一条阻塞级问题
        state["review"] = "Critical: 除零未处理"
        state["issues"] = [{
            "level": "Major", "line": None,
            "description": "除数为零未处理", "suggestion": "加判断", "source": "stub",
        }]
        state["passed"] = False
        state["round"] = state.get("round", 0) + 1
        return state

    def fixer(state):
        fixed = "# v%d\n" % (len(seen) + 1)
        state["fixed"] = fixed
        state["code"] = fixed
        state.setdefault("versions", []).append({"round": state["round"], "source": "fixer", "code": fixed})
        return state

    engine = WorkflowEngine()
    engine.load_from_yaml(str(YAML_PATH),
                          {"coder": coder, "reviewer": reviewer, "fixer": fixer, **EXTRA_NODES})
    return engine


def test_each_round_reviews_fresh_code():
    """三轮循环里，每轮喂给 reviewer 的代码都必须不同。"""
    seen = []
    state = _make_loop_engine(seen).run({"requirement": "写个除法函数"})

    assert len(seen) == 3, "应跑满 3 轮（3 次审查 + 2 次修复）"
    assert seen[0] != seen[1] != seen[2], "每轮审查的代码必须发生变化，否则回炉失效"
    assert state["code"] == seen[-1], "最终产物应是最后一次修复的结果"
    assert state["code"].startswith("# v3")


BUGGY_CODE = (
    "from typing import List\n"
    "\n"
    "def find_second_largest(numbers: List[int]) -> int:\n"
    "    unique = sorted(set(numbers))\n"
    "    return unique[-2]\n"
)

FIXED_CODE = (
    "from typing import List, Optional\n"
    "\n"
    "def find_second_largest(numbers: List[int]) -> Optional[int]:\n"
    "    if len(numbers) < 2:\n"
    "        return None\n"
    "    unique = sorted(set(numbers))\n"
    "    return unique[-2]\n"
)


def test_tool_finding_triggers_fix_loop(monkeypatch):
    """工具查出运行时崩溃 → 不放行 → 修复 → 再审通过，整条链路要跑通。

    用桩替换 LLM 与 coder/fixer，但保留真实的 Inspector（真跑子进程），
    这样验证的是"本地工具 + 引擎循环"的联动，不受模型输出影响。
    """
    from pathlib import Path

    import agents.reviewer as reviewer_mod
    from agents.inspector import Inspector
    from agents.reviewer import reviewer_agent

    monkeypatch.setattr(reviewer_mod.client, "chat",
                        lambda messages: '{"summary": "看起来没问题", "issues": []}')

    def coder(state):
        state["code"] = BUGGY_CODE
        return state

    def fixer(state):
        state["code"] = FIXED_CODE
        return state

    def reviewer(state):
        return reviewer_agent(state, inspector=Inspector(timeout=5))

    engine = WorkflowEngine(verbose=False)
    engine.load_from_yaml(
        str(Path(__file__).parent.parent / "config" / "workflow.yaml"),
        {"coder": coder, "reviewer": reviewer, "fixer": fixer, **EXTRA_NODES},
    )

    state = engine.run({"requirement": "找出列表第二大的元素"})

    assert state["round"] == 2, "第一轮应被工具查出的问题拦下，修完再审才通过"
    assert state["passed"] is True
    assert state["code"] == FIXED_CODE

    # 轨迹里并行节点的两个分支各占一条，容器本身再记一条；
    # 最后一轮审查通过后还要走一次单元测试
    nodes = [r["node"] for r in engine.last_trace.records]
    assert nodes == [
        "code",
        "analyze/review", "analyze/lint", "analyze",
        "fix",
        "analyze/review", "analyze/lint", "analyze",
        "test",
    ]
    """三轮循环里，每轮喂给 reviewer 的代码都必须不同。"""
    seen = []
    state = _make_loop_engine(seen).run({"requirement": "写个除法函数"})

    assert len(seen) == 3, "应跑满 3 轮（3 次审查 + 2 次修复）"
    assert seen[0] != seen[1] != seen[2], "每轮审查的代码必须发生变化，否则回炉失效"
    assert state["code"] == seen[-1], "最终产物应是最后一次修复的结果"
    assert state["code"].startswith("# v3")
