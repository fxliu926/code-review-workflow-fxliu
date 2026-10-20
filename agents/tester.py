"""测试生成 Agent：为代码写单元测试并执行。

分工原则（和审查 Agent 一样）：

- **生成测试**是创造性的活，要理解代码意图、想出边界用例 → 交给 LLM，这是 Agent
- **执行测试**是确定性的活，跑一遍看退出码就行 → 复用 Homework 1 的 CodeRunner，
  它是工具不是 Agent，不占"Agent 数量"

为什么值得单独一个环节：LLM 审查判断的是"代码写得对不对"（读代码），
测试验证的是"代码跑起来对不对"（真执行）。两件事不能互相替代 ——
审查说"这个函数没问题"，不代表它真的能在边界输入上给出正确结果。

对应课程第 2 讲讲的单元测试与回归测试：代码一旦被修复，测试会再跑一遍，
用来确认"修了这个坏没修那个"。
"""
import ast
import re

from agents.llm_client import LLMClient
from agents.prompt import (
    build_tester_messages,
    build_tester_repair_messages,
    strip_code_block,
)
from agents.tools import CodeRunner

client = LLMClient()

DEFAULT_TIMEOUT = 10

# 拼接在被生成测试后面的执行器：逐个调用 test_ 开头的函数，汇总 PASS / FAIL。
# 不用 pytest，是为了让子进程零依赖 —— 被测代码和测试代码拼一起直接跑就行。
HARNESS = """

# ---- 自动执行：逐个调用 test_ 开头的函数 ----
_results = []
for _name, _fn in sorted(list(globals().items())):
    if not _name.startswith("test_") or not callable(_fn):
        continue
    try:
        _fn()
        _results.append((_name, "PASS", ""))
    except Exception as _e:
        _results.append((_name, "FAIL", f"{type(_e).__name__}: {_e}"))

for _n, _status, _msg in _results:
    print(f"RESULT {_status} {_n} {_msg}".rstrip())
if not _results:
    print("RESULT NONE")
"""

RESULT_LINE = re.compile(r"RESULT (PASS|FAIL|NONE) (\w+)(.*)")

# 这些错误发生在"任何一个 test_ 被调用之前"：脚本还没开始跑就挂了。
# 典型的比如 LLM 写了 `from solution import xxx`（它以为被测代码在独立模块里，
# 实际是拼进同一个文件），一 import 就 ModuleNotFoundError，0 个用例。
SCRIPT_ERROR_KINDS = (
    "ModuleNotFoundError", "ImportError", "SyntaxError",
    "IndentationError", "TabError",
)


def parse_results(output: str) -> dict:
    """从执行输出里数出通过/失败的用例。"""
    passed, failed, cases = 0, 0, []
    for line in (output or "").splitlines():
        matched = RESULT_LINE.search(line)
        if not matched:
            continue
        status, name, message = matched.group(1), matched.group(2), matched.group(3).strip()
        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        cases.append({"name": name, "status": status, "message": message})
    return {"passed": passed, "failed": failed, "cases": cases}


def _defined_name(node) -> str | None:
    """模块层这个语句定义了什么名字（函数 / 类 / 变量）。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        return next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
    if isinstance(node, ast.AnnAssign):
        return node.target.id if isinstance(node.target, ast.Name) else None
    return None


def top_level_names(source: str) -> set:
    """源码在模块层定义了哪些名字。"""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return set()   # 源码本身就有语法错误时无从比对，交给执行阶段去报
    return {n for n in (_defined_name(node) for node in tree.body) if n}


def find_contamination(source: str, tests: str) -> list:
    """测试文件里有没有把被测代码又写了一遍。

    这是必须挡住的坑：测试一旦自带一份实现副本，跑的就是副本而不是真代码，
    结果全是假的 —— 被测代码再烂也能"全绿"。
    """
    try:
        tree = ast.parse(tests or "")
    except SyntaxError:
        return []
    defined = {n for n in (_defined_name(node) for node in tree.body) if n}
    return sorted(defined & top_level_names(source))


def strip_redefinitions(tests: str, names) -> tuple:
    """删掉测试里对被测代码的重复定义，保证跑的是真实那份实现。

    返回 (清理后的测试代码, 删掉的定义个数)。
    """
    try:
        tree = ast.parse(tests or "")
    except SyntaxError:
        return tests, 0

    targets = set(names)
    spans = []
    for node in tree.body:
        if _defined_name(node) not in targets:
            continue
        start = node.lineno
        decorators = getattr(node, "decorator_list", None) or []
        if decorators:
            start = min(d.lineno for d in decorators)
        spans.append((start, node.end_lineno))
    if not spans:
        return tests, 0

    lines = (tests or "").splitlines()
    kept = [line for i, line in enumerate(lines, start=1)
            if not any(s <= i <= e for s, e in spans)]
    return "\n".join(kept), len(spans)


def _preamble_lines(source: str) -> int:
    """被测代码在拼好的文件里占几行。

    CodeRunner 用 `preamble.rstrip() + "\\n\\n" + code` 拼接，
    所以测试代码的第一行 = 被测代码行数 + 2（一个换行 + 一个空行）。
    有了这条界线，就能靠 traceback 的行号判断错误出在哪半边。
    """
    return len((source or "").rstrip().splitlines()) + 2


def classify_zero_run(output: str, source: str) -> dict:
    """一个用例都没跑起来时，判断这个失败该记在谁头上。

    返回 {"owner", "kind", "message"}，owner ∈ {"source", "tests"}：
    - source —— 被测代码本身就跑不起来（它自己 import 了不存在的东西、
      有语法错误），这确实是代码的缺陷，该回炉修复；
    - tests  —— 测试脚本自己写错了，或者压根没生成 test_ 函数。
      这是测试环节的失误，不能拿它当"代码没通过"的证据。

    这条区分和之前修过的"把函数正确抛异常误判成缺陷"是同一类问题：
    **工具/脚本自己的错误，不能算到产物头上。**
    """
    text = output or ""
    kind = next((k for k in SCRIPT_ERROR_KINDS
                 if re.search(rf"^{k}\b", text, re.M)), None)
    if kind is None:
        # 没识别出脚本级错误 → 大概率是压根没写出 test_ 函数，算测试的锅
        return {"owner": "tests", "kind": None,
                "message": "没有生成任何可执行的用例（没找到 test_ 开头的函数）"}

    message = next(
        (l.strip() for l in text.splitlines() if l.strip().startswith(kind)), kind)
    frames = re.findall(r'File "<string>", line (\d+)', text)
    line = int(frames[-1]) if frames else None
    # 行号落在被测代码那半边 → 代码的锅；否则是测试脚本的锅
    owner = "tests" if line is None or line >= _preamble_lines(source) else "source"
    return {"owner": owner, "kind": kind, "message": message}


def run_tests(source: str, tests: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """执行测试：被测代码作为前置，测试代码作为主体。

    返回 {"passed", "failed", "cases", "output", "ok", "ran", "broken", "owner"}。
    ok     —— 至少跑起来一个用例且全部通过（这是"代码通过测试"的判据）
    broken —— 一个用例都没跑起来，而且是测试脚本自己的问题（不该回炉）
    """
    empty = {"passed": 0, "failed": 0, "cases": [], "output": "（没有生成测试）",
             "ok": False, "ran": 0, "broken": True, "owner": "tests",
             "kind": None, "message": "没有生成测试代码"}
    if not (tests or "").strip():
        return dict(empty)

    output = CodeRunner(timeout=timeout).run(
        tests.rstrip() + HARNESS, preamble=source)
    result = parse_results(output)
    result["output"] = output
    result["ok"] = result["passed"] > 0 and result["failed"] == 0
    result["ran"] = result["passed"] + result["failed"]

    if result["ran"]:
        result.update({"broken": False, "owner": None, "kind": None, "message": ""})
    else:
        verdict = classify_zero_run(output, source)
        result["broken"] = verdict["owner"] == "tests"
        result["owner"] = verdict["owner"]
        result["kind"] = verdict["kind"]
        result["message"] = verdict["message"]
    return result


MAX_ATTEMPTS = 2   # 生成 + 至多一次带报错的自愈重试


def tester_agent(state: dict) -> dict:
    """生成单元测试并真跑一遍，结果写进共享状态。

    三道保险，都是为了不让"测试环节自己的失误"误伤被测代码：
    1. 测试里把被测代码又抄了一遍 → 静态剔除，保证测的是真实那份实现；
    2. 脚本自己跑不起来（典型：import 了不存在的模块）→ 带着真实报错重生一次；
    3. 还是不行 → 标记 test_script_broken，流程不会再拿它当"代码没通过"的证据。
    """
    code = state.get("code", "") or ""

    if not code.strip():
        # 没有被测代码时测试毫无意义 —— 不挡住的话 LLM 会自己写一份实现再自己测，
        # 结果是"6 通过 0 失败"，把一个空产物粉饰成合格。
        state.update({
            "tests": "", "test_cases": [], "test_summary": "0 通过 / 0 失败",
            "test_output": "（被测代码为空，未执行测试）", "tests_passed": False,
            "test_script_broken": True, "test_note": "被测代码为空，测试无意义",
            "test_failure": "", "test_attempts": 0, "test_contaminated": [],
        })
        return state

    tests = strip_code_block(client.chat(build_tester_messages(code)))

    # 挡住"测试自带实现副本"：抄进来的定义一律删掉，只留真正的用例
    contaminated = find_contamination(code, tests)
    if contaminated:
        tests, _removed = strip_redefinitions(tests, contaminated)

    result = run_tests(code, tests)

    for _ in range(MAX_ATTEMPTS - 1):
        if not result["broken"]:
            break
        # 只有"测试脚本自己的锅"才值得重试；被测代码跑不起来重试也没用
        repaired = strip_code_block(
            client.chat(build_tester_repair_messages(code, tests, result["output"])))
        if not repaired.strip():
            break
        tests = repaired
        result = run_tests(code, tests)

    state["tests"] = tests
    state["test_cases"] = result["cases"]
    state["test_summary"] = f"{result['passed']} 通过 / {result['failed']} 失败"
    state["test_output"] = result["output"]
    state["tests_passed"] = result["ok"]
    state["test_attempts"] = 1 if not result["broken"] else MAX_ATTEMPTS
    # 脚本自身有问题的标记：流程据此决定"要不要因此回炉"
    state["test_script_broken"] = result["broken"]
    state["test_note"] = (
        f"测试脚本自身错误（{result['kind']}）：{result['message']}"
        if result["broken"] else "")
    state["test_contaminated"] = contaminated
    # 失败的详情单独留一份，修复 Agent 要靠它定位问题
    failed_cases = [c for c in result["cases"] if c["status"] == "FAIL"]
    state["test_failure"] = "\n".join(
        f"{c['name']}：{c['message']}" for c in failed_cases)
    return state
