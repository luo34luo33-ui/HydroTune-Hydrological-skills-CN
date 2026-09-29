# 集总式 GR4J 前向模拟 使用指南

## Overview

`run-lumped-gr4j-model` 运行 GR4J 四参数模型：tanh 产流水库（X1）、非线性下渗分流、双单位线（X4）与汇流水库（X3）、地下水交换（X2），输出流域出口流量 `Q`（m³/s）与源码等价的 `Q_MM`（mm/步）。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含；核心只用 `math.tanh`）
- 4 个参数的 JSON/YAML 参数集
- 已标准化的 forcing 表：`P` 与 `PET` 同量纲（每步长水深 mm）

## Typical User Requests

- "用 GR4J 把这段降雨跑一遍。"
- "从上次的末状态继续算。"
- "X2 设成负值试一下外部汇入。"

## Quick Start

```bash
python scripts/run_lumped_gr4j.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --pet-column PET \
  --params parameters.json \
  --area-km2 584 --timestep-hours 1 \
  --mode continuous --output-dir ./gr4j_run
```

从历史状态续算：

```bash
python scripts/run_lumped_gr4j.py \
  --forcing forcing.csv --params parameters.json \
  --area-km2 584 --timestep-hours 1 --mode continuous \
  --initial-state states.json --output-dir ./gr4j_warm
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_gr4j.py --help`。

## What the Agent Will Do

1. 读取参数集并做物理校验（X1/X3/X4 > 0；X2 任意实数，负值 = 外部汇入）。
2. 收集场次并校验时间轴、缺测与预热覆盖。
3. 逐时步递推：tanh 产流分支（净雨/净蒸，scaled 截 13）→ 非线性下渗 → UH 移位卷积 → 汇流水库与地下水交换 → QR + QD。
4. 执行 QC 并写出模拟表与 `result.json`。
5. 任一 QC FAIL 时不产出模拟表，退出码 2。

## 参数集（4 个，全部必填）

| 键 | 语义 | 单位 |
|---|---|---|
| `X1` | 产流水库容量 | mm |
| `X2` | 地下水交换系数；**可为负**（负值 = 外部汇入） | mm |
| `X3` | 汇流水库容量 | mm |
| `X4` | 单位线历时，以"步"为单位；`ceil(X4)` 决定 UH1 长度、`ceil(2·X4)` 决定 UH2 长度 | 步 |

源码未定义任何参数范围，本 Skill 不引入外部先验，无 parameter_range 检查。流域面积与时间步长只从 CLI 提供（源码输出 mm，无换算）。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML |
| `--area-km2` | 是 | 流域面积（源码无面积换算，必须显式提供） |
| `--timestep-hours` | 是 | 时间步长，与时间列推断值不一致即 FAIL |
| `--mode` | 是 | `event` 或 `continuous` |
| `--forcing` | continuous | forcing 表路径 |
| `--events-dir` / `--events-workbook` | event 二选一 | 场次来源 |
| `--time-column` | 否 | 默认 `time` |
| `--precipitation-column` | 否 | 默认 `P` |
| `--pet-column` | 否 | 默认 `PET` |
| `--carry-columns` | 否 | 逗号分隔，原样透传的列 |
| `--warmup-steps` | 否 | 默认 0；仅标记 is_warmup，不重置状态 |
| `--initial-state` | 否 | JSON 文件，键 `production_store`/`routing_store`（mm） |
| `--balance-tolerance-fraction` | 否 | 默认 0.05（5%），水量平衡 WARN 阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## Outputs

- `lumped_gr4j_runoff.<fmt>`：`time`、`is_warmup`、`P`、`PET`、`EA`、`PS`、`PERC`、`PR`、`UH1_IN`、`UH2_IN`、`GW`、`QR`、`QD`、`Q_MM`、`PROD_STORE`、`ROUT_STORE`、`Q`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 把 `Q_MM` 当 m³/s 用：`Q_MM` 是源码等价的 mm/步口径，m³/s 用 `Q` 列。
- 忘记给 `--area-km2`：源码根本没有面积换算，本 Skill 必须显式提供。
- 把 X2 为负当错误：负的地下水交换表示外部汇入，是 GR4J 标准语义。
- 期待参数范围告警：源码未定义范围，本 Skill 不引入外部先验。
- 期待 UH 数组级初始状态：`--initial-state` 只接受两个标量水库状态，UH 固定从全 0 开始。
- 期待水量平衡严格闭合：地下水交换与 percolation 分流使闭合为近似，残差以 warning 报告。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| 输出 qsim 仅 mm/步，无任何面积换算 | 增加 `--area-km2`/`--timestep-hours` 必填，输出 `Q`（m³/s）；`Q_MM` 保留源码口径 | 可用产物必须可换算，源码口径保留作等价锚点 |
| 只返回 qsim（或加状态字典） | 增加输出 EA、PS、PERC、PR、GW、QR、QD、UH 入流列 | 超集输出，算法数值不变，便于诊断 |
| `max(0, ...)` 截断静默发生 | 截断与 tanh 饱和截断计数并以 warning 报告 | 静默水量损耗不可审计 |
| `states` 可含 UH1/UH2 数组 | `--initial-state` 只接受两个标量状态，UH 固定全 0 | CLI 传数组易错；源码默认即全 0 |
| 无输出文件 | 必然写出模拟表与 result.json | 结果必须可审计 |
| 缺测不处理 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造模拟 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`、`run-lumped-dhf-model`、`run-lumped-hbv-model`、`run-lumped-tank-model`：同分类的其它集总式模型。
- `model-calibration/calibrate-model-de`：4 参数的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q 的指标评价。
