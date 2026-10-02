# 使用指南

## 两阶段独立审查

```text
python scripts/review_hydrological_study_report.py --report-dir report --study-manifest study-manifest.json --output-dir review-initial
```

脚本直接重读上游成果并与报告比较，输出 review.json、review.md、review-checklist.csv、result.json。未完成语义审查时返回 needs_human_review，退出码 2。输出目录必须在报告包之外。

Agent 根据 review.json 的 package_fingerprint 阅读报告的每个段落及其原始引用，创建 semantic-review.json：

```json
{
  "schema_version": "1.0",
  "package_fingerprint": "本轮审查输出的64位哈希",
  "reviewer": "实际审查者标识",
  "paragraphs": [
    {"id": "validation-nse", "evidence_ids": ["ev-实际索引中的标识"],
     "verdict": "supported", "rationale": "已核对原始指标、验证输入时段和样本范围，论述仅描述该验证结果。"}
  ]
}
```

必须覆盖全部段落且每段恰好一次，evidence_ids 与被审段落的全部引用一致。unsupported 表示证据不能支撑论述，uncertain 表示存在待核实或无法定论事项。不得通过复制标题或仅检查引用是否存在来填写 supported。

```text
python scripts/review_hydrological_study_report.py --report-dir report --study-manifest study-manifest.json --semantic-review semantic-review.json --output-dir review-final
```

语义文件放在报告包和审查输出目录之外；该 CLI 不调用远程模型，也不自动产生语义判定。Agent 负责逐段审查，脚本负责验证覆盖与绑定。来源和包的 fingerprint 改变时必须重新审查。

## 状态与问题定位

- pass：确定性和 Agent 语义审查均完成，无阻断问题，退出 0。
- needs_revision：存在数值、范围、引用、文件一致性或不受支持的论述问题，退出 2。
- needs_human_review：语义审查未完成或尚有 uncertain 项，退出 2。
- 运行失败：result.json 为 error，退出 1，不冒充审查通过。

检查清单记录报告位置、证据位置、实际值、期望值、严重程度、处理建议和是否需人工判断。Markdown 中稳定 section/paragraph 标识用于定位；手工改动触发不同步问题，审查器不自动修正。

JSON Pointer 定位 JSON 值；/rows/零基行号/列名 定位 CSV 数据行，XLSX 另带 /sheets/工作表名。对于源码层面的缺失单位或范围，仅能标记待核实，不能在审查时补造定义。运行依赖同生成器。

## 三类报告入口

模拟报告使用 --study-manifest，时序报告使用 --processing-manifest，空间报告使用 --spatial-manifest；三者互斥，均使用 --report-dir、--output-dir、可选 --semantic-review 和 --overwrite。

审查重新读取上游与分析参数，不采信生成器 PASS 声明。新版还复核洪水阈值分类、显著峰型与分母、空间数量/面积/连接、运行及 split 分组、最差事件排名、过程诊断和自动图件。报告正文与 JSON 不一致时定位到 Markdown 行号，业务明细被修改时定位到相应 CSV；不自动同步或修改报告。

所有固定说明及 Agent 段落均有稳定 ID。独立 Agent 应逐段检查总体结论、推断强度和限制；特别检查把率定写成验证、把相对最差写成绝对极差、无过程支持的确定成因。没有完成语义审查，状态保持 needs_human_review，退出 2；确定问题为 needs_revision，退出 2；整体通过退出 0；运行失败退出 1。

旧 1.0 研究报告继续按原模板检查；旧 1.0 数据处理报告可以核对来源与数值表，但其未绑定正文保留人工审查项，建议重新生成 2.0 报告后取得完整确定性审查覆盖。

## 时段和运行身份闸门

研究清单必须为 2.0，声明互不重叠且带时区的率定/验证时段，以及每个运行的 name、kind、哈希序列引用和列映射。缺证据时停止并提示迁移。事件按完整评分区间归属，跨界或混合分组时拒绝；基准与校正采用独立运行 ID，校正必须绑定父运行和后处理来源。生成与审查共用校验器，核对真实评分时间、模拟值与观测；直接对比必须使用相同评分样本。不得用论述或清单标签代替证据。

旧研究清单迁移字段与示例见 generate-hydrological-study-report 使用指南；审查不兼容跳过时段和运行证据的旧清单。
