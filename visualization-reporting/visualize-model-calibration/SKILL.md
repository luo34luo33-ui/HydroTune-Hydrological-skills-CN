---
name: visualize-model-calibration
description: 适用于把五种 model-calibration 结果渲染为固定双语率定总览、验证过程和观测模拟散点图；不适用于重新率定、重算指标、算法排名或误差归因。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - model-calibration
    - evaluation-diagnostics
  tool_type: python
  primary_tool: matplotlib
  related_skills:
    - model-calibration/calibrate-model-de
    - model-calibration/calibrate-model-ga
    - model-calibration/calibrate-model-pso
    - model-calibration/calibrate-model-sce-ua
    - model-calibration/calibrate-model-two-stage
---

# 模型率定效果图册

读取任一受支持率定 Skill 的哈希验证 artifacts，以固定 `hydrotune.model-calibration-atlas.v1` 模板生成总览、验证过程和散点图。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 只展示上游已有 trace、参数、指标和序列，不重算性能指标。
- continuous 与 event_collection 使用不同过程模板；事件不得拼接为连续序列。
- warm-up 样本保留并标为非 scored，散点只使用 validation 的 scored 样本。
- 字体、画布、配色、线型、DPI 和布局固定，不接受运行时主题参数。
- 上游 error、哈希错误或缺少观测模拟序列时停止；warning 必须传播到图件 metadata。
