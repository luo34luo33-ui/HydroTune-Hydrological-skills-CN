# post-processing（后处理）

本分类承载模型运行后的输出整理、事件提取、统计聚合、派生变量计算、机器学习校正，以及供评价、绘图和报告消费的时空结果加工。

## 包含项

- 模拟结果的格式标准化与整理
- 模拟后事件切片、统计聚合与派生变量构造
- 基于观测残差的数值校正（后端可替换的机器学习回归）
- 基于历史观测训练的多模型出口流量概率 BMA 集合
- 面向评价、可视化和报告的数据准备

## 排除项

- 原始观测数据的首次整理和洪水事件识别归入 `data-processing`
- 指标解释、误差模式和问题归因归入 `evaluation-diagnostics`
- 图表和报告的最终表达归入 `visualization-reporting`
- 模型结构与前向运行归入 `hydrological-modeling`
- 参数优化归入 `model-calibration`

## 本分类的 Atomic Skill

| Skill | 职责 | 输出 |
|---|---|---|
| `correct-residual-with-ml` | 在基准模拟序列上学习残差（`observed - base`）并回代得到校正序列（`base + predicted_residual`）；后端可替换为多种回归模型，不绑定 XGBoost | 校正序列表、模型卡与序列化模型 |
| `align-observed-simulated-discharge` | 核验观测与模型出口成果，逐时步或逐场次精确对齐，剔除预热 | 并排对齐表、评价表与匹配 QC |
| `ensemble-discharge-with-bma` | 对多个已有出口模拟训练零点删失高斯概率 BMA，独立应用到未来连续期或完整洪水场次 | 集合器、模型卡、拟合诊断、均值／分位数／零概率及成员分布表 |

### 使用约束

- 残差定义固定为 `observed - base`，回代固定为 `base + predicted_residual`。
- 滞后特征只能取当前及过去，负步数会被拒绝。
- 机器学习残差校正训练必须显式给出随机种子与超参，示例文件只用于复现源工程行为。
- 输入缺测默认停止；滞后构造造成的不完整行只能丢弃或显式填充，不得静默填 0。
- 校正后出现负值只告警不裁剪，下限策略由调用方决定。
- BMA 使用混合似然估计权重、线性偏差与误差尺度；应用期无观测，不重跑模型或用 NSE-softmax 代替。
- BMA 必须保持固定成员与同一出口、步长和时区；缺测或覆盖不一致停止，应用有效时步必须晚于训练且不得复用训练场次。

## 交叉边界

- 与 `evaluation-diagnostics`：本分类产出被评价的序列，不评价好坏。
- 与 `hydrological-modeling`：本分类消费模型输出，不改动模型结构或参数。
- 与 `data-processing`：原始观测的首次整理不属于本分类。
