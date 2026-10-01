# visualization-reporting（可视化与报告）

本分类承载水文时序图、事件图、诊断图、空间图、表格和结构化报告，确保事实、推断、建议和限制清晰可追溯。

## 边界

- 性能指标的核心计算归入 `evaluation-diagnostics`；报告模块可执行显式口径下的描述统计、显著峰计数和过程偏差摘要，不重算 NSE 等性能指标。
- 空间拓扑构建归入 `spatial-analysis`。
- 为报告准备的模拟结果聚合归入 `post-processing`。
- 可视化不得为展示效果改写科学结论。

## 当前 Skills

### `visualize-dem-hydrology-atlas`

消费 `prepare-dem-analysis-grid` 与可选 `extract-dem-stream-network` 的 `result.json`，固定生成：

- `dem-basin-context`
- `dem-terrain`
- `flow-accumulation-network`
- `stream-order-subbasins`

缺少 extract result 时只生成前两张并返回 `warning`。

### `visualize-hydrobase-atlas`

消费 `build-hydrological-topology` 与 `validate-hydrological-topology` 的成果，固定生成：

- `hydrobase-topology`
- `hydrobase-morphometry`
- `hydrobase-qc-dashboard`

科学 QC FAIL 时只生成带失败标识的 dashboard，退出码为 `2`；运行错误不生成图。

### `visualize-flood-event-atlas`

消费 `extract-flood-events` 的已校验 artifacts，固定生成：

- 一张 `flood-event-overview` 完整序列总览；
- 每个选中事件一张 `event-<id>-hydrograph`，包含 warm-up、事件窗口、总流量、基流、直接径流和既有事件指标。

长序列仅在显示层使用确定性的最小—最大包络；事件边界和指标不会被重算。支持 `all`、`first` 和 `largest` 选择方式，后两者必须显式给出最大事件数。

### `visualize-model-calibration`

消费任一受支持的 `model-calibration` 结果，固定生成：

- 收敛轨迹、参数边界位置、率定/验证指标和运行证据总览；
- continuous 全验证期或 event_collection 逐场观测—模拟过程图；
- 只使用 validation scored 样本的观测—模拟散点图和 1:1 参考线。

该 Skill 不重算指标、不重新率定、不拼接独立事件，也不根据 validation 结果重新选择参数。

## 研究报告与独立审查

`generate-timeseries-data-processing-report` 汇总时序资料、洪水数量、洪峰/洪量规模分布、显著单峰与多峰比例及事件总体特征。参数显式提供，不重提取事件。

`generate-spatial-data-processing-report` 汇总半分布式空间基础、子流域数量与面积、河段数量与长度、上下游拓扑及空间单元映射，不重新构建空间成果。

`generate-hydrological-study-report` 仅要求模拟与评价，prepare 整理总体指标和 NSE 最低事件过程，Agent 编写条件性解释，finalize 渲染报告。率定/验证、连续/事件、warm-up/scored 分开。

`review-hydrological-study-report` 独立重读上游，审查三类报告的统计、表图与论述，未完成 Agent 语义审查不得整体通过。

正文和业务汇总聚焦水文结论与限制；哈希、字段定位和完整核验记录仅保留机器附件。首版输出 Markdown、JSON、CSV 与静态图，不提供 DOCX/PDF。

## 图件文件规范

- DEM 图册使用 `hydrotune.dem-hydrology-atlas.v2`：白底、无主副标题、内置图例与色标、流域边界裁切，中文宋体、英文 Times New Roman。洪水图册使用 `hydrotune.flood-event-atlas.v2`：白底论文版式、图内三项指标摘要、黑灰过程线、红色洪峰，可叠加已确认流域平均雨量。率定图册使用 `hydrotune.model-calibration-atlas.v2`：白底无图名，实测黑线，支持多模拟及后处理对比，图例显示本场评分样本 NSE。HydroBase 形态三联图使用 `hydrotune.hydrobase-morphometry.v2`（白底三联地图、下方图名、经纬网、比例尺、指北针、真实流域边界）；拓扑图使用 `hydrotune.hydrobase-topology.v2`（紧凑白底主图、内置图例、经纬网、真实流域及浅灰子流域边界）；QC 看板使用 `hydrotune.hydrobase-qc-dashboard.v2`（主图内检查汇总、异常证据按需显示）；均完全离线、可复现。
- 每张图包含 PNG、SVG 与 `figure.json`；DEM v2 PNG 最长边为 2400 px，另一边按流域长宽比调整；洪水 v2 为 `2400×1200 px`，其他图册保留 `2400×1600 px`。
- PNG 面向屏幕、文档和汇报插图，不绑定 A4；SVG 用于无限缩放、排版和印刷。
- 中英文只改变文字，不改变色带、布局或数据表达。
- 不使用彩虹色带、在线瓦片、3D 地形、装饰性阴影或任意主题参数。

## 安装

```bash
bash ./install-codex.sh --categories visualization-reporting
```

```powershell
./install-codex.ps1 --categories visualization-reporting
```

安装产物把 CLI 放入 `scripts/`，固定样式放入 `assets/`。每个 Skill 都是可独立安装、自包含的成果单元。
