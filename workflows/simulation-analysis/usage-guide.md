# 模拟结果智能分析 Workflow 使用指南

## 必需输入

- 标准化模拟结果和对应观测。
- 可比的时间、单位、空间单元和评价期间。
- 模型运行记录、参数、warning 与 provenance。

## 分析层次

1. 基础性能与水量守恒。
2. 时序、洪水事件和高/中/低流量行为。
3. 季节性、空间差异与参数行为。
4. 误差模式、候选水文假设、反证和下一步分析。

## 报告规则

- 明确区分事实、runtime 证据、工程推断、建议和待确认事项。
- validation 表现不能描述为 calibration 证据。
- 反馈到上游 workflow 只是一项人工决策建议，本阶段不自动重跑。

已有模型运行和评价成果时，将每个运行与其评价显式写入 study-manifest.json，调用 generate-hydrological-study-report。prepare 产出证据草稿，Agent 编写有引用的论述，finalize 渲染 Markdown/JSON/CSV 报告包；相关输入不齐时保留缺口，不能冒充完整研究报告。

review-hydrological-study-report 是独立后续步骤，包含脚本一致性检查与逐段 Agent 语义审查。未完成语义审查不能整体通过；发现问题只提出修订意见，不触发上游重算。

