---
name: run-lumped-sacsma-model
description: 适用于在单一计算单元上运行 SAC-SMA（Sacramento Soil Moisture Accounting）16 参数模型的前向模拟，输出流域出口总出流及地表/基流分量；不适用于融雪驱动、PET 公式计算、汇流演算、参数率定或多流域批量建模。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - hydrological-modeling/run-lumped-gr4j-model
    - data-processing/prepare-discharge-timeseries
    - model-calibration/calibrate-model-de
---

# 集总式 SAC-SMA 前向模拟

把降雨与蒸发能力序列推进为流域出口总出流 `Q`（含地表/基流分量）。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，降雨与蒸发能力已是面平均序列（每步长水深 mm）。
- 需要运行 SAC-SMA 标准结构：上/下区张力水与自由水五库、分层蒸散发（ET1–ET5）、张力水-自由水重分配与回补、`ninc` 增量子步核算、下渗需求函数（ZPERC/REXP）、ADIMP 附加不透水面积与 PCTIM/RIVA/SIDE 面积分解。
- 需要从历史状态续算（提供 `--initial-state` 的 6 键或使用源码等价默认 `[0,0,500,500,500,0]`）。
- 需要地表流（ROIMP+SSUR+SIF+SDRO）与基流（主/补自由水）分量用于诊断。

## Do Not Use When

- 输入是雨雪混合驱动（气温、雪水当量）——SNOW-17 融雪不属本 Skill，请先由上游处理成净降雨。
- 蒸发能力未知、想用 Hargreaves/Hamon 由温度反推——PET 公式不属本 Skill。
- 需要单位线或河道汇流演算——归入 `route-muskingum-channel`；本 Skill 的 Q 已是总出流。
- 需要搜索 16 个参数——归入 `model-calibration`。
- 需要多流域批量运行——本 Skill 一次处理一个序列。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。
- 需要亚日尺度的水文结论——源码 depletion 率为 1/day、ET 分层与 ninc 公式均为日核算，非日步长仅记 WARN 放行，行为未经验证且参数不自动换算。

## Governing Principle

1. **五库结构**：上张力水（UZTWM）+ 上自由水（UZFWM）+ 下张力水（LZTWM）+ 下主自由水（LZFPM）+ 下补自由水（LZFSM），外加附加不透水面积库 ADIMC。
2. **分层蒸散发**：ET1 上张力水 → ET2 上自由水（仅当 ET1 超出张力水蓄量）→ 张力水/自由水按总占比重分配 → ET3 下张力水（`red·lztwc/(uztwm+lztwm)`）→ 下自由水向上张力水回补（先补库后主库，`saved=rserv·(lzfpm+lzfsm)` 保留不可转移部分）→ ET5 ADIMP 面 → ET4 河岸植被。
3. **`tot_outflow = surf + base − et4`（源码语义，原样保留）**：河岸 ET 直接从出流中扣除，可能为负，源码兜底截 0；因此 `Q_MM` 与调整后的 `SURF + BASE` 可能不等（源码从不回算总量）。
4. **增量子步**：`ninc = floor(1+0.2·(uzfwc+twx))`，每个子步独立核算基流、下渗与蓄满溢流；`np.floor` 返回的 float 必须显式 `int()`（源码依赖 numba 隐式处理，本 Skill 有意修正）。
5. **下渗分配**：`perct = perc·(1−pfree)` 先填下张力水，剩余按 `hpl·fracp` 分主/补自由水，溢出沿 LZFSC→LZFPC→LZTWC 回补。
6. **双口径输出**：`Q_MM` 是源码等价输出（mm/步，逐位一致），`Q` 是按 `--area-km2` 与 `--timestep-hours` 换算的 m³/s——源码没有任何面积换算，必须显式提供。

## 输入要求与确认闸门

- 降雨 `P` 与蒸发能力 `PET` 必须同量纲（每步长水深 mm）。
- 流域面积 `--area-km2` 与时间步长 `--timestep-hours` 必须显式给出。
- 16 个参数全部必填（源码直接按 `par[0..15]` 取值，无静默默认）；硬校验：5 个容量 > 0、5 个比例 ∈ [0,1]、3 个消耗率 ∈ (0,1]、REXP ≥ 0、SIDE ≥ 0、`ADIMP + PCTIM < 1`。
- `--initial-state` 接受 6 键；缺省用源码等价默认 `[0,0,500,500,500,0]` mm，在 result.json 标注 `initial_states_source`。
- 缺测即 QC 失败并退出码 2。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场从默认（或 `--initial-state`）状态起算。
- `--warmup-steps` 只把前 N 步标记为 `is_warmup`——源码没有 warmup 机制，标记段照常递推。
- 步长 ≠ 24h 放行但记 WARN：depletion 玉率不自动换算，结果仅供流程贯通。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出模拟表 | 先标准化时间轴 |
| `daily_timestep` | 步长 ≠ 24 小时 | warning（非 FAIL） | 明知未验证仍继续时自行承担；日结论请改日步长 |
| `no_missing_forcing` | P 或 PET 含缺测 | 退出码 2 | 显式修复缺测 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级（尤其 ZPERC/REXP） |
| `state_bounds` | 六个水库状态或总出流为负 | 退出码 2 | 不应发生（公式自限 + 兜底），出现即数值异常 |
| `numerical_clipping` | 负出流兜底、阈值复位、库间溢出回补、ratio/defr 截 0 | warning | 检查参数量级与输入 |
| `water_balance` | 水量平衡残差超过相对容差 | warning（非 FAIL） | SIDE 非河道分量、ET4 扣出流与库间回补使闭合为近似 |

## 结果解释边界

- Q 是总出流（m³/s），`SURF`/`BASE` 是源码调整后的分量（`Q_MM` 可能不等于二者之和）。
- `Q_MM` 与源码逐位一致，用于等价性核查；`Q` 用于可用产物。
- 本 Skill 不输出参数是否"合理"的判断——源码未定义范围，合理性由使用者判断。
- ET5 公式上游作者自注"来源不明、可能为负"，本 Skill 保留源码公式，负值由截断逻辑处理并计数。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 同类集总式模型：`run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-hbv-model`、`run-lumped-tank-model`、`run-lumped-gr4j-model`。
