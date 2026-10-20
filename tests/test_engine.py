"""测试 Workflow 引擎。"""
import pytest
from workflow.engine import WorkflowEngine


def dummy_agent(state):
    state["count"] = state.get("count", 0) + 1
    return state


def test_sequential_run():
    """测顺序执行。"""
    engine = WorkflowEngine()
    engine.start = "a"
    engine.add_node("a", dummy_agent, "b")
    engine.add_node("b", dummy_agent, "done")
    state = engine.run({"count": 0})
    assert state["count"] == 2


def test_conditional_branch():
    """测条件分支。"""
    engine = WorkflowEngine()
    engine.start = "a"

    def next_fn(state):
        return "done" if state.get("flag") else "b"

    engine.add_node("a", dummy_agent, next_fn)
    engine.add_node("b", dummy_agent, "done")

    state = engine.run({"flag": True})
    assert state["count"] == 1


def test_missing_node():
    """测节点不存在。"""
    engine = WorkflowEngine()
    engine.start = "missing"
    with pytest.raises(ValueError):
        engine.run({})


def test_load_from_yaml(tmp_path):
    """测 YAML 加载。"""
    yaml_content = """
start: a
nodes:
  - id: a
    agent: dummy
    next: done
"""
    p = tmp_path / "workflow.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    engine = WorkflowEngine()
    engine.load_from_yaml(str(p), {"dummy": dummy_agent})
    state = engine.run({})
    assert state["count"] == 1