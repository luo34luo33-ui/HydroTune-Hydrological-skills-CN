# 使用指南

```text
python scripts/calibrate_model_de.py --problem calibration-problem.json \
  --optimizer-config de-config.json --output-dir calibration-de
```

配置必须提供 `population_multiplier`、`mutation_factor`、`crossover_probability`、`max_generations`、`tolerance` 和 `polish`。`population_multiplier` 使用 SciPy 的“维度倍数”语义，不等同于绝对种群数量。所有 polish 评估也计入 `max_evaluations`。

问题文件引用的适配器必须实现 `create_evaluator()`，其对象实现 `evaluate()` 与 `materialize()`。事件集合必须具有已确认 warm-up 语义，且 validation 只在最优参数锁定后执行。
