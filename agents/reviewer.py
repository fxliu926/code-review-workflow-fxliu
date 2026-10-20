"""审查 Agent：输出结构化 JSON，并按实际问题条数判定是否放行。

**为什么改成结构化**

旧实现是 `passed = ("Critical" not in review) and ("Major" not in review)`。
LLM 输出的是自由文本，只要报告里出现 "## Critical Issues" 这种小标题就会被判为不通过，
反之若它用中文写「严重」又会一路放行 —— 是否回炉修复实际由措辞随机决定。

现在改为：LLM 必须输出 JSON，本地解析成 `issues` 列表后，
按 Critical / Major 的**实际条数**判定；解析不出来就重试一次，仍失败则保守判为不通过。

**本地工具结果优先**

审查前先用 `inspector` 做静态解析（ast）和运行时执行（子进程），
工具查实的问题（语法错误、崩溃堆栈、超时）会合成 Critical / Major 条目并进问题列表，
不依赖 LLM 是否发现。
"""
import json
import re

from agents.inspector import IO_ERROR_NAMES, Inspector
from agents.llm_client import LLMClient
from agents.prompt import build_workflow_review_messages, build_repair_messages

client = LLMClient()

# 测试可替换这个工厂，避免跑真实子进程
INSPECTOR_FACTORY = Inspector

# level 归一化：LLM 常把等级写成各种变体，统一收敛到三个标准值
LEVEL_ALIASES = {
    "critical": "Critical", "blocker": "Critical", "fatal": "Critical",
    "severe": "Critical", "严重": "Critical", "致命": "Critical",
    "major": "Major", "high": "Major", "important": "Major",
    "主要": "Major", "重要": "Major",
    "minor": "Minor", "low": "Minor", "info": "Minor", "nit": "Minor",
    "trivial": "Minor", "suggestion": "Minor", "次要": "Minor",
    "提示": "Minor", "建议": "Minor", "轻微": "Minor",
}

STANDARD_LEVELS = ("Critical", "Major", "Minor")
# 判定是否放行时计入的等级：只有这两类需要回炉修复
BLOCKING_LEVELS = ("Critical", "Major")


class ReviewParseError(ValueError):
    """审查结果无法解析为约定的 JSON 结构。"""


def normalize_level(raw) -> str:
    """把 LLM 的各种等级写法收敛到 Critical / Major / Minor。

    无法识别的等级一律按 Major 处理 —— 宁可让它再修一轮，也不要沉默放行。
    """
    if not isinstance(raw, str):
        return "Major"
    return LEVEL_ALIASES.get(raw.strip().lower(), LEVEL_ALIASES.get(raw.strip(), "Major"))


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 之类的代码块外壳。"""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return fenced.group(1).strip() if fenced else text.strip()


def parse_review(raw: str) -> dict:
    """把 LLM 输出解析成结构化审查结果。

    返回 {"summary": str, "issues": [{"level", "line", "description", "suggestion"}]}
    任何不合规都抛出 ReviewParseError，由调用方决定重试还是降级。
    """
    if not raw or not raw.strip():
        raise ReviewParseError("审查结果为空")

    text = _strip_code_fence(raw)
    # 有些模型会在 JSON 前后加寒暄，截取首尾大括号之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ReviewParseError("输出中找不到 JSON 对象")
    text = text[start:end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReviewParseError(f"JSON 解析失败：{e}") from e

    if not isinstance(data, dict):
        raise ReviewParseError("JSON 根节点必须是对象")

    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        raise ReviewParseError("缺少 issues 数组")

    issues = []
    for i, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            raise ReviewParseError(f"issues[{i}] 不是对象")
        description = str(item.get("description", "")).strip()
        if not description:
            raise ReviewParseError(f"issues[{i}] 缺少 description")
        line = item.get("line")
        issues.append({
            "level": normalize_level(item.get("level")),
            "line": line if isinstance(line, int) else None,
            "description": description,
            "suggestion": str(item.get("suggestion", "")).strip(),
        })

    return {"summary": str(data.get("summary", "")).strip(), "issues": issues}


def count_by_level(issues: list) -> dict:
    """统计各等级条数，缺失等级补 0，方便直接用于条件判断。"""
    counts = {level: 0 for level in STANDARD_LEVELS}
    for issue in issues:
        counts[issue["level"]] = counts.get(issue["level"], 0) + 1
    return counts


def render_review_markdown(parsed: dict) -> str:
    """把结构化结果渲染成 Markdown，供修复 Agent 与最终报告阅读。"""
    issues = parsed.get("issues", [])
    lines = [f"## 总结\n\n{parsed.get('summary') or '（无）'}", "", "## 问题列表"]
    if not issues:
        lines += ["", "未发现问题。"]
    for item in issues:
        loc = f"（第 {item['line']} 行）" if item["line"] else ""
        tag = "（工具检出）" if item.get("source", "llm") != "llm" else ""
        lines.append(f"- **[{item['level']}]** {item['description']}{loc}{tag}")
        if item["suggestion"]:
            lines.append(f"  - 建议：{item['suggestion']}")
    return "\n".join(lines)


# ---------- 本地工具：把静态/运行时事实转成问题条目 ----------

def _excerpt(text: str, limit: int = 200) -> str:
    return text.replace("\n", " ").strip()[:limit]


def tool_issues(context: dict) -> list:
    """把 Inspector 的采集结果合成问题条目。

    这些都是工具查实的事实：语法错误一定 Critical，执行崩溃/超时算 Major。
    """
    issues = []

    if context.get("static_error"):
        issues.append({
            "level": "Critical",
            "line": None,
            "description": f"代码无法通过语法解析：{context['static_error']}",
            "suggestion": "修正语法错误后重新提交",
            "source": "parser",
        })

    runtime = context.get("runtime") or ""
    if "运行超时" in runtime:
        issues.append({
            "level": "Major",
            "line": None,
            "description": f"代码执行超时（可能存在死循环）：{_excerpt(runtime)}",
            "suggestion": "检查循环终止条件",
            "source": "runner",
        })
    elif "运行失败" in runtime:
        issues.append({
            "level": "Major",
            "line": None,
            "description": f"代码运行时报错：{_excerpt(runtime)}",
            "suggestion": "按堆栈信息修复后重试",
            "source": "runner",
        })

    declared = context.get("declared_raises") or {}

    for line in runtime.splitlines():
        stripped = line.strip()
        if not stripped.startswith("probe:"):
            continue
        matched = re.search(r"(\w+)\((.*?)\) -> (.*)", stripped[len("probe:"):])
        if not matched:
            continue
        name, call_args, outcome = matched.group(1), matched.group(2), matched.group(3)
        if not any(word in outcome for word in ("Error", "Exception")):
            continue

        exc_name = outcome.split(":", 1)[0].strip()

        # 函数自己 raise 的异常 = 它按预期拒绝了非法入参，是正确行为不是缺陷。
        # 不区分的话，已经写对的代码会被判成有问题，修无可修、一路空转到轮次上限。
        if exc_name in declared.get(name, ()):
            issues.append({
                "level": "Minor",
                "line": None,
                "description": f"调用 `{name}({call_args})` 抛出 {exc_name}"
                               f"（函数显式的入参校验，属预期行为）",
                "suggestion": "确认这是有意为之；若希望容错，可改为返回默认值",
                "source": "runner",
            })
            continue

        # 示例入参本身不合理造成的 I/O 异常不该算代码缺陷，降级处理
        level = "Minor" if any(word in outcome for word in IO_ERROR_NAMES) else "Major"
        issues.append({
            "level": level,
            "line": None,
            "description": f"调用 `{name}({call_args})` 抛出异常：{outcome}",
            "suggestion": "补充边界条件判断或异常处理",
            "source": "runner",
        })
    return issues


def _static_summary(context: dict) -> str:
    if context.get("static_error"):
        return f"解析失败：{context['static_error']}"
    names = context.get("unit_names") or []
    if not names:
        return "未找到函数或类定义"
    return f"找到 {len(names)} 个可审查单元：" + "、".join(names)


def reviewer_agent(state: dict, max_parse_retries: int = 2, inspector=None) -> dict:
    """审查当前代码，写入结构化 issues 并判定是否放行。

    写入共享状态的字段：
      issues        结构化问题列表（LLM 意见 + 本地工具检出）
      issue_counts  各等级条数
      review        渲染好的 Markdown（保持与下游读取方兼容）
      passed        是否放行（Critical + Major 均为 0）
      round         本轮轮次，从 1 开始计数
      degraded      是否为降级结果
      static        静态解析结果摘要
      runtime       运行时执行结果
    """
    code = state.get("code", "")
    round_no = state.get("round", 0) + 1

    inspector = inspector or INSPECTOR_FACTORY()
    context = inspector.inspect(code)
    local_issues = tool_issues(context)

    raw = ""
    parsed = None
    errors = []

    for attempt in range(max_parse_retries):
        if attempt == 0:
            messages = build_workflow_review_messages(
                code, round_no,
                static_summary=_static_summary(context),
                runtime_info=context.get("runtime", ""),
            )
        else:
            messages = build_repair_messages(raw, str(errors[-1]))
        raw = client.chat(messages)
        try:
            parsed = parse_review(raw)
            break
        except ReviewParseError as e:
            errors.append(e)

    state.update({
        "static": _static_summary(context),
        "runtime": context.get("runtime", ""),
        "unit_count": context.get("unit_count", 0),
        "round": round_no,
    })

    if parsed is None:
        # 降级：拿不到结构化结果时宁可不放行，同时记录原因供报告说明。
        # 即便如此，本地工具查出的问题依然保留 —— 它们不依赖 LLM。
        counts = count_by_level(local_issues)
        merged = {"summary": "LLM 审查结果无法解析，仅保留本地工具检出的问题。",
                  "issues": local_issues}
        state.update({
            "issues": local_issues,
            "issue_counts": counts,
            "review": render_review_markdown(merged),
            "summary": merged["summary"],
            "passed": False,
            "degraded": True,
            "degraded_reason": "parse",
            "parse_errors": [str(e) for e in errors],
        })
        return state

    # LLM 的意见在前，工具查出的事实紧随其后
    llm_issues = [dict(item, source=item.get("source", "llm")) for item in parsed["issues"]]
    issues = llm_issues + local_issues
    counts = count_by_level(issues)
    blocking = sum(counts[level] for level in BLOCKING_LEVELS)

    state.update({
        "issues": issues,
        "issue_counts": counts,
        "review": render_review_markdown({"summary": parsed["summary"], "issues": issues}),
        "summary": parsed["summary"],
        "passed": blocking == 0,
        "degraded": False,
        "parse_errors": [],
    })
    return state
