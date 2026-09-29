---
name: run-semi-distributed-xaj-model
description: 适用于基于已独立验证的 HydroBase 子流域与河段拓扑运行半分布式河海新安江前向模拟；不适用于空间拓扑构建、参数率定、独立分区求和或正式制图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
    - spatial-analysis
  tool_type: python
  primary_tool: pandas
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - hydrological-modeling/run-lumped-xaj-model
---

# 半分布式河海新安江前向模拟

给每个 HydroBase 子流域计算一次新安江产流与三水源坡面汇流，再沿经验证的河段 DAG 演算至唯一出口。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## Use When

- 已有 `build-hydrological-topology` 及 `validate-hydrological-topology` 的可追溯结果，预期出口数为一。
- 每个子流域都有完整、已确认单位和时间含义的降雨及蒸发能力序列。
- 已有全域共享的 XAJ 参数、逐河段 DP、明确初值及预热期，只需前向运行。

## Do Not Use When

- 需要从 DEM 提取河网、修改子流域边界或修复失败拓扑；先用 `spatial-analysis`。
- 需要复现原脚本的九区独立求和作为正式出口结果；该行为仅是局地方程回归参照。
- 需要自动率定参数、计算观测误差指标或制作正式图册。

## Governing Principle

- 源码的 Yield、Divide3Source、hsRouting、Chrouting 和逐段 Muskingum 方程及顺序在局地层保留；全域共享参数和日尺度 KG/KI/CI/CG 折算保留。
- 面积必须来自有哈希证据的 `subbasins.csv`；每个子流域仅在其唯一出口河段注入一次局地三水源，不把面积分配到多个河段。
- 上游三水源按拓扑顺序进入下游相应通道。外部入流按 `reach_id` 加在地表流通道、该河段 Muskingum 之前。
- 时间步、时区、时间戳含义、参数、初值、DP、lag、warm-up 和模拟期间都必须显式配置；缺测不得填零或插值。
- 非空输出目录默认拒绝；覆盖只删除本 Skill 声明的文件。输入 artifact 永不修改。

## QC 与停止条件

- 独立拓扑 QC 有 FAIL、引用哈希不一致、非单出口、子流域出口河段不唯一、拓扑环或单元映射不完整时停止。
- forcing 有重复/缺失单元时步、非有限或负值、时间步不规则或模拟期间不匹配时停止。
- 参数越界、Muskingum 系数不稳定、状态或出流非有限、负状态时停止。
- 原方程中的数值分段上限为每步 400 段，属于来源算法规则；输出 `rain_substeps` 便于审计。
- `result.json` 同时记录局地水量记账量、三水源分解残差和河道入流减出流体积；路由状态跨模拟末端的差额不能直接解释为水量损失。

## 结果解释

`outlet_flow.csv` 是拓扑驱动的流域出口流量，不能与原脚本九区独立求和结果直接宣称等价。预热行保留并标记 `is_warmup`。来自上游拓扑 QC 的 warning 继续出现在运行结果中。
