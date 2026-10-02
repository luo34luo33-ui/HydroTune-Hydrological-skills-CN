# 使用指南

## 率定前确认

执行率定前，必须获得用户对本次数据组织模式的明确选择；直接调用本 Skill 或从其他任务进入率定时都适用。

- 尚未明确选择时，询问：“本次率定采用连续时序 `continuous`，还是逐场洪水集合 `event_collection`？”
- `continuous` 使用连续时间序列；`event_collection` 按洪水事件组织，不将不同事件拼接成连续序列，需明确逐场 warm-up 语义。
- 配置中的 `data_shape`、文件名、已有事件提取成果或 Agent 推断只能作为推荐依据，不能替代用户确认。
- 用户在当前任务中已明确指定模式，例如“用连续模式率定”，即视为完成确认，不重复询问。
- 同一任务、同一批数据且组织模式不变时，切换算法、重试或调整优化配置可复用确认；更换数据或改变组织模式时重新确认。
- 确认前可以读取资料、检查配置和准备执行方案，但不得启动参数搜索或调用率定评估器。
- 执行前核对用户选择与 `problem.data_shape` 一致；有冲突时先说明并解决，不能静默沿用旧配置。用户未回复或回复含糊时，不启动率定。

此规则约束使用 Skill 的 Agent；不改变 CLI 参数或问题文件契约，不对用户直接手动执行 CLI 增加拦截。

## 执行命令

```text
python scripts/calibrate_model_de.py --problem calibration-problem.json \
  --optimizer-config de-config.json --output-dir calibration-de
```

配置必须提供 `population_multiplier`、`mutation_factor`、`crossover_probability`、`max_generations`、`tolerance` 和 `polish`。`population_multiplier` 使用 SciPy 的“维度倍数”语义，不等同于绝对种群数量。所有 polish 评估也计入 `max_evaluations`。

问题文件引用的适配器必须实现 `create_evaluator()`，其对象实现 `evaluate()` 与 `materialize()`。事件集合必须具有已确认 warm-up 语义，且 validation 只在最优参数锁定后执行。
