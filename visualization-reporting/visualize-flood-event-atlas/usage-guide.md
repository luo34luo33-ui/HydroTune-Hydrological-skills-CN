# 使用指南

```text
python scripts/render_flood_event_atlas.py --extract-result events/result.json \
  --rainfall basin-rainfall.csv --rainfall-metadata rainfall-metadata.json \
  --language zh --selection all --output-dir event-atlas
```

`first` 或 `largest` 模式必须同时提供 `--max-events`。每张 v2 图输出 `2400×1200 px` PNG、同布局 SVG 和 `figure.json`，采用白底、无主副标题的紧凑单幅布局。中文要求 SimSun，英文和数字要求 Times New Roman。总览流量使用确定性分箱最小—最大包络，仅改变显示密度。

雨量选项可同时省略；此时图中不画雨量，并在运行 JSON 中明确记录缺失。提供时两项缺一不可。CSV 固定列为 `time,P_mm`，每行是对应时间间隔的已确认流域平均降雨深度，时间戳必须含偏移。元数据示例见 `assets/rainfall-metadata-example.json`：

```json
{
  "spatial_scope": "basin_mean",
  "unit": "mm/step",
  "timestep_seconds": 3600,
  "timestamp_semantics": "interval_end",
  "timezone": "Asia/Shanghai"
}
```

元数据按实际资料填写，示例的小时步长不是默认值。`interval_start` 输入会显式加一个声明时间步转换为区间末端。雨量时间戳必须无重复，落在与事件成果一致的时间步网格上；允许空值、缺行、仅覆盖部分时段或仅表头，不要求覆盖完整流量序列和事件预热窗口。不自动重采样、插值或补零。空间平均必须由上游提供，不在绘图阶段猜测权重或聚合站点。

仅在有有效雨量的时步画柱，缺测处留空，实测零雨仍为有效记录。部分覆盖不会裁掉流量时间范围；某张图没有任何有效雨量时省略雨量柱和右轴，仍输出流量图。缺测步数记录在机器附件及运行 warning 中，不阻止绘图。无效时间、重复时间、非数值、负值、无穷值及时间网格错位仍报错。

总流量黑色实线，基流深灰虚线，洪峰红色三角形；不绘制直接径流填色。预热区浅灰，其他区域白色。逐场图内摘要仅含洪峰流量、总洪量和历时；总洪量显示值为原始 m³ 除以 100000000，中文单位为亿立方米，英文为 10⁸ m³。图名、原始指标和显示舍入规则保留在 `.figure.json`。

雨量柱使用浅蓝色、50% 不透明度，置于流量线下方，自上向下绘制；右侧雨量轴单位 mm，零值在上方。总览超过 1800 个时步时，将相邻时步的已观测雨量相加，末箱可较短；全缺测分箱不画柱，部分缺测分箱显示观测小计，不当作完整累计深度。分箱步数及缺测处理口径记录在图层元数据中。逐场图保留原始逐步雨量。

坐标轴标签 18 pt，刻度、图例和摘要 16 pt；图例与摘要置于主图内，并避开位于上角的洪峰。上游 warning 保留在机器附件中，图内不显示 UPSTREAM WARNING。
