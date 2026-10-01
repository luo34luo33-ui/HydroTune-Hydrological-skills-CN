# 使用指南

## 命令

```text
python scripts/render_hydrobase_atlas.py \
  --build-result PATH \
  --validation-result PATH \
  [--language zh|en] \
  --output-dir PATH \
  [--overwrite]
```

`--language` 默认为 `zh`。输出目录非空时默认拒绝写入；`--overwrite` 只替换本 Skill 声明的输出。

## 固定模板

- `hydrobase-topology`：子流域分区、Strahler 分级河网、流向、主要标签与出口。
- `hydrobase-morphometry`：子流域面积、河段坡度和拓扑层级三联图。
- `hydrobase-qc-dashboard`：单主图与图内 PASS/WARN/FAIL 汇总，仅存在 WARN/FAIL 时显示重要证据。

validation 状态为 `warning` 时看板检查汇总中显示 warning 状态，拓扑图与形态图将 warning 保留在元数据。存在科学 QC `FAIL` 时只输出失败 dashboard，方便报告失败证据但防止把其余成果误当作可发布结果。

## 解释边界

本 Skill 不建立新拓扑、不重算形态指标、不改变出口，也不解释失败的成因。需要技术检查时使用 `validate-hydrological-topology`；需要修复数据时回到对应 spatial-analysis Skill。

## 形态三联图 v2

`hydrobase-morphometry` 为白底论文版式，去除整图主副标题及运行状态徽标；三个子图下方居中标注 `(a) 子流域面积（km²）`、`(b) 河段坡度（%）`、`(c) 拓扑层级`。中文宋体，数字英文 Times New Roman，放大图名、经纬刻度和色标刻度。三个子图均为黑色实线边框、投影后的 WGS84 经纬网及真实经纬刻度，叠加真实流域边界、米制比例尺和黑白指北针。

宽度为 2400 像素，高度按流域纵横比在 760–1000 像素之间自适应，常见流域约为 3:1；地图保持等比例。面积采用 viridis，层级采用离散 plasma，坡度由深蓝经浅色过渡到深红。坡度沿用原有 98 分位显示上限，超过上限的河段用色标最深红色表示，色标端部三角明确标记溢出，不修改原始坡度。

边界来自 `build_result.inputs.basin_boundary`，按构建清单的图层和 CRS 读取，不以子流域栅格外框代替真实轮廓。显示图层裁切到该边界，源数据保持原样。元数据记录边界来源、色标口径和实际输出尺寸。拓扑层级与 Strahler 等级分别解释。QC 看板与拓扑图采用相同白底地图版式。

## 水文拓扑图 v2

拓扑图采用白底紧凑单主图；主副标题、外部图例和 CRS 页脚移至机器附件，图例在主图内空白角落放大显示。中文宋体，数字英文 Times New Roman，黑色实线外框，向内刻度，放大真实经纬刻度。黑白指北针、比例尺和真实流域边界保留在主图内；既有子流域栅格 ID 的显示边界用浅灰线绘制。空白处标注子流域表中唯一 `sub_id` 的数量，不以河段数量代替子流域数量。地图显示裁切到真实流域边界，不修改上游数据。

最长边 2400 像素，横纵比例随流域形状自适应，仅保留经纬标签所需边缘；地图保持等比例。中文文件名为 `HydroBase 水文拓扑.png/.svg/.figure.json`，英文为 `HydroBase hydrological topology.png/.svg/.figure.json`；稳定的 result artifact 键仍为 `hydrobase-topology_*`。读取者通过 result 的 artifact 引用定位文件。`--overwrite` 清理本模板新旧文件名，不清理其他用户文件。

## 质量控制看板 v2

`hydrobase-qc-dashboard` 使用与拓扑图相同的紧凑主图、字体、经纬网、比例尺、黑白指北针和边界风格。主图空白角落展示已有检查的 PASS/WARN/FAIL 数量，无外部证据面板、无主副标题、无图内限制说明。没有 WARN/FAIL 时完全省略重要证据和“未记录 WARN 或 FAIL”文字；存在异常时在另一空白角落显示前六条既有证据，完整记录仍保留在上游附件中。科学 QC FAIL 时检查汇总中明确显示红色 QC FAILED，仍只生成失败看板并返回 2。文件名保持 `hydrobase-qc-dashboard.*`，最长边为 2400 像素，高宽随流域形状自适应。
