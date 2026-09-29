---
name: calibrate-model-pso
description: 适用于使用边界约束粒子群优化率定水文模型并独立验证；不适用于模型结构选择、多算法排名或误差归因。
metadata:
  category: model-calibration
  domains: [model-calibration, hydrological-modeling, evaluation-diagnostics]
  tool_type: python
  primary_tool: numpy
  related_skills: [visualization-reporting/visualize-model-calibration]
---

# 粒子群模型率定

使用显式粒子数、惯性、认知/社会系数和速度上限，在 calibration split 搜索并独立验证。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 速度上限按每个参数的边界跨度计算且只裁剪一次。
- seed、预算与全部 PSO 旋钮必须显式提供。
- 参数越界时裁剪到已确认边界；validation 不影响个体或全局最优。
- 无可行粒子时停止，不回退到其他优化器。
