# 逐场次洪水指标 使用指南

## Overview

`compute-event-flood-metrics` 对切分好的场次分别计算洪量、洪峰、峰现时间误差、NSE、R²、RMSE 与 MAE，一场一行。它不汇总、不归因、不出图。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 每个场次的观测与模拟序列已对齐（同一时间轴、同一单位）

## Typical User Requests

- "把这 60 场洪水的洪量误差、洪峰误差和峰现时间误差算出来。"
- "每场都算 NSE，后面再取平均。"
- "按 20% 与 3 小时判合格，给出合格率。"

## Quick Start

```bash
python scripts/compute_event_metrics.py \
  --events-dir ./events \
  --time-column time --observed-column Q_obs --simulated-column XAJ_output \
  --timestep-hours 1 --volume-unit 10k-m3 \
  --thresholds thresholds.json \
  --output-dir ./event_metrics
```

只查看参数说明不需要真实输入：`python scripts/compute_event_metrics.py --help`。

## What the Agent Will Do

1. 收集场次（单文件、xlsx 多 sheet 或目录内多文件）。
2. 逐场次校验时间轴等间隔且与 `--timestep-hours` 一致。
3. 检查观测与模拟的长度、缺测与有限性；任一项失败即退出码 2。
4. 计算洪量（显式 dt 换算）、洪峰、峰现时间误差、NSE、R²、RMSE、MAE。
5. 若给出阈值则判定合格，否则记为 `unavailable`。
6. 写出 `event_metrics.csv` 与 `result.json`。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--events` / `--events-dir` | 二选一 | 单文件（xlsx 每个 sheet 为一场）或目录 |
| `--time-column` | 否 | 默认 `time` |
| `--observed-column` | 否 | 默认 `Q_obs` |
| `--simulated-column` | 否 | 默认 `Q_total` |
| `--timestep-hours` | 是 | 与时间列推断值不一致即 FAIL |
| `--volume-unit` | 否 | `10k-m3`（默认，万 m³）或 `m3` |
| `--thresholds` | 否 | 合格阈值 JSON 字符串或文件路径 |
| `--event-id-column` | 否 | 默认 `event_id` |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

阈值文件示例：

```json
{
  "relative_volume_error": 0.2,
  "relative_peak_error": 0.2,
  "peak_time_error_hours": 3.0
}
```

下划线开头的键会被忽略，可在文件内写说明。

## 指标定义

| 指标 | 定义 |
|---|---|
| `observed_volume` / `simulated_volume` | `sum(Q) * dt_seconds`，按 `--volume-unit` 换算 |
| `volume_error_relative` | `(观测洪量 − 模拟洪量) / 观测洪量` |
| `observed_peak` / `simulated_peak` | `max(Q)` |
| `peak_error_relative` | `(观测洪峰 − 模拟洪峰) / 观测洪峰` |
| `peak_time_error_hours` | `模拟峰时刻 − 观测峰时刻`，正值表示模拟峰现偏晚 |
| `nse` | `1 − Σ(sim−obs)² / Σ(obs−mean(obs))²`；观测方差为 0 时记 `unavailable` |
| `r2` | 同式，但按 scikit-learn 约定：观测为常数序列时给 1.0 或 0.0 |
| `rmse` / `mae` | 常规定义 |

**关于 NSE 与 R²**：二者在常规序列上数值相同，源码把「确定性系数」实现为 `r2_score`，而另一处写作 `1 − MSE/var`，本质一致。本 Skill 同时输出两列，是为了保留源码命名与标准命名的对应关系，并显式处理边界情形。

## Outputs

- `event_metrics.<fmt>`：场次标识、行数、起止时间、洪量与洪峰、相对误差、峰现时间误差、并列峰计数、`nse`、`r2`、`rmse`、`mae`、三个合格列。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 用默认 `10k-m3` 却按 m³ 解读结果：单位写入 `result.json`，比较前先确认。
- 忘给 `--thresholds` 却期待合格判定：合格列会是 `unavailable`。
- 直接拿单场 NSE 下结论：单场指标对峰现错位极敏感，应结合 `aggregate-event-metrics`。
- 期待峰现时间误差的相反符号：本 Skill 固定为「模拟减观测」。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| 洪量 `sum(Q) * 0.36` 写死 | 按 `--timestep-hours` 与 `--volume-unit` 换算 | 0.36 隐含 dt=1h 与万 m³，易误用 |
| `dt` 为 NaN 时 fallback 3600 秒 | 必须显式且校验一致 | 静默 fallback 会伪造时间轴 |
| 合格阈值 0.2 与 3 小时硬编码 | `--thresholds` 显式给出，缺失记 `unavailable` | 阈值需使用者确认 |
| `r2_score` 当作「确定性系数」 | 同时输出 `nse` 与 `r2` 并说明边界差异 | 保留源码命名与标准命名的对应 |
| 峰现时间误差两处符号相反 | 统一为「模拟 − 观测」 | 消除歧义 |
| `fillna(0)` 后计算 | 缺测即 FAIL | 填 0 会伪造指标 |

## Related Skills

- `evaluation-diagnostics/aggregate-event-metrics`：跨场次汇总与合格率。
- `evaluation-diagnostics/compute-continuous-series-metrics`：连续序列视角。
- `post-processing/correct-residual-with-ml`：被评价的校正结果来源。
