---
name: extract-flood-events
description: 适用于从已完成 Eckhardt 分割的规则连续流量序列提取、合并洪水场次，剔除突跳长直线过程并按用户确认的无雨与雨量缺失策略筛选；不适用于降雨事件、模拟结果切片、人工边界编辑或缺少显式识别参数的任务。
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

按显式 YAML 配置执行 quickflow 洪峰检测、总流量边界搜索、复峰合并、主峰收紧、规模过滤、形态与降雨策略筛选和事件打包。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。

## 提取前询问

每个新提取任务开始时，先分别取得用户选择（均推荐剔除）：

1. “实测没有降水的洪水是否保留？”写入 `rainfall_support.no_rainfall_policy`。
2. “降雨资料全缺失的洪水是否保留？”写入 `rainfall_support.missing_rainfall_policy`。

值为 `keep` 或 `exclude`。用户已在当前任务指令、已确认配置或会话中明确提供选择时，直接使用，不重复询问；未明确选择时询问，不能仅复制示例中的推荐值当作用户已选择。脚本不交互询问，缺少任一策略明确报错。配置与结果记录实际选择。

## 决策规则

- 流量识别与降雨支持参数必须显式提供；形态门槛有可配置的保守推荐值。示例保留来源脚本的流量参数，但新增筛选会改变最终事件集，不是流量与降雨科学参数的通用默认配置。
- 配置必须为 `1.2`，正式提取前取得流域对应的 `rainfall_support.lookback_hours` 和 `min_observed_rainfall_mm`，确认两个保留策略。旧版 `1.0/1.1` 必须迁移。
- 规模过滤之后检查事件本体的总流量：最长近似恒定段持续至少 24 小时、占事件历时至少 50%，且事件内存在不超过 2 个时间步、幅度至少中位流量 10% 的突跳，三条件同时满足则整场剔除。恒定段最大最小差容差为中位流量的 0.5%；参照值至少为 `1e-9 m³/s`。门槛可配置，详情见使用指南；不拆分、不修改原始流量，也不推断成因。
- 必须提供显式语义的流域平均降雨。关联窗口为事件起点前指定小时数至主洪峰，包含窗口内的区间末端时间戳；只累计有效非负雨量，不使用洪峰后的降雨。
- 零雨是有效记录，有雨和零雨交替允许保留。局部缺测不自动否决：已观测累计雨量达到正数门槛即可保留并标记。窗口完整且全零雨、窗口全缺测分别执行用户选择；少雨或局部缺测且观测雨量不足仍剔除，不归为已确认无雨。缺测不填零。
- 有雨、无雨保留选择和全缺测保留选择均不能绕过形态过滤。上游出库不能冒充降雨支持；按用户策略保留的事件必须标明缺少降雨支持并返回 warning。满足筛选条件不代表整场及预热期 forcing 完整，也不证明洪水由降雨引起。
- 最终编号与导出只包含保留事件；`rainfall_screening.csv` 保存通过规模过滤的全部候选场次及原因。
- warm-up 只用于导出，不参与洪峰、边界、历时或洪量计算。
- 所有指标使用标准 `m3/s` 和规则时间步，洪量单位为 `m3`。
- 内置 QC 失败返回退出码 2；零事件返回 warning，不自动降低阈值。
- 本 Skill 不生成图，正式事件图由 `visualize-flood-event-atlas` 负责。
