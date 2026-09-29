---
name: run-lumped-xaj-model
description: 适用于在单一计算单元上做集总式新安江三水源产汇流前向模拟，输出河网入口流量 Qt；不适用于子流域划分、河网拓扑汇流、参数率定、半分布式建模或成果绘图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - data-processing/prepare-discharge-timeseries
    - hydrological-modeling/route-muskingum-channel
    - model-calibration/calibrate-model-de
---

# 集总式新安江前向模拟

把面平均降雨与蒸发能力序列推进为河网入口流量 `Qt`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 流域被当作单一计算单元，forcing 已是面平均序列。
- 已有一套确定的新安江参数，只需要前向模拟，不做参数搜索。
- 需要逐场次独立起算（每场重置土壤状态并预热），或需要连续序列递推。
- 下游还有河道演算步骤（`route-muskingum-channel`）。

## Do Not Use When

- 需要按子流域、水文响应单元或栅格离散并组织拓扑汇流——那是半分布式模型的职责。
- 需要搜索参数、比较目标函数或划分率定/验证期——归入 `model-calibration`。
- 需要把 `Qt` 演进到流域出口，或需要把上游水库出库与区间来水相加——归入 `route-muskingum-channel`。
- 需要正式成果图件——归入 `visualization-reporting`。
- 输入仍是站点原始记录、未确认单位与时区——先用 `data-processing` 标准化。

## Governing Principle

1. **集总式假设**：全流域一个计算单元，降雨与蒸发用面平均值，不区分空间分布。
2. **蓄满产流**：产流由土壤含水量与蓄水容量曲线决定，不是超渗产流。
3. **三层蒸发**：按上层、下层、深层依次供水，蒸发能力 `EP = K × E0`。
4. **不透水面积**：`IM` 把降雨分为可渗透与不可渗透两部分，不可渗透部分直接成为地面径流，不进入土壤水库。
5. **三水源划分**：产流量分为地面径流 `RS`、壤中流 `RI`、地下径流 `RG`，再各自调蓄。
6. **只输出到 `Qt`**：坡面与河网调蓄（`CS`、滞时 `L`）算完后即停止；后续串联演算与多源相加不属于本 Skill。

## 输入要求与确认闸门

- `P`（mm/h，瞬时通量）与 `E0`（mm/h）必须由输入表提供；本 Skill 不内置任何月份蒸发查表。
- 流域面积由 `--area-km2` 显式给出，不设默认值。
- 时间步长由 `--timestep-hours` 显式给出，并与时间列推断值校验一致。
- 参数集由 `--params` 显式给出，`Area` 与 `T` 不接受来自参数文件。
- 缺测不得静默填充：forcing 缺测即 QC 失败并退出码 2。
- 单位、时区或列语义未确认时，停止并回到 `data-processing`，不得按列名猜测。

## 决策规则

- 连续序列用 `--mode continuous`；逐场次用 `--mode event`，每场独立初始化状态并独立预热。
- `--warmup-steps` 只把前 N 步标记为 `is_warmup`，不自动裁剪、不自动选择长度。
- 源工程曾丢弃场次前若干小时以避免起涨段误差，这是该样本工程的决定，本 Skill 不内置弃水；需要时由调用方先裁剪输入。
- 数值裁剪（比率钳制、地面径流钳制）会被计数并产生 warning，不静默吞掉。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不生成模拟表 | 先做时间轴标准化，或修正 `--timestep-hours` |
| `no_missing_forcing` | 降雨或蒸发列含缺测 | 退出码 2 | 回到上游显式修复缺测，不得填 0 |
| `warmup_coverage` | 预热步数不小于序列长度 | 退出码 2，模拟期为空 | 缩短预热或延长序列 |
| `finite_states` | 状态或通量出现 NaN/Inf | 退出码 2 | 检查参数量级与降雨量级 |
| `soil_moisture_bounds` | 分层含水量越界或 W > WM | 退出码 2 | 检查 WM/WUM/WLM 关系与初值 |
| `water_balance` | 水量平衡残差超过容差 | 退出码 2 | 放宽容差前先确认分层截断是否被触发 |
| `numerical_clipping` | 发生比率或地面径流钳制 | warning，退出码 0 | 检查 WM/SM 与降雨量级是否匹配 |

## 结果解释边界

- `Qt` 是河网入口流量，不是流域出口流量；出口结果必须由 `route-muskingum-channel` 产生。
- 本 Skill 不评价模拟好坏；指标计算属于 `evaluation-diagnostics`。
- 模拟质量取决于给定参数；参数来自何处必须写入 `provenance`，不得把示例参数当作推荐值。

## 下一步

- 河道演算：`hydrological-modeling/route-muskingum-channel`。
- 参数搜索：`model-calibration/calibrate-model-de` 等。
- 指标与诊断：`evaluation-diagnostics`。
