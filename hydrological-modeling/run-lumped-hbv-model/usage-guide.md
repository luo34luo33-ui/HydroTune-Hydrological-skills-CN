# 集总式简化 HBV 前向模拟 使用指南

## Overview

`run-lumped-hbv-model` 运行简化 HBV 模型：蓄满产流（`SM/FC` 曲线）加三个线性水库（表层阈值出流 Q0、上层 Q1、下层 Q2，层间交换 kp），输出流域出口流量 `Q`。无雪模块。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 9 个参数的 JSON/YAML 参数集
- 已标准化的 forcing 表：`P` 与 `evap` 同量纲（每步长水深 mm）

## Typical User Requests

- "用 HBV 把这段降雨跑一遍。"
- "换一套参数对比流量过程。"
- "把这 60 场逐场独立起算。"

## Quick Start

```bash
python scripts/run_lumped_hbv.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --evaporation-column E0 \
  --params parameters.json \
  --area-km2 584 --timestep-hours 24 \
  --mode continuous --output-dir ./hbv_run
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_hbv.py --help`。

## What the Agent Will Do

1. 读取参数集并做物理校验（fc/beta 为正、c/lp 在 (0,1]、出流系数非负）。
2. 收集场次并校验时间轴、缺测与预热覆盖。
3. 逐时步递推：蒸发缩减 → 蓄满有效雨 → 土壤更新 → recharge → 三水库出流与层间交换。
4. 执行 QC 并写出模拟表与 `result.json`。
5. 任一 QC FAIL 时不产出模拟表，退出码 2。

## 参数集（9 个，全部必填）

| 键 | 语义 | 源工程范围 | 单位 |
|---|---|---|---|
| `fc` | 土壤持水容量 | [100, 200] | mm |
| `beta` | 蓄水容量曲线指数 | [1, 7] | 无量纲 |
| `c` | 蒸发能力缩放系数 | [0.01, 0.07] | 无量纲 |
| `k0` | 表层快速出流系数 | [0.05, 0.2] | 1/步 |
| `l` | 表层出流阈值 | [2, 5] | mm |
| `k1` | 上层出流系数 | [0.01, 0.1] | 1/步 |
| `k2` | 下层出流系数 | [0.01, 0.05] | 1/步 |
| `kp` | 层间交换系数 | [0.01, 0.05] | 1/步 |
| `lp` | 蒸发放缩比（PWP = lp × fc） | [0.3, 1] | 无量纲 |

范围来自源工程率定先验，超出只产生 warning。流域面积与时间步长不接受参数集，只从 CLI 提供。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML |
| `--area-km2` | 是 | 流域面积（源码硬编码 584.0 已剥离） |
| `--timestep-hours` | 是 | 时间步长（源码写死 86400 秒隐含日步长，此处显式化） |
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

- `lumped_hbv_runoff.<fmt>`：`time`、`is_warmup`、`P`、`PET`、`EA`、`SM`、`RECHARGE`、`SUZ`、`SLZ`、`Q0`、`Q1`、`Q2`、`Q`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 以为有融雪过程：源码即无雪模块，降雪会被当作降雨直接进入土壤层。
- 用源码演示参数当率定参数：`source_equivalent_params.json` 只用于复现源码默认值。
- 忘记 `--timestep-hours` 必须与时间轴一致：源码写死 86400 秒的行为已显式化。
- 期待水量平衡严格闭合：源码 Q0 不从 SM 扣水、effective→recharge 有消减项，残差以 warning 报告。
- 期待 warmup 重置状态：源码没有 warmup 机制，标记段照常递推。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| `p.get('fc', 195.0)` 等 9 个静默默认参数 | 全部必填，缺失即退出码 1 | 静默默认会伪装成率定结果 |
| `area=584.0` 硬编码默认 | `--area-km2` 必填 | 流域面积是输入事实 |
| `unit_conv = area×1000/86400` 写死日步长 | `--timestep-hours` 显式（等价 24） | 隐含步长会让非日序列结果错数倍 |
| 只返回 Q | 增加输出 AE、SM、RECHARGE、SUZ、SLZ、Q0、Q1、Q2 | 超集输出，算法数值不变，便于诊断 |
| 无输出文件 | 必然写出模拟表与 result.json | 结果必须可审计 |
| 缺测不处理 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造模拟 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-tank-model`：同分类的其它集总式模型。
- `model-calibration/calibrate-model-de`：9 参数的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q 的指标评价。
