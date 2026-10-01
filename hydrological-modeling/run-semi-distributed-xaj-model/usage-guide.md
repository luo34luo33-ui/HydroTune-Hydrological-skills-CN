# 半分布式河海新安江模型使用指南

## 输入准备

先运行 `spatial-analysis/build-hydrological-topology`，再运行 `spatial-analysis/validate-hydrological-topology`，并以 `--expected-outlets 1` 完成独立 QC。模型直接读取这两个 `result.json` 中的哈希引用，不从文件名猜测空间语义。每个子流域的 `outlet_reach_ids` 必须恰好一个。

`forcing.csv` 列为 `time,sub_id,P_mm,E0_mm`，逐时步、逐子流域齐全。`time` 必须含 UTC 偏移，并代表**区间末端**。`P_mm` 和 `E0_mm` 是每步累计深度。可选 `boundary.csv` 列为 `time,reach_id,Q_m3s`；未列出的河段或时间步无外部入流。

## 配置

配置为 JSON 或 YAML，字段由 `examples/model_config.schema.json` 约束。示例结构在 `examples/config_shape.json`；其中的参数数值只示意字段形状，不可直接视为率定值。全部 XAJ 参数全域共享；`dp_by_reach` 必须覆盖全部 HydroBase 河段。`KE` 单位小时，`XE` 无量纲；程序拒绝产生负 Muskingum 系数的组合。`initial` 的 soil_fraction 是 WUM、WLM 和 WDM 各层初始充水比例，其他初始状态显式给定。`lag_steps`、`warmup_steps` 和模拟时段必须明确。

## 命令

```powershell
python examples/run_semi_distributed_xaj.py `
  --topology-result <build-result.json> `
  --topology-qc-result <qc-result.json> `
  --forcing <forcing.csv> `
  --params <model-config.json> `
  --boundary-inflow <boundary.csv> `
  --output-detail outlet-only `
  --output-dir <new-output-directory>
```

未使用边界入流时省略 `--boundary-inflow`。输出目录非空时拒绝；`--overwrite` 只替换本 Skill 的五个声明产物。输入或配置错误退出码 1，科学 QC 失败退出码 2，成功及 warning 退出码 0。

常规模拟建议显式传入 `--output-detail outlet-only`：只保存 `outlet_flow.csv`、`model_config.json` 和 `result.json`，不累积内部过程历史。需要子流域状态和逐河段诊断时使用 `--output-detail full`。为兼容原有脚本，省略此选项仍输出 full。`--overwrite` 从 full 切换至 outlet-only 时会移除同目录旧的两张内部过程表，避免把旧文件误认为本次成果。

Python 调用 `run_model(..., save_internal_process=False)` 可同样关闭内部历史；返回的前两张 DataFrame 为空，出口过程及记账量保持不变。该接口不写文件。

## 输出与解释

full 模式中，`subbasin_process.csv` 保存局地产流状态和分量，`reach_process.csv` 保存每个河段的三水源入出流。两种模式均保存 `outlet_flow.csv` 的唯一出口三水源及总流量。所有已输出的过程表保留预热行，并以 `is_warmup` 标注。`result.json` 保存空间验证链、哈希、运行参数、输出模式和水量记账量，只引用本次实际生成的产物。河道入流减出流体积包含末端路由状态，不能单独作为水量守恒失败判据。

该模型的拓扑汇流是对原脚本的有意扩展。原脚本九区独立求和仅用于验证局地 XAJ 与逐段 Muskingum 数值方程。
