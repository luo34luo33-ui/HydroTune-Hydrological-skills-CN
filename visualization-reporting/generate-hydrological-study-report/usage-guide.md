# 使用指南

## 显式研究清单

路径相对于清单所在目录。source ID 使用小写字母、数字、连字符；每个 simulation/evaluation/calibration source 只能绑定一个 run。所有 run 必须至少有一个评价成果。

```json
{
  "schema_version": "2.0",
  "split_periods": {
    "calibration": {"start": "2011-01-01T00:00:00+08:00", "end": "2019-12-31T23:00:00+08:00"},
    "validation": {"start": "2020-01-01T00:00:00+08:00", "end": "2020-12-31T23:00:00+08:00"}
  },
  "title": "流域降雨径流研究",
  "language": "zh",
  "required_preprocessing": ["discharge", "forcing"],
  "sources": [
    {"id": "discharge", "stage": "preprocessing", "result": "discharge/result.json"},
    {"id": "forcing", "stage": "preprocessing", "result": "forcing/result.json"},
    {"id": "hbv", "stage": "simulation", "result": "hbv/result.json"},
    {"id": "hbv-validation", "stage": "evaluation", "result": "metrics/result.json",
     "context": {"mode": "continuous", "split": "validation",
                 "period": {"start": "2020-01-01T00:00:00+08:00", "end": "2020-12-31T23:00:00+08:00"}}}
  ],
  "runs": [{"id": "hbv-run", "name": "HBV", "kind": "baseline",
            "simulation": "hbv", "evaluations": ["hbv-validation"],
            "series": {"path": "hbv/simulation_table.csv", "sha256": "填写实际文件的64位SHA-256",
                       "time_column": "time", "simulated_column": "Q_total", "unit": "m3/s"}}]
}
```

stage 可选 preprocessing、spatial、simulation、postprocessing、calibration、evaluation、atlas；支持的当前上游技能和变量定义见 `assets/upstream-catalog.json`。未知技能和非 1.0 契约拒绝使用。可选 source.run_id 将图册等成果关联到已有运行；不得与 simulation/evaluation/calibration 的运行绑定冲突。评价必须声明 mode、split 和带时区的 period；event_collection 另指定 event_ids。读取已哈希评价输入并按 scored/is_warmup 排除非评分行，核对真实时间、观测和模拟列。缺少输入证据、跨分界事件、范围冲突或运行身份不符时停止。

## prepare → Agent → finalize

```text
python scripts/generate_hydrological_study_report.py prepare --study-manifest study-manifest.json --output-dir draft
```

Agent 阅读 draft/report.json、索引及相关原始 artifacts。仅填写各章节 paragraphs 和 narrative_provenance，例如：

```json
{
  "id": "validation-nse",
  "kind": "fact",
  "text": "验证期 NSE 为 0.850000。",
  "evidence_ids": ["ev-实际索引中的标识"],
  "numeric_bindings": [{"evidence_id": "ev-实际索引中的标识", "text": "0.850000"}]
}
```

非数值段落仍须填写 numeric_bindings 为 []。事实、推断与建议必须有证据；pending 可无引用。所有正文数字须绑定原始或派生统计值，允许整数、一位、三位及六位小数，指标默认三位小数；日期、事件 ID、算法编号等优先引用上下文表格，在段落中用文字描述，避免将标识误作数值。正文不增加手写表格或图像，使用报告自动附加的表图。

总体结论（overview）、总体指标（evaluation）、可能误差来源（discussion）、限制与建议（limitations）四个章节必须至少各有一段 Agent 论述；其余按证据填写。将 narrative_provenance.author 填为实际作者/Agent 标识，completed 设为 true。草稿与最终输出使用不同目录，允许在新目录用同一结构化草稿重复 finalize。

```text
python scripts/generate_hydrological_study_report.py finalize --study-manifest study-manifest.json --report-json draft/report.json --output-dir report
```

language 默认为 zh，也支持 en。Markdown/JSON/CSV 不需要文档工具或模型 API。读取上游 XLSX/Parquet 时安装仓库 timeseries 依赖。输出目录非空默认拒绝；--overwrite 仅替换声明文件和本次同名图件，不删除未知文件，禁止覆盖输入。失败输出 result.json 和 data-gaps.json/.csv，不保留旧正文。

## 输出

report.md、report.json、summary.csv、evidence-index.json/.csv、data-gaps.json/.csv、figures/ 和 result.json。每条 evidence 包含来源、哈希、JSON Pointer 或表格行列定位、原值、单位、定义、上下文和可用状态。CSV 中的标识及原始字符串不转换为浮点；六位小数只影响 display_value。未记录单位时标为 unspecified，不猜测单位。

生成成功退出 0；契约或运行错误退出 1。prepare 只生成草稿，不生成 report.md。finalize 之后尚须独立语义审查。

## 新版模拟汇总与过程诊断

预处理现在可省略；仍需每个运行的模拟与评价成果。固定章节为总体结论、评价范围与总体指标、率定与验证表现、NSE 最低事件、可能误差来源、限制与建议。业务 summary.csv 仅含汇总统计，不含哈希或字段定位。report.json 输出版本 2.0，研究清单也升级为 2.0；旧清单必须迁移后再生成或审查。

清单 analysis.worst_event_count 默认 5。事件统计均为不加权汇总，P10/P90 使用线性插值，逐指标报告有效数与不可用数。不存在上游合格判断时不生成合格率。最差事件按每个评价来源分别筛选，因此不会混合不同单位、时段或 split。

可在 analysis.event_processes 数组显式关联过程：
```json
{
  "run_id": "hbv-run", "evaluation_source": "hbv-validation",
  "event_id": "event-a", "path": "aligned/event-a.csv",
  "sha256": "由实际文件计算的六十四位十六进制摘要",
  "time_column": "time", "observed_column": "observed", "simulated_column": "simulated",
  "warmup_column": "is_warmup", "scored_column": "scored"
}
```
过程流量须为 m3/s，与评价使用的事件、时段及评分样本一致；工作簿多工作表时指定 sheet。可选 precipitation_column 与 context 提供明确单位、warm-up、参数及状态背景，不推测列名或文件关系。显式关联但损坏、时段不一致或数值不可用的过程使生成失败；未关联过程时保留排行并说明诊断不可用。

候选解释结构：
```json
"hypotheses": [{
  "candidate": "可能存在汇流时序偏差",
  "support": "过程证据显示模拟峰偏晚",
  "limitations": "尚无独立河道参数与降雨时序证据",
  "validation": "核对降雨时区、汇流参数和观测时间轴"
}]
```
使用 inference 类型并在 evidence_ids 引用对应过程与指标证据。独立语义审查判断解释是否受支持，不自动认可上述示例。正文、图件、业务表均不展示技术核验台账。

每个有过程证据的入选事件都必须有一个 inference 段落，其 diagnosis 为 {"run": "运行 ID", "evaluation": "评价来源 ID", "event_id": "事件 ID"}，填写 hypotheses 并引用 prepare 提供的 process-* 证据。其他数字仍需 numeric_bindings。过程 flow_unit 默认且仅支持 m3/s；提供降雨列时必须显式指定 precipitation_unit: "mm/step"，不静默换算。

## 从旧研究清单迁移

1. 将 schema_version 改为 2.0，补齐 split_periods.calibration/validation；时间必须有显式时区，区间端点均包含且不可重叠。
2. 为每个 run 补齐非空且互不重复的 name、kind 和 series（path、sha256、time_column、simulated_column、unit；XLSX 另填 sheet）。series 必须是该 simulation 来源的真实 artifact。
3. 每个评价来源补齐 period，并保留指标 result.inputs 中的原始评分表及哈希。模拟列按时间与 run.series 精确核对，不按行号猜测。事件指标表必须保留 start/end 或 start_time/end_time，与完整评分过程一致。预热行不参与范围判断。
4. 旧的全时段事件集合不得统一写 validation；拆成对应时段的真实评价成果。跨分界事件拒绝使用，不自动切断。

校正运行使用独立 ID，例如 xaj-corrected；kind=corrected、parent_run=xaj-baseline、simulation 和 postprocessing 均指向 stage=postprocessing 的残差校正预测 result。series 指向 corrected_table 的校正列；预测表必须保留 parameters.base_column 对应的基准列，其数值要与 parent_run 一致，并保存 model_used artifact。

可用 comparisons 数组显式声明对比：`{"evaluations":["eval-baseline","eval-corrected"]}`。两来源须属于不同运行，且观测、评分时间、事件集合、mode 和 split 完全一致；校正与父运行同一分组的评价也自动检查同样的样本可比性。不同样本时先统一评价输入并重算指标，报告不偷偷求交集或替换上游指标。表格同时保留运行名称、评价来源和时段。数值按已有文件的解析值精确匹配；导出切片时应保留数值精度。

旧指标成果的输入若包含非评分或预热行、但未声明 scoring_policy=exclude_unscored_and_warmup，必须用当前评价 skill 重生成指标；报告不会把未评分行自行从已有指标中扣除。
