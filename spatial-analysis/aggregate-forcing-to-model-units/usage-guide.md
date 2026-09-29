# 使用指南

```powershell
python examples/aggregate_forcing_to_model_units.py --topology-result hydrobase/result.json --topology-qc-result hydrobase-qc/result.json --source-config examples/sources.station.example.json --target basin --output-dir basin-forcing
```

仅用于接口演示的两单元合成资料可通过 `python examples/make_synthetic_example.py --output-dir demo-inputs` 生成；随后将三个路径分别指定为 `demo-inputs/build-result.json`、`demo-inputs/qc-result.json` 和 `demo-inputs/sources.json`。正式分析必须使用真正运行 HydroBase 构建与独立 QC 所得成果。

`--target basin` 产出 `time,P,PET`；`--target subbasin` 产出 `time,sub_id,P_mm,E0_mm`。配置中 `layers.P`、`layers.evap` 均要明确来源与变量。站点来源给出标准化 `result.json`、坐标 CSV `source_id,x,y` 和 `station_crs`。格点可用 `kind=netcdf`，给出路径、数据变量、维度 `[time,y,x]` 与 `grid_crs`；或 `kind=geotiff`，给出 `time,path` 清单。格点还需 `unit`、`timestep_seconds`、`timestamp_semantics`，时间须带时区。所有输出仅写 `--output-dir`。失败退出码 2（QC）或 1（调用/文件）。
