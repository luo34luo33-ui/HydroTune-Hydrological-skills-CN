---
name: generate-hydrological-study-report
description: 适用于汇总已有模拟与评价的总体指标，分析 NSE 最低事件的过程证据并提出条件性误差解释；不适用于重算评价指标、确定性成因归因、上游模拟或 DOCX/PDF 导出。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - evaluation-diagnostics
  tool_type: mixed
  primary_tool: python
  related_skills:
    - visualization-reporting/review-hydrological-study-report
    - evaluation-diagnostics/compute-continuous-series-metrics
    - evaluation-diagnostics/compute-event-flood-metrics
---

# 水文研究报告

将已有科学成果转换为可审查报告包。先建立显式研究清单，再运行 prepare；Agent 在结构化草稿中编写论述，最后 finalize。接口见[使用指南](usage-guide.md)，输入输出见[数据契约](data-contract.yaml)。

## 证据纪律

- 必须有每个运行显式绑定的模拟和评价成果；预处理、空间、率定及图册可选。不得扫描目录猜测最新文件或模型关系。
- 只修改 `sections[].paragraphs` 与 `narrative_provenance`。机器提取的 evidence、单位、定义、上下文及缺口不允许改写。
- 每段使用稳定 ID，标记 fact、inference、recommendation 或 pending，并列出 evidence_ids。数值绑定支持整数、一位、三位或六位小数，保留原值；指标默认三位小数。
- 先读证据原文和上下文，再写摘要、结果解释、限制和建议。连续水量偏差与事件洪量误差的符号定义分别保留，不用同名“误差”替代。
- 不重算性能指标，不新增默认合格阈值，不把率定表现当作独立验证能力。warm-up、scored、事件边界和评价范围不得混用。
- 无诊断证据时只写有条件的候选解释，并明确反证或缺口；不得写确定成因，也不得自动执行反馈建议。
- 所有 warning 和 unavailable 必须保留。可选章节缺失时注明未提供，不写成已经完成。

完成后可由独立 review skill 审查。生成器结构校验通过不等于语义或科学结论通过。首版交付 Markdown、JSON、CSV、总体指标分布和最差事件过程图。正文不展示哈希、字段定位及逐项 QC；后台附件保留追溯信息。

## 分析与论述

prepare 分别汇总连续总体指标及逐场指标分布；后者不称为连续总体 NSE。每个运行、每个 split 按已有 NSE 升序选择最低五场（可配置），排除不可用值，并列按开始时间及事件 ID 排序。有验证集时结论优先讨论验证能力。

为入选事件读取清单显式绑定的过程，提取观测峰附近、涨水段、退水段及整体偏差，保留 warm-up/scored 分离。Agent 在 overview、evaluation、discussion、limitations 各写至少一段论述。有过程时讨论应包含候选解释，使用 inference 段落的 hypotheses 数组，逐项填写 candidate、support、limitations、validation。不要把参数关联当作成因证明；无降雨、状态或初始化证据时不得确定相应原因。没有过程资料时明确只能展示指标排行。

## 时段和运行身份闸门

研究清单必须为 2.0，声明互不重叠且带时区的率定/验证时段，以及每个运行的 name、kind、哈希序列引用和列映射。缺证据时停止并提示迁移。事件按完整评分区间归属，跨界或混合分组时拒绝；基准与校正采用独立运行 ID，校正必须绑定父运行和后处理来源。生成与审查共用校验器，核对真实评分时间、模拟值与观测；直接对比必须使用相同评分样本。不得用论述或清单标签代替证据。
