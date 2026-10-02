# 使用指南

## 环境与接口

在仓库环境安装 `pip install -e ".[bma,test]"`。CLI 与全部依赖 helper 位于 `examples/`，安装后位于本 skill 的 `scripts/`；不依赖 HydroTune-AI-Demo。

典型请求：“已有 HBV、Tank、GR4J 的出口模拟，使用历史期资料训练概率 BMA，输出未来时期的均值与 90% 区间。”

成员清单路径相对于清单所在目录解析，格式如下。出口和 m³/s 声明是调用方确认的数据语义，不能仅根据列名推断；已有源元数据冲突时停止。

```json
{
  "schema_version": "1.0",
  "outlet_id": "basin-outlet",
  "unit": "m3/s",
  "timestep_hours": 1,
  "timezone": "Asia/Shanghai",
  "members": [
    {"model_id": "HBV", "outlet_id": "basin-outlet", "simulation_result": "hbv/result.json"},
    {"model_id": "Tank", "outlet_id": "basin-outlet", "simulation_result": "tank/result.json"}
  ]
}
```

场次成员将 `simulation_result` 替换为 `event_manifest`，其文件内容为 `{"flood-001": "event1/result.json", "flood-002": "event2/result.json"}`，其中路径相对于场次清单目录。一个成员恰好指定一种输入。需要路由时增加 `outlet_declaration` 文件路径，格式沿用出口对齐 skill（`location: basin_outlet`、`outlet_id`、`unit/input_unit: m3/s`、`inflow_sha256`、`column` 及 XAJ 上游引用／预热证据）。

直接识别 HBV、Tank、DHF、GR4J、SAC-SMA 的 `Q`、半分布式 XAJ 的 `Q_m3s`、TOPMODEL 的 `outlet_m3_s`；集总式 XAJ 须先路由。其他模型先在上游生成受支持的出口成果，不能直接传任意列。

## 连续序列完整示例

以下数据完全合成，只验证工作链，不是水文参数推荐。边界包含端点，必须携带时区。
每个成员须精确覆盖所指定的两个端点；越界或非原时间网格边界不会被静默缩短。

```powershell
python examples/make_synthetic_example.py --output-dir demo-continuous
python examples/ensemble_discharge_bma.py --mode train --members demo-continuous/members.json --observed-result demo-continuous/observed-result.json --start 2020-01-01T00:00:00+08:00 --end 2020-01-10T23:00:00+08:00 --random-state 2026 --output-dir fit-continuous
python examples/ensemble_discharge_bma.py --mode predict --members demo-continuous/members.json --model-result fit-continuous/result.json --start 2020-01-11T00:00:00+08:00 --end 2020-01-15T23:00:00+08:00 --output-dir forecast-continuous
```

## 洪水场次完整示例

```powershell
python examples/make_synthetic_example.py --mode event --output-dir demo-events
python examples/ensemble_discharge_bma.py --mode train --members demo-events/members.json --observed-result demo-events/observed-result.json --events flood-001 flood-002 --random-state 2026 --output-dir fit-events
python examples/ensemble_discharge_bma.py --mode predict --members demo-events/members.json --model-result fit-events/result.json --events flood-003 --output-dir forecast-events
```

场次模式禁止时间切片，只选完整场次；每个场次自身等间隔，场次之间允许间断。训练场次合并后每行等权，不按场次等权。应用有效时步必须晚于所有训练有效时步，同一场次 ID 不能复用。

## 参数与产物

- `--random-state`：train 必填的非负整数；predict 禁止指定种子与观测。
- `--n-starts 5 --max-iter 500 --tol 1e-8`：广义 EM 设置，可显式调整；无收敛解时保留诊断后停止。M 步 L-BFGS-B 最多 500 次、`ftol=1e-12`、`gtol=1e-8`，用于确保内层优化精度。
- `--quantiles 0.05 0.5 0.95`：严格递增、不重复且位于 `(0,1)`；默认输出 `p05_m3_s,p50_m3_s,p95_m3_s`。其他概率以百分数命名。
- `--overwrite`：明确重写该输出目录下本 skill 的已知产物；默认非空目录停止。训练与应用应使用不同输出目录。

训练输出 `bma_model.json`（归一化参数及缩放系数）、`model_card.json`、`fit_diagnostics.json`（每次初始化及似然历史）、`alignment_qc.json`、`result.json`。原量纲截距和标准差为归一化值乘缩放系数，斜率不变。

初始化先做非负斜率线性回归，以归一化残差标准差（不低于 `0.01`）作为起点；第一组权重均匀，后续从 Dirichlet(1) 初始化，截距扰动标准差 `0.05`，斜率与尺度的对数扰动标准差 `0.2`。这些数值只用于可复现的优化探索，保存在拟合诊断，不代表水文经验参数。重复预测判断使用相对容差 `1e-12`；斜率／权重不超过 `1e-8`、sigma 在下限的 1% 内时报告工程边界告警。

应用输出 `ensemble_discharge.csv`（均值、分位数、零概率）、`component_predictions.csv`（长表，记录成员模拟、潜在高斯中心、误差尺度、权重与零概率）、QC 和结果清单。时间统一输出 UTC ISO 8601，模型卡保留声明时区；组件的高斯中心可以为负，它不是实际流量预测均值。

集合均值公式为 `Σ w_k [σ_k φ(μ_k/σ_k) + μ_k Φ(μ_k/σ_k)]`。对于 `q ≥ 0`，CDF 为 `Σ w_k Φ((q-μ_k)/σ_k)`；`q < 0` 时为 0。零观测似然是 CDF 在零点的概率质量，正观测似然为混合高斯密度。训练诊断的似然处于归一化量纲，不能直接比较不同缩放训练运行。

## 评价衔接与常见错误

在上游精确对齐观测和集合表后，连续／事件评价 CLI 显式指定 `--simulated-column ensemble_mean_m3_s`，并按其契约提供观测列、时间和步长。该 skill 不生成 NSE、RMSE、CRPS 或区间覆盖率。

不要把参考代码的 NSE-softmax 当作本实现；不要把 90% 区间当作参数置信区间；不要在预测时期重新计算成员权重。改变成员、出口、步长、时区或连续／场次模式后需要重新训练。

科学 QC／拟合失败退出码 2，调用或文件错误为 1。失败 `result.json` 含原因，存在的拟合或对齐诊断仍记录哈希；不会发布可用模型或预测表。

方法依据：[Raftery 等（2005）预测分布 BMA](https://doi.org/10.1175/MWR2906.1)。零点删失高斯是本 skill 为非负及零流量选择的扩展，固定方差与线性校正是第一版假设。
