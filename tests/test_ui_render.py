"""界面渲染层的回归测试。

起因是一个真实事故：给测试 Agent 的节点配了 `c-green` 这个配色类，
却忘了在 `CLASS_COLOR` 里定义它。结果只要轨迹里出现 `test` 节点，
`render_track` 就抛 KeyError —— 界面在"运行中"状态下弹出「错误」，
整个运行被打断。而单元测试全绿，因为没有任何用例渲染过带 test 节点的轨迹。

这里补两类保护：
1. 结构性检查：NODE_STYLE 里用到的每个配色类，CLASS_COLOR 都必须有定义；
2. 行为检查：拿真实的轨迹记录渲染一遍，不抛异常。
"""
import pytest

from ui.app import (
    CLASS_COLOR,
    NODE_LABELS,
    NODE_STYLE,
    PHASE_SWATCH,
    render_stats,
    render_tests,
    render_track,
)


def _record(node: str, step: int = 1, ok: bool = True) -> dict:
    return {"step": step, "node": node, "round": 1, "duration_ms": 12.5,
            "ok": ok, "next": "done", "error": None}


def test_every_node_style_has_a_color():
    """NODE_STYLE 用到的配色类必须在 CLASS_COLOR 里有定义。

    这条就是直接盯着上面那次事故：新增节点时只改了一半。
    """
    missing = sorted({css for _glyph, css in NODE_STYLE.values()
                      if css not in CLASS_COLOR})
    assert not missing, f"这些配色类没有对应色值：{missing}"


def test_every_node_style_has_a_label_and_glyph():
    for node, (glyph, _css) in NODE_STYLE.items():
        assert glyph, f"节点 {node} 缺图标"
        assert node in NODE_LABELS, f"节点 {node} 缺显示名"


def test_phase_swatch_covers_all_phases():
    """时间分布图例的色块也要齐全，缺一个就是 KeyError。"""
    from workflow.psp import PHASE_ORDER

    assert set(PHASE_ORDER) <= set(PHASE_SWATCH)


@pytest.mark.parametrize("node", sorted(NODE_STYLE))
def test_render_track_handles_each_node(node):
    """每个节点单独渲染一遍，任何一个抛异常都说明配色/图标表有洞。"""
    html = render_track([_record(node)])
    assert NODE_LABELS[node] in html


def test_render_track_handles_full_flow_including_test():
    """完整一条轨迹（含 test 节点）必须能渲染 —— 这是事故的原始场景。"""
    nodes = ["code", "analyze/review", "analyze/lint", "analyze",
             "fix", "analyze", "test", "done"]
    html = render_track([_record(n, step=i) for i, n in enumerate(nodes, 1)])
    assert "测试 Agent" in html
    assert "成功" in html


def test_render_track_handles_failure_and_empty():
    assert "失败" in render_track([_record("test", ok=False)])
    assert "等待开始" in render_track([])


def test_render_stats_and_tests_survive_placeholder():
    """初始占位状态不能崩（页面一打开就是这个）。"""
    assert "—" in render_stats(None)
    assert "跑完一次后" in render_tests(None)


def test_render_tests_shows_broken_script_notice():
    """测试脚本自身出错时要给明确提示，而不是摆一个 0 通过 / 0 失败。"""
    html = render_tests({"test_script_broken": True,
                         "test_note": "测试脚本自身错误（ModuleNotFoundError）：x"})
    assert "测试脚本自身错误" in html
    assert "不会因此判定代码不合格" in html
