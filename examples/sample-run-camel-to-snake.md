# Workflow 执行报告

- run_id：`15fb78c7`
- 开始时间：2026-10-20 11:48:08
- 总耗时：7.05s
- 节点执行次数：5
- 结果：通过（第 1 轮，正常）
- 代码版本数：1

## 执行轨迹

| # | 节点 | 轮次 | 耗时(ms) | 结果 | 状态变化 | 跳转 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `code` | - | 1839.3 | 成功 | `code` 新增 = import re\nfrom typing import Any\n\n\ndef camel_to_snake(na…；`versions` 新增 = list[1] | → `analyze` |
| 2 | `analyze/review` | 1 | 2499.0 | 成功 | `static` 新增 = 找到 1 个可审查单元：camel_to_snake；`runtime` 新增 = 运行成功（退出码 0）\nstdout:\n(无输出)；`unit_count` 新增 = 1；`round` 新增 = 1；`issues` 新增 = list[3]；`issue_counts` 新增 = dict[3]；`review` 新增 = ## 总结\n\n代码整体质量良好，逻辑正确、类型校验完善、注释清晰，仅有少量边界与风格层面的改进空间。\n\n## 问…；`summary` 新增 = 代码整体质量良好，逻辑正确、类型校验完善、注释清晰，仅有少量边界与风格层面的改进空间。；`passed` 新增 = True；`degraded` 新增 = False；`parse_errors` 新增 = list[0] | → `analyze` |
| 2 | `analyze/lint` | - | 0.4 | 成功 | `issues` 新增 = list[0]；`lint` 新增 = dict[3] | → `analyze` |
| 2 | `analyze` | 1 | 2499.9 | 成功 | `static` 新增 = 找到 1 个可审查单元：camel_to_snake；`runtime` 新增 = 运行成功（退出码 0）\nstdout:\n(无输出)；`unit_count` 新增 = 1；`round` 新增 = 1；`issues` 新增 = list[3]；`issue_counts` 新增 = dict[3]；`review` 新增 = ## 总结\n\n代码整体质量良好，逻辑正确、类型校验完善、注释清晰，仅有少量边界与风格层面的改进空间。\n\n## 问…；`summary` 新增 = 代码整体质量良好，逻辑正确、类型校验完善、注释清晰，仅有少量边界与风格层面的改进空间。；`passed` 新增 = True；`degraded` 新增 = False；`parse_errors` 新增 = list[0]；`lint` 新增 = dict[3]；`defect_log` 新增 = list[1] | → `test` |
| 3 | `test` | 1 | 2705.5 | 成功 | `tests` 新增 = import re\nfrom typing import Any\n\n\n\n\ndef test_simple_c…；`test_cases` 新增 = list[16]；`test_summary` 新增 = 16 通过 / 0 失败；`test_output` 新增 = 运行成功（退出码 0）\nstdout:\nRESULT PASS test_all_uppercase\nRESULT…；`tests_passed` 新增 = True；`test_attempts` 新增 = 1；`test_script_broken` 新增 = False；`test_note` 新增 = ；`test_contaminated` 新增 = list[1]；`test_failure` 新增 =  | → `done` |

## 单元测试

结果：16 通过 / 0 失败

> 已剔除测试中对 `camel_to_snake` 的重复定义：这些名字在被测代码里已有，留着就会测到副本而不是真实代码。

| 用例 | 结果 | 说明 |
| --- | --- | --- |
| `test_all_uppercase` | PASS | — |
| `test_already_snake` | PASS | — |
| `test_consecutive_uppercase` | PASS | — |
| `test_digits_only` | PASS | — |
| `test_empty_string` | PASS | — |
| `test_leading_uppercase_single` | PASS | — |
| `test_multiple_words` | PASS | — |
| `test_pascal_case` | PASS | — |
| `test_simple_camel` | PASS | — |
| `test_single_lowercase` | PASS | — |
| `test_single_uppercase` | PASS | — |
| `test_type_error_float` | PASS | — |
| `test_type_error_int` | PASS | — |
| `test_type_error_list` | PASS | — |
| `test_type_error_none` | PASS | — |
| `test_with_digits` | PASS | — |

## PSP 度量

### Size（规模）

| 指标 | 数值 |
| --- | --- |
| 最终代码行数 | 32 行（代码 19 / 注释 5 / 空行 8） |
| 函数数 | 1 |
| 产出版本数 | 1 |
| 相对初版行数 | +0 |

### Effort（时间分布）

| 阶段 | 耗时(s) | 占比 |
| --- | --- | --- |
| 编码 | 1.84 | 26% |
| 审查 | 2.50 | 35% |
| 规范检查 | 0.00 | 0% |
| 修复 | 0.00 | 0% |
| 测试 | 2.71 | 38% |
| **合计** | **7.04** | 100% |

- 并行：各分支耗时累加 2.50s，容器实际 2.50s，节省 0%
  - 快分支耗时占比极低，本例的时间收益有限；并行的意义在于再加一个慢分支也不会拖长总时长

### Quality（缺陷）

| 指标 | 数值 |
| --- | --- |
| 发现 | 3 |
| 已修复 | 0 |
| 遗留 | 3 |
| 修复率 | 0% |
| 缺陷密度 | 15.8 / 100 行 |
| 按等级 | Minor 3 |
| 按来源 | llm 3 |

### Schedule（进度）

| 指标 | 数值 |
| --- | --- |
| 计划轮次上限 | 3 |
| 实际轮次 | 1 |
| 结论 | 在预算内（≤ 3 轮），通过 |

### 缺陷追踪

| 等级 | 发现轮次 | 修复轮次 | 存活 | 状态 | 来源 | 描述 |
| --- | --- | --- | --- | --- | --- | --- |
| Minor | 1 | — | — | 遗留 | llm | docstring 未说明对已含下划线或非驼峰输入（如 'already_snake'、'A… |
| Minor | 1 | — | — | 遗留 | llm | 参数类型标注为 Any，但函数实际要求 str 并在运行时抛 TypeError，标注与契约… |
| Minor | 1 | — | — | 遗留 | llm | 对含数字的驼峰串处理不完整，例如 'getHTTP2Response' 会得到 'get_h… |

