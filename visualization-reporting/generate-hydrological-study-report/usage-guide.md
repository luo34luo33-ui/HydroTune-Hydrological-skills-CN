# 使用指南

## 显式研究清单

路径相对于清单所在目录。source ID 使用小写字母、数字、连字符；每个 simulation/evaluation/calibration source 只能绑定一个 run。所有 run 必须至少有一个评价成果。

```json
{
  "schema_version": "1.0",
  "title": "流域降雨径流研究",
  "language": "zh",
  "required_preprocessing": ["discharge", "forcing"],
  "sources": [
    {"id": "discharge", "stage": "preprocessing", "result": "discharge/result.json"},
    {"id": "forcing", "stage": "preprocessing", "result": "forcing/result.json"},
    {"id": "hbv", "stage": "simulation", "result": "hbv/result.json"},
    {"id": "hbv-validation", "stage": "evaluation", "result": "metrics/result.json",
     "context": {"mode": "continuous", "split": "validation",
                 "period": {"start": "2020-01-01", "end": "2020-12-31"}}}
  ],
  "runs": [{"id": "hbv-run", "simulation": "hbv", "evaluations": ["hbv-validation"]}]
}
```

stage 可选 preprocessing、spatial、simulation、calibration、evaluation、atlas；支持的当前上游技能和变量定义见 `assets/upstream-catalog.json`。未知技能和非 1.0 契约拒绝使用。可选 source.run_id 将图册等成果关联到已有运行；不得与 simulation/evaluation/calibration 的运行绑定冲突。评价必须显式声明 mode 和 split；continuous 指定 period，event_collection 指定 event_ids。此范围来自用户清单，报告会注明其来源；可读取的已哈希评价输入会核对时段或事件，独立语义审查还须检查 split 和模型关系，不能将声明当作已验证事实。

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

预处理现在可省略；仍需每个运行的模拟与评价成果。固定章节为总体结论、评价范围与总体指标、率定与验证表现、NSE 最低事件、可能误差来源、限制与建议。业务 summary.csv 仅含汇总统计，不含哈希或字段定位。report.json 输出版本 2.0，旧清单版本仍为 1.0。

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
