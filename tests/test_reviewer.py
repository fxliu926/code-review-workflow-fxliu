"""测试审查 Agent 的结构化解析与通过判定。

重点覆盖旧实现的两个坑：
1. 靠子串匹配 "Critical"/"Major" 判断通过与否 —— 措辞一变就误判；
2. LLM 不按格式输出时没有重试与降级。
"""
import json
import pytest
from pathlib import Path

import agents.reviewer as reviewer_mod
from agents.reviewer import (
    ReviewParseError,
    count_by_level,
    normalize_level,
    parse_review,
    render_review_markdown,
    reviewer_agent,
)
from workflow.engine import WorkflowEngine

YAML_PATH = Path(__file__).parent.parent / "config" / "workflow.yaml"


def _review_json(issues, summary="ok"):
    return json.dumps({"summary": summary, "issues": issues}, ensure_ascii=False)


class StubInspector:
    """替身：不真的起子进程，也不制造本地问题。"""

    def __init__(self, result: dict = None):
        self.result = result or {
            "units": [], "unit_count": 0, "unit_names": [],
            "static_error": "", "runtime": "", "runtime_enabled": False,
        }

    def inspect(self, code: str) -> dict:
        return dict(self.result)


@pytest.fixture(autouse=True)
def stub_inspector(monkeypatch):
    """默认用替身，保证测试不依赖真实子进程。"""
    monkeypatch.setattr(reviewer_mod, "INSPECTOR_FACTORY", StubInspector)
    return StubInspector


@pytest.fixture
def mock_llm(monkeypatch):
    """把 LLM 的返回按顺序喂给 reviewer。"""
    def _set(*responses):
        calls = []
        it = iter(responses)

        def fake_chat(messages):
            calls.append(messages)
            return next(it)

        monkeypatch.setattr(reviewer_mod.client, "chat", fake_chat)
        return calls
    return _set


# ---------- 解析层 ----------

def test_parse_strips_code_fence(mock_llm):
    raw = "```json\n" + _review_json([{"level": "Minor", "description": "命名随意"}]) + "\n```"
    parsed = parse_review(raw)
    assert len(parsed["issues"]) == 1
    assert parsed["issues"][0]["level"] == "Minor"


def test_parse_tolerates_surrounding_text():
    raw = "好的，这是我的审查意见：\n" + _review_json([]) + "\n希望对你有帮助。"
    assert parse_review(raw)["issues"] == []


def test_parse_rejects_missing_issues():
    with pytest.raises(ReviewParseError):
        parse_review('{"summary": "缺少 issues"}')


def test_parse_rejects_issue_without_description():
    with pytest.raises(ReviewParseError):
        parse_review(_review_json([{"level": "Minor"}]))


def test_parse_rejects_non_json():
    with pytest.raises(ReviewParseError):
        parse_review("这段代码看起来没什么问题。")


# ---------- 等级归一化 ----------

@pytest.mark.parametrize("raw,expected", [
    ("Critical", "Critical"), ("critical", "Critical"),
    ("严重", "Critical"), ("blocker", "Critical"),
    ("Major", "Major"), ("high", "Major"), ("重要", "Major"),
    ("Minor", "Minor"), ("nit", "Minor"), ("建议", "Minor"),
    ("看不懂的等级", "Major"),          # 未知等级保守处理为 Major
    (None, "Major"),
])
def test_normalize_level(raw, expected):
    assert normalize_level(raw) == expected


def test_count_by_level_fills_zero():
    issues = [{"level": "Critical"}, {"level": "Critical"}, {"level": "Minor"}]
    assert count_by_level(issues) == {"Critical": 2, "Major": 0, "Minor": 1}


def test_render_markdown_contains_level_and_line():
    parsed = {"summary": "s", "issues": [{"level": "Critical", "line": 12,
                                          "description": "除零", "suggestion": "加判断"}]}
    md = render_review_markdown(parsed)
    assert "Critical" in md and "第 12 行" in md and "加判断" in md


# ---------- 通过判定 ----------

def test_only_minor_issues_passes(mock_llm):
    mock_llm(_review_json([{"level": "Minor", "description": "命名随意"}], summary="质量尚可"))
    state = reviewer_agent({"code": "def f(a, b): return a / b", "round": 0})

    assert state["passed"] is True, "只有 Minor 问题应当放行"
    assert state["issue_counts"] == {"Critical": 0, "Major": 0, "Minor": 1}
    assert state["round"] == 1
    assert state["degraded"] is False


def test_critical_issue_blocks(mock_llm):
    mock_llm(_review_json([
        {"level": "Critical", "line": 2, "description": "除数为零会崩溃"},
        {"level": "Minor", "description": "缺少注释"},
    ]))
    state = reviewer_agent({"code": "def f(a, b): return a / b", "round": 0})
    assert state["passed"] is False
    assert state["issue_counts"]["Critical"] == 1


def test_no_issues_passes(mock_llm):
    mock_llm(_review_json([]))
    assert reviewer_agent({"code": "def f(): pass", "round": 0})["passed"] is True


def test_retry_then_succeed(mock_llm):
    """第一次输出不合规，重试后拿到合法 JSON。"""
    calls = mock_llm("这段代码挺好的。", _review_json([]))
    state = reviewer_agent({"code": "def f(): pass", "round": 0})

    assert len(calls) == 2, "应当发起第二次（纠错）请求"
    assert "重新输出" in calls[1][-1]["content"], "第二次应是纠错 Prompt"
    assert state["passed"] is True
    assert state["degraded"] is False


def test_degrades_safely_when_always_invalid(mock_llm):
    """重试仍失败时降级：保守判为不通过，并记下原因。"""
    calls = mock_llm("输出一坨非 JSON。", "还是不行。")
    state = reviewer_agent({"code": "def f(): pass", "round": 0})

    assert len(calls) == 2
    assert state["passed"] is False, "无法确认质量时应保守不放行"
    assert state["degraded"] is True
    assert len(state["parse_errors"]) == 2


# ---------- 与引擎联动 ----------

def _clean_reviewer(state):
    """桩：永远返回「没有问题」。"""
    state["review"] = "未发现问题。"
    state["issues"] = []
    state["issue_counts"] = {"Critical": 0, "Major": 0, "Minor": 0}
    state["passed"] = True
    state["round"] = state.get("round", 0) + 1
    return state


def _tester_pass(state):
    """桩测试 Agent：标记通过，避免额外的修复轮次。"""
    state["tests_passed"] = True
    return state


def test_engine_stops_early_when_passed():
    """审查通过时不应再进入 fix 节点。"""
    fix_calls = []

    def coder(state):
        state["code"] = "# v1\n"
        return state

    def fixer(state):
        fix_calls.append(1)
        return state

    engine = WorkflowEngine()
    engine.load_from_yaml(str(YAML_PATH),
                          {"coder": coder, "reviewer": _clean_reviewer, "fixer": fixer,
                           "linter": lambda s: s, "merge_issues": lambda s: s,
                           "tester": _tester_pass})
    state = engine.run({"requirement": "写个除法函数"})

    assert fix_calls == [], "通过时不应触发修复"
    assert state["round"] == 1
    assert state["passed"] is True
