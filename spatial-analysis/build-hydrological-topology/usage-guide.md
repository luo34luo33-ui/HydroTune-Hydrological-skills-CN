# Build Hydrological Topology 使用指南

## Quick Start

```bash
python build_hydrological_topology.py \
  --basin prepared/basin_normalized.gpkg \
  --dem-filled hydrology/dem_filled.tif \
  --d8-pointer hydrology/d8_pointer.tif \
  --flow-accumulation hydrology/flow_accumulation_cells.tif \
  --stream-links hydrology/stream_links.tif \
  --stream-order hydrology/strahler_order.tif \
  --subbasins hydrology/subbasins.tif \
  --cell-inclusion center \
  --output-dir hydrobase
```

`center` 表示像元中心位于真实流域内才计入子流域面积；`all-touched` 会改变面积、河段保留和边界结果，必须记录并在项目中保持一致。边界河段长度使用 D8 像元中心连线与真实流域多边形的相交长度，因此不会把穿越边界的有效半段静默丢弃。

可重复传入 `--upstream-result`，将网格准备和河网提取阶段的 warning 原样带入 HydroBase `result.json`；任何上游 `error` 会阻止构建。

## 表语义

- `subbasins.csv`：子流域面积、像元数、河段集合和局地出口。
- `reaches.csv`：河段—子流域映射、Strahler、D8 长度、高程、坡度、上下游和拓扑层级。
- `topology.csv`：供模型路由使用的精简连接表。
- `reaches_clipped.gpkg`：真实边界内带相同属性的河段几何。

出口使用 `0`；多值列表以分号分隔。负高程步不会被伪装成正落差，而是单独计数并产生 warning。

## Failure Handling

循环、自环或非法下游引用属于拓扑失败，不能通过删除记录修补。应回查 D8 编码、网格对齐、边界覆盖、河网连续性和阈值。
