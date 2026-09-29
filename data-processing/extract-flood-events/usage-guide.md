# 使用指南

```text
python scripts/extract_flood_events.py --baseflow-result components/result.json \
  --config scripts/flood_event_config.source-equivalent.yaml \
  --output-format csv --per-event-format csv --output-dir events
```

默认表格格式和逐场格式均为 CSV。可显式选择 Parquet、XLSX，或用 `--per-event-format none` 关闭逐场文件。按峰值日期命名时，同日多事件自动附加稳定序号。

YAML 中的峰值、边界、合并、过滤和 warm-up 字段全部必需。规模阈值可显式设为 `null`；脚本不会根据数据自动放宽阈值。
