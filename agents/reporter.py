"""报告生成模块：把审查结果汇总成 Markdown。"""
from datetime import datetime
from typing import List


class Reporter:
    """生成 Markdown 审查报告。"""

    def generate(self, file_path: str, results: List[dict], mode: str = None) -> str:
        """results 是 [{"unit": CodeUnit, "review": str}, ...]

        mode：审查模式说明（如「结构化解析」「LLM 直接审查」），可选。
        """
        lines = [
            "# 代码审查报告",
            "",
            f"- 文件：`{file_path}`",
            f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 审查单元数：{len(results)}",
        ]
        if mode:
            lines.append(f"- 审查模式：{mode}")
        lines += [
            "",
            "---",
            "",
        ]
        for i, item in enumerate(results, 1):
            unit = item["unit"]
            lines.append(f"## {i}. {unit.type} `{unit.name}`（第 {unit.lineno} 行）")
            lines.append("")
            lines.append(item["review"])
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    def save(self, content: str, output_path: str):
        """保存报告到文件。"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)