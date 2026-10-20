"""测试本地事实采集：静态解析 + 运行时探针。

这里接的是 Homework 1 的 parser 与 tools。工具查实的问题应当成为 Critical / Major，
即使 LLM 的看法再乐观也不能被抹掉。
"""
import pytest

from agents.inspector import Inspector, _example_arg, declared_raises, probe_specs
from agents.reviewer import _static_summary, tool_issues


# ---------- 纯函数：把采集结果转成问题条目 ----------

def test_syntax_error_becomes_critical():
    issues = tool_issues({"static_error": "语法错误：第 1 行 - invalid syntax", "runtime": ""})
    assert len(issues) == 1
    assert issues[0]["level"] == "Critical"
    assert issues[0]["source"] == "parser"


def test_runtime_failure_becomes_major():
    issues = tool_issues({"static_error": "", "runtime": "运行失败（退出码 1）\nstderr:\nNameError"})
    assert [i["level"] for i in issues] == ["Major"]
    assert issues[0]["source"] == "runner"


def test_runtime_timeout_becomes_major():
    issues = tool_issues({"static_error": "", "runtime": "运行超时（>5s），已强制终止。"})
    assert [i["level"] for i in issues] == ["Major"]
    assert "循环终止" in issues[0]["suggestion"]


def test_probe_exception_becomes_major():
    runtime = "运行成功（退出码 0）\nstdout:\nprobe: boom() -> ZeroDivisionError: division by zero"
    issues = tool_issues({"static_error": "", "runtime": runtime})
    assert len(issues) == 1
    assert issues[0]["level"] == "Major"
    assert "boom()" in issues[0]["description"]


def test_successful_probe_yields_nothing():
    runtime = "运行成功（退出码 0）\nstdout:\nprobe: f() -> 42"
    assert tool_issues({"static_error": "", "runtime": runtime}) == []


def test_clean_context_yields_nothing():
    assert tool_issues({"static_error": "", "runtime": ""}) == []


def test_multiple_probe_failures():
    runtime = ("运行成功（退出码 0）\nstdout:\n"
               "probe:\nprobe: a(1) -> ValueError: bad\nprobe: b(1) -> TypeError: worse\n")
    issues = tool_issues({"static_error": "", "runtime": runtime})
    assert len(issues) == 2


def test_io_error_is_downgraded_to_minor():
    """示例入参不合理导致的文件错误不算代码缺陷，避免卡死在修复循环里。"""
    runtime = ("运行成功（退出码 0）\nstdout:\n"
               "probe:\nprobe: read('x') -> FileNotFoundError: no such file\n")
    issues = tool_issues({"static_error": "", "runtime": runtime})
    assert [i["level"] for i in issues] == ["Minor"]


# ---------- 示例入参生成 ----------

@pytest.mark.parametrize("annotation,expected", [
    ("int", "1"), ("float", "1.0"), ("str", '"x"'), ("bool", "True"),
    ("Optional[int]", "1"), ("Union[str, None]", '"x"'), ("int | None", "1"),
    ("list", "[1, 2, 3]"), ("dict", "{}"), ("Dict[str, int]", "{}"),
    ("List[int]", "[1, 2, 3]"), ("List[str]", '["a", "b", "c"]'), ("tuple", "()"),
    (None, None), ("", None), ("MyClass", None),
])
def test_example_arg(annotation, expected):
    assert _example_arg(annotation) == expected


def test_probe_specs_needs_annotations():
    """参数没有注解就不猜，猜错造成的 TypeError 不是缺陷。"""
    source = (
        "def annotated(a: int, b: str) -> None: pass\n"
        "def unannotated(a, b) -> None: pass\n"
        "def no_args() -> None: pass\n"
    )
    specs = probe_specs(source)
    assert specs == [("annotated", '1, "x"'), ("no_args", "")]


def test_probe_specs_capped():
    source = "".join(f"def f{i}(a: int) -> None: pass\n" for i in range(10))
    assert len(probe_specs(source)) == 3


def test_probe_specs_on_broken_code():
    assert probe_specs("def f(:") == []


@pytest.mark.parametrize("context,keyword", [
    ({"static_error": "文件为空", "unit_names": []}, "解析失败"),
    ({"static_error": "", "unit_names": []}, "未找到函数或类"),
    ({"static_error": "", "unit_names": ["f", "g"]}, "找到 2 个可审查单元"),
])
def test_static_summary(context, keyword):
    assert keyword in _static_summary(context)


# ---------- 零参函数识别 ----------

def test_zero_arg_detection():
    source = "def no_args(): pass\ndef with_arg(a: int) -> None: pass\n"
    assert probe_specs(source) == [("no_args", ""), ("with_arg", "1")]


# ---------- Inspector 真实行为（会起子进程） ----------

def test_inspect_valid_code():
    result = Inspector(timeout=5).inspect("def add(a, b):\n    return a + b\n")
    assert result["static_error"] == ""
    assert result["unit_names"] == ["add"]
    assert "运行成功" in result["runtime"]


def test_inspect_syntax_error():
    result = Inspector(timeout=5).inspect("def f(:\n    pass\n")
    assert "语法错误" in result["static_error"]
    assert result["runtime"] == "", "解析失败时不应再尝试执行"


def test_inspect_empty_code():
    assert Inspector(timeout=5).inspect("")["static_error"] == "文件为空"


def test_inspect_catches_runtime_error():
    result = Inspector(timeout=5).inspect("def boom():\n    return 1 / 0\n")
    assert "ZeroDivisionError" in result["runtime"]


def test_runtime_can_be_disabled():
    result = Inspector(timeout=5, runtime=False).inspect("def f(): pass\n")
    assert result["runtime_enabled"] is False
    assert "已关闭" in result["runtime"]


# ---------- 显式入参校验 vs 意外崩溃 ----------

GUARDED_CODE = '''
def average(numbers: list) -> float:
    if not numbers:
        raise ValueError("列表不能为空")
    return sum(numbers) / len(numbers)
'''


def test_declared_raises_finds_explicit_checks():
    found = declared_raises(GUARDED_CODE)
    assert found["average"] == {"ValueError"}


def test_declared_raises_ignores_bare_reraise():
    found = declared_raises("def f():\n    try:\n        pass\n    except Exception:\n        raise\n")
    assert found["f"] == set()


def test_declared_raises_on_syntax_error():
    assert declared_raises("def f(:\n    pass\n") == {}


def test_explicit_validation_is_not_a_defect():
    """回归：函数自己 raise 拒绝非法入参是正确行为，不该判 Major 让它修无可修。"""
    issues = tool_issues({
        "runtime": "probe: average([]) -> ValueError: 列表不能为空",
        "declared_raises": {"average": {"ValueError"}},
    })
    assert len(issues) == 1
    assert issues[0]["level"] == "Minor", "显式入参校验不应阻塞放行"
    assert "预期行为" in issues[0]["description"]


def test_unexpected_crash_still_major():
    issues = tool_issues({
        "runtime": "probe: average([]) -> ZeroDivisionError: division by zero",
        "declared_raises": {"average": {"ValueError"}},
    })
    assert issues[0]["level"] == "Major", "内部运算炸了才是真缺陷"


def test_probe_without_declared_raises_info_stays_conservative():
    """拿不到静态信息时（比如旧版 Inspector）保持原来的保守判定。"""
    issues = tool_issues({"runtime": "probe: f([]) -> ValueError: bad"})
    assert issues[0]["level"] == "Major"


def test_end_to_end_guarded_function_passes():
    """端到端：有入参校验的函数，探针用空列表也只出 Minor。"""
    result = Inspector(timeout=5).inspect(GUARDED_CODE)
    assert result["declared_raises"]["average"] == {"ValueError"}

    levels = [i["level"] for i in tool_issues(result)]
    assert "Major" not in levels, "已经写好校验的函数不该被判成有缺陷"
