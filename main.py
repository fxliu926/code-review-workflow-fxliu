"""Workflow 入口：从 YAML 加载节点，跑完整流程。"""
import argparse
import os
import sys
from pathlib import Path

from workflow.engine import WorkflowEngine
from workflow.psp import summarize
from agents.aggregator import merge_issues
from agents.coder import coder_agent
from agents.fixer import fixer_agent
from agents.linter import linter_agent
from agents.reviewer import reviewer_agent
from agents.tester import tester_agent

AGENT_REGISTRY = {
    "coder": coder_agent,
    "reviewer": reviewer_agent,
    "linter": linter_agent,     # 本地规范检查，不调用 LLM
    "fixer": fixer_agent,
    "tester": tester_agent,       # 生成单元测试，执行走 CodeRunner 工具
    "merge_issues": merge_issues,   # 并行节点的合并函数
}

DEFAULT_REQUIREMENT = "用 Python 写一个安全的除法函数，需要处理除数为零的情况"


def build_mock_registry() -> dict:
    """不需要 LLM 的 Agent 注册表。

    流程和真实运行用的是同一份 YAML、同一个引擎，只把 Agent 实现换成桩。
    没有 API key 也能端到端跑通，同时是"换 Agent 不动引擎"的可运行证据。
    """
    from agents.mock import (
        mock_coder, mock_fixer, mock_linter, mock_reviewer, mock_tester,
    )
    return {
        "coder": mock_coder,
        "reviewer": mock_reviewer,
        "linter": mock_linter,
        "fixer": mock_fixer,
        "tester": mock_tester,
        "merge_issues": merge_issues,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="运行「编码 → 审查 → 修复」Workflow")
    # 默认留 None：mock 模式下要换成桩 Agent 对应的演示需求，
    # 不能在参数解析阶段就填成真实模式的默认需求。
    parser.add_argument("requirement", nargs="?", default=None,
                        help="交给编码 Agent 的需求描述")
    parser.add_argument("-c", "--config", default="config/workflow.yaml",
                        help="流程配置文件路径")
    parser.add_argument("-o", "--output", help="把最后一轮审查意见写入文件")
    parser.add_argument("--trace-dir", default="runs", help="执行轨迹输出目录")
    parser.add_argument("--mock", action="store_true",
                        help="用桩 Agent 跑，不调用 LLM（没有密钥也能演示完整流程）")
    args = parser.parse_args(argv)

    if args.mock:
        from agents.mock import DEMO_REQUIREMENT
        registry = build_mock_registry()
        requirement = args.requirement or DEMO_REQUIREMENT
        print("[mock 模式] 不调用 LLM，用桩 Agent 跑同一份流程配置")
        print("[mock 模式] 轨迹写入 runs/mock/ ，不会和真实运行混在一起")
    else:
        from agents.llm_client import NO_KEY_HINT
        if not os.getenv("OPENAI_API_KEY"):
            print(NO_KEY_HINT)
            return 2
        registry = AGENT_REGISTRY
        requirement = args.requirement or DEFAULT_REQUIREMENT

    engine = WorkflowEngine()
    engine.load_from_yaml(args.config, registry)

    state = engine.run({"requirement": requirement})
    trace_dir = Path(args.trace_dir) / "mock" if args.mock else Path(args.trace_dir)
    paths = engine.save_trace(trace_dir)

    counts = state.get("issue_counts", {})
    print("\n=== 运行结果 ===")
    print(f"需求          ：{requirement}")
    print(f"审查轮次      ：{state.get('round')}")
    print(f"是否通过      ：{state.get('passed')}")
    print(f"遗留问题      ：Critical {counts.get('Critical', 0)} / "
          f"Major {counts.get('Major', 0)} / Minor {counts.get('Minor', 0)}")
    print(f"代码版本数    ：{len(state.get('versions', []))}")
    print(f"单元测试      ：{state.get('test_summary', '未执行')}"
          f"（{'通过' if state.get('tests_passed') else '未通过'}）")
    if state.get("test_note"):
        print(f"测试环节      ：{state['test_note']}")
        print("              （测试脚本自身的问题，不会据此判定代码不合格）")
    print(f"轨迹          ：{paths['report']}")
    if state.get("degraded"):
        print("注意          ：审查结果结构化解析失败，已保守判为不通过")
        for err in state.get("parse_errors", []):
            print(f"               {err}")

    # 引擎未显式传 trace 时，运行期创建的那个放在 last_trace 上
    psp = summarize(state, engine.last_trace.records)
    print(f"代码规模      ：{psp['size']['final']['total']} 行"
          f"（函数 {psp['size']['final']['functions']} 个，"
          f"{psp['size']['versions']} 个版本）")
    print(f"缺陷          ：发现 {psp['quality']['found']}，"
          f"修复 {psp['quality']['fixed']}，遗留 {psp['quality']['remaining']}"
          f"（修复率 {psp['quality']['fix_rate'] * 100:.0f}%）")
    print(f"轮次预算      ：{psp['schedule']['actual_rounds']}"
          f"/{psp['schedule']['planned_rounds']}"
          f"（{'在预算内' if psp['schedule']['within_budget'] else '超出预算'}）")

    print(f"\n--- 最终代码 ---\n{state.get('code', '')}")

    report = state.get("review", "")
    if report:
        print(f"\n--- 最后一轮审查意见 ---\n{report}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n已写入 {args.output}")

    return 0 if state.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
