# spatial-analysis（空间分析）

本分类承载 DEM、流域边界、子流域、河网、站点、模型单元和坐标参考系相关的空间处理，重点表达上下游、汇流顺序与空间连接等水文拓扑语义。

## 边界

- 产流、汇流和状态演算归入 `hydrological-modeling`。
- 只负责结果表达的空间制图归入 `visualization-reporting`。
- 与建模或可视化的交叉通过 metadata 声明，不复制 Skill。

## Atomic Skills

| Skill | 职责 | 关键边界 |
|---|---|---|
| `prepare-dem-analysis-grid` | CRS、垂直单位、真实边界和 buffer 分析网格准备 | 不猜测 CRS 或单位 |
| `extract-dem-stream-network` | WhiteboxTools D8 河网、河段、Strahler 与子流域 | 不执行权威河网 burning |
| `build-hydrological-topology` | 完整分析范围追踪、真实边界裁切和 HydroBase 建库 | 不以裁切线段接触猜测上下游 |
| `validate-hydrological-topology` | 出口、引用、循环、面积、网格和几何独立 QC | 不自动修补失败拓扑 |
| `derive-topmodel-terrain-inputs` | 从已验证单出口 HydroBase 与 D8 栅格生成 TOPMODEL 地形指数和距离面积分布 | 不运行径流模型或修复空间 QC |
| `aggregate-forcing-to-model-units` | 泰森站点或 NetCDF/GeoTIFF 格点面积加权至流域和子流域 | 不填补缺测或重分配缺失权重 |

标准衔接顺序为：

```text
prepare-dem-analysis-grid
  -> extract-dem-stream-network
  -> build-hydrological-topology
  -> validate-hydrological-topology
  -> derive-topmodel-terrain-inputs
```

每个 Skill 的 `examples/` 都是可独立安装和运行的参数化 CLI。运行结果遵循 `resources/schemas/spatial-run-result.schema.json`，未知 CRS、垂直单位、河网阈值或预期出口数量不得由 Agent 静默填入。
