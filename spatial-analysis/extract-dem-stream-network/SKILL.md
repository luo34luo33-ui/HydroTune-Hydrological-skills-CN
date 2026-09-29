---
name: extract-dem-stream-network
description: 适用于从已准备的米制 DEM 通过 WhiteboxTools 提取无河网蚀刻的 D8 河网、河段、Strahler 等级和子流域；不适用于需要权威河网 burning、出口点吸附、多流向或水动力河网的任务。
metadata:
  category: spatial-analysis
  domains:
    - spatial-analysis
    - hydrological-modeling
  tool_type: python
  primary_tool: whitebox-tools
  related_skills:
    - spatial-analysis/prepare-dem-analysis-grid
    - spatial-analysis/build-hydrological-topology
---

# 提取 DEM 河网与子流域

使用固定的 WhiteboxTools D8 链生成网格严格一致的水文 artifacts。详细命令见[使用指南](usage-guide.md)，文件语义见[数据契约](data-contract.yaml)。

## 关键约束

- 输入必须是米制投影、正像元面积的分析 DEM。
- 河网阈值必须以平方千米明确给出，再按完整仿射变换换算为像元数并向上取整。
- 使用 Whitebox 默认的非 ESRI D8 编码；不得在链中混用编码。
- 每一步都检查输出可读性、CRS、仿射变换、行列数和网格一致性。
- 没有河网时停止并报告阈值、像元面积和累积结果，不自动降低阈值。
- 已有输出不会被静默复用；只有显式 `--overwrite` 才可替换本 Skill 声明的文件。

## 方法边界

该方法没有官方河网约束，也不调用 FillBurn 或 BurnStreams。若研究需要让 DEM 河道与权威河网一致，应选择后续专用 Skill，而不是把本结果描述为经过 burning 的河网。

## 完成条件

所有核心栅格必须同网格，河段编号必须包含正整数，且 `result.json` 不得为 `error`。随后使用 `build-hydrological-topology` 在完整分析范围建立拓扑。
