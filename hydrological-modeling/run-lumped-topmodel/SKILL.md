---
name: run-lumped-topmodel
description: 适用于用真实且已验证的地形指数与距离面积分布、连续带时区面平均降雨和 PET 运行集总式 TOPMODEL；不适用于地形推导、参数率定、积雪或正式制图。
metadata:
  category: hydrological-modeling
  domains: [hydrological-modeling, spatial-analysis]
  tool_type: python
  primary_tool: python
  related_skills: [spatial-analysis/derive-topmodel-terrain-inputs]
---

# 集总式 TOPMODEL 前向运行

输入必须来自 `derive-topmodel-terrain-inputs` 的已核验 `result.json`，以及完整、规则、带时区的面平均 `time,P,PET` 连续序列。按[使用指南](usage-guide.md)配置参数，遵守[数据契约](data-contract.yaml)。一次运行一条序列，保留预热行。

## 方程与边界

参考 [GRASS 8.5.0 `r.topmodel` 手册](https://grass.osgeo.org/grass-stable/manuals/r.topmodel.html) 的参数体系，独立实现根区亏缺、非饱和区排水、地形指数控制的局地饱和亏缺、饱和超渗、地下水出流和距离面积汇流。启用 `infiltration_excess` 时使用 Green–Ampt 容量，要求 `K0,psi,dtheta`；关闭时不接受这些未用参数（`td<=0` 时 K0 是排水参数，例外）。所有内部水量为米，时间为小时。

结果提供各分量、实际 ET、蓄量状态、出口流量、末状态及包含末端汇流蓄量的水量残差。缺测、重复时间、错误单位、参数越界、非有限状态、输入哈希不匹配和超出显式水量容差都停止。输出目录非空默认拒绝；输入不修改。教学脚本 `topmodel.py` 与本实现的单位及过程结构不同，不用于数值等价判定。
