"""测试执行轨迹的记录与落盘。"""
from workflow.trace import Trace, diff_state


def test_diff_detects_scalar_change():
    changed = diff_state({"round": 1}, {"round": 2})
    assert "round" in changed and "→" in changed["round"]


def test_diff_detects_new_key():
    assert diff_state({}, {"passed": True})["passed"].startswith("新增")


def test_diff_detects_length_change():
    changed = diff_state({"versions": ["a"]}, {"versions": ["a", "b"]})
    assert changed["versions"] == "长度 1 → 2"


def test_diff_ignores_unchanged():
    assert diff_state({"code": "x", "review": "y"}, {"code": "x", "review": "y"}) == {}


def test_trace_records_and_renders():
    trace = Trace(run_id="testrun")
    trace.record(1, "review", True, 12.3, "done", {"passed": "False → True"},
                 round_no=1, state={"issue_counts": {"Critical": 0}})
    trace.finish({"round": 1, "passed": True, "versions": []})

    assert len(trace.records) == 1
    md = trace.render_markdown()
    assert "`review`" in md and "通过" in md


def test_trace_save_writes_files(tmp_path):
    trace = Trace(run_id="abc")
    trace.record(1, "code", True, 1.0, "review", {}, round_no=None, state={})
    trace.finish({"round": 1, "passed": True})

    paths = trace.save(tmp_path / "run1")
    assert paths["jsonl"].exists() and paths["report"].exists()
    assert len(paths["jsonl"].read_text(encoding="utf-8").strip().splitlines()) == 1


def test_trace_renders_failures():
    trace = Trace(run_id="f")
    trace.record(1, "review", False, 1.0, "abort", {}, error="ValueError: 炸了", state={})
    trace.finish({})
    assert "失败记录" in trace.render_markdown()
    assert "ValueError: 炸了" in trace.render_markdown()
