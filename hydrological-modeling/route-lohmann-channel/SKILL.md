---
name: route-lohmann-channel
description: 适用于把直接径流与基流两条入流序列用标准 Lohmann 扩散波单位线做日尺度河道汇流演算并相加得流域出口总出流；不适用于场次尺度小时演算（那是马斯京根的职责）、产流计算、参数率定或多河段并行演算。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
  tool_type: python
  primary_tool: pandas
  related_skills:
    - hydrological-modeling/route-muskingum-channel
    - hydrological-modeling/run-lumped-sacsma-model
    - model-calibration/calibrate-model-de
---

# Lohmann 河道汇流

把 `direct` 与 `base` 两条入流列分别用扩散波河道单位线与 Gamma HRU 单位线卷积，输出 `routed_direct`、`routed_base` 与相加后的 `Q_total`。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## Use When

- 大尺度（日过程）径流的河道汇流：需要线性化 Saint-Venant 扩散波解析解生成的河道单位线与 Gamma(N, K) HRU 单位线。
- 入流已按直接径流与基流两条分量给出（例如 `run-lumped-sacsma-model` 的 `SURF`/`BASE` 输出，列名可配）。
- 需要河长 `--flowlen-m` 控制河道响应（0 表示单位线退化为脉冲，即不做河道演算）。
- 需要导出单位线本身（`lohmann_unit_hydrographs.json`）用于检查或复用。

## Do Not Use When

- 场次洪水的小时尺度演算——用 `route-muskingum-channel`（单段/多级串联/多路相加）。
- 入流只有一条混合序列且不想拆分量——本 Skill 要求双列；请先由上游拆分。
- 需要产流或融雪——归入各 `run-lumped-*` 模型 Skill；SNOW-17 不属本 Skill。
- 需要搜索 N/K/VELO/DIFF 或河长——归入 `model-calibration`。
- 需要亚日尺度的水文结论——河道与 HRU 单位线均为日尺度（内部小时积分聚合 96 天），非日步长仅记 WARN 放行且参数不换算。
- 输入含缺测、时间轴不等间隔或单位未确认——先回到 `data-processing`。

## Governing Principle

1. **河道单位线**：线性化 Saint-Venant 扩散波解析解 `H = flowlen/(2t√(πt·DIFF))·exp(−((VELO·t−flowlen)²/(4·DIFF·t)))`（`pot ≤ 69` 截断）按小时步生成网格 UH，归一化后与"首个 24 小时均匀单位输入"做**单次卷积**，聚合为 96 天日 UH（纵坐标和 = 1）。
2. **HRU 单位线**：Gamma(N, scale=K) 在完整日箱 `[i, i+1]`（天）上积分得 12 天直径流 UH；基流 UH 为脉冲。
3. **合成与归一化**：HRU UH 与河道 UH 卷积（索引 `k+u`）得 107 步合成单位线，各自归一化。
4. **双分量卷积**：`routed_direct[i] = Σ uh_direct[j]·direct[i−j]`（零初始历史），base 同理；`Q_total = routed_direct + routed_base`。
5. **单位原样通过**：卷积是线性系统且单位线归一化，输入什么单位输出什么单位，不做面积/单位换算。

## 输入要求与确认闸门

- 入流表必须含时间、`direct`、`base` 三列（分量列名可配）；缺列即输入错误（退出码 1）。
- 4 个路由参数 N/K/VELO/DIFF 全部必填（源码按 `par[0..3]` 直接取值，无静默默认）；硬校验 N/K/VELO/DIFF > 0、`--flowlen-m >= 0`。
- 时间轴等间隔且与 `--timestep-hours` 一致为 FAIL 闸门；步长 ≠24h 记 WARN 放行。
- 入流缺测即 QC 失败（退出码 2）；负入流不截断，记 WARN 并声明负值会按 UH 传播。

## 决策规则

- 与马斯京根的选型：日过程、大尺度、有河长与波速参数 → 本 Skill；场次洪水、小时步、需要 n 级串联或多路相加 → `route-muskingum-channel`。
- `--flowlen-m 0` 用于"只要 HRU 单位线平滑、不要河道滞后"的退化场景。
- 序列长度接近或短于合成 UH 底长（107 步）时，出入流体积差主要来自 UH 尾部截断滞留，属预期行为。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `time_axis_regular` | 时间轴不等间隔或与声明步长不一致 | 退出码 2，不产出结果 | 先标准化时间轴 |
| `daily_timestep` | 步长 ≠ 24 小时 | warning（非 FAIL） | 明知未验证仍继续时自行承担；日结论请改日步长 |
| `no_missing_inflow` | direct/base 含缺测 | 退出码 2 | 显式修复缺测 |
| `negative_inflow` | 入流含负值 | warning | 负值会按 UH 传播到出流，本 Skill 不静默截断 |
| `uh_normalization` | 任一单位线纵坐标和偏离 1 | 退出码 2 | 不应发生；出现即实现缺陷 |
| `finite_outflow` | 出流出现 NaN/Inf | 退出码 2 | 检查参数量级 |
| `volume_difference` | 出入流体积相对差超容差 | warning（含 UH 尾部截断滞留说明） | 延长序列或调整容差；短序列属预期 |

## 结果解释边界

- `Q_total` 是流域出口总出流，单位与入流一致；`routed_direct`/`routed_base` 可分开评价。
- 单位线导出物（`lohmann_unit_hydrographs.json`）含 96 天河道 UH、107 步合成 UH 与 12 天 HRU UH，可直接核查形状与峰值。
- 本 Skill 不输出参数是否"合理"的判断——源码未定义范围，合理性由使用者判断。

## 下一步

- 指标评价：`evaluation-diagnostics`。
- 参数搜索：`model-calibration`。
- 并列汇流算法：`route-muskingum-channel`。
