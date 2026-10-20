"""本地代码规范检查节点：不调用 LLM，纯靠规则查。

对应课程第 3 讲「代码规范与代码审查」—— LLM 擅长判断设计是否合理，
而"这行超长""这个函数没写 docstring""用了 eval"这类事情用规则查更稳，
而且不用花钱、不用等网络。

它和审查 Agent 一起挂在并行节点上：LLM 那边慢慢想，这边顺手把规范问题查完。
"""
import ast
import re

from agents.parser import CodeParser

MAX_LINE_LENGTH = 88

TODO_PATTERN = re.compile(r"#.*(TODO|FIXME|XXX|HACK)", re.IGNORECASE)
SECRET_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key)\s*=\s*['\"][^'\"]{4,}['\"]",
    re.IGNORECASE,
)
SNAKE_CASE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _issue(level, line, description, suggestion):
    return {
        "level": level,
        "line": line,
        "description": description,
        "suggestion": suggestion,
        "source": "linter",
    }


def check(code: str) -> list:
    """按规则检查代码，返回结构化问题列表。"""
    if not code or not code.strip():
        return [_issue("Critical", None, "代码为空", "确认生成环节是否正常")]

    issues = []
    lines = code.splitlines()

    # --- 逐行类规则 ---
    for lineno, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_LENGTH:
            issues.append(_issue(
                "Minor", lineno,
                f"该行 {len(line)} 字符，超过 {MAX_LINE_LENGTH} 字符上限",
                "拆分成多行或提取中间变量",
            ))
        if TODO_PATTERN.search(line):
            issues.append(_issue(
                "Minor", lineno, "残留 TODO / FIXME 标记", "补全实现或明确记为待办"))
        if SECRET_PATTERN.search(line):
            issues.append(_issue(
                "Major", lineno, "疑似硬编码了口令或密钥",
                "改为从环境变量或配置读取",
            ))

    # --- 需要语法树的规则 ---
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 语法错误由 inspector 的静态层负责上报，这里不重复
        return issues

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not ast.get_docstring(node):
                issues.append(_issue(
                    "Minor", node.lineno,
                    f"函数 `{node.name}` 缺少 docstring",
                    "补一段说明用途、参数与返回值的 docstring",
                ))
            if not SNAKE_CASE.match(node.name):
                issues.append(_issue(
                    "Minor", node.lineno,
                    f"函数名 `{node.name}` 不符合 snake_case",
                    "改用小写加下划线的命名",
                ))
        elif isinstance(node, ast.ClassDef):
            if not ast.get_docstring(node):
                issues.append(_issue(
                    "Minor", node.lineno,
                    f"类 `{node.name}` 缺少 docstring",
                    "补一段说明职责的 docstring",
                ))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec"):
                issues.append(_issue(
                    "Critical", node.lineno,
                    f"使用了 `{node.func.id}()`，可能被注入执行任意代码",
                    "换成显式解析，例如 ast.literal_eval 或专用解析器",
                ))
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(_issue(
                    "Major", node.lineno, "裸 except 会吞掉所有异常",
                    "捕获具体的异常类型",
                ))
            elif (isinstance(node.type, ast.Name) and node.type.id == "Exception"
                  and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                issues.append(_issue(
                    "Major", node.lineno, "`except Exception: pass` 静默吞掉异常",
                    "至少记录日志，或改为处理具体异常",
                ))
    return issues


def linter_agent(state: dict) -> dict:
    """规范检查节点：把规则查出的问题写进共享状态。"""
    issues = check(state.get("code", ""))
    state["issues"] = issues

    parsed = CodeParser().parse(state.get("code", ""))
    state["lint"] = {
        "checked": True,
        "unit_count": len(parsed.units),
        "issue_count": len(issues),
    }
    return state
