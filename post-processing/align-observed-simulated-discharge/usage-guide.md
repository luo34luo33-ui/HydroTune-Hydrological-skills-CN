# 使用指南

```powershell
python examples/align_observed_simulated_discharge.py --observed-result observed/result.json --simulation-result hbv/result.json --output-dir aligned
```

无需真实资料时，可先运行 `python examples/make_synthetic_example.py --output-dir demo-inputs`，再将上面命令的两个 `result.json` 替换为 `demo-inputs/observed-result.json` 和 `demo-inputs/simulation-result.json`。

直接识别 HBV、Tank、DHF、GR4J、SAC-SMA 的 `Q`，半分布式 XAJ 的 `Q_m3s`，以及 TOPMODEL 的 `outlet_m3_s`。集总式 XAJ 必须先进行河道汇流；路由结果另外提供 `--outlet-declaration`，其中 `location` 为 `basin_outlet`、`outlet_id` 明确、`unit` 与 `input_unit` 均为 `m3/s`、`inflow_sha256` 等于路由结果记录的入流文件哈希、`column` 指定出口列。XAJ 路由还应填 `upstream_xaj_result`，验证路由入流与 XAJ 输出哈希，并按时间传递预热标记；逐场次时可用 `upstream_xaj_results` 按 event ID 映射。其他不带预热列的路由成果必须显式给 `warmup_steps: 0`。逐场次路由使用 `--event-manifest` 的 event ID → result.json 路径映射。输出 `aligned_discharge.csv`、`evaluation_discharge.csv`、`excluded_observations.csv`、`alignment_qc.json` 与 `result.json`。QC 失败退出码 2，调用/文件错误 1。
