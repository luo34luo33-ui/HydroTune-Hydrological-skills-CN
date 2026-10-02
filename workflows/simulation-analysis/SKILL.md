---
name: simulation-analysis
description: 编排观测—模拟对齐、性能评价、事件与过程诊断、证据假设和结构化报告。适用于模型结果已存在并需要系统解释误差模式的任务；不适用于原始数据首次整理、模型前向运行或无证据的因果归因。
metadata:
  category: workflow
  domains:
    - evaluation-diagnostics
    - post-processing
    - visualization-reporting
  tool_type: instruction
  primary_tool: agent-reasoning
  related_skills: []
---

# 模拟结果智能分析 Workflow

智能分析不是把所有指标机械计算一遍，而是把观测事实、计算证据、误差模式、水文假设和下一步建议连接成可审计的推理链。

## 工作方式

1. 先对齐观测、模拟、时间、单位、空间单元和有效评价期间。
2. 从整体性能进入时序、事件、流量分区、季节、水量平衡、空间和参数行为诊断。
3. 将一致出现的差异总结为模式，并为每个假设列出支持证据、反证与不确定性。
4. 只提出有界的可能原因，不把指标、相关性或排名直接解释为因果。
5. 报告可以建议返回数据预处理或模型构建，但不得自动修改输入、模型或参数。
6. 模拟和评价成果可用时，使用 `visualization-reporting/generate-hydrological-study-report` 的 prepare → Agent 论述 → finalize 流程形成研究报告。保留已有诊断证据，汇总总体指标和 NSE 最低事件过程，提出条件性解释，不重算性能指标或确定因果。独立审查按需调用 `visualization-reporting/review-hydrological-study-report`，不得自动重跑上游。

按需阅读[使用指南](usage-guide.md)，并以[机器可读工作流](workflow.yaml)定义的证据链和反馈目标为准。

## 停止条件

观测与模拟不能可靠对齐、评价期间不一致或关键数据语义未知时停止；不得在无可比基础上生成性能结论。


## 时段和运行身份闸门

研究清单必须为 2.0，声明互不重叠且带时区的率定/验证时段，以及每个运行的 name、kind、哈希序列引用和列映射。缺证据时停止并提示迁移。事件按完整评分区间归属，跨界或混合分组时拒绝；基准与校正采用独立运行 ID，校正必须绑定父运行和后处理来源。生成与审查共用校验器，核对真实评分时间、模拟值与观测；直接对比必须使用相同评分样本。不得用论述或清单标签代替证据。

