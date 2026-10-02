---
name: calibrate-model-ga
description: 适用于使用带锦标赛选择和精英保留的遗传算法率定水文模型并独立验证；不适用于模型结构选择、算法比较或误差原因诊断。
metadata:
  category: model-calibration
  domains: [model-calibration, hydrological-modeling, evaluation-diagnostics]
  tool_type: python
  primary_tool: numpy
  related_skills: [visualization-reporting/visualize-model-calibration]
---

# 遗传算法模型率定

## 率定前确认

执行率定前，必须获得用户对本次数据组织模式的明确选择；直接调用本 Skill 或从其他任务进入率定时都适用。

- 尚未明确选择时，询问：“本次率定采用连续时序 `continuous`，还是逐场洪水集合 `event_collection`？”
- `continuous` 使用连续时间序列；`event_collection` 按洪水事件组织，不将不同事件拼接成连续序列，需明确逐场 warm-up 语义。
- 配置中的 `data_shape`、文件名、已有事件提取成果或 Agent 推断只能作为推荐依据，不能替代用户确认。
- 用户在当前任务中已明确指定模式，例如“用连续模式率定”，即视为完成确认，不重复询问。
- 同一任务、同一批数据且组织模式不变时，切换算法、重试或调整优化配置可复用确认；更换数据或改变组织模式时重新确认。
- 确认前可以读取资料、检查配置和准备执行方案，但不得启动参数搜索或调用率定评估器。
- 执行前核对用户选择与 `problem.data_shape` 一致；有冲突时先说明并解决，不能静默沿用旧配置。用户未回复或回复含糊时，不启动率定。

此规则约束使用 Skill 的 Agent；不改变 CLI 参数或问题文件契约，不对用户直接手动执行 CLI 增加拦截。

## 执行说明

使用显式种群、选择、交叉和变异配置搜索 calibration split；锁定最佳参数后才运行 validation。命令见[使用指南](usage-guide.md)，artifact 见[数据契约](data-contract.yaml)。

## 决策规则

- seed、预算和全部 GA 旋钮必须来自版本化配置。
- 单参数问题跳过单点交叉，不构造无效交叉点。
- 精英保留不免除重新评估预算；validation 永不参与适应度。
- 失败评估可追溯，超过明确上限时停止，不自动换算法。

## 最终评分样本隔离

锁定参数后检查 materialize 的两个 split：continuous 的评分时间须带时区、无重复，率定与验证评分时间不得相交；event_collection 的评分事件 ID 不得相交。scored=false 的预热样本不参与交集判断。违规停止，不输出成功率定成果。
