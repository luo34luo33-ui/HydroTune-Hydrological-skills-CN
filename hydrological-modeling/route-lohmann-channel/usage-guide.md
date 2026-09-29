# Lohmann 河道汇流 使用指南

## Overview

`route-lohmann-channel` 用标准 Lohmann 汇流演算（扩散波河道单位线 + Gamma HRU 单位线 + 双分量卷积）把 `direct` 与 `base` 两条入流序列推进为流域出口总出流 `Q_total`。与 `route-muskingum-channel` 并列：本 Skill 面向日过程大尺度汇流，马斯京根面向场次/小时尺度运动波近似。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas、scipy（仓库 `timeseries` 可选依赖组已包含；无 numba）
- 4 个路由参数（N/K/VELO/DIFF）的 JSON/YAML
- 已标准化的入流表：时间、`direct`、`base` 三列（分量列名可配），单位任意（原样通过）

## Typical User Requests

- "把 SAC-SMA 的地表流和基流汇流到流域出口。"
- "河长设 0，只要 HRU 单位线平滑。"
- "导出单位线我看一下形状。"

## Quick Start

```bash
python scripts/route_lohmann.py \
  --inflow inflow.csv \
  --time-column time --direct-column direct --base-column base \
  --route-params route_params.json \
  --flowlen-m 50000 --timestep-hours 24 \
  --output-dir ./lohmann_run
```

与 `run-lumped-sacsma-model` 对接（其输出表的 `SURF`/`BASE` 列直接作入流）：

```bash
python scripts/route_lohmann.py \
  --inflow sacsma_run/lumped_sacsma_runoff.csv \
  --direct-column SURF --base-column BASE \
  --route-params route_params.json \
  --flowlen-m 50000 --timestep-hours 24 \
  --output-dir ./lohmann_run
```

只查看参数说明不需要真实输入：`python scripts/route_lohmann.py --help`。

## What the Agent Will Do

1. 读取路由参数并做物理校验（N/K/VELO/DIFF > 0、flowlen ≥ 0）。
2. 校验时间轴、双列入流完整性；步长非 24h 记 WARN。
3. 生成单位线：扩散波网格 UH（小时步）→ 单次日输入单次卷积 → 96 天河道日 UH；Gamma 日箱积分 → 12 天 HRU UH；卷积成 107 步合成 UH 并归一化。
4. 双分量卷积（零初始历史）并相加；执行 QC 并写出结果表、单位线 JSON 与 `result.json`。
5. 任一 QC FAIL 时不产出结果，退出码 2。

## 路由参数（4 个，全部必填）

| 键 | 语义 | 单位 |
|---|---|---|
| `N` | Gamma HRU UH 形状参数 | 无量纲 |
| `K` | Gamma HRU UH 尺度参数（水库常数） | 天 |
| `VELO` | 线性化 Saint-Venant 波速 | m/s |
| `DIFF` | 扩散系数 | m²/s |

河长 `--flowlen-m` 从 CLI 提供（源码把它硬编码在调用侧）；0 表示河道 UH 退化为脉冲。源码未定义任何参数范围，本 Skill 不引入外部先验。

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--inflow` | 是 | 入流表路径，须含 direct 与 base 两列 |
| `--flowlen-m` | 是 | 河长（m）；0 = 脉冲 UH |
| `--route-params` | 是 | 路由参数 JSON/YAML（N/K/VELO/DIFF） |
| `--timestep-hours` | 是 | 时间步长；与时间列推断值不一致即 FAIL，≠24h 记 WARN |
| `--time-column` | 否 | 默认 `time` |
| `--direct-column` | 否 | 默认 `direct` |
| `--base-column` | 否 | 默认 `base` |
| `--inflow-format` / `--sheet` | 否 | 表格式与 xlsx 工作表 |
| `--volume-tolerance` | 否 | 默认 0.05（5%），体积差 WARN 阈值 |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

## Outputs

- `routed_lohmann.<fmt>`：`time`、`routed_direct`、`routed_base`、`Q_total`。
- `lohmann_unit_hydrographs.json`：参数、河长、96 天河道 UH、107 步合成 UH（direct/base）、12 天 HRU UH。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance。

## 与马斯京根的选型对照

| 维度 | `route-lohmann-channel`（本 Skill） | `route-muskingum-channel` |
|---|---|---|
| 时间尺度 | 日过程（UH 内部小时积分聚合 96 天） | 任意步长（K 与 dt 直接耦合） |
| 方法 | 扩散波解析解 + 单位线卷积 | 运动波近似的马斯京根系数演算 |
| 入流 | 固定双列 direct/base（列名可配） | 任意多条入流列（route-spec 配置） |
| 空间结构 | 单河长单 UH | 单段、n 级串联、多路相加 |
| 典型场景 | 大尺度连续模拟的河道汇流 | 场次洪水演算、水库出库演进 |

## Common Mistakes

- 期待单位换算：卷积是线性系统，输出单位与入流相同；mm 进 mm 出、m³/s 进 m³/s 出。
- 期待负入流被截断：本 Skill 不静默截断，负值会按 UH 传播（有 WARN）。
- 用亚日步长得水文结论：UH 为日尺度，参数不换算，非日步长行为未经验证。
- 序列太短却纠结体积差：零初始历史 + UH 尾部截断滞留使短序列出入流缺口偏大，属预期。
- 单列入流直接调用：本 Skill 要求双列；单列请走马斯京根或先拆分量。

## 有意改变（相对源工程）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| numba `@jit` 加速 | 纯 Python + numpy + scipy | 去编译依赖 |
| `stats.gamma(N, K)`：K 落在 scipy 的 `loc` 槽位 | `stats.gamma(N, scale=K)` | scipy 签名第二位置参数是 loc 不是 scale；标准 Lohmann（Lohmann et al. 1998 / VIC 一脉）用尺度参数 |
| HRU UH 积分区间 `[24i, 24i+1]`（宽 1） | 完整日箱 `[i, i+1]`（天，i=0..11） | 宽 1 的积分窗漏掉 Gamma 密度主体，非标准 |
| UH 合成写 `uh_direct[k+u-1]`（k=u=0 时负索引回卷） | 索引 `k+u`，数组长度恰好容纳 | 负索引回卷污染数组末元素 |
| 河道响应递归读 `FR[t-L,0]`（该列从未被写入，扩散波 UH 为死代码、退化 1 天脉冲） | 网格 UH 与首日单位输入的**单次卷积**，日 UH 纵坐标和 = 1 | 递归读总响应会重复路由已路由水量并发散；标准为单次卷积 |
| 日聚合切片 `[24i−23 : 24i]`（Fortran 1 基照搬） | 0 基 `[24i−24 : 24i]` | 源码每天丢第一个小时的 UH 质量 |
| 无输出文件 | 必然写出结果表、单位线 JSON 与 result.json | 结果必须可审计 |
| 缺测不处理 | 缺测即 FAIL（退出码 2） | 掩盖缺测会伪造演算 |

## Related Skills

- `hydrological-modeling/route-muskingum-channel`：并列的场次尺度汇流算法。
- `hydrological-modeling/run-lumped-sacsma-model`：`SURF`/`BASE` 输出可直接作本 Skill 入流。
- `model-calibration/calibrate-model-de`：N/K/VELO/DIFF 的搜索不属于本 Skill。
- `evaluation-diagnostics`：Q_total 的指标评价。
