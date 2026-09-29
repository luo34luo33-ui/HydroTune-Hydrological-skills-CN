# 集总式 DHF（大伙房）前向模拟 使用指南

## Overview

`run-lumped-dhf-model` 运行大伙房（DHF）模型：双层蓄水容量产流、蒸发亏缺分配与经验 Gamma 型单位线汇流，输出流域出口流量 `Q`。产汇流不可拆分——单位线时程参数 TM 依赖产流状态 YA——因此本 Skill 自包含产汇流，与 `run-lumped-xaj-model` + `route-muskingum-channel` 的两段式结构不同。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含；不依赖 numba）
- 18 个参数的 JSON/YAML 参数集
- 已标准化的 forcing 表：`P` 与 `PET` 同量纲（每步长水深 mm），时间列严格递增且等间隔

## Typical User Requests

- "用大伙房模型把这场洪水跑一遍，参数用这套。"
- "把这段 3 小时步长的序列连续递推。"
- "换归一化参数跑一次（率定器输出 0-1 参数）。"

## Quick Start

```bash
python scripts/run_lumped_dhf.py \
  --forcing forcing.csv \
  --time-column time --precipitation-column P --pet-column PET \
  --params parameters.json \
  --river-length-km 155.763 --area-km2 5482 --timestep-hours 3 \
  --mode continuous --warmup-steps 480 \
  --output-dir ./dhf_run
```

逐场次（目录内每个文件一场）：

```bash
python scripts/run_lumped_dhf.py \
  --events-dir ./events \
  --params parameters.json \
  --river-length-km 155.763 --area-km2 5482 --timestep-hours 3 \
  --mode event --warmup-steps 120 \
  --output-dir ./dhf_events
```

只查看参数说明不需要真实输入：`python scripts/run_lumped_dhf.py --help`。

## What the Agent Will Do

1. 读取参数集并做物理校验（容量为正、系数在界内、COE 不得趋近 0）。
2. 按 `--param-scale` 解释参数：`original` 原样使用；`normalized` 按源工程率定范围线性反归一化。
3. 收集场次并校验时间轴、缺测与预热覆盖。
4. 产流循环：KC 蒸发 → 净雨 → G 分解 → 表层蓄水曲线产流 RR 或蒸发亏缺分支 → 下层分配 YU/YL → 状态更新。
5. 汇流循环：YA 递推 → TM/TT/TS → K3/K3L 归一化 → 地表与地下单位线卷积 → Q = QS + QL。
6. 执行 QC 并写出模拟表、`event_index.csv`（event 模式）与 `result.json`。
7. 任一 QC FAIL 时不产出模拟表，退出码 2。

## 参数集（18 个，全部必填）

| 键 | 语义 | 源工程范围 | 单位 |
|---|---|---|---|
| `S0` | 表层蓄水容量 | [0, 50] | mm |
| `U0` | 下层蓄水容量 | [0, 90] | mm |
| `D0` | 深层蓄水容量 | [70, 160] | mm |
| `KC` | 蒸发系数（源脚本别名 `K`） | [0.1, 0.9] | 无量纲 |
| `KW` | 下层流系数 | [0, 1] | 无量纲 |
| `K2` | 渗透系数 | [0.2, 0.9] | 无量纲 |
| `KA` | 总径流调节系数 | [0.7, 1] | 无量纲 |
| `G` | 不透水面积比例 | [0, 1] | 无量纲 |
| `A` | 表层蓄水指数 | [0, 5] | 无量纲 |
| `B` | 下层蓄水指数 | [1, 3] | 无量纲 |
| `B0` | 汇流参数（河长比） | [0.1, 2] | 无量纲 |
| `K0` | 汇流参数 | [0, 0.8] | 无量纲 |
| `N` | 汇流参数（地下历时倍数） | [2, 6] | 无量纲 |
| `DD` / `CC` | 地表单位线形状 | [0.5, 4] | 无量纲 |
| `COE` | 汇流时间比例 | [0, 0.8] | 无量纲 |
| `DDL` / `CCL` | 地下单位线形状 | [0.5, 4] | 无量纲 |

范围来自 hydromodel 的率定先验，超出只产生 warning，不是硬约束。硬约束（FAIL）见 SKILL.md 的 QC 表。流域属性不接受参数集：`--river-length-km`、`--area-km2`、`--timestep-hours` 只从 CLI 提供。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--params` | 是 | 参数集 JSON/YAML |
| `--river-length-km` | 是 | 主河长 |
| `--area-km2` | 是 | 流域面积，用于 `W0 = Area/(3.6*dt)` |
| `--timestep-hours` | 是 | 时间步长，与时间列推断值不一致即 FAIL |
| `--mode` | 是 | `event` 或 `continuous` |
| `--forcing` | continuous | forcing 表路径 |
| `--events-dir` / `--events-workbook` | event 二选一 | 场次来源 |
| `--time-column` | 否 | 默认 `time` |
| `--precipitation-column` | 否 | 默认 `P`（源数据列名 `rain` 需上游重命名） |
| `--pet-column` | 否 | 默认 `PET`（源数据列名 `ES` 需上游重命名） |
| `--carry-columns` | 否 | 逗号分隔，原样透传的列 |
| `--param-scale` | 否 | `original`（默认）或 `normalized` |
| `--warmup-steps` | 否 | 默认 0；源脚本默认 480 步、help 写 365 天的矛盾已消除 |
| `--initial-state` | 否 | JSON 文件，键 `sa0`/`ua0`/`ya0`，覆盖 warmup 之后的初始状态 |
| `--balance-tolerance-fraction` | 否 | 默认 0.01（1%），水量平衡 WARN 阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## Outputs

- `lumped_dhf_runoff.<fmt>`：`time`、`is_warmup`、`P`、`PET`、`E`、`PE`、`PC`、`Y0`、`EU`、`EL`、`RR`、`Y`、`YU`、`YL`、`RUNOFF`、`QS`、`QL`、`Q`、`SA`、`UA`、`YA`、`EB`，以及透传列。
- `event_index.csv`（event 模式）：场次标识、行数、起止时间、预热步数。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 期待 `Q` 之后再接 `route-muskingum-channel`：DHF 的 Q 已是出口流量，单位线与产流状态耦合、不可拆分。
- 把 `source_equivalent_params.json` 当率定参数：它是源脚本的内置测试参数（U0=100、B=0.6 本身就超出率定范围，会触发 warning）。
- 用参数文件携带流域面积或河长：它们只接受 CLI 参数。
- 忘记 `--param-scale normalized`：率定器输出的 0-1 参数会被当作原始尺度，结果差数个量级。
- 给 COE 填 0：会使 tan(PAI*COE) 除零、汇流退化为零，物理校验直接拒绝。
- 期待水量平衡严格闭合：单位线卷积在序列末端截断，残差以 warning 报告。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| 依赖 hydromodel 包 + hydrodatasource RuntimeDataLoader（csv/sql/stream 等） | 自包含单文件算法，只接受表格输入 | Skill 必须自包含，禁止跨仓库 sys.path 注入 |
| 7 个 numba `@jit` 原子函数 | 不迁移（主函数从未调用它们） | 死代码，且避免 numba 依赖 |
| `main_river_length=155.763`、`basin_area=5482.0` 写死 | `--river-length-km`、`--area-km2` 必填 | 大伙房流域值不能当通用默认值 |
| `--data-path`、`--params-file` 带本机绝对路径默认值 | 全部必填，无默认值 | 本机路径不可迁移 |
| 参数文件缺失时静默回落测试参数 | 必须显式提供参数集 | 静默回落会伪造模拟 |
| `normalized_params="auto"` 自动检测 | `--param-scale` 显式声明 | auto 在参数恰好都在 [0,1] 时会误判 |
| `--warmup-length` 默认 480 但 help 写 365 天 | `--warmup-steps` 显式给出，默认 0 | 消除自相矛盾 |
| `[time, basin, 2]` 多流域批处理 | 单流域单序列，event/continuous 双模式 | 与 XAJ Skill 一致；批量由调用方循环 |
| qobs 加载后被丢弃 | 不接受观测输入（纯模拟） | 明确 Skill 为前向模拟 |
| `--save-results` 声明但未实现，结果只打印 shape | 必然写出模拟表与 result.json | 结果必须可审计 |
| NaN 只计数 warning，不阻止运行 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造模拟 |
| 蒸发分支 NaN 贡献随 numpy 静默传播 | 保留 NaN 语义并用 errstate 静默、isnan 置零并计数 | 与源码数值行为一致且可审计 |
| 卷积尾部截断不可见 | 水量平衡 warning 说明截断影响 | 让使用者知道短序列残差来源 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`：同分类的集总式参照实现（新安江）。
- `hydrological-modeling/route-muskingum-channel`：适用于独立河道演算，不适用于 DHF 内嵌单位线。
- `model-calibration/calibrate-model-de`：18 参数的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q 的指标评价。
