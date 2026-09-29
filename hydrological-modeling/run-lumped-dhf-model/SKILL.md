---
name: run-lumped-dhf-model
description: 适用于在单一计算单元上运行大伙房（DHF）双层蓄水产流与经验单位线汇流的前向模拟，输出流域出口流量 Q；不适用于参数率定、多流域批量建模、河道马斯京根演算或半分布式建模。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - hydrological-modeling/route-muskingum-channel
    - data-processing/prepare-discharge-timeseries
    - model-calibration/calibrate-model-de
---

# 集总式 DHF（大伙房）前向模拟

把降雨与蒸发能力序列推进为流域出口流量 `Q`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，降雨与蒸发能力已是面平均序列。
- 需要运行大伙房（DHF）双层蓄水产流加经验单位线汇流结构。
- 需要连续序列递推或逐场次独立起算（每场重置状态并预热）。
- 需要同时输出产流分量（地表 RR、壤中流 YU、地下 YL、不透水直汇 Y0）用于诊断。

## Do Not Use When

- 需要搜索 18 个参数或划分率定/验证期——归入 `model-calibration`。
- 需要把其它模型的出流做马斯京根河道演算——归入 `route-muskingum-channel`；DHF 的单位线汇流与产流状态 YA 耦合，无法拆出。
- 需要按子流域离散——本 Skill 是集总式，半分布式版本属于后续独立 Skill。
- 需要多流域批量张量运行——本 Skill 一次处理一个序列，多流域请逐一流域调用。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。
- 需要正式成果图件——归入 `visualization-reporting`。

## Governing Principle

1. **双层蓄水产流**：表层蓄水容量曲线（S0、A）控制地表径流 RR；下层蓄水容量（U0、D0、B）经渗透系数 K2 分配壤中流 YU 与地下径流 YL。
2. **不透水面积**：G 把净雨 PE 分为不透水直汇 Y0 与净下渗 PC，与 XAJ 的 IM 分解同理。
3. **蒸发亏缺**：无雨期按累积亏缺 EB 从表层到下层取水（EU、EL），亏缺判据保留源工程（Chu 版本）的边界常数。
4. **经验单位线汇流**：TM 由河长与前期影响雨量决定（`TM=(L/B0)·(YA+R)^(-K0)`），地表与地下分别按 Gamma 型单位线卷积；该汇流与产流状态 YA 耦合，因此整个产汇流不可拆分。
5. **输出即出口流量**：Q = QS + QL，已经过汇流，无需再接河道演算 Skill。

## 输入要求与确认闸门

- 降雨 `P` 与蒸发能力 `PET` 必须同量纲（每步长水深，mm），由输入表提供。
- 河长 `--river-length-km` 与流域面积 `--area-km2` 必须显式给出；源工程的大伙房数值（155.763 km、5482 km²）不作为默认值。
- 时间步长 `--timestep-hours` 必须显式给出并与时间列一致（源工程使用 3 小时）。
- 18 个参数全部必填；规范键名 `KC` 兼容源脚本的 `K` 别名；`--param-scale` 必须显式声明参数是原始尺度还是 0-1 归一化尺度。
- 缺测即 QC 失败并退出码 2，不静默填充。
- 参数文件缺失时不得回落到内置测试参数——那是源脚本的演示行为，本 Skill 要求显式提供。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场独立初始化。
- `--warmup-steps` 的语义与源码一致：warmup 段从默认状态（SA=0、UA=0、YA=0.5）递推，其终态作为模拟期初值；`--initial-state`（sa0/ua0/ya0）覆盖只发生在 warmup 之后。
- `--param-scale normalized` 时参数按源工程率定范围线性反归一化；原始尺度参数应使用 `original`。
- 数值裁剪（状态钳制、负值截断、NaN 置零）计数并产生 warning，不静默吞掉。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出模拟表 | 先标准化时间轴 |
| `no_missing_forcing` | P 或 PET 含缺测 | 退出码 2 | 显式修复缺测 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级 |
| `state_bounds` | SA∉[0,S0]、UA∉[0,U0] 或 YA<0 | 退出码 2 | 检查蓄水容量参数 |
| `parameter_range` | 参数超出源工程率定范围 | warning | 确认取值依据后可继续 |
| `numerical_clipping` | 发生状态钳制或负值截断 | warning | 检查蓄水容量与降雨量级 |
| `water_balance` | 水量平衡残差超过相对容差 | warning（非 FAIL） | 单位线卷积在序列末端截断，残差含截断水量 |

## 结果解释边界

- Q 是出口流量，可直接进入指标评价；但汇流参数与产流状态耦合，不能单独解释汇流效果。
- 水量平衡残差包含单位线卷积的末端截断，序列越短残差越显著，不能据此断言模型漏损。
- 参数超出源工程率定范围只产生 warning；是否可接受由使用者判断并写入 provenance。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 其它模型出流的河道演算：`route-muskingum-channel`。
