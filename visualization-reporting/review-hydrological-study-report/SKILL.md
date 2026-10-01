---
name: review-hydrological-study-report
description: 适用于独立审查时序数据、空间数据及水文模拟的生成或修订报告包，核对原始证据、数值、单位、时段、图表和论述支持度；不适用于任意外部 Word/PDF 报告、重新计算指标或直接修改报告与上游成果。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - evaluation-diagnostics
  tool_type: mixed
  primary_tool: python
  related_skills:
    - visualization-reporting/generate-hydrological-study-report
    - visualization-reporting/generate-timeseries-data-processing-report
    - visualization-reporting/generate-spatial-data-processing-report
---

# 独立报告审查

先运行确定性审查，再由 Agent 逐段读取原始证据并完成语义审查。接口见[使用指南](usage-guide.md)，产物见[数据契约](data-contract.yaml)。

- 重新读取清单和原始成果；不得以生成器的 PASS 或正文流畅度作为审查依据。
- 核对数值、显示舍入、单位、指标定义、时间范围、事件、split、warm-up/scored、表格和图件。
- 每段判定 supported、unsupported 或 uncertain，写出理由，列出已检查的全部引用。引用存在不等于论述受支持。
- 核对调用方声明的评价时段与上游实际输入；无法核实则 uncertain，不得默认为一致。
- 重点检查率定与独立验证混淆、因果强度超过证据、合格阈值缺失、warning 或 unavailable 被忽略。
- 不自动修正文稿或源文件。Markdown 改动与 JSON 不一致时提出问题，要求修订结构化稿后重新 finalize。
- 语义检查必须绑定本轮 package_fingerprint。包或来源改变时重新检查，旧审查不得复用。

未完成语义审查不得给出整体 pass。审查完成并无问题才通过；待核实项保留为 needs_human_review。

## 报告类型

同一入口支持模拟、时序与空间报告，分别传 --study-manifest、--processing-manifest、--spatial-manifest。机器检查重建统计及图件，语义检查必须逐段阅读原始证据和上下文。审查也应检查表格所表达的统计口径及关键遗漏，不能仅凭引用存在认定论述受支持。
