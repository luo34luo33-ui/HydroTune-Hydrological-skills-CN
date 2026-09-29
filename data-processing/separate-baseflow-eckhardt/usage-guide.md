# 使用指南

```text
python scripts/separate_baseflow_eckhardt.py --prepare-result prepared/result.json \
  --bfi-max 0.80 --estimate-alpha --output-format csv --output-dir components
```

也可使用 `--alpha FLOAT` 提供已确认参数。`alpha` 必须属于 `(0, 1)`，`bfi-max` 必须属于 `(0, 1)`。输出格式支持 CSV、Parquet 和 XLSX，默认 CSV，不自动回退。
