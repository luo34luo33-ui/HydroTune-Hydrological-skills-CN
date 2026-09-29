---
name: compute-event-flood-metrics
description: 适用于逐场次计算洪量、洪峰、峰现时间误差、NSE 与 R² 等洪水过程指标；不适用于连续序列整体评价、成因归因、参数率定或成果绘图。
metadata:
  category: evaluation-diagnostics
  domains:
    - evaluation-diagnostics
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - post-processing/correct-residual-with-ml
    - evaluation-diagnostics/aggregate-event-metrics
---

# 逐场次洪水指标

对每个场次分别计算洪量、洪峰、峰现时间误差与拟合指标。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 已经按场次切分好观测与模拟序列，需要每场一个指标记录。
- 需要洪量相对误差、洪峰相对误差、峰现时间误差与合格率。
- 需要在跨场次汇总之前先得到逐场次证据表。

## Do Not Use When

- 只有一条连续序列、没有场次划分——用 `compute-continuous-series-metrics`。
- 需要跨场次平均、分位数或合格率——用 `aggregate-event-metrics`。
- 需要解释误差成因——本 Skill 只产出指标，不做归因。
- 需要搜索参数——归入 `model-calibration`。
- 观测或模拟含缺测且未修复——先回到上游，本 Skill 不会填 0 后计算。

## Governing Principle

1. **dt 显式**：洪量按 `--timestep-hours` 换算，不写死任何系数；时间轴必须与声明步长一致。
2. **误差符号固定**：洪量与洪峰相对误差均为 `(观测 − 模拟) / 观测`；峰现时间误差为 `模拟峰时刻 − 观测峰时刻`，正值表示模拟峰现偏晚。
3. **双口径并列**：同时输出 `nse`（标准定义）与 `r2`（scikit-learn 约定），二者在常规序列上数值一致，只在观测方差为 0 等边界情形不同。
4. **阈值不默认**：合格判定必须由 `--thresholds` 显式给出，缺失时记为 `unavailable`，不套用任何行业惯例值。
5. **缺测即失败**：缺测、长度不一致、时间轴不一致都会让该场次失败并退出码 2。

## 输入要求与确认闸门

- 观测列与模拟列必须存在且长度一致，单位一致（默认 m3/s）。
- `--timestep-hours` 必须显式给出并与时间列推断值一致。
- 合格阈值必须由使用者给出；本 Skill 不内置 20% 或 3 小时之类的默认值。
- 洪量单位可选 `m3` 或 `10k-m3`（万 m³，源码口径），必须在结果中记录。

## 决策规则

- 场次来源：`--events`（单文件；xlsx 每个 sheet 为一场）或 `--events-dir`（目录下每个文件为一场）。
- 洪量与洪峰误差均为相对误差；观测洪量或洪峰为 0 时结果不适用。
- 并列峰时取首个最大值并记录并列数量，同时给出 warning。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `series_length_match` | 观测与模拟长度不一致或为空 | 退出码 2，该场次无指标 | 回到上游对齐两条序列 |
| `timestep_consistency` | 时间轴步长与声明不一致 | 退出码 2 | 先标准化时间轴或修正声明步长 |
| `no_missing_values` | 含缺测 | 退出码 2 | 显式修复缺测，不得填 0 |
| `finite_values` | 出现 NaN/Inf | 退出码 2 | 检查上游模拟是否发散 |
| `peak_tie` | 出现并列峰 | warning | 峰现时间结论需谨慎 |
| `thresholds_provided` | 未给阈值 | warning，合格列记为 `unavailable` | 由使用者给出阈值 |
| `volume_unit_recorded` | 常驻 | PASS，记录洪量单位 | — |

## 结果解释边界

- 单个场次的 NSE 对峰现时间错位极为敏感，不能单独作为模型优劣结论。
- 洪量相对误差依赖 dt 与单位，跨研究比较前必须确认两者一致。
- 并列峰时峰现时间误差只是若干可能解释之一。

## 下一步

- 跨场次汇总：`evaluation-diagnostics/aggregate-event-metrics`。
- 连续序列视角：`evaluation-diagnostics/compute-continuous-series-metrics`。
- 误差成因分析：属于诊断 workflow，不在本 Skill 内完成。
