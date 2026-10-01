# 使用指南

## 安装依赖

在仓库开发环境中安装 `visualization` 可选依赖；安装后的 Skill 自身不需要 WhiteboxTools。

中文渲染需要宋体（SimSun）和 Times New Roman，英文渲染需要 Times New Roman。中文用宋体，英文、数字和单位用 Times New Roman；缺少字体会明确停止。

## 命令

```text
python scripts/render_dem_hydrology_atlas.py \
  --prepare-result PATH \
  [--extract-result PATH] \
  [--language zh|en] \
  --output-dir PATH \
  [--overwrite]
```

`--language` 默认为 `zh`。输出目录非空时默认拒绝写入；`--overwrite` 只替换本 Skill 声明的图件和结果文件。

## 固定模板

- `dem-basin-context`：标准 DEM 在真实流域内的地形与边界，不再绘制外扩分析框。
- `dem-terrain`：分析 DEM 在真实流域内的分层设色和 hillshade。
- `flow-accumulation-network`：`log1p` 显示的汇流累积、地形阴影和提取河网。
- `stream-order-subbasins`：柔和子流域分区及 Strahler 分级河网。

等级河网按 `0.7 + 0.9 × Strahler 等级` pt 加粗显示（1—3 级分别为 1.6、2.5、3.4 pt），图例使用相同线宽。仅扩展绘图足迹并裁切回真实流域，不修改河网栅格、等级或拓扑。

四张图的图例文字统一为 16 pt，线段和色块示意符号同步放大，按实际图例尺寸选择主图内空白位置。

只有前两张依赖 prepare result；后两张要求有效 extract result。每张图生成同名 `.png`、`.svg` 和 `.figure.json`。

所有图件为白底，无主副标题或外置侧栏。原主标题仅作为图名写入 JSON 与 PNG 元数据。图例、色标、比例尺与指北针位于主图内部；色标标签为 15 pt，刻度为 13 pt。最长边为 2400 像素，另一边随流域长宽比调整，实际尺寸见 `figure.json.pixel_size`。图件显示范围为真实流域外接范围加四周 7% 留白；画布外缘为经纬度刻度保留小幅边距。地图数据严格按真实边界裁切，孔洞和流域外为白色。

主图覆盖细灰色虚线经纬网：经纬线由 WGS84 转换到地图 CRS，保持投影后的曲线形状。经度标在底边、纬度标在左边，使用十进制度与 E/W/N/S 标识，字体为 Times New Roman、16 pt，刻度朝内。主图外框为纯黑色、1.2 pt；指北针为黑白双色尖箭头，N 标注为 18 pt。间隔随范围自动选择，每个方向约 5 个刻度；当前模板不支持跨日期变更线的范围。

## 解释边界

图册用于表达已有证据，不用于判断阈值是否科学、修正流域边界、重新划分子流域或解释水文过程原因。上游 warning 会原样进入运行状态与图件 JSON，不绘制在图中。裁切仅影响显示；外扩区域仍保留在原始水文分析栅格中。
