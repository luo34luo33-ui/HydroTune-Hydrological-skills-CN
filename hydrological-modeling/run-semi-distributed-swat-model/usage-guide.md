# 半分布式 SWAT 日尺度前向模拟 · 使用指南

本 Skill 消费 HydroBase 拓扑证据 + 显式 HRU 属性表 + 日尺度 forcing，在唯一出口给出日平均流量，
并提供 HRU / 子流域 / 河段三级过程与四级水量平衡审计。

上游必须是 `spatial-analysis/build-hydrological-topology` 与
`spatial-analysis/validate-hydrological-topology` 的可追溯结果，且 QC 的
`parameters.expected_outlets == 1`。本 Skill 不修补也不重算空间成果。

## 1. 输入准备

| 输入 | 必需 | 说明 |
|---|---|---|
| `--topology-result` | 是 | build 的 `result.json`，其 `artifacts` 引用 `subbasins.csv`（`sub_id, area_km2, outlet_reach_ids`）、`reaches.csv`（`reach_id, sub_id, downstream_reach_id`）、拓扑表；三者都带 SHA-256 |
| `--topology-qc-result` | 是 | 独立 validate 的 `result.json`，必须引用同一 build 且无 FAIL |
| `--hru-table` | 是 | 显式 HRU 属性表，见 `examples/example_hru_table.csv` |
| `--forcing` | 是 | `time,sub_id,P_mm,E0_mm` 长表，每个 `(time, sub_id)` 恰好一行 |
| `--params` | 是 | 模型配置 JSON/YAML，见 `examples/config_shape.json` 与 `examples/swat_model_config.schema.json` |
| `--boundary-inflow` | 否 | `time,reach_id,Q_m3s`；只能进入**承接本地产流**的河段 |
| `--output-dir` | 是 | 必须为空，或加 `--overwrite`（只删除本 Skill 声明的 7 个文件） |

HRU 表硬性约束：

- 必须包含全部必填列（缺列即报错，不做默认值填充）；
- 每个子流域至少一个 HRU，且组内 `area_km2` 之和等于 `subbasins.csv` 的 `area_km2`
  （相对容差 `1e-6`）；
- 土层按 `soil_<i>_<field>` 编号，`i` 必须自 1 连续；五字段齐备；
- 土层 `fc_mm`/`ul_mm`/`thickness_mm`/`ksat_mm_hr` 与含水层参数均为**显式属性**，
  不在 `parameter_overrides` 允许键内。

示例数值（`examples/example_hru_table.csv`）沿用 MiniSWAT-RR 的 `minimal_basin.yaml`，
只用于复现该示例行为，**不是通用水文默认值**。

## 2. 配置字段

```json
{
  "schema_version": "1.0",
  "time": {"step_seconds": 86400, "timezone": "Asia/Shanghai", "timestamp_meaning": "interval_end"},
  "swat": {
    "profile": "source_port",
    "cn_mode": "dynamic",
    "channel_profile": "linear_storage",
    "soil_slug_mm": 1000.0,
    "cn_froz": 0.000862
  },
  "initial": {"soil_fraction": 0.5},
  "reach_params_by_id": {
    "1": {"musk_k_hr": 24.0, "musk_x": 0.2, "ttime_hr": 18.0,
          "scoef": 1.0, "trans_loss_m3_day": 0.0, "evap_m3_day": 0.0}
  },
  "parameter_overrides": {},
  "warmup_steps": 30,
  "simulation": {"start": "2020-01-31T00:00:00+08:00", "end": "2020-12-31T00:00:00+08:00"}
}
```

| 字段 | 语义 |
|---|---|
| `time.step_seconds` | 只允许 `86400`；亚日尺度必须 opt-in 聚合（见第 4 节） |
| `time.timezone` | 时间戳与日历日聚合使用的时区，必须显式给出 |
| `time.timestamp_meaning` | 只允许 `interval_end`（时间戳所指区间的末端） |
| `swat.profile` | `source_port`（忠实）或 `simplified`（简化）；记录到 result.json |
| `swat.cn_mode` | `dynamic` 用 `sq_dailycn` 逐日更新曲线数；`static` 直接取 CN-II |
| `swat.channel_profile` | 默认 `linear_storage`（变储量系数）；`muskingum` 需留意第 6 节的已知坏行为 |
| `swat.soil_slug_mm` | 土壤水再分配的 slug 大小（`swr_percmain` 的 `PERCM_SLUG` 等价量），默认 1000 mm |
| `swat.cn_froz` | 冻结 CN 修正系数；V1 无雪，该分支恒不触发且**禁止覆盖** |
| `initial` | 标量广播的初始状态；`soil_fraction` 决定 `soil_st = fc × soil_fraction` |
| `reach_params_by_id` | 必须覆盖 HydroBase 全部河段且只一次 |
| `parameter_overrides` | 见下表，仅 13 个允许键，`cn_froz` 为 fixed |
| `warmup_steps` | 序列前 N 步标记为预热；`simulation.start` 必须等于第 N 步的时间戳 |
| `simulation.start/end` | 带偏移的区间末端时间 |

允许的 `parameter_overrides`（**全局广播**：同一目标全部对象取同一数值）：

| 键 | 目标 | 范围 | 状态 |
|---|---|---|---|
| `cn2` | HRU | 30–100 | faithful |
| `canmx_mm` | HRU | 0–50 | adapted |
| `brt` | HRU | 0–1 | faithful |
| `latq_co` | HRU | 0–1 | faithful |
| `lat_ttime_days` | HRU | 0.01–200 | faithful |
| `perco_lim` | HRU | 0–1 | faithful |
| `cn3_swf` | HRU | 0–1 | faithful |
| `esco` | HRU | 0–1 | faithful |
| `simplified_ep_frac` | HRU | 0–1 | **replaced**（HRU 表里的列名是 `ep_frac`） |
| `alpha_bf` | aquifer | 0.001–1 | faithful |
| `gw_seep_frac` | aquifer | 0–1 | faithful |
| `musk_k_hr` | reach | 0.1–720 | adapted |
| `musk_x` | reach | 0–0.5 | faithful |

## 3. 运行

PowerShell：

```powershell
python examples/run_semi_distributed_swat.py `
  --topology-result <build>/result.json `
  --topology-qc-result <validate>/result.json `
  --hru-table swat_hru.csv `
  --forcing forcing_daily.csv `
  --params swat_model_config.json `
  --boundary-inflow boundary.csv `
  --output-dir out/swat_daily
```

bash：

```bash
python examples/run_semi_distributed_swat.py \
  --topology-result <build>/result.json \
  --topology-qc-result <validate>/result.json \
  --hru-table swat_hru.csv \
  --forcing forcing_daily.csv \
  --params swat_model_config.json \
  --boundary-inflow boundary.csv \
  --output-dir out/swat_daily
```

`<build>` 与 `<validate>` 分别是 `spatial-analysis` 两个 Skill 的输出目录。

`--help` 不需要任何真实输入或外部工具即可运行。

## 4. 亚日尺度 forcing 的显式日聚合

默认关闭。不带 `--aggregate-to-daily` 且 `step_seconds != 86400` 时直接停止（退出码 1）——
日尺度 CN/土壤/含水层参数不允许套用到其他步长。

```powershell
python examples/run_semi_distributed_swat.py ... --aggregate-to-daily sum
```

| 取值 | 语义 |
|---|---|
| `sum` | 同日历日内各时步深度通量相加，得到日累计深度（mm/day） |
| `mean` | 同日历日内取各时步平均值再乘以每日步数（把每步值视为该步的代表深度）；当日历日步数完整时与 `sum` 数值一致，选择会在 `result.json.parameters.aggregate_to_daily` 中留痕 |

聚合只是 Skill 内的 opt-in 便利手段。若需要带面积加权的分区聚合证据，请优先使用
`spatial-analysis/aggregate-forcing-to-model-units` 在上游完成，再把日尺度结果交给本 Skill。

聚合规则（不通过即 FAIL，退出码 2）：

- `step_seconds` 必须整除 `86400`；
- 每个日历日必须包含同样的完整步数；缺步、多步或 DST 造成的 23/25 小时日一律拒绝；
- 缺测不插值、`sum` 不填零；
- 日都按声明时区划分，聚合后的时间戳取该日最后一个时步的 `interval_end`。

## 5. 输出

| 文件 | 内容 |
|---|---|
| `hru_process.csv` | 逐 HRU：`P_mm, AET_mm, surface_gen_mm, surface_mm, lateral_mm, baseflow_mm, percolation_mm, deep_seepage_mm, revap_mm, soil_water_mm, canopy_mm, aquifer_mm, cnday, storage_mm, residual_mm` |
| `subbasin_process.csv` | 逐子流域体积：`surface/lateral/baseflow_m3_day, local_inflow, boundary_inflow, upstream_inflow, outflow_m3_day, residual_m3` |
| `reach_process.csv` | 逐河段：`local_inflow, boundary_inflow, upstream_inflow, inflow, outflow, loss_m3_day, storage_m3` |
| `outlet_flow.csv` | `time, outlet_reach_id, is_warmup, outflow_m3_day, Q_m3s` |
| `water_balance.json` | 四级残差最大值、容差、流域记账量与 checks |
| `model_config.json` | 运行配置快照 |
| `result.json` | status、参数、输入/产物哈希、checks、warnings、provenance |

单位：HRU 通量为 mm/day（`mm/step`，step 为 1 日），河段/子流域为 m³/day，出口 `Q_m3s` 为 m³/s
（`outflow_m3_day / 86400`），`1 mm × 1 km² = 1000 m³`。

退出码：`0` 成功或仅 warning；`1` 输入/配置错误；`2` 科学 QC 失败。

## 6. 已知限制与有意改变

**不得宣称与 SWAT+ 数值等价。**源工程未做 Fortran/Python 数值对照，相关项一律
`NOT VERIFIED`，该声明随 `result.json.provenance.source_verification` 传播。

未实现的流域过程：融雪与冻土闸门、CN 冻结修正、城市不透水、裂隙流、Green-Ampt、瓦管流、
稻田湿地、作物生长与农业管理、灌溉、水库调度与取水、泥沙养分、含水层间侧向交换。
河道透水损失与水面蒸发保留为显式输入且默认 0，干旱区可能高估径流。

相对源工程 MiniSWAT-RR 的有意改变：

1. `parameter_overrides` 的 schema 不再依赖包外 YAML 或环境变量；
2. 预热改为「前 N 步 + `is_warmup`」（源工程是整段重复跑 N 遍）；
3. forcing/boundary 统一为 `(time, sub_id/reach_id)` 长表；源工程内部 `Boundary` 与 `P` 轴相反，
   已在 CLI 层消化；
4. 四级水量平衡容差由源工程的测试断言提升为显式 QC 检查项，超容差以退出码 2 停止；
5. 坡面滞后去掉了源工程可能凭空注入水量的 `Max(1e-6, ...)` 下限。

日尺度 Muskingum 的坏行为：`musk_k_hr < 24/(2(1-x))`（`x=0.2` 时约 15 hr）时 `C3 < 0`，
入流停止后的递推会出现负出流并被截断，河道储量滞留不释放。检测到时给出 WARN；
除明确接受该限制外请使用 `linear_storage`。

许可：`examples/_swat_core.py` 是 MiniSWAT-RR（LGPL-2.1）的派生实现。完整声明见技能根目录的
`THIRD_PARTY_NOTICES.md`，其可分发副本为 `examples/THIRD_PARTY_NOTICES.md`（安装后位于同一 Skill
的 `scripts/` 目录）；`_swat_core.py` 文件头也复述了溯源、许可与 `NOT VERIFIED` 状态。
