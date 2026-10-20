"""测试「测试生成 Agent」与它的执行器。

分工：生成测试是 Agent（LLM 的活），执行测试是工具（CodeRunner 的活）。
这里重点验证执行与结果解析是真的准 —— 数错了通过数，后面全盘皆错。
"""
import pytest

import agents.tester as tester
from agents.tester import parse_results, run_tests
from workflow.expr import SafeExprError, evaluate

# 注意：不要 `from agents.tester import tester_agent` —— 这个名字以 test 开头，
# pytest 会把它当测试用例收集，然后因为参数 state 找不到 fixture 而报错。
agent = tester.tester_agent

SOURCE = '''
def add(a: int, b: int) -> int:
    """加法。"""
    return a + b

def divide(a: float, b: float) -> float:
    """除法，除数为零抛 ValueError。"""
    if b == 0:
        raise ValueError("除数不能为零")
    return a / b
'''

GOOD_TESTS = '''
def test_add():
    assert add(1, 2) == 3

def test_add_negative():
    assert add(-1, 1) == 0

def test_divide_raises():
    try:
        divide(1, 0)
        assert False, "应当抛异常"
    except ValueError:
        assert True
'''

BAD_TESTS = '''
def test_add_wrong():
    assert add(1, 2) == 4
'''


# ---------- 结果解析 ----------

def test_parse_results_counts_pass_and_fail():
    out = "RESULT PASS test_a\nRESULT FAIL test_b AssertionError: 1 != 2\n"
    r = parse_results(out)
    assert r["passed"] == 1 and r["failed"] == 1
    assert r["cases"][0]["name"] == "test_a"
    assert r["cases"][1]["message"] == "AssertionError: 1 != 2"


def test_parse_results_no_output():
    assert parse_results("")["passed"] == 0


def test_parse_results_none_marker():
    """一个用例都没生成时输出 RESULT NONE。"""
    r = parse_results("RESULT NONE")
    assert r["passed"] == 0 and r["failed"] == 0 and r["cases"] == []


# ---------- 真实执行（会起子进程） ----------

def test_all_tests_pass():
    r = run_tests(SOURCE, GOOD_TESTS)
    assert r["passed"] == 3 and r["failed"] == 0
    assert r["ok"] is True


def test_failing_test_is_caught():
    r = run_tests(SOURCE, BAD_TESTS)
    assert r["passed"] == 0 and r["failed"] == 1
    assert r["ok"] is False
    assert "test_add_wrong" in r["output"]


def test_empty_tests_are_not_ok():
    """没生成测试不能算通过 —— 否则等于这关形同虚设。"""
    r = run_tests(SOURCE, "")
    assert r["ok"] is False
    assert "没有生成测试" in r["output"]


def test_syntax_error_in_tests():
    r = run_tests(SOURCE, "def test_broken(:\n    pass\n")
    assert r["ok"] is False
    assert r["passed"] == 0


def test_only_test_prefixed_functions_run():
    """辅助函数不该被当用例执行。"""
    tests = "def helper():\n    raise RuntimeError('不该被调用')\n\ndef test_ok():\n    assert True\n"
    r = run_tests(SOURCE, tests)
    assert r["passed"] == 1 and r["failed"] == 0


# ---------- 责任归属：0 个用例跑起来时，锅是谁的 ----------

IMPORTING_TESTS = '''
from solution import add

def test_add():
    assert add(1, 2) == 3
'''

BROKEN_SOURCE = '''
import nonexistent_module_xyz

def add(a, b):
    return a + b
'''


def test_import_error_in_tests_is_not_blamed_on_code():
    """LLM 写了 `from solution import ...` → 是测试脚本的锅，不是代码的锅。

    这条是踩过坑之后补的：不区分的话会白跑三轮修复，修一个根本不存在的缺陷。
    """
    r = run_tests(SOURCE, IMPORTING_TESTS)
    assert r["ran"] == 0
    assert r["owner"] == "tests"
    assert r["broken"] is True, "必须标记为脚本自身错误，流程才不会因此回炉"
    assert r["ok"] is False


def test_import_error_in_source_is_blamed_on_source():
    """反过来：被测代码自己 import 挂了，那确实是代码的问题，要能回炉修。"""
    r = run_tests(BROKEN_SOURCE, GOOD_TESTS)
    assert r["ran"] == 0
    assert r["owner"] == "source"
    assert r["broken"] is False, "代码的锅不能算到测试头上"


def test_no_test_functions_counts_as_tester_fault():
    """压根没写 test_ 函数，也是测试环节没干好。"""
    r = run_tests(SOURCE, "x = 1 + 1\nprint(x)\n")
    assert r["ran"] == 0
    assert r["owner"] == "tests"
    assert r["broken"] is True


# ---------- 污染防护：测试里不许自带一份实现副本 ----------

CONTAMINATED_TESTS = '''
# 模型顺手把被测代码抄了一遍 —— 这样跑的是副本，代码再烂也能全绿
def add(a: int, b: int) -> int:
    """加法。"""
    return a + b

def test_add():
    assert add(1, 2) == 3
'''

BUGGY_SOURCE = '''
def add(a: int, b: int) -> int:
    """加法。"""
    return a - b     # 故意写错
'''


def test_find_contamination_detects_copied_source():
    assert tester.find_contamination(SOURCE, CONTAMINATED_TESTS) == ["add"]
    assert tester.find_contamination(SOURCE, GOOD_TESTS) == []


def test_top_level_names_and_syntax_error_tolerance():
    assert tester.top_level_names(SOURCE) == {"add", "divide"}
    assert tester.top_level_names("def broken(:") == set()


def test_strip_redefinitions_keeps_only_tests():
    cleaned, removed = tester.strip_redefinitions(CONTAMINATED_TESTS, {"add"})
    assert removed == 1
    assert "def add" not in cleaned
    assert "def test_add" in cleaned, "用例必须留下"


def test_stripped_tests_actually_exercise_real_code():
    """剔除副本后，有 bug 的真实代码必须被测出来 —— 这才是这层防护的意义。"""
    contaminated = tester.find_contamination(BUGGY_SOURCE, CONTAMINATED_TESTS)
    cleaned, _ = tester.strip_redefinitions(CONTAMINATED_TESTS, contaminated)
    r = run_tests(BUGGY_SOURCE, cleaned)
    assert r["failed"] == 1, "抄来的实现是对的，真代码是错的；不剔除就漏掉了"


def test_tester_agent_strips_contamination(monkeypatch):
    monkeypatch.setattr(tester, "client", _StubClient(CONTAMINATED_TESTS))
    out = agent({"code": BUGGY_SOURCE})
    assert out["test_contaminated"] == ["add"]
    assert "def add" not in out["tests"], "存进状态的应是清理后真正跑的那份"
    assert out["tests_passed"] is False, "真实代码有 bug，不能因为副本是对的就算通过"


def test_tester_agent_refuses_when_code_is_empty(monkeypatch):
    """没有被测代码时不许跑测试 —— 否则模型会自己写实现自己测，刷出一手全绿。"""
    monkeypatch.setattr(tester, "client", _StubClient(GOOD_TESTS))
    out = agent({"code": ""})
    assert out["tests_passed"] is False
    assert out["test_script_broken"] is True
    assert out["test_cases"] == []


def test_preamble_boundary_matches_how_coderunner_joins():
    """行号界线必须和 CodeRunner 的拼接方式一致，否则归属会判反。"""
    assert tester._preamble_lines("a = 1") == 3      # 1 行代码 + 空行 + 换行
    assert tester._preamble_lines("a = 1\nb = 2") == 4


def test_classify_zero_run_reads_kind_and_owner():
    out = ('运行失败（退出码 1）\nstderr:\nTraceback (most recent call last):\n'
           '  File "<string>", line 3, in <module>\n'
           'ModuleNotFoundError: No module named \'solution\'\n')
    v = tester.classify_zero_run(out, "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    assert v["kind"] == "ModuleNotFoundError"
    assert v["owner"] == "source", "line 3 落在被测代码那 5 行里"

    v = tester.classify_zero_run(out.replace("line 3", "line 20"), "a = 1\n")
    assert v["owner"] == "tests", "line 20 在测试那半边"


# ---------- Agent 本身 ----------

class _StubClient:
    def __init__(self, text):
        self.text = text

    def chat(self, messages):
        return self.text


class _SequenceClient:
    """按调用顺序返回不同结果，用来验证自愈重试。"""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def chat(self, messages):
        i = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        return self.texts[i]


def test_tester_agent_writes_state(monkeypatch):
    monkeypatch.setattr(tester, "client", _StubClient(GOOD_TESTS))
    state = {"code": SOURCE}
    out = agent(state)

    assert out["tests_passed"] is True
    assert "3 通过 / 0 失败" in out["test_summary"]
    assert out["test_failure"] == "", "全通过时不该有失败详情"
    assert len(out["test_cases"]) == 3


def test_tester_agent_records_failures(monkeypatch):
    monkeypatch.setattr(tester, "client", _StubClient(BAD_TESTS))
    state = {"code": SOURCE}
    out = agent(state)

    assert out["tests_passed"] is False
    assert "test_add_wrong" in out["test_failure"], "失败详情要留给修复 Agent 看"


def test_tester_agent_strips_code_fence(monkeypatch):
    monkeypatch.setattr(tester, "client", _StubClient("```python\n" + GOOD_TESTS + "```"))
    out = agent({"code": SOURCE})
    assert out["tests_passed"] is True, "围栏必须剥掉，否则语法错误"


def test_tester_agent_self_heals_bad_import(monkeypatch):
    """第一次写出 import 错误 → 带着报错重生一次 → 通过。

    重试要有依据（把真实报错贴回去），不能是无脑再来一遍。
    """
    stub = _SequenceClient(IMPORTING_TESTS, GOOD_TESTS)
    monkeypatch.setattr(tester, "client", stub)
    out = agent({"code": SOURCE})

    assert out["tests_passed"] is True
    assert stub.calls == 2, "应该只重试一次，不能无限重试"
    assert out["test_script_broken"] is False
    assert out["test_attempts"] == 1


def test_tester_agent_marks_broken_when_repair_also_fails(monkeypatch):
    """修不好就老实承认是测试环节的问题，绝不甩锅给代码。"""
    stub = _SequenceClient(IMPORTING_TESTS, IMPORTING_TESTS)
    monkeypatch.setattr(tester, "client", stub)
    out = agent({"code": SOURCE})

    assert out["tests_passed"] is False
    assert out["test_script_broken"] is True, "这是回炉判据：True 就不会让代码白修"
    assert "ModuleNotFoundError" in out["test_note"]
    assert out["test_attempts"] == 2


def test_broken_script_does_not_route_to_fix():
    """流程层面的最后一道保险：脚本坏了不走 fix。"""
    broken = {"round": 1, "tests_passed": False, "test_script_broken": True}
    assert evaluate("not get('tests_passed', False) and "
                    "not get('test_script_broken', True) and round < 3", broken) is False

    real_failure = {"round": 1, "tests_passed": False, "test_script_broken": False}
    assert evaluate("not get('tests_passed', False) and "
                    "not get('test_script_broken', True) and round < 3", real_failure) is True

    # 测试节点被 skip 时字段根本不存在，默认 True 同样不该回炉
    assert evaluate("not get('tests_passed', False) and "
                    "not get('test_script_broken', True) and round < 3", {"round": 1}) is False


def test_source_broken_still_routes_to_fix():
    """被测代码自己跑不起来 → 仍然要回炉。"""
    state = {"round": 1, "tests_passed": False, "test_script_broken": False}
    assert evaluate("not get('tests_passed', False) and "
                    "not get('test_script_broken', True) and round < 3", state) is True


# ---------- get()：字段可能不存在时怎么读 ----------

def test_get_returns_default_for_missing_field():
    assert evaluate("get('tests_passed', False)", {}) is False
    assert evaluate("get('tests_passed', True)", {}) is True


def test_get_returns_actual_value_when_present():
    assert evaluate("get('tests_passed', False)", {"tests_passed": True}) is True


def test_missing_field_still_fails_fast_without_get():
    """不用 get() 时，拼错字段名必须报错，不能静默当 False。"""
    with pytest.raises(SafeExprError, match="状态里没有字段"):
        evaluate("tests_passed", {})


def test_get_rejects_non_string_field():
    with pytest.raises(SafeExprError, match="字段名"):
        evaluate("get(1, False)", {})


def test_get_rejects_too_many_args():
    with pytest.raises(SafeExprError, match="最多两个参数"):
        evaluate("get('a', False, 3)", {})


# ---------- 流程接线 ----------

def test_workflow_yaml_wires_test_node():
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(Path("config/workflow.yaml").read_text(encoding="utf-8"))
    ids = [n["id"] for n in cfg["nodes"]]
    assert "test" in ids, "流程里要有测试节点"

    test_node = next(n for n in cfg["nodes"] if n["id"] == "test")
    assert test_node["agent"] == "tester"
    assert test_node["on_error"] == "skip", "测试环节自己挂了不该拖垮交付"

    analyze = next(n for n in cfg["nodes"] if n["id"] == "analyze")
    fallthrough = [c for c in analyze["next"] if "when" not in c]
    assert fallthrough[0]["goto"] == "test", "审查通过后要去跑测试"
