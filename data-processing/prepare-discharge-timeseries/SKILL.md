---
name: prepare-discharge-timeseries
description: 适用于把 XLSX、CSV 或 Parquet 连续流量记录标准化为有单位、有时区证据且通过时间轴 QC 的序列；不适用于洪水场次识别、基流分割或对未确认变量语义作推断。
metadata:
  category: data-processing
  domains:
    - data-processing
  tool_type: python
  primary_tool: pandas
  related_skills:
    - data-processing/separate-baseflow-eckhardt
    - data-processing/extract-flood-events
---

# 准备连续流量时序

先确认时间列、流量列、单位和时区，再生成下游可验证的标准时序。命令见[使用指南](usage-guide.md)，字段语义见[数据契约](data-contract.yaml)。

## 决策规则

- 不按列名猜测单位、时区或附加列语义。
- 缺测默认停止；有限内部插值、首尾裁切和重复值保留策略都必须显式选择。
- 禁止对首尾缺测作外推；每个插值点必须通过 `is_imputed` 保留证据。
- 时间不规则、负流量、单位未知或有效点不足时停止。
- 原始文件只读，所有产物写入独立输出目录。

## 完成条件

只有标准表、metadata 和 `result.json` 可读取且状态不是 `error`，才能进入 Eckhardt 基流分割。
