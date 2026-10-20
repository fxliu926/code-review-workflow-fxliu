"""PSP 度量：把一次运行整理成 Size / Effort / Quality / Schedule 四组数据。

对应课程第 2 讲 PSP 的四个核心度量：
Size（规模）、Effort（时间）、Quality（缺陷）、Schedule（进度）。

为什么值得做：这些数字本来就在项目里 —— 轨迹记了每个节点的耗时，
共享状态记了每轮的问题，代码就在状态里躺着。缺的只是按 PSP 的方式
把它们组织起来。用自己的 Workflow 反过来度量自己的过程，正好是
"你无法改进你无法度量的东西"这句话的一个小实践。

另外顺手量化一件事：**并行到底省了多少** —— 把各分支耗时累加起来
和容器的实际耗时比一比，就能算出节省比例，不用靠嘴说。
"""
import ast

# 节点名 → PSP 阶段。并行容器的耗时已包含在分支里，不能重复计入，
# 所以容器节点（如 analyze）单独标记为 "parallel" 但不参与阶段耗时汇总。
PHASE_OF_NODE = {
    "code": "编码",
    "coder": "编码",
    "review": "审查",
    "analyze/review": "审查",
    "lint": "规范检查",
    "analyze/lint": "规范检查",
    "fix": "修复",
    "fixer": "修复",
    "test": "测试",
    "tester": "测试",
}

PHASE_ORDER = ("编码", "审查", "规范检查", "修复", "测试")

# 阶段配色，界面画时间分布条时用（与节点色一致：编码蓝、审查紫、规范检查青、修复橙）
PHASE_COLORS = {
    "编码": "#58a6ff",
    "审查": "#a371f7",
    "规范检查": "#39c5cf",
    "修复": "#d29922",
    "测试": "#3fb950",
}

MAX_PLANNED_ROUNDS = 3


def count_loc(code: str) -> dict:
    """统计代码规模：总行 / 代码行 / 注释行 / 空行 / 函数数。"""
    empty = {"total": 0, "code": 0, "comment": 0, "blank": 0, "functions": 0}
    if not code or not code.strip():
        return empty

    lines = code.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.strip().startswith("#"))
    functions = 0
    try:
        tree = ast.parse(code)
        functions = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    except SyntaxError:
        functions = 0

    return {
        "total": len(lines),
        "code": len(lines) - blank - comment,
        "comment": comment,
        "blank": blank,
        "functions": functions,
    }


def _defect_key(issue: dict):
    """缺陷的身份：等级 + 描述。与合并时的去重口径保持一致。"""
    return (issue.get("level"), issue.get("description"))


class DefectLog:
    """按轮次记录发现的问题，回溯每个缺陷的发现与修复时机。

    PSP 关心的正是"缺陷何时注入、何时发现、何时修复"。这里能观察到的是
    发现与修复两个时刻 —— 注入时刻无法从外部判断（问题可能在第 1 轮
    就埋下了，只是第 2 轮才被查出来）。
    """

    def __init__(self, rounds: list = None):
        self.rounds = rounds or []

    def add(self, round_no, issues: list):
        self.rounds.append({"round": round_no, "issues": list(issues or [])})

    def lifecycle(self) -> list:
        """返回每个缺陷的记录：何时发现、是否修好、在哪一轮修好。"""
        first_seen = {}
        last_seen = {}
        sample = {}

        for entry in self.rounds:
            round_no = entry["round"]
            for issue in entry["issues"]:
                key = _defect_key(issue)
                first_seen.setdefault(key, round_no)
                last_seen[key] = round_no
                sample[key] = issue

        final_round = self.rounds[-1]["round"] if self.rounds else None
        result = []
        for key, issue in sample.items():
            found = first_seen[key]
            gone = last_seen[key] != final_round
            fixed = last_seen[key] + 1 if gone else None
            result.append({
                "level": issue.get("level"),
                "description": issue.get("description"),
                "source": issue.get("source", "—"),
                "found_round": found,
                "fixed_round": fixed,
                "survived": (fixed - found) if fixed else None,
                "status": "已修复" if gone else "遗留",
            })
        result.sort(key=lambda d: (d["found_round"], d["level"] or "", d["description"] or ""))
        return result

    def summary(self) -> dict:
        life = self.lifecycle()
        fixed = [d for d in life if d["status"] == "已修复"]
        by_level = {}
        by_source = {}
        for item in life:
            by_level[item["level"]] = by_level.get(item["level"], 0) + 1
            by_source[item["source"]] = by_source.get(item["source"], 0) + 1
        return {
            "found": len(life),
            "fixed": len(fixed),
            "remaining": len(life) - len(fixed),
            "fix_rate": round(len(fixed) / len(life), 3) if life else 0.0,
            "by_level": by_level,
            "by_source": by_source,
            "rounds": len(self.rounds),
        }


def effort_by_phase(records: list) -> dict:
    """按阶段汇总耗时。

    两条记账规则，否则并行部分会算重：

    - 分支节点（名字形如 `analyze/review`）：算进所属阶段，同时累加进 branch_ms
    - 并行容器（`analyze`）：它的耗时已经等于最慢分支，再加一遍就重复了，
      所以只记进 container_ms，用来和 branch_ms 对比算并行收益
    """
    phases = {name: 0.0 for name in PHASE_ORDER}
    container_ms = 0.0
    branch_ms = 0.0

    for item in records:
        node = item["node"]
        ms = item.get("duration_ms", 0) or 0

        if "/" in node:                      # 并行分支
            branch_ms += ms
            phase = PHASE_OF_NODE.get(node) or PHASE_OF_NODE.get(node.split("/")[-1])
        elif _is_container(records, node):   # 并行容器
            container_ms += ms
            continue
        else:                                # 普通节点
            phase = PHASE_OF_NODE.get(node)

        if phase:
            phases[phase] = phases.get(phase, 0.0) + ms

    return {
        "phases": phases,
        "total_ms": sum(phases.values()),
        "container_ms": container_ms,
        "branch_ms": branch_ms,
        "parallel_saving": (1 - container_ms / branch_ms) if branch_ms else 0.0,
    }


def _is_container(records: list, node: str) -> bool:
    """某个节点是不是并行容器：存在以它开头的分支记录，就说明它是容器。"""
    return any(r["node"].startswith(node + "/") for r in records)


def summarize(state: dict, records: list) -> dict:
    """把一次运行压成 PSP 四组数据。"""
    code = state.get("code", "") or ""

    versions = state.get("versions", []) or []
    locs = [count_loc(v.get("code", "")) for v in versions]
    if not locs:
        locs = [count_loc(code)]
    final_loc = count_loc(code)

    log = DefectLog(state.get("defect_log") or [])
    defects = log.summary()
    effort = effort_by_phase(records)

    rounds = state.get("round") or 0
    loc_code = final_loc["code"] or 1

    return {
        "size": {
            "final": final_loc,
            "versions": len(versions),
            "first_loc": locs[0]["code"] if locs else final_loc["code"],
            "growth": (final_loc["code"] - locs[0]["code"]) if locs else 0,
        },
        "effort": effort,
        "quality": {
            **defects,
            "density_per_100loc": round(defects["found"] / loc_code * 100, 1),
        },
        "schedule": {
            "planned_rounds": MAX_PLANNED_ROUNDS,
            "actual_rounds": rounds,
            "within_budget": bool(rounds and rounds <= MAX_PLANNED_ROUNDS),
            "passed": bool(state.get("passed")),
        },
    }


def render_psp_markdown(summary: dict) -> str:
    size = summary["size"]
    effort = summary["effort"]
    quality = summary["quality"]
    schedule = summary["schedule"]

    total_s = effort["total_ms"] / 1000
    loc = size["final"]

    lines = [
        "## PSP 度量",
        "",
        "### Size（规模）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 最终代码行数 | {loc['total']} 行（代码 {loc['code']} / 注释 {loc['comment']} / 空行 {loc['blank']}） |",
        f"| 函数数 | {loc['functions']} |",
        f"| 产出版本数 | {size['versions']} |",
        f"| 相对初版行数 | {size['growth']:+d} |",
        "",
        "### Effort（时间分布）",
        "",
        "| 阶段 | 耗时(s) | 占比 |",
        "| --- | --- | --- |",
    ]
    for phase in PHASE_ORDER:
        ms = effort["phases"].get(phase, 0.0)
        ratio = (ms / effort["total_ms"] * 100) if effort["total_ms"] else 0
        lines.append(f"| {phase} | {ms / 1000:.2f} | {ratio:.0f}% |")
    lines.append(f"| **合计** | **{total_s:.2f}** | 100% |")

    if effort["branch_ms"]:
        saving = max(0.0, effort["parallel_saving"]) * 100
        branch_s = effort["branch_ms"] / 1000
        container_s = effort["container_ms"] / 1000
        lines += [
            "",
            f"- 并行：各分支耗时累加 {branch_s:.2f}s，容器实际 {container_s:.2f}s，"
            f"节省 {saving:.0f}%",
        ]
        # 分支耗时悬殊时并行几乎省不下时间，如实说明，别拿它充数
        if saving < 1:
            lines.append(
                "  - 快分支耗时占比极低，本例的时间收益有限；"
                "并行的意义在于再加一个慢分支也不会拖长总时长"
            )

    lines += [
        "",
        "### Quality（缺陷）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 发现 | {quality['found']} |",
        f"| 已修复 | {quality['fixed']} |",
        f"| 遗留 | {quality['remaining']} |",
        f"| 修复率 | {quality['fix_rate'] * 100:.0f}% |",
        f"| 缺陷密度 | {quality['density_per_100loc']} / 100 行 |",
    ]
    if quality["by_level"]:
        dist = "、".join(f"{k} {v}" for k, v in sorted(quality["by_level"].items()))
        lines.append(f"| 按等级 | {dist} |")
    if quality["by_source"]:
        src = "、".join(f"{k} {v}" for k, v in sorted(quality["by_source"].items()))
        lines.append(f"| 按来源 | {src} |")

    verdict = "在预算内（≤ 3 轮）" if schedule["within_budget"] else "超出预算"
    lines += [
        "",
        "### Schedule（进度）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 计划轮次上限 | {schedule['planned_rounds']} |",
        f"| 实际轮次 | {schedule['actual_rounds']} |",
        f"| 结论 | {verdict}，{'通过' if schedule['passed'] else '未通过'} |",
        "",
    ]
    return "\n".join(lines)


def render_defect_table(log: DefectLog) -> str:
    """缺陷生命周期表：哪个缺陷、哪一轮发现、有没有修好。"""
    life = log.lifecycle()
    if not life:
        return ""
    lines = [
        "### 缺陷追踪",
        "",
        "| 等级 | 发现轮次 | 修复轮次 | 存活 | 状态 | 来源 | 描述 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in life:
        desc = (item["description"] or "").replace("|", "\\|")
        if len(desc) > 46:
            desc = desc[:46] + "…"
        survived = item["survived"] if item["survived"] is not None else "—"
        lines.append(
            f"| {item['level']} | {item['found_round']} | {item['fixed_round'] or '—'} | "
            f"{survived} | {item['status']} | {item['source']} | {desc} |"
        )
    lines.append("")
    return "\n".join(lines)
