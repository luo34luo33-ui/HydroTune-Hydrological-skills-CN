---
name: extract-flood-events
description: 适用于从已经完成 Eckhardt 分割的规则连续流量序列提取、合并和验证洪水场次；不适用于降雨事件、模拟结果切片、人工边界编辑或缺少显式识别参数的任务。
metadata:
  category: data-processing
  domains:
    - data-processing
    - hydrological-modeling
  tool_type: python
  primary_tool: scipy
  related_skills:
    - data-processing/prepare-discharge-timeseries
    - data-processing/separate-baseflow-eckhardt
    - visualization-reporting/visualize-flood-event-atlas
---

# 提取洪水事件

按显式 YAML 配置执行 quickflow 洪峰检测、总流量边界搜索、复峰合并、主峰收紧、规模过滤和事件打包。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## 决策规则

- 配置必须包含全部科学参数；示例值用于复现来源脚本，不是通用默认值。
- warm-up 只用于导出，不参与洪峰、边界、历时或洪量计算。
- 所有指标使用标准 `m3/s` 和规则时间步，洪量单位为 `m3`。
- 内置 QC 失败返回退出码 2；零事件返回 warning，不自动降低阈值。
- 本 Skill 不生成图，正式事件图由 `visualize-flood-event-atlas` 负责。
