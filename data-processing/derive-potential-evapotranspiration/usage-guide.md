# 使用指南

按 `examples/config.daily.example.json` 指明所有气象列单位，站点表含 `source_id,latitude_deg,elevation_m`。可直接运行合成日尺度例子：

```powershell
python examples/derive_potential_evapotranspiration.py --source examples/met.daily.example.csv --stations examples/stations.example.csv --config examples/config.daily.example.json --output-dir eto-out
```

日尺度采用 [FAO-56 第四章](https://www.fao.org/4/x0490e/x0490e08.htm)日 Penman–Monteith 方程，小时尺度采用该章式 53，小时分母风速系数固定为 0.34；配置中的 `units` 必须逐项声明摄氏度、MJ/m²/步、kPa 和 m/s。净辐射、水汽压与小时土壤热通量必须提供实测或已计算值，不在此 Skill 估算。`mapping` 可选；`factor` 将 ETo 原值乘为 PET 或 E0，负值处理设为 `error` 或 `floor_zero`。失败退出码 2（QC）或 1（调用/文件）。
