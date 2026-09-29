---
name: validate-hydrological-topology
description: 适用于独立验证 HydroBase 的上下游引用、出口、栅格一致性、面积和河段几何并生成 QC 证据；不适用于重新构建拓扑、修改失败成果或诊断水文模型模拟误差。
metadata:
  category: spatial-analysis
  domains:
    - spatial-analysis
    - hydrological-modeling
  tool_type: python
  primary_tool: python
  related_skills:
    - spatial-analysis/build-hydrological-topology
---

# 验证水文拓扑

把 HydroBase 作为外部输入重新计算关键不变量，不因上游构建脚本报告成功就跳过检查。使用方式见[使用指南](usage-guide.md)，输入输出见[数据契约](data-contract.yaml)。

## 用户确认闸门

运行前必须确认预期出口数量。单出口是常见情形，但不是可以替所有流域填写的事实；真实多排水系统应提供相应数量。

## FAIL 条件

- ID 重复、下游引用不存在或上下游列表不互反。
- 自环、循环、拓扑层级不一致或未覆盖全部河段。
- 出口数量与用户确认值不符。
- 缺失子流域映射、非正河长、空或无效几何。
- 核心裁切栅格错位、子流域面积不一致或河段数量不一致。

## WARN 条件

上游构建阶段记录的 buffer 覆盖不足、多下游候选、追踪异常或负高程步必须在报告中保留。WARN 不等于数据无问题；只有它不破坏当前科学用途且风险被用户接受时才可继续。

## 输出纪律

检查失败时不修改输入或自动删改河段。输出检查表、文本报告、固定拓扑检查图和 `result.json`；`result.json.status=error` 且进程退出码为 2。
