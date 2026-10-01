---
name: visualize-model-calibration
description: 适用于把五种 model-calibration 结果渲染为论文风格双语率定总览、多模拟验证过程和观测模拟散点图；不适用于重新率定、替换上游评价指标、算法排名或误差归因。
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

读取任一受支持率定 Skill 的哈希验证 artifacts，以固定 `hydrotune.model-calibration-atlas.v2` 模板生成总览、验证过程和散点图。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- 上游 trace、参数与汇总指标保持原值。过程图图例额外计算本图 scored 样本的 NSE，作为显示诊断；逐场 NSE 不冒充连续总体 NSE，不回写上游评价结果。无有效评分样本或观测常量时标注 NSE 不可用。
- continuous 与 event_collection 使用不同过程模板；事件不得拼接为连续序列。
- warm-up 样本保留并标为非 scored，散点只使用 validation 的 scored 样本。
- 所有图白底、无图名，横纵轴标题齐全；实测黑色实线，模拟按清单顺序使用不同颜色，坐标与图例放大。中文宋体，数字和英文 Times New Roman。
- 使用 `--comparison-manifest` 按用户需要叠加模型或后处理结果，使用清单中的名称作图例；必须明确验证 split、相同单位与事件/时间关联，禁止按行位置猜测关联或混入率定期。详见使用指南。
- 上游 error、哈希错误或缺少观测模拟序列时停止；warning 必须传播到图件 metadata。
