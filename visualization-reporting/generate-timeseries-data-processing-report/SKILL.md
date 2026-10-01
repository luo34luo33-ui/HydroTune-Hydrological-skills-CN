---
name: generate-timeseries-data-processing-report
description: 适用于把已完成的时序整理、基流分割和洪水提取成果汇总为规模分布、显著峰型及总体特征报告；不适用于空间成果报告、重新提取事件或模型性能评价。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - data-processing
  tool_type: python
  primary_tool: python
  related_skills:
    - data-processing/extract-flood-events
    - data-processing/prepare-discharge-timeseries
    - visualization-reporting/visualize-flood-event-atlas
---

# 时序数据预分析报告

读取显式清单，输出结论、关键业务图表和限制。阅读[使用指南](usage-guide.md)了解统计口径，输入输出见[数据契约](data-contract.yaml)。

- 不扫描目录选最新版，不自动运行上游计算。
- 正文及业务汇总不展示哈希、字段定位或逐项 QC；完整性和证据记录仅保留后台附件。
- 上游 error、科学 QC FAIL、未知契约或损坏成果时停止正文生成。可选资料缺失明确说明限制。
- 统计原值保留在 JSON/CSV；数量整数、比例一位小数，普通指标三位小数。
- 默认中文，支持英文；不导出 DOCX/PDF。生成后可独立调用 review-hydrological-study-report，未完成语义审查不认定整体通过。
- 洪峰和总洪量分别按显式阈值分级，不采用默认工程等级。
- 显著峰统计必须明确提供突出度和最小峰间隔，排除 warm-up，不改变事件边界。
- 未提供参数或资料不足时列为不可用；不以原始局部峰数替代显著峰数。
