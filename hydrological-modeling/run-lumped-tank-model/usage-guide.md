# 集总式 Tank 水箱模型前向模拟 使用指南

## Overview

`run-lumped-tank-model` 运行 Tank 水箱模型：四个串联水箱，顶箱双高度双侧孔加底孔，逐层底孔下渗进入下一箱，末箱单一侧孔，输出流域出口流量 `Q`。蒸发直接从降雨扣除，不做土壤调蓄。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 16 个参数的 JSON/YAML 参数集
- 已标准化的 forcing 表：`P` 与 `evap` 同量纲（每步长水深 mm）

## Typical User Requests

- "用 Tank 模型跑这段降雨。"
- "调整顶箱侧孔高度对比洪峰。"
- "把这批场次逐场独立起算。"

## Quick Start

```bash
python scripts/run_lumped_tank.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --evaporation-column E0 \
  --params parameters.json \
  --area-km2 584 --timestep-hours 1 \
  --mode continuous --output-dir ./tank_run
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_tank.py --help`。

## What the Agent Will Do

1. 读取参数集并做物理校验（出流系数在 [0,1]、孔高非负、上侧孔不低于下侧孔）。
2. 收集场次并校验时间轴、缺测与预热覆盖。
3. 逐时步递推：各箱侧孔与底孔出流 → 底孔串联下灌 → 蓄水更新（floor 0）。
4. 执行 QC 并写出模拟表与 `result.json`。
5. 任一 QC FAIL 时不产出模拟表，退出码 2。

## 参数集（16 个，全部必填）

| 键 | 语义 | 源工程范围 | 单位 |
|---|---|---|---|
| `t0_is` | 顶箱初始蓄水 | [0, 50] | mm |
| `t0_boc` | 顶箱底孔出流系数 | [0.15, 0.5] | 1/步 |
| `t0_soc_uo` | 顶箱上侧孔出流系数 | [0.2, 0.6] | 1/步 |
| `t0_soc_lo` | 顶箱下侧孔出流系数 | [0.15, 0.5] | 1/步 |
| `t0_soh_uo` | 顶箱上侧孔高度 | [50, 120] | mm |
| `t0_soh_lo` | 顶箱下侧孔高度 | [10, 50] | mm |
| `t1_is` / `t1_boc` / `t1_soc` / `t1_soh` | 第二箱初值/底孔/侧孔/孔高 | [0,50]/[0.1,0.4]/[0.1,0.4]/[20,80] | 混合 |
| `t2_is` / `t2_boc` / `t2_soc` / `t2_soh` | 第三箱同构 | [0,50]/[0.05,0.3]/[0.05,0.3]/[10,60] | 混合 |
| `t3_is` / `t3_soc` | 末箱初值/侧孔系数 | [0,50]/[0.001,0.05] | 混合 |

范围来自源工程率定先验，超出只产生 warning。硬约束（FAIL）：出流系数在 [0,1]、孔高非负、`t0_soh_uo >= t0_soh_lo`。流域面积与时间步长只从 CLI 提供。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML |
| `--area-km2` | 是 | 流域面积（源码硬编码 584.0 已剥离） |
| `--timestep-hours` | 是 | 时间步长（对应源码 `del_t`） |
| `--mode` | 是 | `event` 或 `continuous` |
| `--forcing` | continuous | forcing 表路径 |
| `--events-dir` / `--events-workbook` | event 二选一 | 场次来源 |
| `--time-column` | 否 | 默认 `time` |
| `--precipitation-column` | 否 | 默认 `P` |
| `--evaporation-column` | 否 | 默认 `E0` |
| `--carry-columns` | 否 | 逗号分隔，原样透传的列 |
| `--warmup-steps` | 否 | 默认 0；仅标记 is_warmup，不重置状态 |
| `--balance-tolerance-fraction` | 否 | 默认 0.05（5%），水量平衡 WARN 阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## Outputs

- `lumped_tank_runoff.<fmt>`：`time`、`is_warmup`、`P`、`EVAP`、`NET`、`S0..S3`、`QS0_LO`、`QS0_UO`、`QS1`、`QS2`、`QS3`、`QB0`、`QB1`、`QB2`、`Q`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 期待土壤蓄满或分层蒸散发：Tank 的蒸发直接扣净雨，旱季蒸散发能力全部扣除。
- 把 `t0_soh_uo` 设得低于 `t0_soh_lo`：物理校验直接拒绝。
- 用源码演示参数当率定参数：`source_equivalent_params.json` 只用于复现源码默认值。
- 期待水量平衡严格闭合：floor 0 截断与负净雨入流使闭合为近似，残差以 warning 报告。
- 期待 warmup 重置状态：源码没有 warmup 机制，每场从 `t*_is` 重新起算。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| `p.get('t0_is', 10.0)` 等 16 个静默默认参数 | 全部必填，缺失即退出码 1 | 静默默认会伪装成率定结果 |
| `area=584.0` 硬编码默认 | `--area-km2` 必填 | 流域面积是输入事实 |
| 顶箱侧孔合并为一个值输出 | 拆分输出 `QS0_LO` 与 `QS0_UO` | 超集输出，算法数值不变，便于诊断 |
| 只返回 Q | 增加四箱蓄水与各孔出流列 | 超集输出，算法数值不变 |
| 无输出文件 | 必然写出模拟表与 result.json | 结果必须可审计 |
| 缺测不处理 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造模拟 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`、`run-lumped-hbv-model`、`run-lumped-dhf-model`：同分类的其它集总式模型。
- `model-calibration/calibrate-model-de`：16 参数的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q 的指标评价。
