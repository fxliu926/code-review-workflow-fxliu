"""测试受限表达式求值器。

它替代了引擎里原来的 `eval()`，所以「拒绝危险语法」和「支持正常条件」同样重要。
"""
import pytest

from workflow.expr import SafeExprError, evaluate


def ok(expr, state):
    return evaluate(expr, state)


def test_literal_and_comparison():
    assert ok("passed", {"passed": True}) is True
    assert ok("round >= 3", {"round": 3}) is True
    assert ok("round >= 3", {"round": 2}) is False


def test_boolean_logic():
    assert ok("not passed and round < 3", {"passed": False, "round": 1}) is True
    assert ok("not passed and round < 3", {"passed": False, "round": 3}) is False


def test_subscript_and_builtin_funcs():
    state = {"issue_counts": {"Critical": 1, "Major": 0}, "code": "def f(): pass"}
    assert ok("issue_counts['Critical'] > 0", state) is True
    assert ok("issue_counts['Critical'] + issue_counts['Major'] == 1", state) is True
    assert ok("len(code) > 5", state) is True
    assert ok("max(1, issue_counts['Critical']) == 1", state) is True


def test_attribute_access():
    assert ok("point.x > 1", {"point": type("P", (), {"x": 3})()}) is True


def test_missing_field_raises():
    with pytest.raises(SafeExprError):
        ok("unknown_field", {})


def test_syntax_error_raises():
    with pytest.raises(SafeExprError):
        ok("round >=", {"round": 1})


@pytest.mark.parametrize("expr", [
    "__import__('os').getcwd()",
    "open('a.txt')",
    "eval('1+1')",
    "state.__class__",
    "lambda: 1",
    "(a := 1) > 0",
])
def test_rejects_dangerous_syntax(expr):
    """曾经用 eval 时，这些表达式里的函数调用会被真实执行。"""
    with pytest.raises(SafeExprError):
        ok(expr, {"state": {}, "a": 1})


def test_rejects_private_attribute():
    class Box:
        def __init__(self):
            self._secret = 1
    with pytest.raises(SafeExprError):
        ok("box._secret", {"box": Box()})
