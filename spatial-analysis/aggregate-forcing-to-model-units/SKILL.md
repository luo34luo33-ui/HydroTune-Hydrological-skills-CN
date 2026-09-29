---
name: aggregate-forcing-to-model-units
description: 适用于将标准站点或本地 NetCDF、GeoTIFF 格点 forcing 按真实流域及子流域交叠面积聚合；不适用于缺测补值或未经 QC 的空间输入。
metadata:
  category: spatial-analysis
  domains:
    - spatial-analysis
  tool_type: python
  primary_tool: rasterio
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - data-processing/prepare-model-forcing-timeseries
    - data-processing/derive-potential-evapotranspiration
---

# 聚合 forcing 到模型单元

提交已验证的单出口 HydroBase 和明确来源的 P、PET/E0 两层，使用[命令与示例](usage-guide.md)。字段与覆盖约束见[数据契约](data-contract.yaml)。

## 决策规则

- 站点用泰森单元与模型单元真实几何相交算权重；格点用像元几何相交算权重。
- 每变量逐单元报告面积覆盖；缺站、缺格点或覆盖不足停止，不重分配权重。
- 集总式需要 PET，半分布式 XAJ 需要 E0，语义必须与上游显式映射一致。
