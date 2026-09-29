---
name: calibrate-model-ga
description: 适用于使用带锦标赛选择和精英保留的遗传算法率定水文模型并独立验证；不适用于模型结构选择、算法比较或误差原因诊断。
metadata:
  category: model-calibration
  domains: [model-calibration, hydrological-modeling, evaluation-diagnostics]
  tool_type: python
  primary_tool: numpy
  related_skills: [visualization-reporting/visualize-model-calibration]
---

# 遗传算法模型率定

使用显式种群、选择、交叉和变异配置搜索 calibration split；锁定最佳参数后才运行 validation。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- seed、预算和全部 GA 旋钮必须来自版本化配置。
- 单参数问题跳过单点交叉，不构造无效交叉点。
- 精英保留不免除重新评估预算；validation 永不参与适应度。
- 失败评估可追溯，超过明确上限时停止，不自动换算法。
