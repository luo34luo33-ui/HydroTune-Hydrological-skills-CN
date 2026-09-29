---
name: calibrate-model-two-stage
description: 适用于使用模拟退火全局搜索后接 L-BFGS-B 局部优化率定水文模型并独立验证；不适用于不可导约束解释、算法排名或误差归因。
metadata:
  category: model-calibration
  domains: [model-calibration, hydrological-modeling, evaluation-diagnostics]
  tool_type: python
  primary_tool: scipy
  related_skills: [visualization-reporting/visualize-model-calibration]
---

# 两阶段模型率定

先以 dual annealing 搜索全局区域，再从其最佳可行解启动 L-BFGS-B；两阶段完成后锁定参数并独立验证。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 两阶段预算之和不得超过全局 `max_evaluations`。
- 不按参数维度静默改变迭代或预算。
- 局部阶段失败时可保留退火阶段的可行最佳解，但必须记录 termination/warning。
- validation 不参与任一阶段的目标函数。
