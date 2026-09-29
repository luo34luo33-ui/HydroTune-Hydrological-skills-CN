# 使用指南

```text
python scripts/prepare_discharge_timeseries.py --input PATH --input-format auto \
  --time-column time --flow-column GZ_in --flow-unit m3/s --timezone naive \
  --edge-missing trim --output-format csv --output-dir prepared
```

支持 XLSX、CSV 和 Parquet。XLSX 可用 `--sheet` 指定工作表。默认缺测、重复时间和首尾缺测均报错；只有显式参数才能改变处理方式。输出保留原始附加列，但其语义仍为未确认。

`--missing-policy interpolate` 必须同时给出 `--max-interpolation-gap-hours`。CSV 默认使用 UTF-8-SIG；Parquet 和 XLSX 只有在相应依赖可用时写出，不自动改用其他格式。
