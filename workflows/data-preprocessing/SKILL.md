---
name: data-preprocessing
description: 编排水文建模前的时序质量控制、洪水事件识别和空间拓扑准备。适用于需要把原始水文气象与空间输入整理为模型可消费结构的任务；不适用于模型运行、参数率定或模拟结果诊断。
metadata:
  category: workflow
  domains:
    - data-processing
    - spatial-analysis
    - visualization-reporting
  tool_type: instruction
  primary_tool: agent-reasoning
  related_skills: []
---

# 数据预处理 Workflow

将时序处理和空间拓扑视为可并行但必须在输出验证处汇合的两条证据链。不要根据列名、文件名或常见惯例直接确认单位、变量角色、时间连续性、坐标参考系或拓扑关系。

## 工作方式

1. 先盘点输入、来源、时间范围、空间范围和已确认元数据。
2. 分别执行时序质量控制与空间数据准备；缺少空间输入时，应显式记录空间分支不可用的原因，而不是伪造结果。
3. 识别降雨和洪水事件时，保留事件边界、时间分辨率和前期状态证据。新洪水提取任务先分别询问用户实测无降水与雨量全缺失的洪水是否保留（均推荐剔除，已有明确选择不重复询问），将选择写入配置。突跳长直线候选整场剔除，任何降雨策略不能绕过；通过形态检查后按显式响应窗口、雨量门槛和用户策略筛选。零雨与缺测区分，局部缺测但观测雨量达标可保留并标记，少雨或局部缺测未达门槛仍剔除；策略保留的事件不能冒充有降雨支持。事件筛选通过不等于完整建模 forcing 就绪。
4. 检查河网、子流域、站点和模型单元的上下游与汇流关系。
5. 只有在两条分支的必需 QC 通过后，才声明输出可供建模消费。
6. 已完成一项或多项处理成果时，可使用 `visualization-reporting/generate-timeseries-data-processing-report` 汇总时序资料、洪水规模与峰型；空间成果使用独立空间报告；单项报告不要求模拟或评价，也不替代完整就绪性验证。

按需阅读[使用指南](usage-guide.md)，并以[机器可读工作流](workflow.yaml)中的 stage、依赖和 QC 定义为准。

## 停止条件

必需变量语义、单位、时间连续性或关键空间关系未确认时停止，不得用方便的默认值填补。


时序分支使用 generate-timeseries-data-processing-report 汇总洪水规模和显著峰型；半分布式空间准备分支使用 generate-spatial-data-processing-report 汇总流域、子流域、河段与拓扑。两类正文不展示核验台账，不自动重跑上游。
