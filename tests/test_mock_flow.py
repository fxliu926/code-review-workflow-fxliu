"""离线桩 Agent 的回归测试。

这些用例的意义：换一台没有 API key 的机器 clone 下来，也能端到端跑通整条流程。
同时它也是"可扩展性"的可运行证据 —— 同一份 YAML、同一个引擎，
只换掉 Agent 实现就照样跑通，引擎一行没改。
"""
import os

import pytest

from agents.aggregator import merge_issues
from agents.llm_client import NO_KEY_HINT, LLMClient
from agents.mock import DEMO_REQUIREMENT, V2, V3, mock_coder, mock_fixer, mock_linter
from agents.mock import mock_reviewer, mock_tester
from workflow.engine import WorkflowEngine

REGISTRY = {
    "coder": mock_coder,
    "reviewer": mock_reviewer,
    "linter": mock_linter,
    "fixer": mock_fixer,
    "tester": mock_tester,
    "merge_issues": merge_issues,
}


def run_mock_flow() -> tuple:
    engine = WorkflowEngine()
    engine.load_from_yaml("config/workflow.yaml", REGISTRY)
    state = engine.run({"requirement": DEMO_REQUIREMENT})
    return state, engine


def test_mock_flow_completes_without_llm():
    """整条流程跑完，且不依赖任何密钥。"""
    state, _ = run_mock_flow()
    assert state["passed"] is True
    assert state["round"] == 3
    assert state["tests_passed"] is True
    assert "4 通过 / 0 失败" in state["test_summary"]


def test_mock_flow_uses_same_config_as_real_run():
    """用的就是仓库里那份 workflow.yaml —— 换了 Agent，配置和引擎都没动。"""
    _, engine = run_mock_flow()
    assert set(engine.nodes) == {"code", "analyze", "review", "lint", "test", "fix"}


def test_mock_flow_hits_both_loops():
    """三轮跑出两种回炉：审查拦下一次，测试再拦下一次。"""
    state, engine = run_mock_flow()
    nodes = [r["node"] for r in engine.last_trace.records]

    # 审查回炉：analyze → fix
    assert "fix" in nodes
    # 测试回炉：test → fix → analyze → test
    assert nodes.count("test") == 2, "第二次测试是被失败的用例拉回来的"
    assert len(state["versions"]) == 3, "初版 + 两次修复"
    assert "isinstance" in state["code"], "最终版应补上入参类型校验"


def test_minor_does_not_block_the_flow():
    """规范检查每轮都报 Minor，但不该触发回炉 —— 只有 Critical / Major 才拦。"""
    state, engine = run_mock_flow()
    counts = state["issue_counts"]
    assert counts["Minor"] >= 1
    assert counts["Major"] == 0 and counts["Critical"] == 0


def test_mock_reviewer_only_blocks_first_round():
    """桩审查只在第 1 轮报 Major，用来演示条件分支。"""
    first = mock_reviewer({"round": 0})
    assert [i["level"] for i in first["issues"]] == ["Major"]
    assert mock_reviewer({"round": 1})["issues"] == []


def test_mock_tester_runs_real_code_runner():
    """桩测试只替换"生成"，执行仍走真实的子进程 —— 通过/失败是真跑出来的。"""
    # V2 补了空列表校验但没做类型校验：find_max(None) 抛的是 ValueError 而非 TypeError，
    # 于是 test_non_list_raises 会失败 —— 这正是第二轮被拉回修复的原因
    bad = mock_tester({"code": V2})
    assert bad["tests_passed"] is False
    assert "test_non_list_raises" in bad["test_failure"], "失败详情要留给修复 Agent"

    good = mock_tester({"code": V3})
    assert good["tests_passed"] is True
    assert len(good["test_cases"]) == 4


def test_llm_client_defers_key_check(monkeypatch):
    """缺密钥不该在导入/构造阶段就炸，否则整套测试和离线演示都跑不起来。"""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    client = LLMClient()          # 构造必须成功
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        client.chat([{"role": "user", "content": "hi"}])
    assert "mock" in NO_KEY_HINT, "报错里要给得出可操作的下一步"


def test_no_real_key_is_needed_for_any_import():
    """所有 Agent 模块在没有密钥时都能被导入（这条保护的是 collection 阶段）。"""
    import importlib

    for module in ("agents.coder", "agents.reviewer", "agents.linter",
                   "agents.fixer", "agents.tester", "agents.mock"):
        assert importlib.import_module(module) is not None
    assert os.getenv("OPENAI_API_KEY")
