"""代码解析模块：用 ast 把 Python 代码拆成可审查的单元。"""
import ast
from dataclasses import dataclass, field
from typing import List


@dataclass
class CodeUnit:
    """一个可审查的代码单元（函数/类/方法）。"""
    name: str
    type: str
    source: str
    lineno: int
    docstring: str = ""


@dataclass
class ParseResult:
    """解析结果。"""
    units: List[CodeUnit] = field(default_factory=list)
    error: str = ""


class CodeParser:
    """把 Python 源码解析成 CodeUnit 列表。"""

    def parse(self, source: str) -> ParseResult:
        if not source or not source.strip():
            return ParseResult(error="文件为空")

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return ParseResult(error=f"语法错误：第 {e.lineno} 行 - {e.msg}")

        lines = source.splitlines()
        units: List[CodeUnit] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.append(self._build_unit(node, lines, "function"))
            elif isinstance(node, ast.ClassDef):
                units.append(self._build_unit(node, lines, "class"))

        if not units:
            return ParseResult(error="未找到函数或类")

        return ParseResult(units=units)

    def parse_module(self, source: str, name: str = "input") -> ParseResult:
        """非 Python 语言没有 ast 可用，整段源码作为一个审查单元。

        这是「降级路径」：不做结构化拆分，直接交给 LLM 审查，
        报告里会注明该文件走的是 LLM 直接审查模式。
        """
        if not source or not source.strip():
            return ParseResult(error="文件为空")
        return ParseResult(
            units=[CodeUnit(name=name, type="文件", source=source, lineno=1)]
        )

    def _build_unit(self, node, lines, unit_type: str) -> CodeUnit:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        source = "\n".join(lines[start:end])
        docstring = ast.get_docstring(node) or ""
        return CodeUnit(
            name=node.name,
            type=unit_type,
            source=source,
            lineno=node.lineno,
            docstring=docstring,
        )