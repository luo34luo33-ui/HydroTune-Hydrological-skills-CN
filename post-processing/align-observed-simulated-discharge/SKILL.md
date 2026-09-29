---
name: align-observed-simulated-discharge
description: 适用于验证观测与模型成果哈希，按时间或 event_id,time 对齐流域出口流量并剔除预热行；不适用于将河网入口 Qt 当作出口流量。
metadata:
  category: post-processing
  domains:
    - post-processing
  tool_type: python
  primary_tool: pandas
  related_skills:
    - data-processing/prepare-discharge-timeseries
    - evaluation-diagnostics/compute-continuous-series-metrics
    - evaluation-diagnostics/compute-event-flood-metrics
---

# 对齐观测与模拟出口流量

从[使用指南](usage-guide.md)确认模型适配字段后执行，输出结构见[数据契约](data-contract.yaml)。

## 决策规则

- 模型结果仅读取，按原时间精确匹配，不平移洪峰或重采样。
- XAJ 原始 `Qt` 是河网入口，不能直接评价；须提供后续路由成果及出口、m³/s 声明。
- 所有预热行在完整表保留，在评价表剔除；逐场次用 `event_id,time` 唯一键。
