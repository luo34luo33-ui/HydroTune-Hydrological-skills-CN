---
name: derive-potential-evapotranspiration
description: 适用于从完整实测气象量按 FAO-56 日或小时方程计算 ETo，显式映射后另输出 PET 或 E0；不适用于缺项估算或隐式转换。
metadata:
  category: data-processing
  domains:
    - data-processing
  tool_type: python
  primary_tool: pandas
  related_skills:
    - data-processing/prepare-model-forcing-timeseries
    - spatial-analysis/aggregate-forcing-to-model-units
---

# 计算参考蒸散 ETo

按[使用指南](usage-guide.md)提交实测气象与站点高程，字段见[数据契约](data-contract.yaml)。

## 决策规则

- 日尺度需要气温均值、最高/最低气温、净辐射、实测水汽压和 2 m 风速；小时尺度需要均温、净辐射、土壤热通量、实测水汽压和 2 m 风速。
- 不估算缺少的气象量；始终保留原始 `ETo_mm`。
- 只有显式声明 `mapping.target`、正转换系数和负值策略时才产生模型用 PET/E0。
