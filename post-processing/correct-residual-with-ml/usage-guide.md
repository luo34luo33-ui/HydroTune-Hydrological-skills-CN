# 机器学习残差校正 使用指南

## Overview

`correct-residual-with-ml` 在基准模拟序列上学习残差（`observed - base`）并回代得到校正序列（`base + predicted_residual`）。后端通过工厂注入，可替换为多种回归模型，本 Skill 不绑定 XGBoost。

安装后本文件位于 `references/usage-guide.md`，脚本位于 `scripts/`。

## Prerequisites

- Python 3.10+
- numpy、pandas、scikit-learn、joblib（仓库 `ml` 可选依赖组）
- 可选：xgboost（仓库 `ml-xgboost` 可选依赖组），仅在 `--backend xgboost` 时需要
- 输入表：时间列、基准列、观测列（train 必填）、特征列

## Typical User Requests

- "用随机森林把新安江残差学一遍再回代。"
- "换成梯度提升，比较两种后端。"
- "用训练好的模型对新序列做残差校正。"

## Quick Start

训练：

```bash
python scripts/correct_residual_ml.py \
  --mode train \
  --table train_table.csv \
  --time-column time --base-column Q_total --observed-column Q_obs \
  --feature-columns P,Qt \
  --lag-spec "residual:1,2,3,4,5" \
  --backend random-forest \
  --hyperparameters '{"n_estimators": 200, "max_depth": 5}' \
  --random-state 2025 --split chronological \
  --output-dir ./ml_train
```

预测：

```bash
python scripts/correct_residual_ml.py \
  --mode predict \
  --table new_table.csv \
  --model-in ./ml_train/model.joblib \
  --output-dir ./ml_predict
```

只查看参数说明不需要真实输入：`python scripts/correct_residual_ml.py --help`。

## What the Agent Will Do

1. 校验后端、超参、随机种子与滞后配置；必须包含残差滞后 1 至 5，禁止当前 `residual`、`residual:0` 与负步数。
2. 读取输入表并校验必需列；predict 模式从模型卡读取特征列与滞后配置并校验一致性。
3. 有观测时先计算残差 `observed - base`；无观测时校验输入历史 `residual` 列存在。
4. 构造滞后特征；不完整行按 `--lag-policy` 丢弃、显式填充或拒绝。输入缺测按 `--missing-policy` 停止或丢行。
5. train：划分训练/测试集、拟合、写出模型与模型卡；predict：加载模型并预测。
6. 回代 `corrected = base + predicted_residual`，执行 QC 并写出校正表与 `result.json`。
7. 预测残差非有限时不产出校正表，退出码 2。

## 后端

| `--backend` | 估计器 | 依赖 |
|---|---|---|
| `gradient-boosting` | `GradientBoostingRegressor` | scikit-learn |
| `random-forest` | `RandomForestRegressor` | scikit-learn |
| `extra-trees` | `ExtraTreesRegressor` | scikit-learn |
| `linear` | `LinearRegression` | scikit-learn |
| `ridge` | `Ridge` | scikit-learn |
| `xgboost` | `XGBRegressor` | xgboost（可选，缺失时报可操作错误并退出码 1） |

`linear` 与 `ridge` 不接受 `random_state`，工厂会自动剔除该键；其余后端会把 `--random-state` 作为 `random_state` 传入。

## Inputs

| 参数 | 必填 | 说明 |
|---|---|---|
| `--mode` | 是 | `train` 或 `predict` |
| `--table` | 是 | 输入表 |
| `--base-column` | 否 | 默认 `Q_total` |
| `--observed-column` | train 是 | 默认 `Q_obs` |
| `--feature-columns` | train 是 | 逗号分隔；predict 从模型卡读取 |
| `--lag-spec` | train 是 | `COLUMN:STEPS`，可重复；必须包含 `residual:1,2,3,4,5`；predict 沿用模型卡；其他变量步数非负 |
| `--lag-policy` | 否 | `drop`（默认）、`fill`、`fail` |
| `--lag-fill-value` | `fill` 时是 | 显式填充值 |
| `--backend` | 是 | 见后端表 |
| `--hyperparameters` | 是 | JSON 字符串或 JSON 文件路径，允许 `{}` |
| `--random-state` | train 是 | 保证可复现 |
| `--split` | 否 | `random`（默认，源码等价）或 `chronological` |
| `--test-size` | 否 | 默认 0.2，来源为源码默认参数，非通用推荐 |
| `--min-train-samples` | 否 | 默认 10 |
| `--missing-policy` | 否 | `fail`（默认）或 `drop-rows` |
| `--model-out` / `--model-in` | 见模式 | 模型路径，必须位于输出目录内 |
| `--output-column` | 否 | 默认 `corrected` |
| `--output-format` | 否 | `csv`（默认）、`parquet`、`xlsx` |
| `--output-dir` | 是 | 唯一写入位置，非空需 `--overwrite` |

必需配置为 `--lag-spec "residual:1,2,3,4,5"`，生成 `residual_lag_1`…`residual_lag_5`，分别对应 `residual(t-1)` 至 `residual(t-5)`。步数按输入序列的时间步计算；输入需按时间顺序排列且步长规则。可附加其他合法特征，如 `--lag-spec "P:5"`，但不得省略任一必需残差滞后，也不得将当前 `residual` 或 `residual:0` 输入模型。

`residual` 在有观测列时由本 Skill 用 `observed - base` 计算，在构造滞后特征之前生成。predict 模式无观测列时，输入表必须自带由已可用历史观测与基准模拟得到的同定义 `residual` 列；每个预测时刻所需的前 5 步残差必须已经可用，不得引入尚不可用的观测。缺少历史残差来源时停止，不自动以预测残差替代；本脚本不执行递推预测。

默认 `--lag-policy drop` 在输入完整且仅使用必需滞后时丢弃开头 5 行，并记录丢弃数量；附加更长滞后时可能丢弃更多行。predict 同样校验模型卡是否包含完整残差滞后，旧模型不满足要求时拒绝使用并提示重新训练。

## Outputs

- `corrected_series.<fmt>`：时间列、`base`、`observed`（若有）、`residual`、`predicted_residual`、`corrected` 以及全部使用的特征列（含滞后列）。
- `model_card.json`（train）：后端、超参、`random_state`、划分方式、特征列、滞后配置、残差定义、训练/测试行数与指标、后端版本。
- `model.joblib`（train）：序列化模型。
- `result.json`：参数、输入与产物 SHA-256、checks、warnings、provenance（含 scikit-learn 与 joblib 版本）。

## Common Mistakes

- 用 `random` 划分下结论：残差强自相关，随机划分会高估泛化能力，正式结论用 `chronological`。
- 把 `source_equivalent_hyperparameters.json` 当推荐超参：它只用于复现源 notebook 的 XGBoost 调用。
- 期待自动填充缺测：默认停止；`drop-rows` 也只处理输入缺测，滞后不完整行由 `--lag-policy` 单独决定。
- 单独拷贝 `model.joblib`：模型卡记录特征列与滞后配置，缺了它 predict 会拒绝。
- 用负 lag 引入未来信息：会被直接拒绝。
- 省略残差滞后 1 至 5 或输入当前残差：会被直接拒绝；这 5 个历史残差是必需输入。
- 期待本 Skill 评价好坏：评价属于 `evaluation-diagnostics`。

## 有意改变（相对源 notebook）

| 源行为 | 本 Skill | 原因 |
|---|---|---|
| 直接 `XGBRegressor`，导入即依赖 xgboost | 后端工厂，xgboost 惰性导入且可选 | 用户要求不绑定 XGB |
| `fillna(0)` 填充滞后与特征 | 缺测默认停止；滞后不完整行可丢弃或显式填充 | 填 0 会伪造残差结构 |
| `random_state=2025` 写在调用里 | `--random-state` 必填并写入模型卡 | 可复现性必须由调用方声明 |
| `train_test_split` 随机划分 | 保留 `random` 为默认，同时提供 `chronological` | 随机划分对自相关残差过于乐观 |
| 特征列与滞后列写在代码里 | `--feature-columns` 与 `--lag-spec` 显式声明 | 特征口径必须可审计 |
| `model.save_model('xgboost_model.json')` 无模型卡 | 模型与模型卡配对保存 | 缺模型卡无法安全复用 |
| 预测后直接相加，无 QC | 增加有限性检查与负值告警 | 非有限预测必须显式失败 |

## Related Skills

- `hydrological-modeling/run-lumped-xaj-model`、`hydrological-modeling/route-muskingum-channel`：基准序列来源。
- `evaluation-diagnostics/compute-continuous-series-metrics`：校正效果评价。
