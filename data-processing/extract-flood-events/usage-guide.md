# 使用指南

```text
python scripts/extract_flood_events.py --baseflow-result components/result.json \
  --config scripts/flood_event_config.source-equivalent.yaml \
  --rainfall basin_rainfall.csv --rainfall-metadata rainfall_metadata.json \
  --output-format csv --per-event-format csv --output-dir events
```

默认表格格式和逐场格式均为 CSV。可显式选择 Parquet、XLSX，或用 `--per-event-format none` 关闭逐场文件。按峰值日期命名时，同日多事件自动附加稳定序号。

YAML 中的峰值、边界、合并、过滤、warm-up 和 rainfall_support 字段全部必需。规模阈值可显式设为 `null`；脚本不会根据数据自动放宽阈值。`shape_filter` 区块及其字段可省略，脚本使用下面列明的推荐值，并在导出的配置和运行结果中记录实际门槛。

## 提取前询问

每个新提取任务开始时，分别询问用户：

- “实测没有降水的洪水是否保留？”对应 `rainfall_support.no_rainfall_policy`。
- “降雨资料全缺失的洪水是否保留？”对应 `rainfall_support.missing_rainfall_policy`。

两个选项均推荐 `exclude`（剔除），也可明确选择 `keep`（保留）。当前任务已有明确指令或用户已确认的配置时不重复询问；不要把复制示例配置视为用户已经确认。脚本不交互读取 stdin，缺少任一策略直接报错，实际选择记录在配置、审计表和结果中。

## 从旧配置迁移

将配置 `schema_version` 改为 `"1.2"`，保留原流量参数，并显式提供：

```yaml
rainfall_support:
  lookback_hours: 24
  min_observed_rainfall_mm: 5
  no_rainfall_policy: exclude
  missing_rainfall_policy: exclude
shape_filter:
  min_plateau_duration_hours: 24
  min_plateau_fraction: 0.5
  plateau_range_fraction: 0.005
  min_jump_fraction: 0.1
  jump_window_steps: 2
```

前溯小时数和累计雨量门槛仅用于演示，正式运行必须确认目标流域参数；前溯小时数可以为零，雨量门槛必须大于零。两个 `exclude` 是供用户选择的推荐值，不能替代询问。形态参数是可配置的保守推荐值。旧版 `1.0/1.1` 配置报迁移错误。运行结果与数据契约仍使用公共 `1.0` 格式，只有事件配置升级至 `1.2`。

## 突跳长直线形态过滤

边界收紧、规模过滤之后，对事件本体 `[start_idx,end_idx]` 的总流量检查最长连续近似恒定段，不使用 warm-up，不修改原始流量，不拆分事件。以 `max(事件总流量中位数, 1e-9 m³/s)` 为参照流量：

- 一个连续段的最大最小流量差不超过参照值的 `plateau_range_fraction`，即视为近似恒定；取最长段，并列时取最早一段。推荐容差 0.005（0.5%）。
- 最长段至少持续 `min_plateau_duration_hours`（推荐 24 小时），并占事件历时至少 `min_plateau_fraction`（推荐 0.5）。持续时间按首末时间之差计算，不按记录条数乘步长。
- 事件内相隔 1 至 `jump_window_steps` 步的总流量变化绝对值，其最大值至少为参照值的 `min_jump_fraction`（推荐 0.1）；推荐窗口 2 步，同时检查突升和突降。

上述长段历时、占比和突跳三个条件同时满足时，整场剔除，原因 `abrupt_jump_long_plateau`。即使有雨或用户选择保留无雨/全缺测场次，也不能绕过此规则。单独快速上涨、短时平顶或平缓退水不会触发这个组合规则；形态结果不用于判断是测量异常还是调度造成。

形态历时和突跳比例须为有限正数，平台占比在 `(0,1]`，波动容差在 `[0,1)`，窗口为正整数步数；可按资料精度与时间分辨率调整门槛。

## 雨量输入与时间对齐

雨量表包含 `time,P_mm`，支持 CSV、Parquet、XLSX 的首张表。例如：

```csv
time,P_mm
2020-01-01T00:00:00+08:00,0
2020-01-01T01:00:00+08:00,3
2020-01-01T02:00:00+08:00,
2020-01-01T03:00:00+08:00,2
```

元数据 JSON：

```json
{
  "spatial_scope": "basin_mean",
  "unit": "mm/step",
  "timezone": "Asia/Shanghai",
  "timestep_seconds": 3600,
  "timestamp_semantics": "interval_end"
}
```

必须提供已确认的流域平均雨量，不在本 skill 中推断站点权重。时间戳需带偏移并符合声明时区；`interval_start` 时间戳按声明步长移动至区间末端后，与流量网格对齐。不同步长或错位时间报错，不自动重采样。允许空值、缺行和仅表头的全缺测雨量表；无效时间、重复时间、非数值、负值及无穷值报错。

## 筛选规则与审计

规模过滤之后、编号导出之前，对每个候选事件记录形态证据，同时检查 `[start_time - lookback_hours, peak_time]`，累计闭区间内区间末端时间戳的有效雨量。非整步前溯时数不向外取整；前期窗口超出流量或雨量范围也不截断，未覆盖时步记为缺测。洪峰之后的雨量不计入，退水段和导出 warm-up 的缺测不作为本筛选门槛。

形态通过后，有雨、零雨交替且观测累计雨量达标的场次正常保留。局部缺测但观测累计雨量达到门槛（含等值）也可保留，标记缺测并返回 warning。

窗口完整且累计雨量为零时执行 `no_rainfall_policy`；窗口没有任何有效记录时执行 `missing_rainfall_policy`。任一策略保留时明确标注无降雨支持并返回 warning。全缺测的累计雨量为空，不写成零。少雨未达门槛、局部缺测且观测雨量不足（包括观测值全为零）仍剔除，不归为已确认无雨，不推断未观测雨量。

`rainfall_screening.csv` 保留文件名并扩展为联合审计表，记录通过规模过滤的全部候选事件，包括被形态规则剔除的场次：

- 稳定 `candidate_id`、保留事件的正式 `event_id`（剔除项为空）、事件起峰终时间和索引。
- 雨量窗口、`expected_steps/valid_steps/missing_steps`、`has_missing_rainfall`、`observed_rainfall_mm`、门槛、两个用户策略。
- `rainfall_supported` 表示实测累计雨量达标，`rainfall_accepted` 表示降雨策略接受，`rainfall_reason` 记录降雨结果。
- `shape_rejected`、参照流量、最长恒定段起止索引/时间、历时、占比、范围，以及 `max_jump_change_m3_s/max_jump_fraction/jump_window_steps`。
- 最终 `retained` 及 `reason`；形态剔除优先，降雨支持和策略接受不能覆盖形态剔除。

降雨原因是 `rainfall_supported`、`rainfall_all_missing`、`rainfall_all_zero`、`observed_rainfall_below_threshold`、`no_rainfall_kept_by_policy` 或 `missing_rainfall_kept_by_policy`；形态剔除的最终原因是 `abrupt_jump_long_plateau`。没有候选事件时仍输出全部表头。

事件汇总、过程表、连续序列标注和逐场文件只纳入保留事件，编号从 1 连续递增。`result.json` 登记雨量文件和元数据哈希、实际形态门槛、用户策略、候选/剔除/保留数量以及审计产物哈希。`shape_rejected_event_count` 统计形态剔除，`rainfall_rejected_event_count` 仅统计形态通过后被降雨规则剔除的事件，二者与最终数量相加等于通过规模过滤的数量；`policy_retained_event_count` 统计按用户策略保留且无降雨支持的场次。全剔除时输出空事件表并返回 warning，退出码仍为 0。

满足降雨支持条件不证明洪水成因，也不代表预热期及整场 forcing 完整；上游出库不能冒充降雨支持。按用户选择保留无雨或全缺测场次时不额外推断成因。下游建模仍需检查完整 forcing；图册降雨柱叠加允许局部缺测与部分覆盖，仅绘制已有雨量，不填零或插补。
