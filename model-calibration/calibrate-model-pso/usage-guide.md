# 使用指南

```text
python scripts/calibrate_model_pso.py --problem calibration-problem.json \
  --optimizer-config pso-config.json --output-dir calibration-pso
```

配置必须提供 `particle_count`、`max_iterations`、`inertia_weight`、`cognitive_coefficient`、`social_coefficient` 和 `velocity_limit_fraction`。速度上限等于参数跨度乘以显式比例。
