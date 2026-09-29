# 使用指南

可直接用仓库中的两站合成序列运行：

```powershell
python examples/prepare_model_forcing_timeseries.py --source examples/stations.example.csv --config examples/config.example.json --output-dir forcing-out
```

配置中的 `value_semantics` 为 `step_depth`、`rate` 或 `running_cumulative`。累计值要额外配置 `columns.reset`；第一行是计数器基线，输出从第二行起。时间戳可声明为区间起点或末端，输出统一为末端。`--overwrite` 仅覆盖本 Skill 命名的产物。QC 失败返回 2，调用或文件错误返回 1。
