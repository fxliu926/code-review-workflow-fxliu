"""Workflow 可视化界面。

风格：深色控制台（GitHub Dark / Linear 那一路）—— 深底 #0d1117、卡片 #161b22、
#30363d 边框、#e6edf3 正文；节点配 SVG 图标，耗时用条形图表示。

不用渐变、不用 emoji、不做居中对称的模板式布局；层次靠配色、字号与间距拉开。
"""
import html
import os
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gradio as gr
import yaml

from agents.aggregator import merge_issues
from agents.coder import coder_agent
from agents.fixer import fixer_agent
from agents.linter import linter_agent
from agents.reviewer import reviewer_agent
from agents.tester import tester_agent
from workflow.engine import WorkflowEngine
from workflow.psp import PHASE_COLORS, PHASE_ORDER, DefectLog, summarize
from workflow.trace import Trace

AGENT_REGISTRY = {
    "coder": coder_agent,
    "reviewer": reviewer_agent,
    "linter": linter_agent,
    "fixer": fixer_agent,
    "tester": tester_agent,       # 生成单元测试，执行走 CodeRunner 工具
    "merge_issues": merge_issues,
}

CONFIG_PATH = ROOT / "config" / "workflow.yaml"
# 输入框默认留空，只给占位提示 —— 预填一句示例需求会让人误以为那是必填项，
# 也容易在演示时忘了改就跑起来。
PLACEHOLDER = (
    "告诉编码 Agent 要写什么，例如：写一个函数，返回列表中最大的元素，"
    "假设输入列表非空，不需要做参数校验"
)

# ---------- 图标与配色 ----------

# 16x16 stroke 图标，颜色跟随所在元素的 color
ICON_PATHS = {
    "code": '<path d="M6 3 2 8l4 5"/><path d="M10 3l4 5-4 5"/>',
    "review": '<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/>',
    "lint": '<path d="M2.5 4h11"/><path d="M2.5 8h11"/><path d="M2.5 12h7"/>',
    "fix": '<path d="M13.6 8A5.6 5.6 0 1 1 11.7 3.8"/><path d="M13.6 2v3.3h-3.3"/>',
    "parallel": '<path d="M8 13.5V8"/><path d="M8 8 4.6 4.4"/><path d="M8 8l3.4-3.6"/>',
    "check": '<path d="M2.5 8.5 6 12l7.5-8"/>',
    "flow": '<path d="M2.5 4.5h4l3 7 4-7"/><path d="M12 4.5h1.5"/>',
}

# 每个节点的图标与配色 class（用 class 而不是内联 style，
# 免得被前端的 HTML 清洗剥掉颜色）
NODE_STYLE = {
    "code": ("code", "c-blue"),
    "analyze": ("parallel", "c-blue"),
    "analyze/review": ("review", "c-purple"),
    "analyze/lint": ("lint", "c-cyan"),
    "review": ("review", "c-purple"),
    "lint": ("lint", "c-cyan"),
    "fix": ("fix", "c-amber"),
    "test": ("check", "c-green"),
}

# 与上面 class 一一对应的实色，给耗时条用（SVG fill 属性，不受清洗影响）
CLASS_COLOR = {
    "c-blue": "#58a6ff", "c-purple": "#a371f7", "c-cyan": "#39c5cf",
    "c-amber": "#d29922", "c-green": "#3fb950", "c-muted": "#8b949e",
}

NODE_LABELS = {
    "code": "编码 Agent",
    "analyze": "并行容器",
    "analyze/review": "审查 Agent（LLM）",
    "analyze/lint": "规范检查（本地规则）",
    "review": "审查 Agent",
    "lint": "规范检查",
    "fix": "修复 Agent",
    "test": "测试 Agent",
}

AGENT_LABELS = {
    "coder": "编码 Agent",
    "reviewer": "审查 Agent",
    "linter": "规范检查 Agent",
    "fixer": "修复 Agent",
    "tester": "测试 Agent",
}

GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
MUTED = "#8b949e"

# 时间分布图例用的小色块，走 class 而非内联 style（前端会清洗 style）
PHASE_SWATCH = {
    "编码": "sw-code",
    "审查": "sw-review",
    "规范检查": "sw-lint",
    "修复": "sw-fix",
    "测试": "sw-test",
}


def icon(name: str, size: int = 14) -> str:
    body = ICON_PATHS.get(name, "")
    return (
        f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 16 16" '
        f'fill="none" stroke="currentColor" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CUSTOM_CSS = """
:root {
  --bg: #0d1117; --card: #161b22; --card2: #0d1117;
  --border: #30363d; --border-soft: #21262d;
  --text: #e6edf3; --muted: #8b949e;
  --green: #3fb950; --red: #f85149; --amber: #d29922;
  --blue: #58a6ff; --purple: #a371f7; --cyan: #39c5cf;
}
/* Gradio 自己用一套 CSS 变量控制配色，逐个覆盖元素压不住，
   必须把这些变量本身改掉，输入框、卡片、边框才会整体变深。 */
.gradio-container, gradio-app, :root {
  --body-text-color: #e6edf3;
  --body-text-color-subdued: #8b949e;
  --body-background-fill: #0d1117;
  --background-fill-primary: #161b22;
  --background-fill-secondary: #0d1117;
  --block-background-fill: #161b22;
  --block-border-color: #30363d;
  --block-label-background-fill: transparent;
  --block-label-text-color: #8b949e;
  --block-title-text-color: #e6edf3;
  --input-background-fill: #0d1117;
  --input-border-color: #30363d;
  --border-color-primary: #30363d;
  --border-color-accent: #58a6ff;
  --color-accent: #58a6ff;
  --neutral-50: #161b22;
  --neutral-100: #161b22;
  --neutral-200: #30363d;
  --neutral-300: #30363d;
  --neutral-400: #6e7681;
  --neutral-500: #8b949e;
  --neutral-600: #b1bac4;
  --neutral-700: #c9d1d9;
  --neutral-800: #e6edf3;
  --neutral-900: #e6edf3;
  --neutral-950: #f0f6fc;
  --code-background-fill: #0d1117;
  color-scheme: dark;
}
.gradio-container { background: var(--bg) !important; max-width: 1320px !important; }
.gradio-container .block { padding: 8px 0; }
body { background: #0d1117; }

/* ---- 卡片与顶栏 ---- */
#header { padding: 14px 18px; background: var(--card); border: 1px solid var(--border);
          border-radius: 10px; }
#logo-wrap { display: flex; align-items: center; gap: 10px; }
#logo-mark { width: 30px; height: 30px; border-radius: 7px; display: flex;
             align-items: center; justify-content: center;
             background: rgba(88,166,255,.13); border: 1px solid rgba(88,166,255,.35);
             color: var(--blue); flex: none; }
#logo { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 15px;
        font-weight: 600; color: var(--text); letter-spacing: -0.01em; }
#subtitle { font-size: 12px; color: var(--muted); margin-top: 2px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 12px 14px; }
.hint { color: var(--muted); font-size: 13px; padding: 10px 2px; }

/* ---- 分区标题 ---- */
h3 { font-size: 12px !important; font-weight: 600 !important; color: var(--text) !important;
     margin: 2px 0 8px !important; padding-bottom: 7px !important;
     border-bottom: 1px solid var(--border) !important; letter-spacing: 0.02em; }
h3::before { content: ""; display: inline-block; width: 3px; height: 11px;
             background: var(--blue); border-radius: 2px; margin-right: 7px;
             vertical-align: -1px; }

/* ---- 统计格 ---- */
.stat-grid { display: grid !important;
             grid-template-columns: repeat(7, minmax(0, 1fr)) !important; gap: 8px; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 10px 13px; position: relative; overflow: hidden; }
.stat::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
                background: var(--border); }
.stat.is-ok::before { background: var(--green); }
.stat.is-bad::before { background: var(--red); }
.stat-label { font-size: 10px; color: var(--muted); letter-spacing: 0.05em;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stat-value { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
              font-size: 20px; font-weight: 600; color: var(--text); line-height: 1.4;
              font-variant-numeric: tabular-nums; }
.v-ok { color: var(--green) !important; }
.v-bad { color: var(--red) !important; }
.v-dim { color: var(--muted) !important; }
.mid { font-size: 15px; }
.gap-top { margin-top: 8px; }

/* ---- 状态点 / 徽章 ---- */
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
       margin-right: 6px; vertical-align: 1px; background: #6e7681; }
.dot-run { background: var(--amber); box-shadow: 0 0 0 3px rgba(210,153,34,.18); }
.dot-ok { background: var(--green); box-shadow: 0 0 0 3px rgba(63,185,80,.16); }
.dot-bad { background: var(--red); box-shadow: 0 0 0 3px rgba(248,81,73,.16); }
.chip { display: inline-flex; align-items: center; font-family: ui-monospace,
        SFMono-Regular, Consolas, monospace; font-size: 12px; padding: 4px 11px;
        border-radius: 999px; border: 1px solid var(--border);
        background: var(--card); color: var(--text); }
.chip-run { border-color: rgba(210,153,34,.45); background: rgba(210,153,34,.12);
            color: #e3b341; }
.chip-ok { border-color: rgba(63,185,80,.45); background: rgba(63,185,80,.12);
           color: #56d364; }
.chip-bad { border-color: rgba(248,81,73,.45); background: rgba(248,81,73,.12);
            color: #ff7b72; }

/* ---- 表格 ---- */
table.tbl { width: 100%; border-collapse: collapse; font-size: 13px;
            table-layout: auto; }
table.tbl th { text-align: left; font-size: 10px; font-weight: 600; color: var(--muted);
               letter-spacing: 0.06em; padding: 7px 10px;
               border-bottom: 1px solid var(--border); background: var(--card2);
               white-space: nowrap; }
/* td 里的颜色会被 Gradio 的 .prose 表格样式盖掉，逐个声明并加 !important */
table.tbl td { padding: 8px 10px; border-bottom: 1px solid var(--border-soft);
               color: var(--text) !important; vertical-align: middle;
               white-space: nowrap; background: transparent; }
table.tbl td.dim, table.tbl td .dim { color: var(--muted) !important; }
table.tbl td.wrap { white-space: normal; min-width: 190px; }
table.tbl td .mono { color: inherit; }
table.tbl tbody tr:hover td { background: rgba(88,166,255,.05); }
table.tbl tr.row-branch td { background: rgba(110,118,129,.05); }
table.tbl tr.row-branch td:first-child { padding-left: 22px; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.dim { color: var(--muted); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.ic { vertical-align: -2px; margin-right: 7px; flex: none; }
.node { display: inline-flex; align-items: center; }
/* 节点名的颜色要压过 td 上那条 !important */
.c-blue, .c-blue .node-id { color: var(--blue) !important; }
.c-purple, .c-purple .node-id { color: var(--purple) !important; }
.c-cyan, .c-cyan .node-id { color: var(--cyan) !important; }
.c-amber, .c-amber .node-id { color: var(--amber) !important; }
.c-green, .c-green .node-id { color: var(--green) !important; }
.c-muted, .c-muted .node-id { color: var(--muted) !important; }
.small { font-size: 12px; margin-top: 2px; }
.meta { font-size: 12px; margin-bottom: 9px; }

/* ---- 耗时条 ---- */
.bar { display: block; width: 100%; height: 4px; margin-top: 5px; min-width: 60px; }
.bar-bg { fill: #21262d; }
table.tbl th:nth-child(4) { text-align: right; }

/* ---- PSP 时间分布 ---- */
.psp-bar { margin-top: 10px; }
.stack { display: block; width: 100%; height: 8px; border-radius: 4px;
         background: var(--card2); overflow: hidden; }
.legend { margin-top: 8px; font-size: 12px; color: var(--muted); }
.lg { margin-right: 16px; white-space: nowrap; }
.sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
      margin-right: 5px; vertical-align: 0; }
.sw-code { background: var(--blue); }
.sw-review { background: var(--purple); }
.sw-lint { background: var(--cyan); }
.sw-fix { background: var(--amber); }
.sw-test { background: var(--green); }

/* ---- 缺陷等级标签 ---- */
.t-critical { border-color: rgba(248,81,73,.45); background: rgba(248,81,73,.12);
              color: #ff7b72; }
.t-major { border-color: rgba(210,153,34,.45); background: rgba(210,153,34,.12);
           color: #e3b341; }
.t-minor { border-color: var(--border); background: var(--card2); color: var(--muted); }

/* ---- 流程定义 ---- */
.node-id { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
           font-weight: 600; }
.tag { display: inline-flex; align-items: center; font-family: ui-monospace,
       SFMono-Regular, Consolas, monospace; font-size: 11px; padding: 2px 7px;
       border-radius: 5px; border: 1px solid var(--border); background: var(--card2);
       color: var(--muted); }
.tag-para { border-color: rgba(88,166,255,.4); background: rgba(88,166,255,.12);
            color: #79c0ff; }
.cond { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px;
        color: #79c0ff; background: rgba(88,166,255,.1); border: 1px solid rgba(88,166,255,.3);
        border-radius: 5px; padding: 1px 6px; display: inline-block; margin-bottom: 3px; }

/* ---- Gradio 自带控件深色化 ----
   textarea 里的文字颜色要连 -webkit-text-fill-color 一起改，
   只写 color 会被浏览器默认的填充色压掉，看起来就是黑底黑字。 */
input, textarea { background: var(--card2) !important; color: var(--text) !important;
                  -webkit-text-fill-color: var(--text) !important;
                  caret-color: var(--blue) !important;
                  border: 1px solid var(--border) !important; border-radius: 8px !important;
                  font-family: ui-monospace, SFMono-Regular, Consolas, monospace !important; }
textarea::placeholder, input::placeholder {
    color: #6e7681 !important; -webkit-text-fill-color: #6e7681 !important; }
label, .block-label, span[data-testid="block-info"] { color: var(--muted) !important; }
#run-btn { background: #238636 !important; border: 1px solid rgba(240,246,252,.1) !important;
           color: #fff !important; border-radius: 8px !important; }
#run-btn:hover { background: #2ea043 !important; }
#clear-btn { background: var(--card) !important; border: 1px solid var(--border) !important;
             color: var(--text) !important; border-radius: 8px !important; }
/* 代码区：Gradio 的 Code 组件在空值时是一块浅色占位，非空时是编辑器，两处都要压深 */
#code-block { background: var(--card2) !important; border: 1px solid var(--border) !important;
              border-radius: 8px !important; }
#code-block .empty, #code-block [aria-label="Empty value"] {
    background: var(--card2) !important; color: var(--muted) !important; }
#code-block .empty svg { color: #30363d !important; }
#code-block textarea, #code-block pre, #code-block code {
    background: var(--card2) !important; color: var(--text) !important;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace !important; }
.cm-editor, .cm-scroller, .cm-content { background: var(--card2) !important; }
.cm-gutters { background: var(--card) !important; border-color: var(--border) !important;
              color: var(--muted) !important; }
.gr-markdown, .gr-markdown p, .gr-markdown li { color: var(--text) !important; }
.gr-markdown code { background: var(--card2) !important; border: 1px solid var(--border);
                    border-radius: 5px; }
.gr-markdown h2, .gr-markdown h3, .gr-markdown strong { color: var(--text) !important; }
"""


class LiveTrace(Trace):
    """在普通 Trace 之上加一条队列，供界面增量取用。"""

    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()

    def record(self, *args, **kwargs):
        super().record(*args, **kwargs)
        self.queue.put(self.records[-1])


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def chip(text: str, kind: str = "") -> str:
    cls = f"chip chip-{kind}" if kind else "chip"
    dot = f'<span class="dot dot-{kind}"></span>' if kind else '<span class="dot"></span>'
    return f'<span class="{cls}">{dot}{esc(text)}</span>'


def duration_bar(ms: float, max_ms: float, color: str) -> str:
    """耗时条形图：撑满单元格宽度，长度按相对最慢节点的比例。

    用 preserveAspectRatio="none" 让 SVG 随列宽拉伸，不用算像素。
    """
    ratio = (ms / max_ms) if max_ms else 0
    filled = max(1.5, ratio * 100)
    return (
        '<svg class="bar" viewBox="0 0 100 4" preserveAspectRatio="none">'
        '<rect class="bar-bg" x="0" y="0" width="100" height="4" rx="2"/>'
        f'<rect x="0" y="0" width="{filled:.1f}" height="4" rx="2" fill="{color}"/>'
        "</svg>"
    )


# ---------- 执行轨迹 ----------

def render_track(records: list) -> str:
    if not records:
        return '<div class="hint">等待开始…</div>'

    max_ms = max((r["duration_ms"] for r in records), default=0) or 1
    rows = []
    for item in records:
        node = item["node"]
        label = NODE_LABELS.get(node, node)
        glyph, css = NODE_STYLE.get(node, ("flow", "c-muted"))
        # 用 get 兜底：界面渲染少一个色值不该让整次运行崩掉
        # （这里踩过坑：给新节点加了 c-green 却忘了配色值，KeyError 直接把运行打断）
        color = CLASS_COLOR.get(css, MUTED)
        is_branch = "/" in node
        kind = "ok" if item["ok"] else "bad"

        row_class = ' class="row-branch"' if is_branch else ""
        rows.append(
            f"<tr{row_class}>"
            f'<td class="mono dim">{item["step"]}</td>'
            f'<td><span class="node {css}">{icon(glyph)}{esc(label)}</span></td>'
            f'<td class="mono dim">{item["round"] or "-"}</td>'
            f'<td class="num"><span class="mono">{item["duration_ms"]:.1f}</span>'
            f"{duration_bar(item['duration_ms'], max_ms, color)}</td>"
            f'<td><span class="dot dot-{kind}"></span>'
            f'{"成功" if item["ok"] else "失败"}</td>'
            f'<td class="mono dim">→ {esc(item["next"])}</td>'
            f"</tr>"
        )
    return (
        '<table class="tbl"><thead><tr>'
        "<th>#</th><th>节点</th><th>轮次</th><th>耗时(ms)</th><th>结果</th><th>跳转</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


# ---------- 结果统计 ----------

STAT_SLOTS = ("轮次", "结果", "Critical", "Major", "Minor", "版本", "单元测试")


def render_stats(state: dict = None, error: str = None) -> str:
    if error:
        return (
            '<div class="stat-grid"><div class="stat is-bad">'
            '<div class="stat-label">状态</div>'
            '<div class="stat-value v-bad mid">执行中断</div>'
            "</div></div>"
            f'<div class="hint gap-top">{esc(error)}</div>'
        )

    if state is None:
        cells = "".join(
            f'<div class="stat"><div class="stat-label">{esc(k)}</div>'
            f'<div class="stat-value v-dim">—</div></div>'
            for k in STAT_SLOTS
        )
        return f'<div class="stat-grid">{cells}</div>'

    counts = state.get("issue_counts", {})
    passed = state.get("passed")

    def cell(label, value, cls="", bar=""):
        return (f'<div class="stat{bar}"><div class="stat-label">{esc(label)}</div>'
                f'<div class="stat-value {cls}">{esc(value)}</div></div>')

    cells = [
        cell("轮次", state.get("round", "—")),
        cell("结果", "通过" if passed else "未通过",
             "v-ok" if passed else "v-bad", " is-ok" if passed else " is-bad"),
        cell("Critical", counts.get("Critical", 0),
             "v-bad" if counts.get("Critical") else "",
             " is-bad" if counts.get("Critical") else ""),
        cell("Major", counts.get("Major", 0),
             "v-bad" if counts.get("Major") else "",
             " is-bad" if counts.get("Major") else ""),
        cell("Minor", counts.get("Minor", 0), "v-dim"),
        cell("版本", len(state.get("versions", []))),
        cell("单元测试", _test_stat(state)[0], *_test_stat(state)[1:]),
    ]
    return f'<div class="stat-grid">{"".join(cells)}</div>'


def _test_stat(state: dict) -> tuple:
    """统计格里那格"单元测试"：值 / 颜色 class / 左侧竖条。

    脚本自身出错时显示"—"而不是"0 通过 / 0 失败"——后者会让人误以为代码有问题。
    """
    if state.get("test_script_broken"):
        return ("—", "v-dim", "")
    if not state.get("test_cases"):
        return ("未执行", "v-dim", "")
    ok = state.get("tests_passed")
    return (state.get("test_summary", "—"), "v-ok" if ok else "v-bad",
            " is-ok" if ok else " is-bad")


def render_tests(state: dict = None) -> str:
    """单元测试面板：逐条列出用例与结果。

    测试脚本自己写错时要说清楚，并且明确"不据此判定代码不合格"——
    否则面板上摆着 0 通过 / 0 失败，看起来就像代码烂透了。
    """
    if state is None:
        return '<div class="hint">跑完一次后会显示用例与执行情况</div>'

    if state.get("test_script_broken"):
        return (
            '<div class="hint">' + chip("测试脚本自身错误", "bad")
            + f' {esc(state.get("test_note", ""))}'
            '<div class="small dim">这是测试环节的问题，已记为不回炉，'
            "不会因此判定代码不合格。</div></div>"
        )

    cases = state.get("test_cases") or []
    if not cases:
        return '<div class="hint">未执行单元测试</div>'

    # 模型把被测代码抄进测试文件时，抄来的定义会被静态剔除，这里如实标注
    contaminated = state.get("test_contaminated") or []
    if contaminated:
        names = "、".join(f'<span class="mono">{esc(n)}</span>' for n in contaminated)
        notice = (f'<div class="small dim">已剔除测试中对 {names} 的重复定义，'
                  "确保测到的是真实代码而非副本。</div>")
    else:
        notice = ""

    rows = []
    for case in cases:
        ok = case["status"] == "PASS"
        rows.append(
            "<tr>"
            f'<td class="mono">{esc(case["name"])}</td>'
            f'<td><span class="dot dot-{"ok" if ok else "bad"}"></span>'
            f"{esc(case['status'])}</td>"
            f'<td class="wrap mono dim">{esc(case["message"] or "—")}</td>'
            "</tr>"
        )
    summary = state.get("test_summary", "")
    return (
        f'<div class="meta">{chip(summary, "ok" if state.get("tests_passed") else "bad")}'
        f'　<span class="dim">共 {len(cases)} 个用例，由测试 Agent 生成、'
        f"经代码执行工具真实运行</span>{notice}</div>"
        '<table class="tbl"><thead><tr><th>用例</th><th>结果</th><th>失败信息</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


# ---------- 流程定义（从 YAML 自动生成） ----------

def _render_next(next_spec) -> str:
    if isinstance(next_spec, list):
        parts = []
        for cond in next_spec:
            if "when" in cond:
                parts.append(f'<div><span class="cond">when {esc(cond["when"])}</span>'
                             f' → <span class="mono">{esc(cond["goto"])}</span></div>')
            else:
                parts.append(f'<div><span class="mono dim">否则</span> → '
                             f'<span class="mono">{esc(cond["goto"])}</span></div>')
        return "".join(parts)
    return f'<span class="mono">{esc(next_spec or "done")}</span>'


def render_flow() -> str:
    try:
        config = load_config()
    except Exception as e:  # 配置读不出来不该让整个界面挂掉
        return f'<div class="hint">读取流程配置失败：{esc(e)}</div>'

    rows = []
    for spec in config.get("nodes", []):
        node_id = spec["id"]
        if spec.get("parallel"):
            glyph, css = NODE_STYLE["analyze"]
            kind = f'<span class="tag tag-para">{icon("parallel", 12)}并行容器</span>'
            detail = (f'<span class="mono">{esc(", ".join(spec["parallel"]))}</span>'
                      f'<div class="dim small">'
                      f'合并：{esc(spec.get("merge", "—"))}</div>')
        else:
            agent = spec.get("agent", "—")
            glyph, css = NODE_STYLE.get(node_id, ("code", "c-muted"))
            kind = f'<span class="tag">{icon(glyph, 12)}Agent</span>'
            detail = esc(AGENT_LABELS.get(agent, agent))
        rows.append(
            "<tr>"
            f'<td><span class="node {css}">{icon(glyph)}'
            f'<span class="node-id">{esc(node_id)}</span></span></td>'
            f"<td>{kind}</td>"
            f"<td>{detail}</td>"
            f'<td class="mono dim">{esc(spec.get("retries", "—"))}</td>'
            f'<td class="mono dim">{esc(spec.get("on_error", "abort"))}</td>'
            f'<td class="wrap">{_render_next(spec.get("next", "done"))}</td>'
            "</tr>"
        )
    return (
        f'<div class="dim meta">'
        f'start: <span class="mono">{esc(config.get("start", "—"))}</span>'
        f'　·　共 {len(rows)} 个节点　·　由 '
        f'<span class="mono">config/workflow.yaml</span> 驱动</div>'
        '<table class="tbl"><thead><tr>'
        "<th>节点</th><th>类型</th><th>执行内容</th><th>重试</th><th>失败策略</th><th>下一跳</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


# ---------- PSP 度量面板 ----------

def render_psp(summary: dict = None, defect_log: list = None) -> str:
    """PSP 四组数据：关键数字 + 时间分布条 + 缺陷追踪表。"""
    if summary is None:
        return '<div class="hint">跑完一次后会显示 Size / Effort / Quality / Schedule</div>'

    size, effort, quality, schedule = (
        summary["size"], summary["effort"], summary["quality"], summary["schedule"])

    def cell(label, value, cls="", rule=""):
        return (f'<div class="stat{rule}"><div class="stat-label">{esc(label)}</div>'
                f'<div class="stat-value {cls}">{esc(value)}</div></div>')

    loc = size["final"]
    total_s = effort["total_ms"] / 1000
    cards = [
        cell("代码行数", loc["total"], "", ""),
        cell("总耗时", f"{total_s:.1f}s", "", ""),
        cell("发现缺陷", quality["found"],
             "v-bad" if quality["found"] else "", " is-bad" if quality["found"] else ""),
        cell("修复率", f"{quality['fix_rate'] * 100:.0f}%", "v-ok", ""),
        cell("缺陷密度", f"{quality['density_per_100loc']:.0f}/百行", "v-dim", ""),
        cell("轮次", f"{schedule['actual_rounds']}/{schedule['planned_rounds']}",
             "v-ok" if schedule["within_budget"] else "v-bad",
             " is-ok" if schedule["within_budget"] else " is-bad"),
    ]

    # 时间分布：一条堆叠横条，各阶段按比例占宽；下面配图例
    segments = []
    legend = []
    cursor = 0.0
    for phase in PHASE_ORDER:
        ms = effort["phases"].get(phase, 0.0)
        if not ms or not effort["total_ms"]:
            continue
        width = ms / effort["total_ms"] * 100
        segments.append(
            f'<rect x="{cursor:.1f}" y="0" width="{width:.1f}" height="8" '
            f'fill="{PHASE_COLORS[phase]}"/>')
        cursor += width
        legend.append(
            f'<span class="lg"><span class="sw {PHASE_SWATCH[phase]}"></span>'
            f"{esc(phase)} {width:.0f}%</span>")

    bar = ('<svg class="stack" viewBox="0 0 100 8" preserveAspectRatio="none">'
           + "".join(segments) + "</svg>") if segments else ""

    html_out = (
        f'<div class="stat-grid">{"".join(cards)}</div>'
        f'<div class="psp-bar">{bar}</div>'
        f'<div class="legend">{"".join(legend)}</div>'
    )

    if defect_log:
        html_out += render_defect_table_ui(DefectLog(defect_log))
    return html_out


def render_defect_table_ui(log) -> str:
    life = log.lifecycle()
    if not life:
        return ""
    rows = []
    for item in life:
        desc = esc(item["description"])
        if len(desc) > 52:
            desc = desc[:52] + "…"
        badge = "ok" if item["status"] == "已修复" else "bad"
        rows.append(
            "<tr>"
            f'<td><span class="tag t-{esc((item["level"] or "").lower())}">'
            f'{esc(item["level"])}</span></td>'
            f'<td class="mono dim">{item["found_round"]}</td>'
            f'<td class="mono dim">{item["fixed_round"] or "—"}</td>'
            f'<td class="mono dim">{item["survived"] if item["survived"] is not None else "—"}</td>'
            f'<td><span class="dot dot-{badge}"></span>{esc(item["status"])}</td>'
            f'<td class="mono dim">{esc(item["source"])}</td>'
            f"<td>{desc}</td>"
            "</tr>"
        )
    return (
        '<table class="tbl"><thead><tr>'
        "<th>等级</th><th>发现</th><th>修复</th><th>存活</th><th>状态</th>"
        "<th>来源</th><th>描述</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


# ---------- 运行 ----------

def run_workflow(requirement: str):
    requirement = (requirement or "").strip()
    if not requirement:
        # 留空就直说，不要偷偷拿一句默认需求顶上
        yield (chip("请先填写需求描述", ""), render_track([]), render_stats(None),
               "", "", "", "")
        return

    engine = WorkflowEngine(trace=LiveTrace(), verbose=False)
    engine.load_from_yaml(str(CONFIG_PATH), AGENT_REGISTRY)

    outcome = {}

    def worker():
        try:
            outcome["state"] = engine.run({"requirement": requirement})
            outcome["paths"] = engine.save_trace(ROOT / "runs")
        except Exception as e:  # noqa: BLE001 —— 界面层要把异常展示给用户
            outcome["error"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    seen = []
    while thread.is_alive() or not engine.trace.queue.empty():
        time.sleep(0.2)
        while not engine.trace.queue.empty():
            seen.append(engine.trace.queue.get())
        yield (chip(f"运行中 · 已完成 {len(seen)} 步", "run"),
               render_track(seen), render_stats(None), render_psp(None),
               render_tests(None), "", "", "")
    thread.join()

    if "error" in outcome:
        yield (chip("执行中断", "bad"), render_track(seen),
               render_stats({}, outcome["error"]), render_psp(None),
               render_tests(None), "", "", "")
        return

    state = outcome["state"]
    label = "通过" if state.get("passed") else "未通过"
    if state.get("degraded"):
        label += "（解析降级）"
    psp = render_psp(summarize(state, engine.last_trace.records),
                     state.get("defect_log"))
    yield (chip(label, "ok" if state.get("passed") else "bad"),
           render_track(seen), render_stats(state), psp, render_tests(state),
           state.get("review", ""), state.get("code", ""), state.get("tests", ""))


def reset():
    return (chip("就绪", ""), render_track([]), render_stats(None),
            render_psp(None), render_tests(None), "", "", "")


def build_ui():
    """搭出界面。样式在 launch 时通过 css= 传入 —— Gradio 6 起 css 从
    Blocks 构造函数挪到了 launch()，放在构造函数里会被忽略。"""
    with gr.Blocks(title="code-review-workflow") as demo:
        with gr.Row(elem_id="header"):
            with gr.Column(scale=4, min_width=280):
                gr.HTML(
                    '<div id="logo-wrap">'
                    f'<div id="logo-mark">{icon("flow", 17)}</div>'
                    "<div>"
                    '<div id="logo">code-review-workflow</div>'
                    '<div id="subtitle">编码 → 并行（审查 ‖ 规范检查）→ 修复 → 测试'
                    "　·　五个 Agent 协作，测试执行走工具而非 Agent</div>"
                    "</div></div>"
                )
            with gr.Column(scale=1, min_width=150):
                status = gr.HTML(chip("就绪", ""), elem_id="status")

        requirement = gr.Textbox(
            label="需求描述", lines=3, value="", placeholder=PLACEHOLDER,
        )

        with gr.Row():
            run_btn = gr.Button("运行审查流程", elem_id="run-btn", scale=5)
            clear_btn = gr.Button("清空", elem_id="clear-btn", scale=1)

        gr.Markdown("### 运行结果")
        stats = gr.HTML(render_stats(None))

        gr.Markdown("### 执行轨迹")
        track = gr.HTML(render_track([]), elem_id="panel")

        gr.Markdown("### PSP 度量")
        psp_box = gr.HTML(render_psp(None), elem_id="panel")

        gr.Markdown("### 单元测试")
        tests_box = gr.HTML(render_tests(None), elem_id="panel")

        # 流程定义表有 6 列，占满整行才不挤
        gr.Markdown("### 流程定义")
        gr.HTML(render_flow(), elem_id="panel")

        gr.Markdown("### 审查意见")
        review_md = gr.Markdown("_最后一轮审查意见会显示在这里_", elem_id="panel")

        gr.Markdown("### 最终代码")
        code_out = gr.Code(language="python", value="", elem_id="code-block",
                           show_label=False)

        gr.Markdown("### 生成的测试代码")
        tests_code = gr.Code(language="python", value="", elem_id="code-block",
                             show_label=False)

        outputs = [status, track, stats, psp_box, tests_box,
                   review_md, code_out, tests_code]
        run_btn.click(fn=run_workflow, inputs=[requirement], outputs=outputs)
        clear_btn.click(fn=reset, outputs=outputs)
    return demo


if __name__ == "__main__":
    # 端口可用 CRW_PORT 覆盖：上一次的服务没关掉时，不用改代码就能换一个端口起来
    port = int(os.environ.get("CRW_PORT", "7860"))
    print(f"界面地址：http://127.0.0.1:{port}")
    build_ui().launch(server_name="127.0.0.1", server_port=port, css=CUSTOM_CSS)
