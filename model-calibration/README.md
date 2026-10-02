# model-calibration（模型率定）

本分类承载参数边界、目标函数、优化器、率定与验证划分、参数可辨识性、敏感性和不确定性证据。

## 边界

- 不含参数搜索的前向模拟归入 `hydrological-modeling`。
- 对最终模拟结果的误差模式解释归入 `evaluation-diagnostics`。
- 参数和优化设置必须来自确定性 artifact、率定结果或用户确认，不得静默猜测。

## 率定模式确认

全部五个率定 Skill 在执行前，都必须获得用户对 `continuous`（连续时序）或 `event_collection`（逐场洪水集合）的明确选择。配置已有 `data_shape` 也不能替代用户确认；未确认时只能检查资料和准备方案，不得启动参数搜索或调用率定评估器。

当前任务中用户已明确指定模式时不重复询问；同一任务、同一批数据且组织模式不变的算法切换、重试和优化配置调整可复用确认，更换数据或改变组织模式必须重新确认。用户选择须与 `problem.data_shape` 一致；冲突未解决、用户未回复或回复含糊时不得执行。事件集合不拼接成连续序列，并需明确逐场 warm-up 语义。

该确认规则约束使用 Skill 的 Agent，CLI 和成果契约保持兼容，不增加手动运行脚本的拦截。

## 当前 Skills

五个率定 Skill 使用统一问题、Python 评估器、参数边界、目标方向、随机种子和评估预算契约：

- `calibrate-model-de`：差分进化与可选受预算约束的 polish。
- `calibrate-model-ga`：锦标赛选择、单点交叉、边界内变异与精英保留。
- `calibrate-model-pso`：具有按参数跨度定义速度上限的粒子群。
- `calibrate-model-sce-ua`：复形排序、轮转分组、复形内演化和重新洗牌。
- `calibrate-model-two-stage`：dual annealing 全局搜索后接 L-BFGS-B 局部优化。

所有 Skill 只在 calibration split 搜索，锁定参数后才物化 calibration 与 validation 指标和观测模拟序列。失败评估不会用有限大数伪装为有效目标，也不会触发静默算法回退。

率定效果展示由 `visualization-reporting/visualize-model-calibration` 消费这些统一 artifacts 完成。

