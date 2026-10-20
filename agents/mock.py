"""离线桩 Agent：不调用 LLM，用来在没有密钥的机器上跑通整条流程。

两个用处：

1. **换台机器就能验证编排** —— clone 下来没配 API key 也能端到端跑一遍，
   顺序 / 并行 / 条件分支 / 回炉循环 / 测试闭环 / 错误恢复全都能看到。
2. **作为"可扩展性"的可运行证据** —— 流程和真实运行用的是**同一份
   `config/workflow.yaml`、同一个引擎**，只把 Agent 实现换掉就照样跑通，
   引擎一行没改。这比在文档里写"我们支持扩展"有说服力得多。

桩的行为是刻意设计成"三轮跑出两种回炉"的，这样一份演示就能覆盖：
    code → analyze(第 1 轮：审查报 Major) → fix
          → analyze(第 2 轮：审查放行) → test(用例失败) → fix
          → analyze(第 3 轮：审查放行) → test(全部通过) → done
"""
from agents.tester import run_tests

DEMO_REQUIREMENT = "写一个函数，返回列表中最大的元素，需要处理空列表和非法入参"

# 第一版：直接用内置 max()，空列表会抛 ValueError，非列表入参行为也不明确
V1 = '''def find_max(numbers):
    """返回列表中最大的元素。"""
    return max(numbers)
'''

# 第二版：补上空列表校验
V2 = '''def find_max(numbers):
    """返回列表中最大的元素。

    Raises:
        ValueError: 列表为空时抛出。
    """
    if not numbers:
        raise ValueError("列表不能为空")
    return max(numbers)
'''

# 第三版：再补上入参类型校验（第二版里 find_max(None) 会抛出 ValueError 而非 TypeError）
V3 = '''def find_max(numbers):
    """返回列表中最大的元素。

    Raises:
        TypeError: numbers 不是列表时抛出。
        ValueError: 列表为空时抛出。
    """
    if not isinstance(numbers, list):
        raise TypeError("numbers 必须是列表")
    if not numbers:
        raise ValueError("列表不能为空")
    return max(numbers)
'''

# 固定的一套用例：只覆盖上面三版逐步补上的两个契约
TESTS = '''
def test_max_normal():
    assert find_max([1, 3, 2]) == 3

def test_max_negative():
    assert find_max([-5, -1, -3]) == -1

def test_empty_raises():
    try:
        find_max([])
        assert False, "应当抛 ValueError"
    except ValueError:
        assert True

def test_non_list_raises():
    try:
        find_max(None)
        assert False, "应当抛 TypeError"
    except TypeError:
        assert True
'''


def _issue(level, description, line=1, suggestion="", source="mock"):
    return {"level": level, "line": line, "description": description,
            "suggestion": suggestion, "source": source}


def mock_coder(state: dict) -> dict:
    """桩编码 Agent：忽略需求，直接给出第一版（带缺陷的）实现。"""
    state["code"] = V1
    state.setdefault("versions", []).append(
        {"round": 0, "source": "mock-coder", "code": V1})
    return state


def mock_reviewer(state: dict) -> dict:
    """桩审查 Agent：只在第 1 轮报一个 Major，之后放行。

    这样第 1 轮会被拦下回炉，第 2、3 轮放行 —— 用来演示循环与条件分支。
    """
    state["round"] = state.get("round", 0) + 1
    if state["round"] == 1:
        state["issues"] = [_issue(
            "Major", "find_max 未处理空列表，max([]) 会抛出 ValueError，"
                     "应显式校验并给出明确提示", line=3,
            suggestion="先判断 not numbers 再取最大值")]
    else:
        state["issues"] = []
    state["summary"] = f"（桩审查）第 {state['round']} 轮"
    return state


def mock_linter(state: dict) -> dict:
    """桩规范检查 Agent：每轮报一个不阻塞的 Minor。

    它的作用是让并行节点里两个分支都真的产出东西，
    也验证"Minor 不会触发回炉"这条判定是对的。
    """
    code = state.get("code", "")
    state["lint"] = {"units": 1, "issues": 1, "checked": len(code.splitlines())}
    state["issues"] = [_issue(
        "Minor", "find_max 缺少参数与返回值类型注解", line=1,
        suggestion="补上 numbers: list[int] -> int")]
    return state


def mock_fixer(state: dict) -> dict:
    """桩修复 Agent：按当前代码缺什么，给出下一版。

    确定性推进，不依赖 LLM，方便复现同一条轨迹。
    """
    code = state.get("code", "")
    if "raise ValueError" not in code:
        fixed = V2
    elif "isinstance" not in code:
        fixed = V3
    else:
        fixed = code

    state["code"] = fixed
    state["fixed"] = fixed
    state.setdefault("versions", []).append(
        {"round": state.get("round", 0), "source": "mock-fixer", "code": fixed})
    return state


def mock_tester(state: dict) -> dict:
    """桩测试 Agent：用例是固定的，执行仍然走真实的 CodeRunner。

    刻意只替换"生成"这一步、保留真实执行 —— 这样演示出来的通过/失败
    是真跑出来的，不是编出来的。
    """
    result = run_tests(state.get("code", ""), TESTS)

    state["tests"] = TESTS
    state["test_cases"] = result["cases"]
    state["test_summary"] = f"{result['passed']} 通过 / {result['failed']} 失败"
    state["test_output"] = result["output"]
    state["tests_passed"] = result["ok"]
    state["test_script_broken"] = result["broken"]
    state["test_note"] = (
        f"测试脚本自身错误（{result['kind']}）：{result['message']}"
        if result["broken"] else "")
    state["test_contaminated"] = []
    state["test_attempts"] = 1
    state["test_failure"] = "\n".join(
        f"{c['name']}：{c['message']}"
        for c in result["cases"] if c["status"] == "FAIL")
    return state
