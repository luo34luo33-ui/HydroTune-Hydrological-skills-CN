---
name: visualize-hydrobase-atlas
description: 适用于把已构建并完成独立 QC 的 HydroBase 拓扑、形态属性和检查证据渲染为固定版式成果图；不适用于重新验证拓扑、解释失败原因、修改河网关系或制作任意主题地图。
metadata:
  category: visualization-reporting
  domains:
    - visualization-reporting
    - spatial-analysis
  tool_type: python
  primary_tool: matplotlib
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - visualization-reporting/visualize-dem-hydrology-atlas
---

# HydroBase 成果图册

拓扑图使用 `hydrotune.hydrobase-topology.v2`；QC 看板使用 `hydrotune.hydrobase-qc-dashboard.v2`；形态三联图使用论文风格 `hydrotune.hydrobase-morphometry.v2`。参数和命令见[使用指南](usage-guide.md)，数据语义见[数据契约](data-contract.yaml)。

## 决策规则

- 同时要求 build result 与 validation result，并核验所有被使用 artifact 的 SHA-256。
- QC 为 `success` 时生成三张成果图；`warning` 时生成三张图；看板在检查汇总中保留 warning 状态，拓扑和形态图将 warning 保留在元数据。
- validation 中存在科学 QC `FAIL` 时只生成带红色 `QC FAILED` 标识的 dashboard，运行状态为 `error`，退出码为 `2`。
- validation 运行错误或缺少检查 artifacts 时不生成图，退出码为 `1`。
- dashboard 只陈列已有 QC 证据，不重新计算检查，也不推断失败原因。

## 视觉边界

只标注出口与主要河段，按固定防拥挤规则选择流向箭头。形态三联图白底、无主副标题，下方依次标注 (a)(b)(c) 图名；中文宋体、数字英文 Times New Roman，黑色边框，经纬网、比例尺、指北针及真实流域边界齐全。画布按流域外接范围自适应，宽高比约 3:1，不拉伸地理形状。面积与层级采用 viridis、plasma，坡度采用深蓝到深红渐变。拓扑图白底单主图、无主副标题，图名用于文件名；图内大字号图例、子流域数量、黑白指北针、经纬网与向内刻度、真实流域边界及浅灰子流域边界。拓扑图中文宋体、数字英文 Times New Roman，画布最长边 2400 像素且按流域比例自适应。QC 看板与拓扑图使用同一紧凑单主图底图，检查汇总在主图空白处；仅有 WARN/FAIL 时展示重要证据，删除图内限制说明；不使用在线瓦片、彩虹色带、3D 地形或运行时改色。

## 完成条件

已允许生成的每个模板都必须具有 PNG（形态三联图宽 2400 像素、高度自适应；拓扑图最长边 2400 像素且自适应；QC 看板同样最长边 2400 像素、自适应）、可解析 SVG 和通过 schema 的 `figure.json`，并由总 `result.json` 记录所有输出哈希与上游状态。
