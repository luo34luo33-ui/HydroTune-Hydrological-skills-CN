# Prepare DEM Analysis Grid 使用指南

## Prerequisites

- 单波段 DEM，具有有效 CRS。
- 一个或多个有效流域多边形，具有有效 CRS。
- 已确认的高程单位、分析 buffer 距离，以及必要时的目标米制 CRS。
- Python 3.10+ 和项目 `spatial` 可选依赖。

## Quick Start

```bash
python prepare_dem_analysis_grid.py --dem input.tif --basin basin.gpkg --analysis-buffer-km 3 --vertical-unit m --auto-utm --output-dir prepared
```

若 DEM 已是米制投影，可省略 `--target-crs` 和 `--auto-utm`。若两者同时提供，命令会拒绝执行。

## 输出与状态

- `dem_metric.tif`：米制水平 CRS、米制高程、Float32 的标准 DEM。
- `dem_analysis_buffer.tif`：按真实边界外扩后裁出的分析 DEM。
- `basin_normalized.gpkg`：修复、合并并投影到分析 CRS 的真实流域边界。
- `result.json`：输入哈希、参数、覆盖检查、工具版本和输出哈希。

真实流域没有被 DEM 完整覆盖是 error。真实流域完整、但外扩范围不完整是 warning；后续拓扑的边界出口可能不可靠。

## Common Mistakes

- 不要根据文件名猜测 CRS 或高程单位。
- 不要把地理坐标的度当成米计算 buffer 或面积。
- 不要用 nearest 重采样连续高程；脚本对 DEM 重投影使用 bilinear。
- 不要把自动 UTM 当作跨分区大流域的普遍最佳方案。
