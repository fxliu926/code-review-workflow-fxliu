"""本地事实采集：给审查 Agent 提供「可验证」的依据，而不只是 LLM 的主观意见。

接进来的是 Homework 1 的两个模块：
- `parser.CodeParser`：用 ast 把代码拆成函数/类单元，顺便抓语法错误
- `tools.CodeRunner`：真的在子进程里跑一遍，抓运行时错误

LLM 擅长发现"设计上可疑"的地方，但语法错误、崩溃堆栈这类事情用工具查更可靠，
两者互补。工具查出来的问题会合成 Critical / Major 条目进问题列表，即使 LLM 漏看也不放过。

关于自动探针：它会根据函数签名里的类型注解构造一组示例入参去调用函数，
把抛出的异常作为运运行时证据。这是一个"尽力而为"的手段 ——
有些异常是因为示例入参本身不合理（比如用占位文件名去读文件），
这类 I/O 相关异常会被降级为 Minor，避免把正常代码卡在修复循环里。
"""
import ast
import os

from agents.parser import CodeParser
from agents.tools import CodeRunner

RUNTIME_ENABLED_ENV = "CRW_ENABLE_RUNTIME"
DEFAULT_TIMEOUT = 5
MAX_PROBE_CALLS = 3

# I/O 类异常多半是示例入参不合理导致的，降级为 Minor，避免误伤
IO_ERROR_NAMES = (
    "FileNotFoundError", "PermissionError", "IsADirectoryError",
    "NotADirectoryError", "OSError", "ConnectionError", "TimeoutError",
)

SCALAR_EXAMPLES = {
    "int": "1", "float": "1.0", "str": '"x"', "bool": "True", "bytes": 'b"x"',
}
# 列表类入参要给出多个元素，单个元素测不出遍历逻辑的问题
SCALAR_VALUES = {
    "1": ["1", "2", "3"],
    "1.0": ["1.0", "2.0", "3.0"],
    '"x"': ['"a"', '"b"', '"c"'],
    "True": ["True", "False", "True"],
}
CONTAINER_EXAMPLES = {
    "list": "[1, 2, 3]", "dict": "{}", "tuple": "()", "set": "set()",
}


def _runtime_enabled() -> bool:
    return os.getenv(RUNTIME_ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no")


def _annotation_name(node) -> str:
    """把类型注解还原成可读的名字，如 List[int] / Optional[str] / Tuple。"""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        base = _annotation_name(node.value)
        inner = _annotation_name(node.slice)
        return f"{base}[{inner}]" if inner else base
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):  # int | None
        return f"{_annotation_name(node.left)}|{_annotation_name(node.right)}"
    return ""


def _unwrap_optional(annotation: str) -> str:
    """Optional[str] / Union[str, None] / str | None → str。"""
    if annotation.startswith("Optional[") and annotation.endswith("]"):
        return annotation[len("Optional["):-1]
    if annotation.startswith("Union[") and annotation.endswith("]"):
        parts = [p.strip() for p in annotation[len("Union["):-1].split(",")]
        real = [p for p in parts if p not in ("None", "NoneType")]
        return real[0] if real else ""
    if "|" in annotation:
        real = [p.strip() for p in annotation.split("|") if p.strip() not in ("None", "NoneType")]
        return real[0] if real else ""
    return annotation


CONTAINER_BASES = ("List", "Sequence", "Iterable", "Tuple", "Set", "FrozenSet",
                   "Dict", "Mapping", "list", "tuple", "set", "dict", "frozenset")


def _is_container(annotation: str) -> bool:
    return _unwrap_optional(annotation).split("[")[0].strip() in CONTAINER_BASES


def _example_arg(annotation: str, mode: str = "normal"):
    """根据类型注解生成一个示例入参表达式；认不出来就返回 None。

    mode="empty" 时容器类型给空值，用来逼出下标越界一类的边界缺陷。
    """
    if not annotation:
        return None
    annotation = _unwrap_optional(annotation)
    if not annotation:
        return None
    base = annotation.split("[")[0].strip()

    if mode == "empty" and base in CONTAINER_BASES:
        return {}.get(base, "[]") if base in ("Dict", "Mapping", "dict") else "[]"

    return _normal_example(annotation, base)


def _normal_example(annotation: str, base: str):
    """生成「正常用法」下的示例入参。"""
    if annotation.startswith(("List[", "Sequence[", "Iterable[")):
        inner_expr = None
        if "[" in annotation:
            inner_expr = _normal_example(annotation.split("[", 1)[1].rstrip("]"),
                                         annotation.split("[", 1)[1].rstrip("]").split("[")[0])
        if inner_expr and SCALAR_VALUES.get(inner_expr):
            values = SCALAR_VALUES[inner_expr]
            return "[" + ", ".join(values) + "]"
        return "[1, 2, 3]"
    if base in ("Dict", "Mapping"):
        return "{}"
    if base in ("Tuple", "Set", "FrozenSet"):
        return CONTAINER_EXAMPLES.get(base.lower(), "()")
    if base in SCALAR_EXAMPLES:
        return SCALAR_EXAMPLES[base]
    if base in CONTAINER_EXAMPLES:
        return CONTAINER_EXAMPLES[base]
    return None


def probe_specs(source: str) -> list:
    """找出可以用示例入参调用的函数，返回 [(函数名, 入参表达式), ...]。

    只对「所有参数都有可识别注解」的函数下手 —— 猜错参数造成的 TypeError
    不是代码缺陷，混进报告只会误导后续修复。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    specs = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        args = node.args
        posonly = getattr(args, "posonlyargs", [])
        positional = posonly + args.args
        annotations = [_annotation_name(a.annotation) for a in positional]

        def build(mode):
            exprs = [_example_arg(ann, mode) for ann in annotations]
            return ", ".join(exprs) if all(e is not None for e in exprs) else None

        normal = build("normal")
        if normal is None:
            continue

        required_kwonly = len(args.kwonlyargs) - sum(
            1 for d in args.kw_defaults if d is not None)
        if required_kwonly > 0 or getattr(args, "kwarg", None):
            continue

        specs.append((node.name, normal))
        # 有容器参数时再补一个空值用例，逼出下标越界一类的边界缺陷
        if any(_is_container(ann) for ann in annotations):
            empty = build("empty")
            if empty is not None and empty != normal:
                specs.append((node.name, empty))

    return specs[:MAX_PROBE_CALLS]


def declared_raises(source: str) -> dict:
    """扫出每个函数**自己显式抛出**的异常类型名，形如 {函数名: {"ValueError", ...}}。

    用途：探针会故意用空列表之类的边界入参去调用。这时函数有两种反应 ——

    - 自己 `raise ValueError("列表不能为空")` 拒绝非法输入：**正确行为**
    - 内部运算炸了（IndexError、ZeroDivisionError）：**缺陷**

    两者在子进程里看起来都是"抛异常"，不区分的话，已经写对的代码会被判成
    有问题，于是修无可修、一路空转到轮次上限。所以要用静态信息区分。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Raise) or sub.exc is None:
                continue
            exc = sub.exc
            if isinstance(exc, ast.Call):      # raise ValueError("...")
                exc = exc.func
            if isinstance(exc, ast.Name):
                names.add(exc.id)
            elif isinstance(exc, ast.Attribute):   # raise mod.CustomError
                names.add(exc.attr)
        result[node.name] = names
    return result


class Inspector:
    """采集代码的静态信息与运行时表现。"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, runtime: bool = None):
        self.parser = CodeParser()
        self.runner = CodeRunner(timeout=timeout)
        self.runtime_enabled = _runtime_enabled() if runtime is None else runtime

    def inspect(self, code: str) -> dict:
        """返回 {"unit_count", "unit_names", "static_error", "runtime", ...}。"""
        result = {
            "unit_count": 0,
            "unit_names": [],
            "static_error": "",
            "runtime": "",
            "probe_specs": [],
            "declared_raises": {},
            "runtime_enabled": self.runtime_enabled,
        }

        parsed = self.parser.parse(code)
        if parsed.error:
            result["static_error"] = parsed.error
            # 解析不了就没法跑，不再采集运行时信息
            return result

        result["unit_count"] = len(parsed.units)
        result["unit_names"] = [u.name for u in parsed.units]

        if not self.runtime_enabled:
            result["runtime"] = "（运行时执行已关闭）"
            return result

        result["probe_specs"] = probe_specs(code)
        result["declared_raises"] = declared_raises(code)
        result["runtime"] = self._probe(code, result["probe_specs"])
        return result

    def _probe(self, code: str, specs: list) -> str:
        """在子进程里执行代码，并按示例入参调用可探测的函数。"""
        if not specs:
            return self.runner.run(code)

        cases = ", ".join(f"({name!r}, {args!r})" for name, args in specs)
        probe = "\n".join([
            code.rstrip(),
            "",
            "# ---- 自动探针：用示例入参调用函数，观察是否抛异常 ----",
            "_probe_report = []",
            f"for _name, _args in [{cases}]:",
            "    try:",
            "        _value = eval(f'{_name}({_args})')",
            "        _probe_report.append(f'{_name}({_args}) -> {repr(_value)[:80]}')",
            "    except Exception as _e:",
            "        _probe_report.append(f'{_name}({_args}) -> {type(_e).__name__}: {_e}')",
            "print('probe:')",
            "for _line in _probe_report:",
            "    print('probe:', _line)",
        ])
        return self.runner.run(probe)
