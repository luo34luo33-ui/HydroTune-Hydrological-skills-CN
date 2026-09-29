# 使用指南

```text
python scripts/calibrate_model_sce_ua.py --problem calibration-problem.json \
  --optimizer-config sce-ua-config.json --output-dir calibration-sce
```

配置必须提供 `complex_count`、`points_per_complex`、`evolution_steps`、`max_loops`、`reflection_coefficient`、`contraction_coefficient`、`stall_loops` 和 `objective_tolerance`。
