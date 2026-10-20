# Workflow 执行报告

- run_id：`53ef1eb1`
- 开始时间：2026-10-20 14:46:54
- 总耗时：0.64s
- 节点执行次数：14
- 结果：通过（第 3 轮，正常）
- 代码版本数：3

## 执行轨迹

| # | 节点 | 轮次 | 耗时(ms) | 结果 | 状态变化 | 跳转 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `code` | - | 0.0 | 成功 | `code` 新增 = def find_max(numbers):\n    """返回列表中最大的元素。"""\n    return ma…；`versions` 新增 = list[1] | → `analyze` |
| 2 | `analyze/review` | 1 | 0.0 | 成功 | `round` 新增 = 1；`issues` 新增 = list[1]；`summary` 新增 = （桩审查）第 1 轮 | → `analyze` |
| 2 | `analyze/lint` | - | 0.0 | 成功 | `lint` 新增 = dict[3]；`issues` 新增 = list[1] | → `analyze` |
| 2 | `analyze` | 1 | 1.1 | 成功 | `round` 新增 = 1；`issues` 新增 = list[2]；`summary` 新增 = （桩审查）第 1 轮；`lint` 新增 = dict[3]；`issue_counts` 新增 = dict[3]；`passed` 新增 = False；`review` 新增 = ## 总结\n\n（桩审查）第 1 轮\n\n## 问题列表\n- **[Major]** find_max 未处理空列…；`defect_log` 新增 = list[1] | → `fix` |
| 3 | `fix` | 1 | 0.0 | 成功 | `code` def find_max(numbers):\n    """返回列表中最大的元素。"""\n    return ma… → def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n …；`fixed` 新增 = def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n … | → `analyze` |
| 4 | `analyze/review` | 2 | 0.0 | 成功 | `round` 1 → 2；`issues` 长度 2 → 0；`summary` （桩审查）第 1 轮 → （桩审查）第 2 轮 | → `analyze` |
| 4 | `analyze/lint` | 1 | 0.0 | 成功 | `issues` 长度 2 → 1 | → `analyze` |
| 4 | `analyze` | 2 | 0.3 | 成功 | `round` 1 → 2；`issues` 长度 2 → 1；`summary` （桩审查）第 1 轮 → （桩审查）第 2 轮；`passed` False → True；`review` ## 总结\n\n（桩审查）第 1 轮\n\n## 问题列表\n- **[Major]** find_max 未处理空列… → ## 总结\n\n（桩审查）第 2 轮\n\n## 问题列表\n- **[Minor]** find_max 缺少参数与… | → `test` |
| 5 | `test` | 2 | 304.6 | 成功 | `tests` 新增 = \ndef test_max_normal():\n    assert find_max([1, 3, 2]) == …；`test_cases` 新增 = list[4]；`test_summary` 新增 = 3 通过 / 1 失败；`test_output` 新增 = 运行成功（退出码 0）\nstdout:\nRESULT PASS test_empty_raises\nRESULT …；`tests_passed` 新增 = False；`test_script_broken` 新增 = False；`test_note` 新增 = ；`test_contaminated` 新增 = list[0]；`test_attempts` 新增 = 1；`test_failure` 新增 = test_non_list_raises：ValueError: 列表不能为空 | → `fix` |
| 6 | `fix` | 2 | 0.0 | 成功 | `code` def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n … → def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n …；`fixed` def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n … → def find_max(numbers):\n    """返回列表中最大的元素。\n\n    Raises:\n … | → `analyze` |
| 7 | `analyze/review` | 3 | 0.1 | 成功 | `round` 2 → 3；`issues` 长度 1 → 0；`summary` （桩审查）第 2 轮 → （桩审查）第 3 轮 | → `analyze` |
| 7 | `analyze/lint` | 2 | 0.0 | 成功 | — | → `analyze` |
| 7 | `analyze` | 3 | 0.4 | 成功 | `round` 2 → 3；`summary` （桩审查）第 2 轮 → （桩审查）第 3 轮；`review` ## 总结\n\n（桩审查）第 2 轮\n\n## 问题列表\n- **[Minor]** find_max 缺少参数与… → ## 总结\n\n（桩审查）第 3 轮\n\n## 问题列表\n- **[Minor]** find_max 缺少参数与… | → `test` |
| 8 | `test` | 3 | 332.4 | 成功 | `test_summary` 3 通过 / 1 失败 → 4 通过 / 0 失败；`test_output` 运行成功（退出码 0）\nstdout:\nRESULT PASS test_empty_raises\nRESULT … → 运行成功（退出码 0）\nstdout:\nRESULT PASS test_empty_raises\nRESULT …；`tests_passed` False → True；`test_failure` test_non_list_raises：ValueError: 列表不能为空 →  | → `done` |

## 单元测试

结果：4 通过 / 0 失败

| 用例 | 结果 | 说明 |
| --- | --- | --- |
| `test_empty_raises` | PASS | — |
| `test_max_negative` | PASS | — |
| `test_max_normal` | PASS | — |
| `test_non_list_raises` | PASS | — |

## PSP 度量

### Size（规模）

| 指标 | 数值 |
| --- | --- |
| 最终代码行数 | 12 行（代码 11 / 注释 0 / 空行 1） |
| 函数数 | 1 |
| 产出版本数 | 3 |
| 相对初版行数 | +8 |

### Effort（时间分布）

| 阶段 | 耗时(s) | 占比 |
| --- | --- | --- |
| 编码 | 0.00 | 0% |
| 审查 | 0.00 | 0% |
| 规范检查 | 0.00 | 0% |
| 修复 | 0.00 | 0% |
| 测试 | 0.64 | 100% |
| **合计** | **0.64** | 100% |

- 并行：各分支耗时累加 0.00s，容器实际 0.00s，节省 0%
  - 快分支耗时占比极低，本例的时间收益有限；并行的意义在于再加一个慢分支也不会拖长总时长

### Quality（缺陷）

| 指标 | 数值 |
| --- | --- |
| 发现 | 2 |
| 已修复 | 1 |
| 遗留 | 1 |
| 修复率 | 50% |
| 缺陷密度 | 18.2 / 100 行 |
| 按等级 | Major 1、Minor 1 |
| 按来源 | mock 2 |

### Schedule（进度）

| 指标 | 数值 |
| --- | --- |
| 计划轮次上限 | 3 |
| 实际轮次 | 3 |
| 结论 | 在预算内（≤ 3 轮），通过 |

### 缺陷追踪

| 等级 | 发现轮次 | 修复轮次 | 存活 | 状态 | 来源 | 描述 |
| --- | --- | --- | --- | --- | --- | --- |
| Major | 1 | 2 | 1 | 已修复 | mock | find_max 未处理空列表，max([]) 会抛出 ValueError，应显式校验并给… |
| Minor | 1 | — | — | 遗留 | mock | find_max 缺少参数与返回值类型注解 |

