# Extract DEM Stream Network 使用指南

## Quick Start

```bash
python extract_dem_stream_network.py --analysis-dem prepared/dem_analysis_buffer.tif --stream-init-area-km2 25 --output-dir hydrology
```

`--stream-init-area-km2` 没有默认值。应依据流域尺度、DEM 分辨率和目标空间离散程度获得用户确认。`--whitebox-dir` 仅在需要指定 WhiteboxTools 可执行目录时使用。

## 固定处理链

1. Fill Depressions，并处理平坦区。
2. 非 ESRI D8 Pointer。
3. 以 cells 输出 D8 Flow Accumulation。
4. 按确认面积阈值提取河网。
5. Stream Link Identifier。
6. Strahler Stream Order。
7. 河网矢量化并规范为 GeoPackage。
8. Subbasins。

## 输出

输出文件依次为 `dem_filled.tif`、`d8_pointer.tif`、`flow_accumulation_cells.tif`、`streams.tif`、`stream_links.tif`、`strahler_order.tif`、`streams.gpkg`、`subbasins.tif` 和 `result.json`。

## Failure Handling

命令失败时先检查投影、NoData、汇流累积和面积到像元数的换算。不得通过极小阈值掩盖 CRS 或数据覆盖问题。
