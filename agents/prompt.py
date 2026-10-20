"""Prompt 模块：集中管理所有 Prompt，方便调整。

Prompt 支持多语言审查：
- Python：配合 ast 拆分的单元（function / class）逐个审查；
- 其他语言（JavaScript / Java / C++ 等）：整文件作为一个单元，
  由 LLM 直接审查（没有 ast 拆分和代码执行工具的辅助）。
"""

SYSTEM_PROMPT_TEMPLATE = """你是一位资深的 {language} 代码审查员，擅长发现代码中的 Bug、\
代码质量问题、性能问题和安全隐患。你的审查风格是：
1. 客观、具体，指出问题时给出理由
2. 按严重程度分类：Critical / Major / Minor
3. 给出可操作的改进建议
4. 输出结构化 Markdown，方便程序解析
"""

REVIEW_PROMPT_TEMPLATE = """请审查以下 {language} {unit_type}：

```{lang_tag}
{source}
```

请从以下四个维度分析：
1. **潜在 Bug**：逻辑错误、边界情况、异常处理、空值处理
2. **代码质量**：命名、可读性、重复代码、注释
3. **性能问题**：不必要的循环、低效操作、内存占用
4. **改进建议**：给出修改后的代码片段

输出格式必须严格如下：

## 问题列表
- [Critical/Major/Minor] 问题描述（附行号）
- ...

## 改进建议
```{lang_tag}
修改后的代码
```

## 总结
一句话总结这个 {unit_type} 的整体质量。
"""

# 语言标识 → prompt 中的展示名（fence 标签直接用语言标识，如 ```javascript）
LANG_DISPLAY = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
    "go": "Go",
    "rust": "Rust",
}


# ============ Workflow 编码专用 Prompt ============
#
# 踩过的坑：早期让编码 Agent 自由发挥，它会连同 Markdown 讲解和 ``` 围栏一起输出，
# 结果这些解说文字被当成"代码"写进了 state["code"]，审查 Agent 还得专门吐槽这一点。
# 这里在 Prompt 和输出清洗两侧各堵一道。

CODER_SYSTEM = """你是一位 Python 工程师。你写的代码要能直接复制运行。"""

CODER_TEMPLATE = """请根据以下需求写 Python 代码：

<<REQUIREMENT>>

要求：
- 只输出代码本身，不要讲解、不要 Markdown 代码块标记（```）、不要多余的标题
- 必须包含必要的导入、类型注解和函数 docstring
- 用中文写注释
- 处理常见的边界情况（空输入、类型错误等）"""


def build_coder_messages(requirement: str) -> list:
    """构造编码 Agent 的 Prompt。"""
    return [
        {"role": "system", "content": CODER_SYSTEM},
        {"role": "user", "content": CODER_TEMPLATE.replace("<<REQUIREMENT>>", requirement)},
    ]


TESTER_SYSTEM = """你是一位 Python 测试工程师。你写的测试要能直接复制运行。"""

# 刻意不引入 pytest：生成的测试只靠 assert 和 try/except，
# 这样子进程零依赖，被测代码和测试代码拼起来就能跑。
# fixture / parametrize 这类需要 pytest 运行时的写法一律禁止。
TESTER_TEMPLATE = """请为下面的 Python 代码编写单元测试：

<<CODE>>

要求：
- 每个测试是一个 `test_` 开头的函数，函数内用 assert 断言
- 覆盖三类情况：正常输入、边界输入（空值 / 零 / 负数 / 单元素）、
  以及函数文档里声明会抛异常的情况（用 try/except 配合 assert 验证）
- 只使用标准库，不要 import pytest，不要用 fixture、parametrize、mock
- **不要 import 被测代码**：被测代码会和你的测试拼进同一个文件，直接调用即可。
  写成 `from solution import xxx` 或 `import main` 会直接报 ModuleNotFoundError
- 不要写 if __name__ == "__main__"，执行器会自动调用所有 test_ 函数
- 只输出测试代码本身，不要讲解、不要 Markdown 代码块标记（```）"""


def build_tester_messages(code: str) -> list:
    """构造测试生成 Agent 的 Prompt。"""
    return [
        {"role": "system", "content": TESTER_SYSTEM},
        {"role": "user", "content": TESTER_TEMPLATE.replace("<<CODE>>", code)},
    ]


# 自愈用的 Prompt：第一次生成的测试跑不起来时，把真实的报错贴回去让它改。
# 这比直接放弃强，也比无脑重试强 —— 重试有明确依据，不是碰运气。
TESTER_REPAIR_TEMPLATE = """你上次写的测试跑不起来，执行结果是：

<<ERROR>>

当时的测试代码：

<<TESTS>>

被测代码（它和测试拼在同一个文件里，已经在作用域中）：

<<CODE>>

请重写一遍测试，修掉上面的问题。注意：
- 被测代码已经在同一文件里，**绝对不要 import 它**
- 仍然只输出测试代码本身，不要讲解、不要 Markdown 代码块标记"""


def build_tester_repair_messages(code: str, tests: str, error: str) -> list:
    """测试脚本自身出错时，带着报错重新生成一次。"""
    content = (TESTER_REPAIR_TEMPLATE
               .replace("<<ERROR>>", (error or "").strip()[:1500])
               .replace("<<TESTS>>", (tests or "").strip()[:3000])
               .replace("<<CODE>>", code))
    return [
        {"role": "system", "content": TESTER_SYSTEM},
        {"role": "user", "content": content},
    ]


def strip_code_block(text: str) -> str:
    """兜底清洗：万一 LLM 还是裹了 ```python 围栏，把它剥掉。"""
    import re
    if text is None:
        return ""
    fenced = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


# ============ Workflow 审查专用 Prompt（结构化输出） ============
#
# 为什么要求 JSON：Workflow 要靠机器判断「是否通过」来决定是否回炉修复。
# 用自由文本 + 子串匹配（例如 "Critical" in review）是不可靠的 ——
# LLM 只要在报告里写上 "## Critical Issues" 这个小标题就会被误判为不通过。

WORKFLOW_REVIEW_SYSTEM = """你是一位严格的 Python 代码审查员。你只输出 JSON，不输出任何其他内容。"""

WORKFLOW_REVIEW_TEMPLATE = """请审查以下 Python 代码：

```python
<<CODE>>
```

这是第 <<ROUND>> 轮审查。请按要求输出一个 JSON 对象：

{
  "summary": "一句话总结这段代码的总体质量",
  "issues": [
    {
      "level": "Critical | Major | Minor",
      "line": 12,
      "description": "问题描述，要具体",
      "suggestion": "可操作的修改建议"
    }
  ]
}

打分规则（务必遵守，这决定代码是否需要返工）：
- Critical：会导致程序崩溃、结果错误或安全漏洞的缺陷
- Major：明显的健壮性/正确性问题，但不至于立刻崩溃
- Minor：命名、可读性、重复代码、注释等风格或优化建议
- level 必须是 Critical / Major / Minor 之一，不要写成「严重」等中文，
  也不要用 high / low / blocker / 建议 等其他写法

其他要求：
- 没有问题就把 issues 写成空数组 []
- line 指原代码行号，无法确定时填 null
- 只输出 JSON，不要加 ```json 代码块标记，也不要写解释"""

REPAIR_TEMPLATE = """你上一次的输出无法解析成 JSON，原因：<<ERROR>>

原始输出如下：
<<RAW>>

请重新输出，严格要求：只输出一个 JSON 对象，不要代码块标记，不要解释文字。"""


def build_workflow_review_messages(code: str, round_no: int = 1,
                                   static_summary: str = "",
                                   runtime_info: str = "") -> list:
    """构造 Workflow 审查 Prompt（要求 JSON 输出）。

    static_summary / runtime_info 是本地工具查出来的客观事实，
    拼进 Prompt 后 LLM 能据此判断，而不是凭空推测。
    """
    user_content = (
        WORKFLOW_REVIEW_TEMPLATE
        .replace("<<CODE>>", code)
        .replace("<<ROUND>>", str(round_no))
    )

    extras = []
    if static_summary:
        extras.append("以下是本地静态解析（ast）的结果：\n```text\n" + static_summary + "\n```")
    if runtime_info:
        extras.append(
            "以下是这段代码的实际运行结果，务必据此判断是否存在运行时错误：\n"
            "```text\n" + runtime_info + "\n```"
        )
    if extras:
        user_content += "\n\n" + "\n\n".join(extras)

    return [
        {"role": "system", "content": WORKFLOW_REVIEW_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_repair_messages(raw: str, error: str) -> list:
    """构造纠错 Prompt：上次输出不合规时要求 LLM 重来一次。"""
    user_content = REPAIR_TEMPLATE.replace("<<RAW>>", raw).replace("<<ERROR>>", error)
    return [
        {"role": "system", "content": WORKFLOW_REVIEW_SYSTEM},
        {"role": "user", "content": user_content},
    ]


class PromptBuilder:
    """构造审查 Prompt。"""

    def build(self, unit, runtime_info: str = None, language: str = "python") -> list:
        """返回 messages 列表，供 LLM 调用。

        runtime_info：代码执行工具返回的运行结果，可选
        （仅 Python 有，其他语言没有代码执行工具）。
        language：被审查代码的语言标识，如 "python" / "javascript"。
        """
        display = LANG_DISPLAY.get(language, language.capitalize())
        user_content = REVIEW_PROMPT_TEMPLATE.format(
            language=display,
            unit_type=unit.type,
            source=unit.source,
            lang_tag=language,
        )
        if runtime_info:
            user_content += (
                "\n\n以下是整段代码的运行结果，供你审查时参考：\n"
                "```text\n"
                f"{runtime_info}\n"
                "```\n"
                "若运行结果包含错误，请在「潜在 Bug」中重点说明。"
            )
        return [
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(language=display)},
            {"role": "user", "content": user_content},
        ]
