---
name: prepare-dem-analysis-grid
description: 适用于将 DEM 与真实流域边界整理为米制、带外扩范围的水文分析网格；不适用于仅制作地形图、缺少可确认 CRS 或垂直单位的资料，以及河网提取本身。
metadata:
  category: spatial-analysis
  domains:
    - spatial-analysis
    - hydrological-modeling
  tool_type: python
  primary_tool: rasterio
  related_skills:
    - spatial-analysis/extract-dem-stream-network
    - spatial-analysis/build-hydrological-topology
---

# 准备 DEM 水文分析网格

在任何面积、河长或 D8 分析之前，先把 DEM、流域边界、水平 CRS 和垂直单位变成可追溯的确定性 artifacts。详细参数和命令见[使用指南](usage-guide.md)，文件语义见[数据契约](data-contract.yaml)。

## 决策规则

- 必须确认 DEM 和流域边界的 CRS；缺失 CRS 时停止，不猜测。
- DEM 非米制投影时，要求用户确认目标 CRS，或明确授权按研究区位置选择当地 UTM。
- 必须确认高程单位。输入为英尺时显式换算为米，并在结果中记录换算。
- buffer 距离由用户或已有确定性 artifact 给出，不把示例值当作默认事实。
- DEM 未完整覆盖真实流域时停止；只缺少部分外扩区时可继续，但必须返回 warning。
- 不修改原始文件，所有产物写入独立输出目录。

## 方法边界

该 Skill 只负责网格、边界、单位和覆盖范围准备。河网提取交给 `extract-dem-stream-network`，拓扑建库交给 `build-hydrological-topology`。跨 UTM 分区、极区或明显不适合 UTM 的研究区，应由用户确认更合适的米制 CRS。

## 完成条件

只有 `dem_metric.tif`、`dem_analysis_buffer.tif`、`basin_normalized.gpkg` 可读取，且 `result.json` 为 `success` 或经用户接受的 `warning`，才可进入下一步。
