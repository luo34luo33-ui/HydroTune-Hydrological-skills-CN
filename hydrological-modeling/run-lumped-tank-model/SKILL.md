---
name: run-lumped-tank-model
description: 适用于在单一计算单元上运行 Tank 四箱串联水箱模型（多孔出流）的前向模拟，输出流域出口流量 Q；不适用于土壤蓄满过程、参数率定、多流域批量建模或成果绘图。
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

# 集总式 Tank 水箱模型前向模拟

把降雨与蒸发能力序列推进为流域出口流量 `Q`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，降雨与蒸发能力已是面平均序列。
- 需要运行 Tank 结构：四个串联水箱，顶箱双高度双侧孔加底孔，逐层底孔下渗进入下一箱。
- 需要连续序列递推或逐场次独立起算。
- 需要各箱蓄水与各孔出流列用于诊断。

## Do Not Use When

- 期待土壤蓄满或分层蒸散发机制——Tank 的蒸发直接从降雨扣除，不做土壤调蓄，这是结构差异而非缺陷。
- 需要搜索 16 个参数——归入 `model-calibration`。
- 需要其它模型出流的河道演算——归入 `route-muskingum-channel`；本 Skill 的 Q 已是出口流量。
- 需要多流域批量张量运行——本 Skill 一次处理一个序列。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。

## Governing Principle

1. **四箱串联**：顶层入流为净雨 `P - evap`（可为负）；每层底孔出流 `boc × 蓄水` 进入下一层；末层只有侧孔。
2. **多高度侧孔**：顶箱有两个侧孔（下孔 `t0_soh_lo/t0_soc_lo`、上孔 `t0_soh_uo/t0_soc_uo`），蓄水越过孔高才出流。
3. **初值即参数**：四箱初始蓄水由 `t0_is..t3_is` 参数给出。
4. **max(·,0) 兜底**：每箱更新带 floor 0，蒸发大于降雨时净雨为负会触发截断并损耗水量——这是源码结构，截断计数以 warning 报告。
5. **水量平衡是近似的**：floor 0 截断与负净雨入流使闭合为近似，水量平衡只作 WARN。

## 输入要求与确认闸门

- 降雨 `P` 与蒸发能力 `evap` 必须同量纲（每步长水深 mm）。
- 流域面积 `--area-km2` 必须显式给出；源码硬编码的 584.0 不作为默认值。
- 时间步长 `--timestep-hours` 必须显式给出（对应源码 `del_t`）。
- 16 个参数全部必填，无静默默认。
- `t0_soh_uo` 必须不小于 `t0_soh_lo`（上侧孔不得低于下侧孔），否则退出码 1。
- 缺测即 QC 失败并退出码 2。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场从 `t*_is` 初值重新起算。
- `--warmup-steps` 只把前 N 步标记为 `is_warmup`——源码没有 warmup 机制，标记段照常递推。
- 参数超出源工程率定范围（`TANK_PARAM_BOUNDS`）只产生 warning。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出模拟表 | 先标准化时间轴 |
| `no_missing_forcing` | P 或 evap 含缺测 | 退出码 2 | 显式修复缺测 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级 |
| `state_bounds` | 任一箱蓄水为负 | 退出码 2 | 不应发生（floor 0），出现即数值异常 |
| `parameter_range` | 参数超出源工程率定范围 | warning | 确认取值依据后可继续 |
| `numerical_clipping` | floor 0 或负流量截断被触发 | warning | 常因蒸发大于降雨，检查 forcing |
| `water_balance` | 水量平衡残差超过相对容差 | warning（非 FAIL） | floor 0 截断与负净雨使闭合为近似 |

## 结果解释边界

- Q 是出口流量（各侧孔之和按面积换算），可直接进入指标评价。
- 蒸发不经过土壤调蓄：旱季蒸发能力全部扣在净雨上，与蓄满类模型的行为差异显著。
- 水量平衡残差包含 floor 0 截断损耗，不是实现错误。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 同类集总式模型：`run-lumped-xaj-model`、`run-lumped-hbv-model`、`run-lumped-dhf-model`。
