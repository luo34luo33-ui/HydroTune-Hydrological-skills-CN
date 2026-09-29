# 使用指南

```text
python scripts/calibrate_model_ga.py --problem calibration-problem.json \
  --optimizer-config ga-config.json --output-dir calibration-ga
```

配置必须提供 `population_size`、`max_generations`、`tournament_size`、`elite_count`、`crossover_probability`、`mutation_probability` 和 `gene_mutation_probability`。变异在显式触发后对选中的基因于原边界内重新采样。
