---
name: run-lumped-hbv-model
description: 适用于在单一计算单元上运行简化 HBV 蓄满产流与线性水库汇流的前向模拟，输出流域出口流量 Q；不适用于融雪过程、参数率定、多流域批量建模或成果绘图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - hydrological-modeling/run-lumped-dhf-model
    - data-processing/prepare-discharge-timeseries
    - model-calibration/calibrate-model-de
---

# 集总式简化 HBV 前向模拟

把降雨与蒸发能力序列推进为流域出口流量 `Q`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，降雨与蒸发能力已是面平均序列。
- 需要运行简化 HBV 结构：蓄满产流（`SM/FC` 曲线）加三个线性水库（表层阈值出流 Q0、上层 Q1、下层 Q2）。
- 需要连续序列递推或逐场次独立起算。
- 需要产流分量与状态列（AE、SM、RECHARGE、SUZ、SLZ）用于诊断。

## Do Not Use When

- 流程包含积雪与融雪——本 Skill 是无雪简化版，源码即无雪模块。
- 需要搜索 9 个参数——归入 `model-calibration`。
- 需要其它模型出流的河道演算——归入 `route-muskingum-channel`；本 Skill 的 Q 已是出口流量。
- 需要多流域批量张量运行——本 Skill 一次处理一个序列。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。

## Governing Principle

1. **蓄满产流**：有效雨 `effective = P × min(SM/FC, 1)^beta`，土壤含水量越高产流比例越大。
2. **蒸发受调蓄**：`PET = evap × c`；当 `SM <= PWP = lp × fc` 时实际蒸发按 `SM/PWP` 线性缩减。
3. **三个线性水库**：表层阈值出流 `Q0 = k0 × max(SM - l, 0)`；上层 `Q1 = k1 × SUZ`；下层 `Q2 = k2 × SLZ`；层间交换 `kp × (SLZ - SUZ)`。
4. **max(·,0) 兜底**：SM/SUZ/SLZ 更新式带 floor 0，触发时会损耗水量——这是源码结构，截断计数以 warning 报告。
5. **水量平衡是近似的**：源码的 Q0 不从 SM 扣水、effective 到 recharge 有消减项，水量平衡只能作 WARN。

## 输入要求与确认闸门

- 降雨 `P` 与蒸发能力 `evap` 必须同量纲（每步长水深 mm）。
- 流域面积 `--area-km2` 必须显式给出；源码硬编码的 584.0 不作为默认值。
- 时间步长 `--timestep-hours` 必须显式给出；源码换算写死 86400 秒（隐含日步长），本 Skill 暴露为参数。
- 9 个参数全部必填，无静默默认；`fc/beta/c/k0/l/k1/k2/kp/lp` 缺一即退出码 1。
- 缺测即 QC 失败并退出码 2。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场独立初始化（源码初值为 0）。
- `--warmup-steps` 只把前 N 步标记为 `is_warmup`——源码没有 warmup 机制，标记段照常递推，只是不纳入模拟期统计。
- 参数超出源工程率定范围（`HBV_PARAM_BOUNDS`）只产生 warning，是否可接受由使用者判断。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出模拟表 | 先标准化时间轴 |
| `no_missing_forcing` | P 或 evap 含缺测 | 退出码 2 | 显式修复缺测 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级 |
| `state_bounds` | SM、SUZ、SLZ 为负 | 退出码 2 | 不应发生（floor 0），出现即数值异常 |
| `parameter_range` | 参数超出源工程率定范围 | warning | 确认取值依据后可继续 |
| `numerical_clipping` | max(·,0) 兜底被触发 | warning | 截断会损耗水量，检查参数 |
| `water_balance` | 水量平衡残差超过相对容差 | warning（非 FAIL） | 源码结构固有损耗，见上 |

## 结果解释边界

- Q 是出口流量，可直接进入指标评价。
- 水量平衡残差包含源码结构性损耗（Q0 不扣 SM、effective→recharge 消减），不是实现错误。
- 本 Skill 无雪模块，冬季流域使用前必须确认无融雪过程。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 同类集总式模型：`run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-tank-model`。
