---
name: calibrate-model-de
description: 适用于使用差分进化在显式参数边界和评估预算内率定水文模型并独立验证；不适用于模型结构选择、算法横向排名或结果误差归因。
metadata:
  category: model-calibration
  domains:
    - model-calibration
    - hydrological-modeling
    - evaluation-diagnostics
  tool_type: python
  primary_tool: scipy
  related_skills:
    - visualization-reporting/visualize-model-calibration
---

# 差分进化模型率定

读取经哈希固定的率定问题、Python 评估器和完整 DE 配置，在 calibration split 搜索参数，并在锁定参数后运行 validation。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 目标方向、参数边界、seed、预算和 DE 旋钮必须显式提供。
- validation 不得参与搜索、停止判断或参数选择。
- 非有限目标记录为失败评估，不使用有限大数伪装有效结果。
- 预算耗尽但存在可行解时保留结果并报告 warning；无可行解时停止。
- 本 Skill 不自动更换优化器，也不解释率定误差原因。
