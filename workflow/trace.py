"""执行追踪：记录每个节点的出入状态、耗时与跳转，用于排查与复盘。

作业要求里的「执行日志与状态追踪」就落在这里：
每次运行产出 `runs/<时间戳>/trace.jsonl`（机器可读）+ `report.md`（人可读）。
"""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from workflow.psp import DefectLog, render_psp_markdown, render_defect_table, summarize

SCALAR_TYPES = (str, int, float, bool, type(None))
PREVIEW_LEN = 60


def _preview(value) -> str:
    """把任意字段压成一行预览，避免日志被整段代码撑爆。"""
    if isinstance(value, SCALAR_TYPES):
        text = str(value).replace("\n", "\\n")
    elif isinstance(value, list):
        text = f"list[{len(value)}]"
    elif isinstance(value, dict):
        text = f"dict[{len(value)}]"
    else:
        text = type(value).__name__
    return text if len(text) <= PREVIEW_LEN else text[:PREVIEW_LEN] + "…"


def diff_state(before: dict, after: dict) -> dict:
    """浅比较两个状态快照，返回变化的字段（Team 日志里最关心的部分）。"""
    changed = {}
    for key, value in after.items():
        if key not in before:
            changed[key] = f"新增 = {_preview(value)}"
            continue
        old = before[key]
        if isinstance(old, SCALAR_TYPES) and isinstance(value, SCALAR_TYPES):
            if old != value:
                changed[key] = f"{_preview(old)} → {_preview(value)}"
        elif type(old) is not type(value):
            changed[key] = f"类型变化 {type(old).__name__} → {type(value).__name__}"
        elif isinstance(old, (list, dict)) and isinstance(value, (list, dict)):
            if len(old) != len(value):
                changed[key] = f"长度 {len(old)} → {len(value)}"
    return changed


class Trace:
    """一次 Workflow 运行的完整轨迹。"""

    def __init__(self, run_id: str = None):
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.started_at = datetime.now()
        self.records = []
        self.finished_at = None
        self.final_state = {}

    # ---------- 记录 ----------

    def record(self, step: int, node: str, ok: bool, duration_ms: float,
               next_node: str, changed: dict, error: str = None,
               round_no=None, state: dict = None):
        self.records.append({
            "step": step,
            "node": node,
            "round": round_no,
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "next": next_node,
            "changed": changed,
            "error": error,
            "issues": dict(state.get("issue_counts", {})) if isinstance(state, dict) else {},
            "at": datetime.now().isoformat(timespec="seconds"),
        })

    def finish(self, state: dict):
        self.finished_at = datetime.now()
        self.final_state = dict(state or {})

    # ---------- 输出 ----------

    @property
    def duration_ms(self) -> float:
        if not self.finished_at:
            return (datetime.now() - self.started_at).total_seconds() * 1000
        return (self.finished_at - self.started_at).total_seconds() * 1000

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds") if self.finished_at else None,
            "duration_ms": round(self.duration_ms, 1),
            "steps": len(self.records),
            "records": self.records,
            "final": {
                "round": self.final_state.get("round"),
                "passed": self.final_state.get("passed"),
                "degraded": self.final_state.get("degraded"),
                "issue_counts": self.final_state.get("issue_counts", {}),
                "versions": len(self.final_state.get("versions", [])),
            },
        }

    def save(self, run_dir: Path):
        """落盘 jsonl + markdown 报告，返回生成的文件路径。"""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        jsonl = run_dir / "trace.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            for item in self.records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        report = run_dir / "report.md"
        report.write_text(self.render_markdown(), encoding="utf-8")
        return {"jsonl": jsonl, "report": report}

    def render_markdown(self) -> str:
        final = self.to_dict()["final"]
        lines = [
            "# Workflow 执行报告",
            "",
            f"- run_id：`{self.run_id}`",
            f"- 开始时间：{self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 总耗时：{round(self.duration_ms / 1000, 2)}s",
            f"- 节点执行次数：{len(self.records)}",
            f"- 结果：{'通过' if final.get('passed') else '未通过'}"
            f"（第 {final.get('round')} 轮，{'降级' if final.get('degraded') else '正常'}）",
            f"- 代码版本数：{final.get('versions')}",
            "",
            "## 执行轨迹",
            "",
            "| # | 节点 | 轮次 | 耗时(ms) | 结果 | 状态变化 | 跳转 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in self.records:
            changed = "；".join(f"`{k}` {v}" for k, v in item["changed"].items()) or "—"
            status = "成功" if item["ok"] else f"失败：{item['error']}"
            lines.append(
                f"| {item['step']} | `{item['node']}` | {item['round'] or '-'} | "
                f"{item['duration_ms']} | {status} | {changed} | → `{item['next']}` |"
            )

        failed = [r for r in self.records if not r["ok"]]
        if failed:
            lines += ["", "## 失败记录", ""]
            for r in failed:
                lines.append(f"- 步骤 {r['step']} 节点 `{r['node']}`：{r['error']}")

        state = self.final_state or {}
        tests = state.get("test_cases") or []
        if tests:
            passed = sum(1 for c in tests if c["status"] == "PASS")
            lines += [
                "",
                "## 单元测试",
                "",
                f"结果：{passed} 通过 / {len(tests) - passed} 失败",
                "",
            ]
            # 模型偶尔会把被测代码抄进测试文件；抄来的定义已静态剔除，
            # 否则跑的是副本，结果没有意义 —— 这件事要在报告里说清楚。
            contaminated = state.get("test_contaminated") or []
            if contaminated:
                names = "、".join(f"`{n}`" for n in contaminated)
                lines += [
                    f"> 已剔除测试中对 {names} 的重复定义："
                    "这些名字在被测代码里已有，留着就会测到副本而不是真实代码。",
                    "",
                ]
            lines += [
                "| 用例 | 结果 | 说明 |",
                "| --- | --- | --- |",
            ]
            for case in tests:
                note = case.get("message") or "—"
                lines.append(f"| `{case['name']}` | {case['status']} | {note} |")

        if self.final_state:
            lines += ["", psp_section(self.final_state, self.records)]

        lines.append("")
        return "\n".join(lines)

    def psp(self) -> dict:
        """本次运行的 PSP 四组度量，供界面复用。"""
        return summarize_psp(self.final_state, self.records)


def psp_section(state: dict, records: list) -> str:
    """PSP 度量 + 缺陷追踪，两段拼在一起。"""
    return render_psp_markdown(summarize(state, records)) + "\n" + render_defect_table(
        DefectLog(state.get("defect_log") or []))


def summarize_psp(state: dict, records: list) -> dict:
    return summarize(state, records)


def make_run_dir(base: Path, run_id: str) -> Path:
    """生成 runs/<日期时间>-<run_id> 形式的目录。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(base) / f"{stamp}-{run_id}"
