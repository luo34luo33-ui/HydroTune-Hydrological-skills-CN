# 使用指南

```text
python scripts/calibrate_model_two_stage.py --problem calibration-problem.json \
  --optimizer-config two-stage-config.json --output-dir calibration-two-stage
```

配置必须显式提供两阶段评估预算和迭代上限，以及 `initial_temperature`、`restart_temperature_ratio`、`visit`、`accept` 与 `local_ftol`。局部阶段从全局阶段最佳可行参数启动。
