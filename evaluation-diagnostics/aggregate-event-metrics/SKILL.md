---
name: aggregate-event-metrics
description: 适用于把逐场次指标表汇总为均值、中位数、分位数与合格率；不适用于计算场次指标本身、连续序列指标、成因归因或成果绘图。
metadata:
  category: evaluation-diagnostics
  domains:
    - evaluation-diagnostics
  tool_type: python
  primary_tool: pandas
  related_skills:
    - evaluation-diagnostics/compute-event-flood-metrics
    - evaluation-diagnostics/compute-continuous-series-metrics
    - visualization-reporting/visualize-model-calibration
---

# 场次指标汇总

把逐场次指标表汇总为分组统计量与合格率。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 已有逐场次指标表，需要均值、中位数、分位数或极值。
- 需要按字段分组（如分期、分流域、分方案）比较。
- 需要计算合格率，且阈值由使用者显式给出。

## Do Not Use When

- 还没有场次指标表——先用 `compute-event-flood-metrics`。
- 需要连续序列指标——用 `compute-continuous-series-metrics`。
- 需要解释误差成因或给出改进建议——本 Skill 只做统计汇总。
- 需要绘图——归入 `visualization-reporting`。

## Governing Principle

1. **统计口径固定**：默认等权，不提供加权选项，避免无依据的权重。
2. **缺失显式处理**：无法解析为数值的单元不计入统计，但计数记入 `result.json`。
3. **阈值不默认**：合格率必须由 `--pass-thresholds` 显式给出，缺失时记为 `unavailable`。
4. **不重算指标**：本 Skill 只消费上游指标表，不重新计算任何场次指标。

## 输入要求与确认闸门

- `--value-columns` 必须显式给出待聚合的数值列。
- `--pass-columns` 与 `--pass-thresholds` 配套，缺阈值就不判定合格。
- 统计量只能取 `mean`、`median`、`p10`、`p90`、`min`、`max`、`count`。

## 决策规则

- 未给 `--group-by` 时整体聚为 `all`。
- 合格判定列的值为「是」才计入合格，其余值（含 `unavailable`）不计入。
- 空表会给出 warning 并产出空结果，不静默失败。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `columns_present` | 聚合列或合格列不存在 | 退出码 1 | 修正列名 |
| `row_count` | 指标表为空 | warning | 确认上游是否产出指标 |
| `unavailable_accounted` | 常驻 | PASS，记录被排除的不可用单元数 | — |
| `thresholds_provided` | 给了合格列但没给阈值 | warning，合格率记 `unavailable` | 显式给出阈值 |
| `statistics_recorded` | 常驻 | PASS，记录统计量清单 | — |

## 结果解释边界

- 等权平均对场次长度与量级差异不敏感，跨量级比较前应确认是否合适。
- 合格率依赖上游判定阈值，阈值变化会直接改变结论。
- 汇总结果不能替代单场检查，极端场次会被平均掩盖。

## 下一步

- 结果表达：`visualization-reporting`。
- 单场证据：`evaluation-diagnostics/compute-event-flood-metrics`。
