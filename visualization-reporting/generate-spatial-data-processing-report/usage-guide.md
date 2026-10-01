# 使用指南

```text
python scripts/generate_spatial_data_processing_report.py --spatial-manifest spatial-processing-manifest.json --output-dir report
```

加 `--overwrite` 可替换已声明产物；禁止覆盖输入目录，保留其他用户文件。JSON/CSV 使用基础依赖，XLSX/Parquet 使用 timeseries 可选依赖；图表需要 matplotlib，显著峰需要 scipy。无需远程模型 API。

清单使用 schema_version: "1.0"，包含 title、language（zh/en，默认 zh）、required_sources 和 sources。sources 的 id 唯一，result 路径相对清单目录；阶段为 spatial 或 atlas。旧处理清单中的空间来源仍可读取，但不会进入时序业务汇总，请单独生成空间报告。

至少声明并要求一个 build-hydrological-topology 来源。读取其 subbasins_table、reaches_table、topology 表；独立验证须关联同一构建结果，不能使用另一套拓扑的验证记录。

报告包括流域已有属性、子流域数量和面积分布、河段数量和长度、源头与出口、汇合节点、拓扑层级及单元映射。优先复用图册地图，生成面积分布和连接示意图。河段超过六十条时正文用层级概览，完整连接图留在 figures 附件中。

输出 subbasins.csv、reaches.csv、topology.csv，子流域面积使用上游栅格口径，不新增无依据的边界面积、高程或坡度。

共有产物：report.md、report.json（2.0）、summary.csv（业务统计）、analysis-tables.json、figures/；后台产物为 evidence-index.json/.csv、data-gaps.json/.csv 和 result.json。sources、配置和原始值保留在 JSON，正文不展示核验台账。运行失败只输出失败状态与资料缺口，success/warning 退出 0，失败退出 1。

审查使用对应清单参数：
```text
python scripts/review_hydrological_study_report.py --spatial-manifest spatial-processing-manifest.json --report-dir report --output-dir review
```
独立 Agent 语义审查完成后才能整体通过。

已有 DEM 栅格时可用 rasterio 读取网格分辨率与 CRS（安装 spatial 可选依赖），不从 DEM 新增高程/坡度统计。没有栅格读取依赖时保留相应限制，不影响 CSV 拓扑汇总。
