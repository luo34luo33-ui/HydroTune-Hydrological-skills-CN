---
name: run-lumped-gr4j-model
description: 适用于在单一计算单元上运行 GR4J 四参数模型（tanh 产流水库加双单位线汇流）的前向模拟，输出流域出口流量 Q；不适用于融雪模块、参数率定、多流域批量建模或成果绘图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - hydrological-modeling/run-lumped-hbv-model
    - data-processing/prepare-discharge-timeseries
    - model-calibration/calibrate-model-de
---

# 集总式 GR4J 前向模拟

把降雨与蒸发能力序列推进为流域出口流量 `Q`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，降雨与蒸发能力已是面平均序列（每步长水深 mm）。
- 需要运行 GR4J 标准结构：tanh 产流水库（X1）、非线性下渗分流、双单位线（X4）与汇流水库（X3）、地下水交换（X2）。
- 需要从历史状态续算（提供 `--initial-state`）。
- 需要产流分量与状态列（EA、PS、PERC、PR、GW、QR、QD）用于诊断。

## Do Not Use When

- 流程包含积雪与融雪——GR4J 本体无雪模块（GR5J/CemaNeige 才有）。
- 需要搜索 X1/X2/X3/X4 参数——归入 `model-calibration`。
- 需要其它模型出流的河道演算——归入 `route-muskingum-channel`；本 Skill 的 Q 已是出口流量。
- 需要多流域批量运行——本 Skill 一次处理一个序列。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。
- 期待基于源工程的参数范围告警——源码没有定义任何参数范围，本 Skill 不引入外部先验。

## Governing Principle

1. **tanh 产流水库**：净雨/净蒸先除以 X1 再 tanh，scaled 超过 13 截断（tanh 饱和防护，源码行为）。
2. **非线性下渗**：`percolation = S/(1+(S/2.25/X1)^4)^0.25`，其余部分进入单位线。
3. **双单位线**：UH1（90% 进汇流水库 R）与 UH2（10% 直接出流 QD），形状由 S 曲线差分决定，长度 `ceil(X4)` 与 `ceil(2·X4)`。
4. **地下水交换可负**：`X2·(R/X3)^3.5` 为负表示外部汇入，这是 GR4J 标准语义，不做非负校验。
5. **双口径输出**：`Q_MM` 是源码等价输出（mm/步，逐位一致），`Q` 是按 `--area-km2` 与 `--timestep-hours` 换算的 m³/s——源码没有任何面积换算，必须显式提供。

## 输入要求与确认闸门

- 降雨 `P` 与蒸发能力 `PET` 必须同量纲（每步长水深 mm）。
- 流域面积 `--area-km2` 与时间步长 `--timestep-hours` 必须显式给出；源码输出 mm，不给面积就无法换算。
- 4 个参数全部必填（源码即直接 KeyError，无静默默认）；硬校验 X1/X3/X4 > 0，X2 任意实数。
- `--initial-state` 只接受 `production_store`/`routing_store` 标量；UH 数组初值固定全 0（源码默认）。
- 缺测即 QC 失败并退出码 2。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场从默认（或 `--initial-state`）状态起算。
- `--warmup-steps` 只把前 N 步标记为 `is_warmup`——源码没有 warmup 机制，标记段照常递推。
- X4 以"步"为单位；X4 变化会同时改变单位线长度与形状，非整数按 ceil。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出模拟表 | 先标准化时间轴 |
| `no_missing_forcing` | P 或 PET 含缺测 | 退出码 2 | 显式修复缺测 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级 |
| `state_bounds` | production/routing store 或 Q 为负 | 退出码 2 | 不应发生（公式自限 + floor 0），出现即数值异常 |
| `numerical_clipping` | max(0,·) 兜底或 tanh 饱和截断被触发 | warning | 截断会损耗水量，检查 X2 与降雨量级 |
| `water_balance` | 水量平衡残差超过相对容差 | warning（非 FAIL） | 地下水交换与 percolation 分流使闭合为近似 |

## 结果解释边界

- Q 是出口流量（m³/s），可直接进入指标评价；`Q_MM` 是与源码逐位一致的锚点，用于等价性核查。
- 地下水交换项（X2）使水量平衡闭合为近似，残差以 warning 报告。
- 本 Skill 不输出参数是否"合理"的判断——源码未定义范围，合理性由使用者判断。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 同类集总式模型：`run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-hbv-model`、`run-lumped-tank-model`。
