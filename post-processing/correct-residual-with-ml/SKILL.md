---
name: correct-residual-with-ml
description: 适用于在已有基准模拟序列上训练并应用机器学习残差校正，后端可替换为多种回归模型；不适用于参数率定、模型结构改动、指标评价、成因解释，也不适用于用未来信息构造特征。
metadata:
  category: post-processing
  domains:
    - post-processing
    - hydrological-modeling
  tool_type: python
  primary_tool: scikit-learn
  related_skills:
    - hydrological-modeling/run-lumped-xaj-model
    - hydrological-modeling/route-muskingum-channel
    - data-processing/prepare-discharge-timeseries
    - evaluation-diagnostics/compute-continuous-series-metrics
---

# 机器学习残差校正

在基准模拟序列上学习残差并回代，得到校正序列。预测时刻 t 的残差时，输入必须包含前 5 个时步的残差 `residual(t-1)` 至 `residual(t-5)`；其他合法特征可继续使用。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。本 Skill 不绑定任何具体模型库。

## Use When

- 已有基准模拟（如新安江输出）与同期观测，需要学习系统性偏差并回代。
- 想比较多种回归后端（梯度提升、随机森林、极端随机树、线性、岭回归，或可选 xgboost）。
- 需要把残差自身或驱动量的滞后值作为特征。

## Do Not Use When

- 需要搜索水文模型参数或目标函数——归入 `model-calibration`。
- 需要评价校正效果好坏——归入 `evaluation-diagnostics`。
- 需要解释模型为何出错——本 Skill 只做数值校正，不做成因归因。
- 特征需要用到未来时刻的值——滞后步数必须非负，负值会被拒绝。
- 输入含缺测且未修复——默认停止，不静默填 0。

## Governing Principle

1. **残差定义固定**：`residual = observed - base`，符号写入模型卡与 `result.json`，不接受相反约定。
2. **回代方式固定**：`corrected = base + predicted_residual`，不直接预测流量本身。
3. **必需残差历史**：必须使用 `residual(t-1)` 至 `residual(t-5)`，即 `--lag-spec "residual:1,2,3,4,5"`；不得输入当前 `residual` 或 `residual:0`。其他变量滞后步数保持非负，负步数直接拒绝。
4. **后端可替换**：任何实现 `fit`/`predict` 的回归器都可接入，本 Skill 只依赖接口，不依赖具体库。
5. **随机性必须固定**：训练必须给出 `--random-state`，否则拒绝执行。
6. **缺测不静默**：输入缺测默认停止；滞后构造造成的不完整行默认丢弃并计数，也可以显式填充或拒绝。

## 输入要求与确认闸门

- 基准列（`--base-column`）与观测列（`--observed-column`，train 模式必填）必须存在。
- 特征列必须显式列出，不自动推断、不自动排除时间列。
- 训练滞后特征必须由 `--lag-spec` 显式声明列与步数，且包含完整残差滞后 1 至 5；步数单位为输入序列的时间步，不是固定小时或天。
- 预测沿用模型卡，并校验完整残差滞后 1 至 5；旧模型卡不满足要求时停止并提示重新训练。
- 预测历史残差必须来自已可用的历史观测与基准模拟（`observed - base`）。输入无观测列时必须提供同定义的历史 `residual` 列；缺少来源时停止，不自动以预测残差替代，也不得使用尚不可用的观测。
- 超参与 `random_state` 必须显式给出；本 Skill 不提供"推荐超参"，示例文件只用于复现源码。
- 单位与时间步长由上游契约保证，本 Skill 不换算、不校验量纲一致性。

## 决策规则

- 划分方式：`--split random` 为源码等价行为；`--split chronological` 按时间前后划分。残差通常强自相关，随机划分会高估泛化能力，正式结论应优先使用时序划分。
- 滞后不完整行默认 `drop`（仅使用必需滞后且输入完整时丢弃序列开头 5 行），可选择 `fill`（需显式给出 `--lag-fill-value`）或 `fail`。
- 校正后出现负值只告警不裁剪：裁剪会破坏水量平衡，下限策略应由调用方决定。

## QC 与失败模式

| 检查 | 触发条件 | 症状 | 修正方向 |
|---|---|---|---|
| `residual_definition` | 常驻 | PASS，记录 `observed - base` | — |
| `required_residual_lags` | 缺少任一残差滞后 1 至 5，或使用当前残差 | 退出码 1；合法配置记录 PASS | 补齐必需残差滞后，移除当前残差；旧模型重新训练 |
| `lag_nonnegative` | 步数为负 | 退出码 1，拒绝执行 | 改为非负步数，不得使用未来信息 |
| `feature_columns_present` | 特征列缺失 | 退出码 1 | 修正 `--feature-columns` |
| `train_sample_count` | 训练样本少于阈值 | warning | 增加样本或降低阈值前先确认统计意义 |
| `prediction_finite` | 预测残差含 NaN/Inf | 退出码 2，不产出校正表 | 检查特征量级与超参 |
| `corrected_negative` | 校正序列出现负值 | warning，不裁剪 | 由调用方决定下限策略 |
| `lag_rows_dropped` | 滞后构造丢弃开头行 | warning 并计数 | 提供前期历史或显式选择填充；不得省略必需残差滞后 |
| `missing_rows` | 输入缺测 | 默认退出码 1；`drop-rows` 时 warning | 回到上游修复缺测 |

## 结果解释边界

- 校正结果只表示在给定特征、后端与随机种子下的数值输出，不代表物理可解释性。
- 训练/测试指标写入模型卡，只是拟合诊断，不等于预报能力评价。
- 模型文件与模型卡必须一起保存；模型卡记录特征列、滞后配置与残差定义，缺少它无法安全复用。

## 下一步

- 评价校正效果：`evaluation-diagnostics/compute-continuous-series-metrics` 或 `compute-event-flood-metrics`。
- 若校正效果差，应回到模型结构或参数（`hydrological-modeling`、`model-calibration`），而不是无限制增加特征。
