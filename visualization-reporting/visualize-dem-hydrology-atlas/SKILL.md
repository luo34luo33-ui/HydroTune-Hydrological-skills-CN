---
name: visualize-dem-hydrology-atlas
description: 适用于把 HydroTune 的 DEM 准备与无蚀刻河网提取 artifacts 渲染为固定版式的中英文水文图册；不适用于修改空间数据、重新计算水文指标、在线底图制图或任意主题设计。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - spatial-analysis
  tool_type: python
  primary_tool: matplotlib
  related_skills:
    - spatial-analysis/prepare-dem-analysis-grid
    - spatial-analysis/extract-dem-stream-network
    - visualization-reporting/visualize-hydrobase-atlas
---

# DEM 水文过程图册

把已经通过上游契约检查的 DEM、分析范围、汇流累积、河网等级与子流域渲染为固定的 `hydrotune.spatial-atlas.v1` 图册。参数和运行方式见[使用指南](usage-guide.md)，输入输出语义见[数据契约](data-contract.yaml)。

## 决策规则

- 只读取 `prepare-dem-analysis-grid` 与可选 `extract-dem-stream-network` 的 `result.json`，并先核验所有被使用 artifact 的 SHA-256。
- 缺少提取结果时仅生成 `dem-basin-context` 和 `dem-terrain`，状态为 `warning`。
- 上游状态为 `error`、CRS 不一致、栅格网格不一致或哈希不符时停止，不生成貌似可信的图。
- `log1p` 只用于汇流累积的视觉表达，不改变原始数据，也不写回上游 artifacts。
- 中文模式找不到支持的 CJK 字体时停止，避免输出缺字图。

## 视觉边界

画布、色带、图层顺序、标签密度、比例尺和指北针均由固定样式资产决定。脚本不接收画布、DPI、配色或在线瓦片参数；PNG 面向屏幕和报告插图，SVG 用于缩放与印刷。

## 完成条件

每个已生成模板都必须同时具有 `2400×1600` PNG、可解析 SVG 与通过 schema 的 `figure.json`；总运行结果写入 `result.json`。
