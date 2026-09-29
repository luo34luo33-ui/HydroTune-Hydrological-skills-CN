---
name: prepare-model-forcing-timeseries
description: 适用于将本地站点降雨、PET 或 E0 的 CSV、XLSX、Parquet 序列标准化为有时区、步长和哈希证据的 mm/步表；不适用于推断蒸散语义或填补缺测。
metadata:
  category: data-processing
  domains:
    - data-processing
  tool_type: python
  primary_tool: pandas
  related_skills:
    - data-processing/derive-potential-evapotranspiration
    - spatial-analysis/aggregate-forcing-to-model-units
---

# 准备模型 forcing 时序

按[使用指南](usage-guide.md)指定列、单位、时区、时间戳和原始数值语义。读取[数据契约](data-contract.yaml)后运行 CLI。

## 决策规则

- 仅处理 `P`、`PET` 或 `E0` 中一种明确变量；不同变量分次运行。
- 累计雨量的复位必须有逐行证据；第一行用作基线。
- 缺测、重复、负值和不规则步长停止，不自动补值。
- 原始文件只读，输出目录包含标准长表和逐文件哈希。
