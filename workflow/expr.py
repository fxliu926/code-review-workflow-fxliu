"""受限表达式求值器：替代 eval()，用于在 YAML 里安全地写跳转条件。

**为什么不用 eval**

引擎原本用 `eval(cond["when"], {}, state)` 直接执行配置里的字符串。
配置文件等同于外部输入，一旦被改成 `__import__("os").system(...)` 之类就会被原样执行，
在第 3 讲「代码规范 / 安全性」的语境下这是明显的扣分项。

这里用 ast 白名单实现：只允许字面量、变量取值、属性访问、下标、比较、布尔运算、
四则运算和少量内置函数，其余语法一律拒绝。
"""
import ast
import operator


class SafeExprError(ValueError):
    """表达式不合法或不受支持。"""


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}

_UNARY_OPS = {
    ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos,
}

_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: operator.contains(b, a),
    ast.NotIn: lambda a, b: not operator.contains(b, a),
}

# 仅开放纯函数、无副作用的内置
_ALLOWED_FUNCS = {
    "len": len, "abs": abs, "sum": sum, "min": min, "max": max,
    "round": round, "bool": bool, "int": int, "float": float, "str": str,
}

_ALLOWED_NODES = tuple(
    [
        # 结构
        ast.Expression, ast.Load,
        # 运算
        ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.BinOp, ast.Compare,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
        ast.Not, ast.USub, ast.UAdd,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
        # 取值与调用
        ast.Call, ast.Name, ast.Constant, ast.Attribute, ast.Subscript,
        ast.Tuple, ast.List,
    ]
    + ([ast.Index] if hasattr(ast, "Index") else [])  # Python 3.9 之前的下标节点
)


def _check(node):
    if not isinstance(node, _ALLOWED_NODES):
        raise SafeExprError(f"不支持的语法：{type(node).__name__}")
    for child in ast.iter_child_nodes(node):
        _check(child)


def _eval(node, state: dict):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.BoolOp):
        values = [_eval(v, state) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise SafeExprError(f"不支持的运算符：{type(node.op).__name__}")
        return op(_eval(node.operand, state))

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise SafeExprError(f"不支持的运算符：{type(node.op).__name__}")
        return op(_eval(node.left, state), _eval(node.right, state))

    if isinstance(node, ast.Compare):
        left = _eval(node.left, state)
        for op, comparator in zip(node.ops, node.comparators):
            fn = _CMP_OPS.get(type(op))
            if fn is None:
                raise SafeExprError(f"不支持的比较：{type(op).__name__}")
            right = _eval(comparator, state)
            if not fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Name):
        if node.id not in state:
            raise SafeExprError(f"状态里没有字段：{node.id}")
        return state[node.id]

    if isinstance(node, ast.Attribute):
        value = _eval(node.value, state)
        if node.attr.startswith("_"):
            raise SafeExprError("不允许访问下划线开头的私有属性")
        if not hasattr(value, node.attr):
            raise SafeExprError(f"对象没有属性：{node.attr}")
        return getattr(value, node.attr)

    if isinstance(node, ast.Subscript):
        value = _eval(node.value, state)
        return value[_eval(node.slice, state)]

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise SafeExprError("只允许调用白名单内置函数")
        # get("字段", 默认值)：读一个**可能不存在**的字段。
        # 直接写变量名在字段缺失时会报 SafeExprError（fail fast，防拼错），
        # 但有些字段确实可能还没被写入 —— 比如某个节点被 skip 跳过了，
        # 它负责的字段就根本不会出现。这种情况要显式用 get() 表示"没有就算默认值"。
        if node.func.id == "get":
            args = [_eval(a, state) for a in node.args]
            if not args or not isinstance(args[0], str):
                raise SafeExprError('get() 的第一个参数必须是字段名字符串')
            if len(args) > 2:
                raise SafeExprError("get() 最多两个参数")
            return state.get(args[0], args[1] if len(args) > 1 else None)
        fn = _ALLOWED_FUNCS.get(node.func.id)
        if fn is None:
            raise SafeExprError(f"不允许调用函数：{node.func.id}")
        return fn(*[_eval(a, state) for a in node.args])

    if isinstance(node, ast.List):
        return [_eval(e, state) for e in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_eval(e, state) for e in node.elts)

    raise SafeExprError(f"不支持的语法：{type(node).__name__}")


def evaluate(expr: str, state: dict) -> bool:
    """求值 YAML 里的 when 表达式，返回布尔结果。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise SafeExprError(f"表达式语法错误：{e}") from e

    _check(tree)
    return bool(_eval(tree.body, state))
