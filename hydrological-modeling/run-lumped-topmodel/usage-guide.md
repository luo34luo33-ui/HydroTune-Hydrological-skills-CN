# 使用指南

```powershell
python examples/run_lumped_topmodel.py --terrain-result PATH/terrain/result.json --forcing PATH/forcing.csv --params PATH/config.json --output-dir PATH/model
```

`forcing.csv` 严格为 `time,P,PET`，时间为含偏移的区间末端；P/PET 单位均为 mm/步。配置需符合 `examples/model_config.schema.json`，包含 `schema_version=1.0`、`timestep_hours`、IANA `timezone`、`timestamp_semantics=interval_end`、`forcing_unit=mm/step`、`warmup_steps`、`simulation_start`、`simulation_end`、`water_balance_tolerance_m` 和 `parameters`。`qs0` 为 m/h，`lnTe` 为 ln(m²/h)，`m,Sr0,Srmax,psi` 为 m，`td` 为 h/m（正值）或负的排水系数，`vch,vr` 为 m/h，`K0` 为 m/h，`dtheta` 无量纲。参数必须由研究者给出；Skill 不内置默认值。

`examples/synthetic_config.json` 和 `examples/synthetic_forcing.csv` 仅用于算法测试，不是实际流域的参数建议。

输出 `process.csv`（深度 mm/步、流量 m³/s、预热标记）、`final_state.json`、`model_config.json`、`result.json`。依赖 `numpy,pandas,PyYAML,jsonschema`；正常运行无需 GRASS。重跑可用 `--overwrite`，仅替换本 Skill 声明的文件。输入错误退出 1，科学 QC 错误退出 2。
