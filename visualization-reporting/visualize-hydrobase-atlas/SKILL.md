---
name: visualize-hydrobase-atlas
description: 适用于把已构建并完成独立 QC 的 HydroBase 拓扑、形态属性和检查证据渲染为固定版式成果图；不适用于重新验证拓扑、解释失败原因、修改河网关系或制作任意主题地图。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - spatial-analysis
  tool_type: python
  primary_tool: matplotlib
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - visualization-reporting/visualize-dem-hydrology-atlas
---

# HydroBase 成果图册

使用固定 `hydrotune.spatial-atlas.v1` 模板展示 HydroBase 拓扑、形态属性和已有 QC 证据。参数和命令见[使用指南](usage-guide.md)，数据语义见[数据契约](data-contract.yaml)。

## 决策规则

- 同时要求 build result 与 validation result，并核验所有被使用 artifact 的 SHA-256。
- QC 为 `success` 时生成三张成果图；`warning` 时生成三张图并保留琥珀色标识。
- validation 中存在科学 QC `FAIL` 时只生成带红色 `QC FAILED` 标识的 dashboard，运行状态为 `error`，退出码为 `2`。
- validation 运行错误或缺少检查 artifacts 时不生成图，退出码为 `1`。
- dashboard 只陈列已有 QC 证据，不重新计算检查，也不推断失败原因。

## 视觉边界

只标注出口与主要河段，按固定防拥挤规则选择流向箭头。画布、配色、分级、边距和图例均固定；不使用在线瓦片、彩虹色带、3D 地形或运行时改色。

## 完成条件

已允许生成的每个模板都必须具有 `2400×1600` PNG、可解析 SVG 和通过 schema 的 `figure.json`，并由总 `result.json` 记录所有输出哈希与上游状态。
