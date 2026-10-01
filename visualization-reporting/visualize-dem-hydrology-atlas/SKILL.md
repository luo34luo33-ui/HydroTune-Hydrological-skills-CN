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

把已经通过上游契约检查的 DEM、汇流累积、河网等级与子流域渲染为固定的 `hydrotune.dem-hydrology-atlas.v2` 图册。所有地图内容按真实流域边界裁切，仅改变显示，不改变上游成果。参数和运行方式见[使用指南](usage-guide.md)，输入输出语义见[数据契约](data-contract.yaml)。

## 决策规则

- 只读取 `prepare-dem-analysis-grid` 与可选 `extract-dem-stream-network` 的 `result.json`，并先核验所有被使用 artifact 的 SHA-256。
- 缺少提取结果时仅生成 `dem-basin-context` 和 `dem-terrain`，状态为 `warning`。
- 上游状态为 `error`、CRS 不一致、栅格网格不一致或哈希不符时停止，不生成貌似可信的图。
- `log1p` 只用于汇流累积的视觉表达，不改变原始数据，也不写回上游 artifacts。
- 中文必须使用宋体（SimSun），英文、数字及单位使用 Times New Roman；缺少规定字体时停止，不静默替换字体。

## 视觉边界

白色画布和白色主图，不绘制主副标题、状态徽标或外部页脚。原主标题作为图名保留在 `figure.json.title` 和 PNG 元数据中，稳定模板文件名保持不变。图例和放大的横向 colorbar 放在主图内，按流域覆盖率选择空白较多的位置，并避开彼此、比例尺和指北针。

画布随流域范围长宽比调整，流域四周保留适量边缘；不保留独立的标题区或图例侧栏。所有栅格图层及河网显示通过像元掩膜与矢量边界裁切，支持孔洞和多部件边界。色带显示范围仅使用流域内有效像元。脚本不接收配色或在线瓦片参数；PNG 用于报告插图，SVG 用于缩放与印刷。

主图绘制 WGS84 经纬网，以原地图 CRS 投影后显示为细灰色虚线；底边标经度、左边标纬度，使用度数和 E/W/N/S 标识。标注位置取经纬线与图框的真实交点，不把米制坐标刻度改名为经纬度。图框四周仅保留刻度所需边距。

主图外框使用纯黑色，刻度朝内；经纬度标注为 16 pt Times New Roman。指北针使用黑白双色尖箭头及 18 pt 的 N 标注。

## 完成条件

每个已生成模板都必须同时具有最长边为 2400 像素的 PNG、相同布局的可解析 SVG 与通过 schema 的 `figure.json`，其中记录实际像素尺寸；总运行结果写入 `result.json`。
