---
name: build-hydrological-topology
description: 适用于从同网格的 D8、河段、Strahler 和子流域栅格构建真实流域内 HydroBase 上下游拓扑与河段指标；不适用于根据裁切后线段接触猜测拓扑、模型汇流计算或模拟误差诊断。
metadata:
  category: spatial-analysis
  domains:
    - spatial-analysis
    - hydrological-modeling
  tool_type: python
  primary_tool: rasterio
  related_skills:
    - spatial-analysis/prepare-dem-analysis-grid
    - spatial-analysis/extract-dem-stream-network
    - spatial-analysis/validate-hydrological-topology
---

# 构建水文拓扑 HydroBase

在完整 buffer 网格上沿 D8 建立河段拓扑，再按真实流域筛选；这一区别决定边界河段和出口是否可信。详细接口见[使用指南](usage-guide.md)，表与栅格语义见[数据契约](data-contract.yaml)。

## 不变量

- filled DEM、D8、汇流累积、河段、Strahler 和子流域必须完全同网格。
- D8 只接受 Whitebox 非 ESRI 编码。
- 先追踪完整分析范围的下游关系，再保留真实流域内河段；下游离开保留集合时记为 `0`。
- 不得依据裁切线段相交或端点邻近重新推断上下游。
- 子流域面积按真实边界内有效像元计数；河长沿 D8 像元中心连接累计。
- 列表与记录按整数 ID 稳定排序；原始输入和上游 artifacts 不得被修改。

## 停止条件

发现非法 D8 值、网格错位、河段循环、自环、无法解析的拓扑层级、无流域内河段、缺失子流域映射或无法形成必需河段几何时停止。多下游候选、追踪警告和负高程步保留为 warning 证据。

## 下一步

构建成功后必须使用 `validate-hydrological-topology` 独立检查出口预期、表间引用、栅格数量和检查图，再把成果交给半分布式模型。
