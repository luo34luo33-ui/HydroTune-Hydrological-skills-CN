---
name: hydrological-modeling
description: 编排集总式与半分布式降雨径流模型的结构选择、初始化、warm-up、模拟和输出标准化。适用于已有建模就绪数据并需要构建或运行模型的任务；不适用于完全分布式、水动力、地下水模型或参数自动率定。
metadata:
  category: workflow
  domains:
    - hydrological-modeling
    - spatial-analysis
  tool_type: instruction
  primary_tool: agent-reasoning
  related_skills: []
---

# 水文模型构建 Workflow

先确认输入 readiness 和建模空间结构，再进入集总式或半分布式分支。模型参数、状态、warm-up、routing 与运行时设置必须来自确定性 artifact 或用户确认。

## 工作方式

1. 检查时序、事件、空间结构与模型目标是否相容。
2. 明确选择集总式或半分布式路径，不把两者的状态和参数语义混用。
3. 半分布式路径必须引用经验证的子流域或模型单元拓扑。
4. 在 simulation 前确认初始状态、warm-up 和运行期间。
5. 标准化模拟输出，同时保存参数、软件上下文、provenance、warning 和失败状态。

调用 `run-semi-distributed-xaj-model` 进行常规或长时段模拟时，显式使用 `--output-detail outlet-only`，保留出口过程及运行记录；仅在需要内部状态或河段过程诊断时选择 `full`。Python 前向调用可用 `save_internal_process=False` 关闭内部历史累积。

按需阅读[使用指南](usage-guide.md)，并以[机器可读工作流](workflow.yaml)定义的分支与 QC 为准。

## 停止条件

空间结构、关键参数、warm-up 或 routing 要求不明确时停止并请求确认；不得为了完成运行而发明参数或绕过必需路由。

