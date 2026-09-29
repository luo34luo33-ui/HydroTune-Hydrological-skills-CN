---
name: compute-continuous-series-metrics
description: 适用于对一条连续观测—模拟序列计算 NSE、RMSE、MAE、R² 与水量平衡偏差；不适用于逐场次洪水过程指标、成因归因、参数率定或成果绘图。
metadata:
  category: evaluation-diagnostics
  domains:
    - evaluation-diagnostics
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/route-muskingum-channel
    - post-processing/correct-residual-with-ml
    - evaluation-diagnostics/aggregate-event-metrics
---

# 连续序列指标

对一条连续的观测—模拟序列计算整体拟合指标与水量平衡偏差。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 有一段时间连续的模拟结果（连续运行或校正后的序列），需要整体性能指标。
- 需要 NSE、RMSE、MAE、R² 与水量平衡偏差。
- 想在逐场次指标之外提供连续期视角。

## Do Not Use When

- 需要洪峰、洪量、峰现时间等过程指标——用 `compute-event-flood-metrics`。
- 需要跨场次汇总——用 `aggregate-event-metrics`。
- 需要解释误差成因——本 Skill 只产出指标。
- 需要搜索参数——归入 `model-calibration`。
- 序列含缺测且未修复——先回到上游。

## Governing Principle

1. **整体口径**：对整条序列一次性计算，不切分场次、不加权。
2. **水量偏差显式**：`volume_bias = Σ(模拟 − 观测) × dt`，并给出相对偏差；dt 必须由 `--timestep-hours` 显式给出。
3. **不可计算即标记**：观测方差为 0 时 `nse` 记为 `unavailable`，`r2` 按 scikit-learn 约定给边界值，并给出 warning。
4. **缺测即失败**：缺测、长度不一致、时间轴不一致都会退出码 2，不填 0 后计算。

## 输入要求与确认闸门

- 观测列与模拟列长度一致、单位一致。
- `--timestep-hours` 必须显式给出并与时间列推断值一致。
- 指标子集可显式选择，未知指标名会被拒绝。

## 决策规则

- 默认计算全部指标：`nse, rmse, mae, r2, volume_bias, relative_volume_bias`。
- 观测总量为 0 时相对水量偏差不适用，记为 `unavailable`。
- 结果以 `metric/value/unit/status` 长表输出，便于下游汇总与制图。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `series_length_match` | 长度不一致或为空 | 退出码 2 | 回到上游对齐序列 |
| `timestep_consistency` | 时间轴与声明步长不一致 | 退出码 2 | 标准化时间轴或修正声明 |
| `no_missing_values` | 含缺测 | 退出码 2 | 显式修复缺测 |
| `finite_values` | 出现 NaN/Inf | 退出码 2 | 检查模拟是否发散 |
| `variance_available` | 观测方差为 0 | warning，`nse` 记 unavailable | 确认观测序列是否有效 |
| `metrics_recorded` | 常驻 | PASS，记录已计算指标 | — |

## 结果解释边界

- 连续期 NSE 会被高流量时段主导，低流量表现需要另行分区评价。
- 水量偏差只说明总量差异，不说明时间分配。
- 本 Skill 不判断"好"或"坏"，阈值由使用者在下游解释。

## 下一步

- 与逐场次结果合并：`evaluation-diagnostics/aggregate-event-metrics`。
- 过程层面指标：`evaluation-diagnostics/compute-event-flood-metrics`。
