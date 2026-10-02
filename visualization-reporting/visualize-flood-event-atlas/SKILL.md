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

读取已验证的事件汇总、过程和完整序列，以及可选的已确认流域平均雨量，生成论文插图风格的 `hydrotune.flood-event-atlas.v2` 总览与逐场图。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## 决策规则

- 先验证 extract result、artifact 哈希和表间事件一致性。
- 只展示已有边界和指标，不在绘图阶段重新计算或修正。
- 默认绘制全部事件；`first` 和 `largest` 必须显式给出最大数量。
- 上游 warning 保留在运行状态和机器附件中，不绘制标题、UPSTREAM WARNING 或其他徽标；error 或哈希错误时不生成图。
- 画布、字体、配色和布局固定，不接受任意主题参数。

## 绘图规则

- 白底单幅主图，删除外置摘要卡片和主副标题，图名仅保留在元数据中。只留坐标轴及刻度所需边距。
- 总流量为黑色实线，基流为深灰虚线，洪峰为红色三角形；不绘制或标注直接径流。预热区浅灰，事件区白色。
- 逐场摘要位于主图上角，仅展示已有洪峰流量、总洪量和历时；总洪量除以 100000000 后显示为亿立方米，原值在图件元数据的指标图层记录中保留。
- 中文用宋体 SimSun，英文和数字用 Times New Roman；坐标轴标签 18 pt、刻度和图例 16 pt。缺少规定字体时停止。
- 雨量必须是调用方已经确认的流域平均雨量。不得将站点雨量简单平均或把某个站点当作流域平均值。需要聚合时先在上游完成并记录权重。
- 同时提供雨量 CSV 和元数据后，按明确的 mm/step、时区和区间含义对齐；不填零、插值或改变雨量时间步。允许把明确的区间起点转换为同一间隔的末端。
- 雨量叠加不要求完整、无缺测覆盖流量或预热时段。允许空值、缺行和部分时段覆盖，按已有雨量绘制，缺测处留空；零雨仍为有效记录。绘图区间全无有效雨量时省略雨量柱和右轴，缺测情况记入附件，不阻止流量图输出。
- 雨量柱自上向下，右侧倒置雨量轴，浅蓝色、50% 不透明度，图层位于流量线下方。缺少雨量时省略雨量柱和右轴，仅在附件记录 warning。
- 总览雨量过密时将相邻时步的已观测雨量相加，缺测不按零处理，全缺测分箱不画柱；部分缺测分箱仅代表观测小计，并在元数据记录分箱与缺测口径。逐场图保留逐步雨量。
