# 连续序列指标 使用指南

## Overview

`compute-continuous-series-metrics` 对一条连续的观测—模拟序列计算整体性能指标与水量平衡偏差。它不切分场次、不汇总、不归因。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas（仓库 `timeseries` 可选依赖组已包含）
- 观测与模拟已对齐（同一时间轴、同一单位）

## Typical User Requests

- "这段连续模拟的 NSE 和水量偏差是多少？"
- "校正前后各算一次连续期指标。"
- "只要 NSE 和 RMSE。"

## Quick Start

```bash
python scripts/compute_series_metrics.py \
  --series corrected_series.csv \
  --time-column time --observed-column Q_obs --simulated-column corrected \
  --timestep-hours 1 \
  --output-dir ./series_metrics
```

只查看参数说明不需要真实输入：`python scripts/compute_series_metrics.py --help`。

## What the Agent Will Do

1. 读取序列并校验时间轴等间隔且与 `--timestep-hours` 一致。
2. 检查观测与模拟长度、缺测与有限性；任一项失败即退出码 2。
3. 计算所选指标。
4. 以 `metric/value/unit/status` 长表写出，并给出 `result.json`。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--series` | 是 | 序列文件路径 |
| `--time-column` | 否 | 默认 `time` |
| `--observed-column` | 否 | 默认 `Q_obs` |
| `--simulated-column` | 否 | 默认 `Q_total` |
| `--timestep-hours` | 是 | 与时间列推断值不一致即 FAIL |
| `--metrics` | 否 | 逗号分隔，默认全部 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## 指标定义

| 指标 | 定义 | 单位 |
|---|---|---|
| `nse` | `1 − Σ(sim−obs)² / Σ(obs−mean(obs))²` | 无量纲 |
| `r2` | 同式，观测为常数序列时按 scikit-learn 约定给 1.0 或 0.0 | 无量纲 |
| `rmse` | `sqrt(mean((sim−obs)²))` | m3/s |
| `mae` | `mean(abs(sim−obs))` | m3/s |
| `volume_bias` | `Σ(sim−obs) × dt` | m3 |
| `relative_volume_bias` | `volume_bias / Σobs × dt` | 无量纲 |

## Outputs

- `series_metrics.<fmt>`：`metric`、`value`、`unit`、`status`（`ok` 或 `unavailable`）。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## Common Mistakes

- 把连续期 NSE 与场次平均 NSE 混为一谈：两者口径不同，不可直接比较。
- 用 `volume_bias` 判断峰现表现：它只说明总量差异。
- 期待缺测被自动跳过：缺测会直接失败。
- 传入未知指标名：会被拒绝并列出可用指标。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| `dt` 为 NaN 时 fallback 3600 秒 | 必须显式且校验一致 | 静默 fallback 会伪造水量换算 |
| 指标散落在绘图循环里 | 独立 Skill，指标与绘图分离 | 指标应可独立复算与审计 |
| `r2_score` 当作「确定性系数」 | 同时输出 `nse` 与 `r2` 并说明边界差异 | 保留源码命名与标准命名的对应 |
| 缺测 `fillna(0)` 后计算 | 缺测即 FAIL | 填 0 会伪造指标 |

## Related Skills

- `evaluation-diagnostics/compute-event-flood-metrics`：过程层面指标。
- `evaluation-diagnostics/aggregate-event-metrics`：跨场次汇总。
- `post-processing/correct-residual-with-ml`：被评价的校正序列来源。
