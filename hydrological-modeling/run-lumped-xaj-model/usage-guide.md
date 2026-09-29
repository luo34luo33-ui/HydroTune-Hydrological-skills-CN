# 集总式新安江前向模拟 使用指南

## Overview

`run-lumped-xaj-model` 把面平均降雨与蒸发能力序列推进为河网入口流量 `Qt`。它只负责产流、蒸散发、三水源划分和坡面/河网调蓄；把 `Qt` 演进到流域出口、做单元河网串联以及把上游水库出库与区间来水相加，都由 `route-muskingum-channel` 完成。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 一份已确认参数集（JSON 或 YAML）
- 已标准化的 forcing 表：时间列严格递增且等间隔，`P` 与 `E0` 单位为 mm/h

## Typical User Requests

- "用这套新安江参数把这场洪水跑一遍，输出 Qt。"
- "把这 60 场洪水逐场独立起算，每场前面留预热。"
- "把这段连续序列跑成 Qt，后面再接马斯京根。"

## Quick Start

连续序列：

```bash
python scripts/run_lumped_xaj.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --evaporation-column E0 \
  --params parameters.json \
  --area-km2 640 --timestep-hours 1 \
  --mode continuous --warmup-steps 24 \
  --output-dir ./xaj_run
```

逐场次（目录内每个文件一场）：

```bash
python scripts/run_lumped_xaj.py \
  --events-dir ./events \
  --params parameters.json \
  --area-km2 640 --timestep-hours 1 \
  --mode event --warmup-steps 12 \
  --output-dir ./xaj_events
```

逐场次（一个 xlsx 的每个 sheet 一场）：

```bash
python scripts/run_lumped_xaj.py \
  --events-workbook floods.xlsx \
  --params parameters.json \
  --area-km2 640 --timestep-hours 1 \
  --mode event --warmup-steps 12 \
  --output-dir ./xaj_events
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_xaj.py --help`。

## What the Agent Will Do

1. 读取参数集并校验物理范围（`WM`、`WUM+WLM`、`KG+KI`、初值区间等）。
2. 收集场次（continuous 为单序列；event 为目录内文件或工作簿内 sheet）。
3. 逐场次解析时间列，校验严格递增、等间隔且与 `--timestep-hours` 一致。
4. 检查 forcing 缺测；有缺测即停止，不填 0。
5. 逐时步推进：三层土壤再分配 → 三层蒸发 → 净雨与产流 → 产流系数 → 三水源划分 → 坡面与河网调蓄。
6. 执行 QC 并写出模拟表、`event_index.csv`（event 模式）与 `result.json`。
7. 任一 QC FAIL 时不产出模拟表，退出码 2。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML，字段见下表 |
| `--area-km2` | 是 | 流域面积，用于 `U = Area / (3.6 * T)` |
| `--timestep-hours` | 是 | 时间步长，与时间列推断值不一致即 FAIL |
| `--mode` | 是 | `event` 或 `continuous` |
| `--forcing` | continuous | forcing 表路径 |
| `--events-dir` / `--events-workbook` | event 二选一 | 场次来源 |
| `--time-column` | 否 | 默认 `time` |
| `--precipitation-column` | 否 | 默认 `P` |
| `--evaporation-column` | 否 | 默认 `E0` |
| `--carry-columns` | 否 | 逗号分隔，原样透传的列（如 `Qres_in`、`Q_obs`），缺测只 warning |
| `--warmup-steps` | 否 | 默认 0；前 N 步标记 `is_warmup` |
| `--balance-tolerance-fraction` | 否 | 默认 1e-3（0.1%），水量平衡残差相对可渗透区降雨总量的容差 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |
| `--overwrite` | 否 | 只删除本 Skill 声明的产物 |

参数集字段（全部必填）：`K, B, C, WM, WUM, WLM, IM, SM, EX, KG, KI, CG, CI, CS, L, WUM_init, WLM_init, WDM_init, S1, FR1, Q`。
派生量不接收：`WDM = max(0, WM - WUM - WLM)`，`WDM_init` 会被裁剪到 `WDM`。
已移出本 Skill：`X`、`n`（单元河网串联）与 `K_res`、`X_res`（上游水库演进）归 `route-muskingum-channel`；`Area` 与 `T` 由 CLI 提供。

## Outputs

- `lumped_xaj_runoff.<fmt>`：`time`、`is_warmup`、`P`、`E0`、`P_perv`、`P_im`、`WU`、`WL`、`WD`、`EP`、`EU`、`EL`、`ED`、`E`、`PE`、`W`、`R`、`FR`、`S1`、`RS`、`RI`、`RG`、`QS`、`QI`、`QG`、`QT`、`Qt`、`R_im`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 把 `Qt` 当成出口流量直接评价：出口结果要先过 `route-muskingum-channel`。
- 用 `examples/source_equivalent_params.json` 当推荐参数：它只用于复现源 notebook 的行为。
- 期待本 Skill 自动裁剪起涨段：源工程丢弃的小时数属于该样本工程决定，需要时先裁剪输入。
- 给 `E0` 传缺测并期待填 0：缺测会直接 FAIL。
- 把 `Area` 或 `T` 放进参数集：它们只接受 CLI 参数。
- 用半分布式需求调用本 Skill：集总式版本不做子流域划分与拓扑汇流。

## 与半分布式版本的关系

本 Skill 名为 `run-lumped-xaj-model`，`lumped` 显式声明空间离散方式。后续半分布式或分布式新安江将作为并列 Skill 出现（例如 `run-semi-distributed-xaj-model`），共享蒸发与产流语义，但输入需要空间单元与拓扑，输出需要单元汇流组织。本 Skill 不会就地扩展成分空间版本。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| `run_model` 内部调用 `calculate_Qi` 与 `muskingum_route`，直接输出 `Q_total` | 只输出到 `Qt`，串联演算与多源相加交给 `route-muskingum-channel` | 河道演算需要独立复用、独立解释与独立验证 |
| `df.loc[i, col]` 逐格写入 | numpy 标量循环，末尾一次性建表 | 60 场 × 数百步时性能差异显著，公式逐项不变 |
| `Area = 640.0` 硬编码 | `--area-km2` 必填 | 流域面积是输入事实 |
| 月份蒸发查表硬编码 12 个值 | 不内置，`E0` 由输入提供 | 站点经验值不能成为通用默认值 |
| `remove_first_hours_rows(..., 65)` 内置弃水 | 不内置，由调用方先裁剪 | 65 小时是该样本工程的决定 |
| 静默 `fillna(0)` | 缺测即 FAIL，退出码 2 | 填充会伪造水量平衡 |
| 静默数值裁剪 | 裁剪保留但计数并 warning | 裁剪说明参数或 forcing 越过定义域 |

## Related Skills

- `hydrological-modeling/route-muskingum-channel`：把 `Qt` 演进到出口并合并上游来水。
- `data-processing/prepare-discharge-timeseries`：标准化观测与 forcing 序列。
- `model-calibration/calibrate-model-de`：参数搜索不属于本 Skill。
