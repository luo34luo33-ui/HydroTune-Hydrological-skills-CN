---
name: generate-spatial-data-processing-report
description: 适用于汇总半分布式建模流水线已有流域基础、子流域、河段和拓扑成果；不适用于重新划分子流域、构建拓扑、模型模拟或时序预分析。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - spatial-analysis
  tool_type: python
  primary_tool: python
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - visualization-reporting/visualize-hydrobase-atlas
---

# 半分布式空间数据处理报告

读取显式清单，输出结论、关键业务图表和限制。阅读[使用指南](usage-guide.md)了解统计口径，输入输出见[数据契约](data-contract.yaml)。

- 不扫描目录选最新版，不自动运行上游计算。
- 正文及业务汇总不展示哈希、字段定位或逐项 QC；完整性和证据记录仅保留后台附件。
- 上游 error、科学 QC FAIL、未知契约或损坏成果时停止正文生成。可选资料缺失明确说明限制。
- 统计原值保留在 JSON/CSV；数量整数、比例一位小数，普通指标三位小数。
- 默认中文，支持英文；不导出 DOCX/PDF。生成后可独立调用 review-hydrological-study-report，未完成语义审查不认定整体通过。
- 必须提供一个拓扑构建结果及子流域、河段、拓扑表。DEM、河网提取、独立验证和图册可选。
- 子流域与河段允许一对多；栅格面积与真实边界面积分开，拓扑层级与 Strahler 等级分开。
- 无独立验证时注明尚未独立验证；不宣称空间成果等于模型整体就绪。
