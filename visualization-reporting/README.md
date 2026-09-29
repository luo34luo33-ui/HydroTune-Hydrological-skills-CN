# visualization-reporting（可视化与报告）

本分类承载水文时序图、事件图、诊断图、空间图、表格和结构化报告，确保事实、推断、建议和限制清晰可追溯。

## 边界

- 指标和诊断证据的核心计算归入 `evaluation-diagnostics`。
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

## 固定图册规范

- 空间、洪水事件和率定图册样式版本分别为 `hydrotune.spatial-atlas.v1`、`hydrotune.flood-event-atlas.v1` 和 `hydrotune.model-calibration-atlas.v1`；三者完全离线、可复现。
- 每张图包含 `2400×1600 px` PNG、SVG 与 `figure.json`。
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
