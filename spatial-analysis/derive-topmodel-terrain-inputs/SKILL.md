---
name: derive-topmodel-terrain-inputs
description: 适用于从已验证的单出口 HydroBase 与同网格 Whitebox D8 成果推导 TOPMODEL 地形指数类别及距离面积曲线；不适用于构建拓扑、模拟径流或修补失败空间成果。
metadata:
  category: spatial-analysis
  domains: [spatial-analysis, hydrological-modeling]
  tool_type: python
  primary_tool: rasterio
  related_skills: [spatial-analysis/extract-dem-stream-network, spatial-analysis/build-hydrological-topology, spatial-analysis/validate-hydrological-topology]
---

# 推导 TOPMODEL 地形输入

使用[使用指南](usage-guide.md)中的 CLI，从真实流域掩膜、填洼 DEM、D8 和以像元数计的汇流累积推导 `ln(a/tanβ)` 类别与到出口的距离面积曲线。字段和单位见[数据契约](data-contract.yaml)。

## 使用边界

上游 HydroBase 必须独立验证、仅有一个出口，栅格必须同一米制投影网格。类别数、距离分箱及最小坡度由用户明确给出。每个真实流域像元必须沿 D8 到唯一出口。无效流路、多个出口、哈希变化或空间 QC FAIL 时停止。

## 数值定义

像元坡度取沿 D8 方向的中心高差除以中心距离；低于最小值（含出口与平地）的像元按最小坡度计算，并记录数量。每单位等高线长度的上坡贡献面积为 `flow_accumulation_cells × cell_area_m2 / D8_contour_width_m`。地形指数等于该面积除以坡度后取自然对数。类别均值按所含像元计算，面积权重是像元数比例。距离从出口像元中心起算，沿 D8 中心线求和。

[GRASS `r.topidx` 文档](https://www.ibiblio.org/pub/packages/gis/grass/gdp/html_grass64/r.topidx.html)使用同一数学指数定义；本 Skill 的 D8 坡度与汇流面积离散规则可能与 GRASS 原生地形算法不同，比较模型方程时应使用同一类别表。输入只读，非空输出目录默认拒绝。
