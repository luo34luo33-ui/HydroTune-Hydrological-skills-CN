# Validate Hydrological Topology 使用指南

## Quick Start

```bash
python validate_hydrological_topology.py --build-result hydrobase/result.json --expected-outlets 1 --output-dir topology-qc
```

`--expected-outlets` 必须大于零，并来自项目目标、出口资料或用户确认，而不是从当前结果反推后再当作预期。

## Outputs

- `topology_checks.csv`：每项检查的 `PASS`、`WARN` 或 `FAIL`、数量和详情。
- `topology_check_report.txt`：面向审阅的稳定文本摘要。
- `network_topology_check.png`：子流域、河段、流向、ID 和出口检查图。
- `result.json`：权威机器可读状态和输出哈希。

退出码为 0 表示 success 或 warning，1 表示输入/运行错误，2 表示科学 QC 失败。任何 `FAIL` 都必须阻止后续建模。

## External HydroBase

外部成果可用兼容的 build `result.json` 接入，但文件名、字段、CRS、栅格和哈希必须满足本 Skill 的数据契约。不得伪造上游成功状态来绕过缺失 artifact。
