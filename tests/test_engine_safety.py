"""测试引擎的自我保护：配置校验、步数上限、节点错误恢复。

这些都是初版的坑 —— 配置写错没有任何提示，节点一抛异常整条流程就崩。
"""
import pytest

from workflow.engine import (
    ConfigError,
    NodeExecutionError,
    StepLimitExceeded,
    WorkflowEngine,
)


def write_yaml(tmp_path, content):
    p = tmp_path / "wf.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


def dummy(state):
    state.setdefault("count", 0)
    state["count"] += 1
    return state


REGISTRY = {"dummy": dummy, "boom": lambda s: (_ for _ in ()).throw(RuntimeError("炸"))}


# ---------- 加载期校验 ----------

@pytest.mark.parametrize("yaml_text,keyword", [
    ("nodes:\n  - id: a\n    agent: dummy\n    next: done\n", "start"),
    ("start: a\nnodes: []\n", "nodes"),
    ("start: a\nnodes:\n  - agent: dummy\n    next: done\n", "id"),
    ("start: a\nnodes:\n  - id: a\n    next: done\n", "agent"),
    ("start: a\nnodes:\n  - id: a\n    agent: nope\n    next: done\n", "未注册"),
    ("start: a\nnodes:\n  - id: a\n    agent: dummy\n    next: b\n", "不存在"),
    ("start: z\nnodes:\n  - id: a\n    agent: dummy\n    next: done\n", "start"),
])
def test_invalid_config_rejected(tmp_path, yaml_text, keyword):
    engine = WorkflowEngine()
    with pytest.raises(ConfigError) as exc:
        engine.load_from_yaml(write_yaml(tmp_path, yaml_text), REGISTRY)
    assert keyword in str(exc.value)


def test_duplicate_node_id_rejected(tmp_path):
    yaml_text = (
        "start: a\n"
        "nodes:\n"
        "  - id: a\n    agent: dummy\n    next: done\n"
        "  - id: a\n    agent: dummy\n    next: done\n"
    )
    with pytest.raises(ConfigError, match="重复"):
        WorkflowEngine().load_from_yaml(write_yaml(tmp_path, yaml_text), REGISTRY)


# ---------- 运行期保护 ----------

def test_step_limit_stops_infinite_loop():
    engine = WorkflowEngine(max_steps=5, verbose=False)
    engine.start = "a"
    engine.add_node("a", dummy, "b")
    engine.add_node("b", dummy, "a")
    with pytest.raises(StepLimitExceeded):
        engine.run({})


def test_node_error_aborts_by_default():
    engine = WorkflowEngine(verbose=False, max_node_retries=0, retry_backoff=0)
    engine.start = "boom"
    engine.add_node("boom", REGISTRY["boom"], "done")
    with pytest.raises(NodeExecutionError):
        engine.run({})


def test_node_retries_then_succeeds():
    calls = []

    def flaky(state):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("网络抖了一下")
        state["ok"] = True
        return state

    engine = WorkflowEngine(verbose=False, max_node_retries=2, retry_backoff=0)
    engine.start = "a"
    engine.add_node("a", flaky, "done")
    state = engine.run({})

    assert state["ok"] is True
    assert len(calls) == 3


def test_skip_policy_keeps_running():
    """on_error: skip 时该节点失败不应拖垮整条流程。"""
    engine = WorkflowEngine(verbose=False, max_node_retries=0, retry_backoff=0)
    engine.start = "boom"
    engine.add_node("boom", REGISTRY["boom"], "next", on_error="skip")
    engine.add_node("next", dummy, "done")

    state = engine.run({"count": 0})
    assert state["count"] == 1
    assert engine.last_trace.records[0]["ok"] is False


def test_goto_policy_redirects_on_failure():
    engine = WorkflowEngine(verbose=False, max_node_retries=0, retry_backoff=0)
    engine.start = "boom"
    engine.add_node("boom", REGISTRY["boom"], "dead", on_error={"goto": "recover"})
    engine.add_node("recover", dummy, "done")

    state = engine.run({"count": 0})
    assert state["count"] == 1
    assert [r["node"] for r in engine.last_trace.records] == ["boom", "recover"]


def test_trace_captured_even_when_aborted():
    """中断也要留下轨迹，否则排查无从下手。"""
    engine = WorkflowEngine(verbose=False, max_node_retries=0, retry_backoff=0)
    engine.start = "boom"
    engine.add_node("boom", REGISTRY["boom"], "done")
    with pytest.raises(NodeExecutionError):
        engine.run({})

    records = engine.last_trace.records
    assert len(records) == 1
    assert records[0]["ok"] is False
    assert "RuntimeError" in records[0]["error"]
