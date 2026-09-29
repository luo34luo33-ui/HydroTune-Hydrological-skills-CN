---
name: separate-baseflow-eckhardt
description: 适用于对已标准化的连续非负流量序列执行 Eckhardt 数字滤波并生成基流和直接径流；不适用于事件边界识别、其他基流方法比较或缺测时序修复。
metadata:
  category: data-processing
  domains:
    - data-processing
    - hydrological-modeling
  tool_type: python
  primary_tool: numpy
  related_skills:
    - data-processing/prepare-discharge-timeseries
    - data-processing/extract-flood-events
---

# Eckhardt 基流分割

使用显式 `BFImax` 和显式或可证据化估计的 recession alpha 生成流量分量。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## 决策规则

- 只消费 `prepare-discharge-timeseries` 的已验证 result 和时序 artifact。
- `BFImax` 不使用通用静默默认值。
- 自动 alpha 需要至少 20 个有效递减流量对；证据不足时停止，不回退经验常数。
- 基流必须位于 `[0, discharge]`，分量守恒必须通过 QC。
- 本 Skill 不判断洪水事件，也不比较不同基流分割方法。
