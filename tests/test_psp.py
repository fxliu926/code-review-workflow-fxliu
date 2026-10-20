"""测试 PSP 度量与缺陷追踪。

对应课程第 2 讲的四个核心度量：Size / Effort / Quality / Schedule，
以及"缺陷何时发现、何时修复"的追踪。
"""
from workflow.psp import (
    DefectLog,
    MAX_PLANNED_ROUNDS,
    count_loc,
    effort_by_phase,
    render_defect_table,
    render_psp_markdown,
    summarize,
)

SAMPLE_CODE = '''def average(numbers: list) -> float:
    """求平均值。"""
    # 先检查空列表
    if not numbers:
        raise ValueError("列表不能为空")

    return sum(numbers) / len(numbers)
'''


# ---------- Size ----------

def test_count_loc():
    loc = count_loc(SAMPLE_CODE)
    assert loc["total"] == 7
    assert loc["functions"] == 1
    assert loc["comment"] == 1, "井号开头的行算注释"
    assert loc["blank"] == 1
    assert loc["code"] == 5, "docstring 是字符串不是注释，计入代码行"


def test_count_loc_empty():
    loc = count_loc("")
    assert loc["total"] == 0 and loc["functions"] == 0


def test_count_loc_syntax_error_still_counts_lines():
    """语法不通也要报行数，只是数不出函数。"""
    loc = count_loc("def f(:\n    pass\n")
    assert loc["total"] == 2
    assert loc["functions"] == 0


def test_size_growth_between_versions():
    state = {
        "code": SAMPLE_CODE,
        "versions": [{"code": "def f(): pass\n"}, {"code": SAMPLE_CODE}],
    }
    size = summarize(state, [])["size"]
    assert size["versions"] == 2
    assert size["growth"] == 4, "初版 1 行代码，最终版 5 行"


# ---------- Effort ----------

RECORDS = [
    {"node": "code", "duration_ms": 1757.2},
    {"node": "analyze/review", "duration_ms": 3507.0},
    {"node": "analyze/lint", "duration_ms": 0.5},
    {"node": "analyze", "duration_ms": 3507.7},
    {"node": "fix", "duration_ms": 4267.4},
]


def test_effort_sums_each_phase():
    effort = effort_by_phase(RECORDS)
    assert effort["phases"]["编码"] == 1757.2
    assert effort["phases"]["审查"] == 3507.0
    assert effort["phases"]["规范检查"] == 0.5
    assert effort["phases"]["修复"] == 4267.4


def test_container_is_not_counted_twice():
    """并行容器的耗时已经等于最慢分支，再累加一遍就重复了。"""
    effort = effort_by_phase(RECORDS)
    assert effort["container_ms"] == 3507.7
    assert abs(effort["total_ms"] - (1757.2 + 3507.0 + 0.5 + 4267.4)) < 0.01, (
        "容器耗时不能混进阶段合计"
    )


def test_parallel_saving_is_reported():
    """两个慢分支并行时，节省应该算得出来。"""
    effort = effort_by_phase([
        {"node": "p/a", "duration_ms": 1000.0},
        {"node": "p/b", "duration_ms": 1000.0},
        {"node": "p", "duration_ms": 1010.0},
    ])
    assert effort["branch_ms"] == 2000.0
    assert abs(effort["parallel_saving"] - 0.495) < 0.01


def test_parallel_saving_zero_without_branches():
    assert effort_by_phase([{"node": "code", "duration_ms": 100.0}])["parallel_saving"] == 0.0


# ---------- Quality / 缺陷追踪 ----------

def _issue(level, desc, source="linter"):
    return {"level": level, "description": desc, "source": source}


def test_defect_fixed_next_round():
    log = DefectLog()
    log.add(1, [_issue("Major", "空列表会崩"), _issue("Minor", "缺 docstring")])
    log.add(2, [_issue("Minor", "缺 docstring")])

    life = {d["description"]: d for d in log.lifecycle()}
    assert life["空列表会崩"]["status"] == "已修复"
    assert life["空列表会崩"]["found_round"] == 1
    assert life["空列表会崩"]["fixed_round"] == 2
    assert life["空列表会崩"]["survived"] == 1
    assert life["缺 docstring"]["status"] == "遗留"
    assert life["缺 docstring"]["fixed_round"] is None


def test_defect_reappears_counts_as_remaining():
    """最后一轮还在的问题就是遗留，不管中间消失过没有。"""
    log = DefectLog()
    log.add(1, [_issue("Major", "A")])
    log.add(2, [])
    log.add(3, [_issue("Major", "A")])

    summary = log.summary()
    assert summary["found"] == 1
    assert summary["remaining"] == 1, "最后一轮又出现了，应算遗留"


def test_quality_summary():
    log = DefectLog()
    log.add(1, [_issue("Major", "A", "runner"), _issue("Minor", "B")])
    log.add(2, [_issue("Minor", "B")])

    s = log.summary()
    assert s["found"] == 2 and s["fixed"] == 1 and s["remaining"] == 1
    assert s["fix_rate"] == 0.5
    assert s["by_level"] == {"Major": 1, "Minor": 1}
    assert s["by_source"] == {"runner": 1, "linter": 1}


def test_empty_defect_log():
    s = DefectLog().summary()
    assert s["found"] == 0 and s["fix_rate"] == 0.0


def test_defect_density_per_100_lines():
    state = {"code": SAMPLE_CODE, "defect_log": [
        {"round": 1, "issues": [_issue("Minor", f"问题{i}") for i in range(5)]},
    ]}
    quality = summarize(state, [])["quality"]
    # SAMPLE_CODE 有 5 行有效代码，5 个缺陷 → 100 / 100 行
    assert quality["density_per_100loc"] == 100.0


# ---------- Schedule ----------

def test_schedule_within_budget():
    state = {"code": SAMPLE_CODE, "round": 2, "passed": True}
    s = summarize(state, [])["schedule"]
    assert s["planned_rounds"] == MAX_PLANNED_ROUNDS
    assert s["actual_rounds"] == 2
    assert s["within_budget"] is True


def test_schedule_over_budget():
    state = {"code": SAMPLE_CODE, "round": 4, "passed": False}
    s = summarize(state, [])["schedule"]
    assert s["within_budget"] is False


# ---------- 渲染 ----------

def test_report_contains_four_measures():
    state = {
        "code": SAMPLE_CODE,
        "round": 2,
        "passed": True,
        "versions": [{"code": SAMPLE_CODE}],
        "defect_log": [
            {"round": 1, "issues": [_issue("Major", "空列表会崩", "runner")]},
            {"round": 2, "issues": []},
        ],
    }
    md = render_psp_markdown(summarize(state, RECORDS))
    for section in ("Size", "Effort", "Quality", "Schedule"):
        assert section in md, f"报告缺少 {section} 段"
    assert "缺陷追踪" in render_defect_table(DefectLog(state["defect_log"]))


def test_report_handles_empty_run():
    """没跑过任何东西时不能崩。"""
    md = render_psp_markdown(summarize({}, []))
    assert "Size" in md
    assert render_defect_table(DefectLog()) == ""
