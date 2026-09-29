# model-calibration（模型率定）

本分类承载参数边界、目标函数、优化器、率定与验证划分、参数可辨识性、敏感性和不确定性证据。

## 边界

- 不含参数搜索的前向模拟归入 `hydrological-modeling`。
- 对最终模拟结果的误差模式解释归入 `evaluation-diagnostics`。
- 参数和优化设置必须来自确定性 artifact、率定结果或用户确认，不得静默猜测。

## 当前 Skills

五个率定 Skill 使用统一问题、Python 评估器、参数边界、目标方向、随机种子和评估预算契约：

- `calibrate-model-de`：差分进化与可选受预算约束的 polish。
- `calibrate-model-ga`：锦标赛选择、单点交叉、边界内变异与精英保留。
- `calibrate-model-pso`：具有按参数跨度定义速度上限的粒子群。
- `calibrate-model-sce-ua`：复形排序、轮转分组、复形内演化和重新洗牌。
- `calibrate-model-two-stage`：dual annealing 全局搜索后接 L-BFGS-B 局部优化。

所有 Skill 只在 calibration split 搜索，锁定参数后才物化 calibration 与 validation 指标和观测模拟序列。失败评估不会用有限大数伪装为有效目标，也不会触发静默算法回退。

率定效果展示由 `visualization-reporting/visualize-model-calibration` 消费这些统一 artifacts 完成。

