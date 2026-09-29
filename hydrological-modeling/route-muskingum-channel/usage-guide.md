# 马斯京根河道演算 使用指南

## Overview

`route-muskingum-channel` 承担全部河道演算任务：单河段演进、单元河网多级串联、上游水库出库演进，以及把多路出流相加成出口流量。它不产流、不率定、不出图。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 入流表：时间列严格递增且等间隔，入流单位 m3/s
- 路由配置：JSON 或 YAML，含 `routes` 数组

## Typical User Requests

- "把新安江的 Qt 演进到流域出口。"
- "上游水库出库和区间来水分别演进后相加。"
- "按 4 级单元河网串联算一遍出流。"

## Quick Start

```bash
python scripts/route_muskingum.py \
  --inflow routed_input.csv \
  --time-column time --timestep-hours 1 \
  --route-spec route_spec.json \
  --combine sum --output-column Q_total \
  --output-dir ./routed
```

只查看参数说明不需要真实输入：`python scripts/route_muskingum.py --help`。

## What the Agent Will Do

1. 读取 `--route-spec`，校验每条路由的必填字段与布局约束。
2. 读取入流表，校验时间列严格递增、等间隔且与 `--timestep-hours` 一致。
3. 检查入流缺测；有缺测即停止，退出码 2。
4. 逐条路由演进：单河段用 `single`，单元河网串联用 `cascade`。
5. 记录系数、等效 `X`、`Q0` 来源与负值计数到 `route_coefficients.json`。
6. 按 `--combine` 合并出流，写出 `routed_flow.<fmt>` 与 `result.json`。
7. 任一 QC FAIL 时不产出演进表，退出码 2。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--inflow` | 是 | 入流表路径，可含多条入流列 |
| `--timestep-hours` | 是 | 时间步长，与时间列推断值不一致即 FAIL |
| `--route-spec` | 是 | 路由配置，含 `routes` 数组 |
| `--time-column` | 否 | 默认 `time` |
| `--combine` | 否 | `sum`（默认）或 `none` |
| `--output-column` | 否 | 合并列名称，默认 `Q_total` |
| `--volume-tolerance` | 否 | 默认 0.02（2%），体积相对差告警阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |
| `--overwrite` | 否 | 只删除本 Skill 声明的产物 |

`--route-spec` 的 `routes` 每一项：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 路由标识，输出列名与检查项前缀，不可重复 |
| `column` | 是 | 入流列名 |
| `layout` | 是 | `single`（单河段）或 `cascade`（单元河网串联） |
| `k` | 是 | 槽蓄时间常数（h）；`cascade` 下等效 `K` 固定等于时间步长 |
| `x` | 是 | 权重因子；`cascade` 下参与 `x_l = 0.5 - n(1-2X)/2` |
| `reaches` | `cascade` 时是 | 串联级数 n |
| `q0` | 否 | 初始出流；缺省取该入流列首值 |
| `output_level` | 否 | `final`（默认，完整串联结果）或 `first`（第一级输出） |

## 公式

单河段：

```text
denom = K - K*X + 0.5*dt
C0 = (-K*X + 0.5*dt) / denom
C1 = ( K*X + 0.5*dt) / denom
C2 = ( K - K*X - 0.5*dt) / denom
Q[0] = Q0
Q[t] = C0*I[t] + C1*I[t-1] + C2*Q[t-1]
```

单河段模式把 `C0`、`C1` 裁剪到 `[0, 1]` 并令 `C2 = 1 - C0 - C1`（源码行为，命中即 warning）。

单元河网串联：

```text
K_l = dt
x_l = 0.5 - n*(1 - 2*X)/2          # 不裁剪
C0 = (0.5*dt - K_l*x_l) / (0.5*dt + K_l - K_l*x_l)
C1 = (0.5*dt + K_l*x_l) / (0.5*dt + K_l - K_l*x_l)
C2 = 1 - C0 - C1
Q{p+1}[t] = C0*Q{p}[t] + C1*Q{p}[t-1] + C2*Q{p+1}[t-1]
```

注意：当 `X` 较小而 `n` 较大时，`x_l` 会明显小于 0（源码示例 `X=0, n=4` 给出 `-1.5`）。本 Skill 保留该取值以维持源码等价，并给出 `stability_range` warning。

## Outputs

- `routed_flow.<fmt>`：`time`、`routed_<id>`（每条路由一列）、合并列（默认 `Q_total`）。
- `route_coefficients.json`：每条路由的 `layout`、`k`、`x`、`reaches`、`output_level`、`q0` 与来源、`C0/C1/C2`、`x_used`、是否裁剪系数、裁剪前负值计数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 把 `cascade` 的 `k` 当成有效参数：`cascade` 的等效 `K` 固定等于时间步长，`k` 只作记录。
- 期望 `output_level=final` 与源码结果一致：源码 `Q_total` 固定取 `Q2`（第一级），复现源码请用 `first`。
- 忽视 `Q0`：缺省取入流首值，短序列结果对初值敏感。
- 把体积差超限当成错误：它通常反映河槽蓄量变化，属 warning。
- 用本 Skill 率定 `K`/`X`：率定属于 `model-calibration`。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| `calculate_Qi` 与 `muskingum_route` 内嵌在模型类里 | 独立 Skill，逐条路由配置化 | 河道演算需要独立复用、独立解释与独立验证 |
| `Q_total = Q2 + Qres_routed` 固定取 `Q2` | `output_level` 可选 `final` 或 `first` | 源码固定取第一级与 `n=4` 的设置不一致，这里保留两种口径并显式选择 |
| 静默 `clip(lower=0)` | 保留裁剪但计数并 warning | 负值说明参数或步长越过稳定条件 |
| `K_res`、`X_res`、`X`、`n` 写在模型参数集里 | 全部进入 `--route-spec` | 河道参数与产流参数解耦 |
| `dt` 缺失时 fallback 3600 秒 | 必须显式且校验一致 | 静默 fallback 会伪造时间轴 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`：提供 `Qt` 等入流。
- `spatial-analysis/build-hydrological-topology`：河网与拓扑来源。
- `model-calibration/calibrate-model-de`：`K`、`X` 的搜索不属于本 Skill。
