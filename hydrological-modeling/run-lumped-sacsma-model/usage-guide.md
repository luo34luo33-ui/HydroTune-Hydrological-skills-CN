# 集总式 SAC-SMA 前向模拟 使用指南

## Overview

`run-lumped-sacsma-model` 运行 SAC-SMA 16 参数模型：上/下区张力水与自由水五库、分层蒸散发（ET1–ET5）、下渗需求函数、ADIMP 附加不透水面积与 PCTIM/RIVA/SIDE 面积分解，输出流域出口总出流 `Q`（m³/s）、源码等价 `Q_MM`（mm/步）及地表/基流分量。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含；无 numba）
- 16 个参数的 JSON/YAML 参数集
- 已标准化的 forcing 表：`P` 与 `PET` 同量纲（每步长水深 mm）

## Typical User Requests

- "用 SAC-SMA 把这段降雨跑一遍。"
- "从上次的六库状态继续算。"
- "把 RIVA 设成 0.02 看河岸损失的影响。"

## Quick Start

```bash
python scripts/run_lumped_sacsma.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --pet-column PET \
  --params parameters.json \
  --area-km2 1200 --timestep-hours 24 \
  --mode continuous --output-dir ./sacsma_run
```

从历史状态续算：

```bash
python scripts/run_lumped_sacsma.py \
  --forcing forcing.csv --params parameters.json \
  --area-km2 1200 --timestep-hours 24 --mode continuous \
  --initial-state states.json --output-dir ./sacsma_warm
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_sacsma.py --help`。

## What the Agent Will Do

1. 读取参数集并做物理校验（容量 > 0、比例 ∈ [0,1]、消耗率 ∈ (0,1]、ADIMP+PCTIM < 1）。
2. 收集场次并校验时间轴、缺测与预热覆盖；步长非 24h 记 WARN。
3. 逐时步递推：ET 分层与重分配回补 → twx 盈余 → `ninc` 子步循环（基流、下渗、分配、蓄满溢流）→ 面积汇总与 `side`/`et4` 调整。
4. 执行 QC 并写出模拟表与 `result.json`。
5. 任一 QC FAIL 时不产出模拟表，退出码 2。

## 参数集（16 个，全部必填）

| 键 | 语义 | 单位 |
|---|---|---|
| `UZTWM` / `UZFWM` | 上区张力水 / 自由水容量 | mm |
| `LZTWM` | 下区张力水容量 | mm |
| `LZFPM` / `LZFSM` | 下区主 / 补自由水容量 | mm |
| `ADIMP` | 附加不透水面积比例（与 ADIMC 库联动） | 小数 |
| `UZK` | 上自由水侧向消耗率 | 1/day |
| `LZPK` / `LZSK` | 下区主 / 补自由水消耗率 | 1/day |
| `ZPERC` / `REXP` | 下渗需求尺度 / 形状参数 | 无量纲 |
| `PCTIM` | 永久不透水面积比例 | 小数 |
| `PFREE` | 下渗水中直接进入自由水的比例 | 小数 |
| `RIVA` | 河岸植被面积比例（其 ET 直接扣出流） | 小数 |
| `SIDE` | 深层补给与河道基流之比（非河道基流分量被消耗） | 无量纲 |
| `RSERV` | 下自由水不可转移比例 | 小数 |

`ADIMP + PCTIM` 必须小于 1（透水面积 PAREA 必须为正）。源码未定义任何参数范围，本 Skill 不引入外部先验，无 parameter_range 检查。流域面积与时间步长只从 CLI 提供（源码输出 mm，无换算）。

## 初始状态（6 键，可选）

| 键 | 语义 | 源码等价默认 |
|---|---|---|
| `UZTWC` / `UZFWC` | 上区张力水 / 自由水蓄量 | 0 / 0 mm |
| `LZTWC` / `LZFSC` / `LZFPC` | 下区张力水 / 补 / 主自由水蓄量 | 500 / 500 / 500 mm |
| `ADIMC` | 附加不透水面积库蓄量 | 0 mm |

缺省时使用源码等价默认 `[0, 0, 500, 500, 500, 0]`，并在 `result.json` 的 `initial_states_source` 标注 `source_equivalent_default`（显式提供则标注 `explicit`）——这是源码函数签名的字面默认值，已在文档与本表公开，不算静默默认。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML（16 键） |
| `--area-km2` | 是 | 流域面积（源码无面积换算，必须显式提供） |
| `--timestep-hours` | 是 | 时间步长；与时间列推断值不一致即 FAIL，≠24h 记 WARN |
| `--mode` | 是 | `event` 或 `continuous` |
| `--forcing` | continuous | forcing 表路径 |
| `--events-dir` / `--events-workbook` | event 二选一 | 场次来源 |
| `--time-column` | 否 | 默认 `time` |
| `--precipitation-column` | 否 | 默认 `P` |
| `--pet-column` | 否 | 默认 `PET` |
| `--carry-columns` | 否 | 逗号分隔，原样透传的列 |
| `--warmup-steps` | 否 | 默认 0；仅标记 is_warmup，不重置状态 |
| `--initial-state` | 否 | JSON 文件，6 键可选 |
| `--balance-tolerance-fraction` | 否 | 默认 0.05（5%），水量平衡 WARN 阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## Outputs

- `lumped_sacsma_runoff.<fmt>`：`time`、`is_warmup`、`P`、`PET`、`ET1`–`ET5`、`TET`、`ROIMP`、`SSUR`、`SIF`、`SDRO`、`SURF`、`BASE`、`PERC`、`Q_MM`、六状态列、`Q`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、初始状态来源、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 把 `Q_MM` 当 m³/s 用：`Q_MM` 是源码等价的 mm/步口径，m³/s 用 `Q` 列。
- 假设 `Q_MM == SURF + BASE`：源码 `tot_outflow = surf + base − et4` 先计算、后调整分量且从不回算总量，二者可能不等；`et4`（河岸 ET）直接扣出流是源码语义。
- 忘记给 `--area-km2`：源码根本没有面积换算，本 Skill 必须显式提供。
- 用亚日步长得出水文结论：源码为日水量核算，UZK/LZPK/LZSK 不自动换算，非日步长行为未经验证。
- 期待 PET 由温度推算：Hargreaves/Hamon 不属本 Skill，PET 必须已在 forcing 中。
- 期待雪模块：SNOW-17 不属本 Skill，请先由上游把雨雪混合驱动处理成净降雨。
- 期待参数范围告警：源码未定义范围，本 Skill 不引入外部先验。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| numba `@jit` 加速 | 纯 Python 循环 + numpy | 去编译依赖，算术逐位一致 |
| `np.floor` 返回 float 直接进 `range(ninc)` | 显式 `int(ninc)` | 纯 Python 必须整型；有意修正并记录 |
| 只返回 [总出流, 地表流, 基流] 3×N | 超集输出 ET1–ET5、TET、分量与六状态列 | 算法数值不变，便于诊断 |
| 负出流兜底、阈值复位、库间溢出静默发生 | 全部截断计数并以 warning 报告 | 静默水量调整不可审计 |
| `initstate` 默认 `[0,0,500,500,500,0]` | 保留为源码等价默认，result.json 标注来源 | 默认值公开且可追溯，不算静默默认 |
| 无输出文件 | 必然写出模拟表与 result.json | 结果必须可审计 |
| 缺测不处理 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造模拟 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-hbv-model`、`run-lumped-tank-model`、`run-lumped-gr4j-model`：同分类的其它集总式模型。
- `model-calibration/calibrate-model-de`：16 参数的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q 的指标评价。
