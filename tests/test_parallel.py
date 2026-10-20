"""测试并行节点。

覆盖三件事：分支确实同时跑、结果能合并、以及出错了怎么办。
"""
import time

import pytest

from agents.aggregator import merge_issues
from workflow.engine import ConfigError, NodeExecutionError, WorkflowEngine


def slow_agent(seconds, marker_key):
    def fn(state):
        time.sleep(seconds)
        state[marker_key] = True
        return state
    return fn


def test_parallel_runs_both_branches():
    engine = WorkflowEngine(verbose=False)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"])
    engine.add_node("a", slow_agent(0, "ran_a"), "done")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")

    state = engine.run({})
    assert state["ran_a"] and state["ran_b"]


def test_parallel_branches_run_concurrently():
    """两个各睡 0.3 秒的分支，并行总耗时应明显小于 0.6 秒。"""
    engine = WorkflowEngine(verbose=False)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"])
    engine.add_node("a", slow_agent(0.3, "ran_a"), "done")
    engine.add_node("b", slow_agent(0.3, "ran_b"), "done")

    state = engine.run({})
    records = {r["node"]: r for r in engine.last_trace.records}
    branch_total = records["p/a"]["duration_ms"] + records["p/b"]["duration_ms"]

    assert branch_total >= 600, "两个分支各睡了 0.3 秒"
    assert records["p"]["duration_ms"] < branch_total * 0.75, "并行总耗时应明显小于串行累加"


def test_merge_fn_receives_branch_states():
    seen = {}

    def spy_merge(state, branch_states):
        seen.update(branch_states)
        return state

    engine = WorkflowEngine(verbose=False)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"], merge=spy_merge)
    engine.add_node("a", slow_agent(0, "ran_a"), "done")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")

    engine.run({})
    assert set(seen) == {"a", "b"}
    assert seen["a"]["ok"] is True and seen["a"]["state"]["ran_a"] is True


def test_stale_issues_do_not_leak_between_runs():
    """回归：早期版本把列表"追加"合并，导致上一轮的问题残留到下一轮，判定永远翻不了身。"""
    issues_a = [{"level": "Major", "line": None, "description": "除零未处理",
                 "suggestion": "", "source": "stub"}]

    def branch_a(state):
        state["issues"] = list(issues_a)
        return state

    def branch_b(state):
        state["issues"] = []
        return state

    engine = WorkflowEngine(verbose=False)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"], merge=merge_issues)
    engine.add_node("a", branch_a, "done")
    engine.add_node("b", branch_b, "done")

    first = engine.run({})
    assert first["passed"] is False

    # 第二轮分支不再报问题 —— 合并结果也必须跟着清空
    issues_a.clear()
    second = engine.run({})
    assert second["issues"] == [], "上一轮的问题不应残留"
    assert second["passed"] is True


def test_parallel_branch_failure_aborts():
    def boom(state):
        raise RuntimeError("分支炸了")

    engine = WorkflowEngine(verbose=False, retry_backoff=0)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"])
    engine.add_node("a", boom, "done")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")

    with pytest.raises(NodeExecutionError):
        engine.run({})

    failed = [r for r in engine.last_trace.records if not r["ok"]]
    assert failed and "RuntimeError" in failed[0]["error"]


def test_parallel_branch_failure_can_be_skipped():
    def boom(state):
        raise RuntimeError("分支炸了")

    engine = WorkflowEngine(verbose=False, retry_backoff=0)
    engine.start = "p"
    engine.add_node("p", None, "next", parallel=["a", "b"], on_error="skip")
    engine.add_node("a", boom, "done")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")
    engine.add_node("next", slow_agent(0, "ran_next"), "done")

    state = engine.run({})
    assert state["ran_b"] is True, "另一个分支的结果仍需保留"
    assert state["ran_next"] is True, "skip 策略下流程应继续"


def test_tolerated_branch_does_not_fail_container():
    """辅助分支自己配了 skip：它挂了，另一分支的成果和整条流程都还在。"""
    def boom(state):
        raise RuntimeError("辅助分支炸了")

    engine = WorkflowEngine(verbose=False, retry_backoff=0)
    engine.start = "p"
    engine.add_node("p", None, "next", parallel=["a", "b"])
    engine.add_node("a", boom, "done", on_error="skip")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")
    engine.add_node("next", slow_agent(0, "ran_next"), "done")

    state = engine.run({})
    assert state["ran_b"] is True
    assert state["ran_next"] is True, "分支被容忍后，容器应视为成功并继续往下走"

    outcomes = {r["node"]: r["ok"] for r in engine.last_trace.records}
    assert outcomes["p/a"] is False, "失败仍要如实记录，不能假装没发生"
    assert outcomes["p"] is True


def test_failed_branch_state_is_not_merged():
    """失败的分支可能只写了一半状态，不该污染主状态。"""
    def half_done(state):
        state["partial"] = "半截数据"
        raise RuntimeError("写到一半就炸了")

    engine = WorkflowEngine(verbose=False, retry_backoff=0)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"])
    engine.add_node("a", half_done, "done", on_error="skip")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")

    state = engine.run({})
    assert "partial" not in state
    assert state["ran_b"] is True


def test_parallel_records_branch_traces():
    engine = WorkflowEngine(verbose=False)
    engine.start = "p"
    engine.add_node("p", None, "done", parallel=["a", "b"])
    engine.add_node("a", slow_agent(0, "ran_a"), "done")
    engine.add_node("b", slow_agent(0, "ran_b"), "done")

    engine.run({})
    nodes = [r["node"] for r in engine.last_trace.records]
    assert nodes == ["p/a", "p/b", "p"]


# ---------- 配置校验 ----------

YAML_TEMPLATE = """
start: p
nodes:
  - id: p
    {parallel}
    next: done
  - id: a
    agent: dummy
    next: done
  - id: q
    parallel: [p]
    next: done
"""


def write_yaml(tmp_path, body):
    p = tmp_path / "wf.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def dummy(state):
    return state


REGISTRY = {"dummy": dummy, "merge_issues": merge_issues}


def test_unknown_parallel_branch_rejected(tmp_path):
    body = YAML_TEMPLATE.format(parallel="parallel: [a, missing]")
    with pytest.raises(ConfigError, match="并行分支不存在"):
        WorkflowEngine().load_from_yaml(write_yaml(tmp_path, body), REGISTRY)


def test_nested_parallel_rejected(tmp_path):
    body = YAML_TEMPLATE.format(parallel="parallel: [a, q]")
    with pytest.raises(ConfigError, match="嵌套"):
        WorkflowEngine().load_from_yaml(write_yaml(tmp_path, body), REGISTRY)


def test_unknown_merge_fn_rejected(tmp_path):
    body = YAML_TEMPLATE.format(parallel="parallel: [a]\n    merge: nope")
    with pytest.raises(ConfigError, match="合并函数"):
        WorkflowEngine().load_from_yaml(write_yaml(tmp_path, body), REGISTRY)


def test_empty_parallel_rejected(tmp_path):
    body = YAML_TEMPLATE.format(parallel="parallel: []")
    with pytest.raises(ConfigError, match="非空列表"):
        WorkflowEngine().load_from_yaml(write_yaml(tmp_path, body), REGISTRY)
