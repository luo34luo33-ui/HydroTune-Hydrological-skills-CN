---
name: visualize-flood-event-atlas
description: 适用于把 extract-flood-events 的既有事件 artifacts 渲染为固定中英文总览和逐场过程图；不适用于重新识别事件、修改边界、计算新指标或临时改变模板样式。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - data-processing
  tool_type: python
  primary_tool: matplotlib
  related_skills:
    - data-processing/extract-flood-events
    - data-processing/separate-baseflow-eckhardt
---

# 洪水事件图册

读取已验证的事件汇总、过程和完整序列，生成固定 `hydrotune.flood-event-atlas.v1` 总览与逐场图。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## 决策规则

- 先验证 extract result、artifact 哈希和表间事件一致性。
- 只展示已有边界和指标，不在绘图阶段重新计算或修正。
- 默认绘制全部事件；`first` 和 `largest` 必须显式给出最大数量。
- 上游 warning 原样显示，error 或哈希错误时不生成图。
- 画布、字体、配色和布局固定，不接受任意主题参数。
