# 使用指南

```text
python scripts/generate_timeseries_data_processing_report.py --processing-manifest processing-manifest.json --output-dir report
```

加 `--overwrite` 可替换已声明产物；禁止覆盖输入目录，保留其他用户文件。JSON/CSV 使用基础依赖，XLSX/Parquet 使用 timeseries 可选依赖；图表需要 matplotlib，显著峰需要 scipy。无需远程模型 API。

清单使用 schema_version: "1.0"，包含 title、language（zh/en，默认 zh）、required_sources 和 sources。sources 的 id 唯一，result 路径相对清单目录；阶段为 preprocessing 或 atlas。旧处理清单中的空间来源仍可读取，但不会进入时序业务汇总，请单独生成空间报告。

可从单个时序处理结果生成；无洪水提取结果时仅提供资料概况。多个事件集合按 source + event_id 区分。

清单可添加：
```json
"analysis": {
  "peak_thresholds_m3_s": [100, 500],
  "volume_thresholds_m3": [1000000, 5000000],
  "significant_peaks": {"prominence_m3_s": 20, "minimum_spacing_hours": 6}
}
```
以上仅为接口示例，不是默认参数。两阈值必须递增；小洪水 < 下阈值，中洪水 >= 下阈值且 < 上阈值，大洪水 >= 上阈值。

基于事件开始至结束的观测总流量，以 scipy.signal.find_peaks 统计显著峰；间隔向上取整为步数，平顶峰一次。缺测、不完整时段、不规则时间轴、边界最高峰及无显著峰列为无法分类。单峰/多峰比例分母仅含有效分类事件，无法分类另列。排除 warm-up，不修改原事件边界，不重算已有洪量。

输出 events.csv，含原始事件属性、两套规模分类及显著峰数量/分类原因。月份分布仅描述已有时段。

共有产物：report.md、report.json（2.0）、summary.csv（业务统计）、analysis-tables.json、figures/；后台产物为 evidence-index.json/.csv、data-gaps.json/.csv 和 result.json。sources、配置和原始值保留在 JSON，正文不展示核验台账。运行失败只输出失败状态与资料缺口，success/warning 退出 0，失败退出 1。

审查使用对应清单参数：
```text
python scripts/review_hydrological_study_report.py --processing-manifest processing-manifest.json --report-dir report --output-dir review
```
独立 Agent 语义审查完成后才能整体通过。
