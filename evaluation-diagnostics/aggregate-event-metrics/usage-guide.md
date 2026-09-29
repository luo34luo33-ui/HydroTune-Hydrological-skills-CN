# 场次指标汇总 使用指南

## Overview

`aggregate-event-metrics` 把逐场次指标表汇总为分组统计量与合格率。它不计算指标本身，只消费上游结果。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 上游已产出逐场次指标表（例如 `compute-event-flood-metrics` 的 `event_metrics.csv`）

## Typical User Requests

- "把 60 场的 NSE 取平均。"
- "按分期分组，算洪峰误差的中位数和合格率。"
- "洪量误差的合格率是多少？"

## Quick Start

```bash
python scripts/aggregate_event_metrics.py \
  --metrics-table event_metrics.csv \
  --value-columns nse,volume_error_relative,peak_error_relative \
  --pass-columns qualified_volume,qualified_peak \
  --pass-thresholds thresholds.json \
  --output-dir ./aggregate
```

只查看参数说明不需要真实输入：`python scripts/aggregate_event_metrics.py --help`。

## What the Agent Will Do

1. 校验聚合列、合格列与分组列存在。
2. 把无法解析为数值的单元排除并计数。
3. 按 `--group-by` 分组（缺省整体为 `all`），计算所选统计量。
4. 若同时给出合格列与阈值，计算合格率；缺阈值则记为 `unavailable`。
5. 写出 `aggregate_metrics.csv` 与 `result.json`。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--metrics-table` | 是 | 逐场次指标表 |
| `--value-columns` | 是 | 逗号分隔的待聚合数值列 |
| `--statistics` | 否 | 默认 `mean,median,p10,p90,min,max,count` |
| `--group-by` | 否 | 分组列名；缺省整体聚合 |
| `--pass-columns` | 否 | 逗号分隔的合格判定列 |
| `--pass-thresholds` | 与 `--pass-columns` 配套 | 合格阈值 JSON 字符串或文件路径 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

可用统计量：`mean`、`median`、`p10`、`p90`、`min`、`max`、`count`。

`--pass-thresholds` 的键必须与 `--pass-columns` 的列名一致（例如 `{"qualified_volume": 0.2, "qualified_peak": 0.2}`）；实际合格判定由上游完成，这里只统计判定为「是」的比例，阈值同时作为判定口径写入 `result.json`。这与 `compute-event-flood-metrics` 不同，后者按指标名给阈值（如 `relative_volume_error`）。

## Outputs

- `aggregate_metrics.<fmt>`：`group`、`events`、每个数值列 × 每个统计量、每个合格列的 `qualified_rate`。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 给了 `--pass-columns` 却没给 `--pass-thresholds`：合格率会是 `unavailable`。
- 把等权平均当成按洪量加权：本 Skill 只做等权，加权需另作说明。
- 用汇总结果掩盖极端场次：应先看单场指标。
- 期待本 Skill 重算指标：它只消费上游表。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| 汇总与指标计算、绘图写在同一循环里 | 独立 Skill，只消费指标表 | 汇总应可独立复算与审计 |
| 合格阈值 0.2 与 3 小时硬编码 | `--pass-thresholds` 显式给出，缺失记 `unavailable` | 阈值需使用者确认 |
| 缺测被 `fillna(0)` 后参与平均 | 非数值单元排除并计数 | 填 0 会伪造平均值 |
| 结果直接写进 Excel 与统计 CSV | 统一输出聚合表与 `result.json` | 产物与证据分离 |

## Related Skills

- `evaluation-diagnostics/compute-event-flood-metrics`：上游逐场次指标。
- `evaluation-diagnostics/compute-continuous-series-metrics`：连续序列视角。
- `visualization-reporting/visualize-model-calibration`：结果表达。
