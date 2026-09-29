---
name: route-muskingum-channel
description: 适用于把入流序列按马斯京根法演进到下游断面，包括单河段演进、单元河网多级串联、上游水库出库演进以及多路出流相加；不适用于产流计算、参数率定、空间河网提取或成果绘图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - data-processing/prepare-discharge-timeseries
    - spatial-analysis/build-hydrological-topology
---

# 马斯京根河道演算

把一条或多条入流序列演进为出流，并可把多路出流相加。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 已有坡面/河网入口流量（例如新安江的 `Qt`），需要演进到下游断面。
- 需要把单元河网按多级串联方式演进。
- 需要把上游水库出库单独演进，再与区间来水相加得到出口流量。
- 需要同时处理多条入流并为每条使用不同参数。

## Do Not Use When

- 还没有入流序列：先由 `run-lumped-xaj-model` 或其它模型产生产流结果。
- 需要搜索 K、X 或河段数：归入 `model-calibration`。
- 需要从 DEM 提取河网或构建拓扑：归入 `spatial-analysis`。
- 需要正式成果图件：归入 `visualization-reporting`。
- 入流含缺测且未修复：先回到上游显式处理，本 Skill 不会静默填充。

## Governing Principle

1. **单河段公式**：`C0 = (-KX + 0.5Δt) / (K - KX + 0.5Δt)`，`C1 = (KX + 0.5Δt) / (K - KX + 0.5Δt)`，`C2 = (K - KX - 0.5Δt) / (K - KX + 0.5Δt)`，`C0 + C1 + C2 = 1`。
2. **数值稳定**：单河段模式按源码行为把 `C0`、`C1` 裁剪到 `[0, 1]` 并令 `C2 = 1 - C0 - C1`，裁剪会记为 warning。
3. **单元河网串联**：`K_l = Δt`，`x_l = 0.5 - n(1 - 2X)/2`；该式在 `X` 较小且 `n` 较大时会得到小于 0 的 `x_l`，本 Skill 保留该取值不裁剪，并给出 warning。
4. **初始条件显式**：`Q0` 缺省取入流首值，但初值会影响演进前若干步，长度短或起涨陡时尤其明显。
5. **体积差不等于误差**：马斯京根演进前后体积差反映河槽蓄量变化，超限只作 warning，需结合初末蓄量解释。

## 输入要求与确认闸门

- 入流表必须含时间列与至少一条入流列，单位为 m3/s。
- 时间步长 `--timestep-hours` 必须显式给出，并与时间列推断值一致。
- 每条路由的参数由 `--route-spec` 显式给出：`id`、`column`、`layout`、`k`、`x`，`cascade` 还需 `reaches`。
- 入流缺测即 QC 失败，退出码 2。
- `Q0` 缺省时取入流首值并写入 `route_coefficients.json`，使用者需自行判断该初值是否可接受。

## 决策规则

- 单河段演进用 `single`；单元河网多级串联用 `cascade`。
- 多源合并默认 `--combine sum`，输出列默认 `Q_total`；不需要合并时用 `--combine none`。
- `cascade` 的 `output_level` 取 `final`（完整串联结果）或 `first`（第一级输出，用于复现固定取 `Q2` 的源码行为）。
- `K`、`X`、`reaches` 必须来自率定或有依据的取值，本 Skill 不推荐默认值。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出结果 | 先标准化时间轴 |
| `no_missing_inflow` | 入流列含缺测 | 退出码 2 | 回到上游显式修复 |
| `coefficient_sum` | `C0+C1+C2` 与 1 偏差超过 1e-9 | 退出码 2 | 检查 K、X 与步长的量级 |
| `finite_outflow` | 出现 NaN/Inf | 退出码 2 | 检查入流量级与参数 |
| `stability_range` | 等价 `X` 超出 `[0, 0.5]` | warning | 确认 `reaches` 与 `X` 组合是否有率定依据 |
| `negative_outflow` | 演进出现负值 | warning，按源码裁剪到 0 | 检查 `K` 与 `Δt` 的稳定条件 |
| `volume_difference` | 体积相对差超过容差 | warning | 结合初末河槽蓄量解释，必要时调整 `Q0` |

## 结果解释边界

- 出流只表示按给定参数的演进结果，不代表模型整体精度。
- 负出流被裁剪为 0 后体积不再严格守恒，裁剪次数写入 `result.json`。
- `Q0` 的影响随序列长度衰减，短序列结果对初值敏感。

## 下一步

- 上游产流：`hydrological-modeling/run-lumped-xaj-model`。
- 参数来源：`model-calibration`。
- 结果评价：`evaluation-diagnostics`。
