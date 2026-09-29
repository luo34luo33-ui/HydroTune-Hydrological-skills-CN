---
name: calibrate-model-sce-ua
description: 适用于使用标准复形洗牌演化 SCE-UA 率定水文模型并独立验证；不适用于差分进化别名、算法排名或误差原因诊断。
metadata:
  category: model-calibration
  domains: [model-calibration, hydrological-modeling, evaluation-diagnostics]
  tool_type: python
  primary_tool: numpy
  related_skills: [visualization-reporting/visualize-model-calibration]
---

# SCE-UA 模型率定

按排序、轮转分组、有偏单纯形选择、复形内演化和重新洗牌执行参数搜索，再独立验证。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 不把差分进化或随机替换循环标记为 SCE-UA。
- 每个复形至少包含 `参数维度 + 1` 个点。
- 反射、收缩、演化步数、停滞窗口和容差全部显式配置。
- validation 不参与复形排序、停滞判断或参数选择。
