# 使用指南

## 命令

```text
python scripts/render_hydrobase_atlas.py \
  --build-result PATH \
  --validation-result PATH \
  [--language zh|en] \
  --output-dir PATH \
  [--overwrite]
```

`--language` 默认为 `zh`。输出目录非空时默认拒绝写入；`--overwrite` 只替换本 Skill 声明的输出。

## 固定模板

- `hydrobase-topology`：子流域分区、Strahler 分级河网、流向、主要标签与出口。
- `hydrobase-morphometry`：子流域面积、河段坡度和拓扑层级三联图。
- `hydrobase-qc-dashboard`：拓扑地图、PASS/WARN/FAIL 汇总、出口信息和已有限制说明。

validation 状态为 `warning` 时三张图均显示克制的 warning 标识。存在科学 QC `FAIL` 时只输出失败 dashboard，方便报告失败证据但防止把其余成果误当作可发布结果。

## 解释边界

本 Skill 不建立新拓扑、不重算形态指标、不改变出口，也不解释失败的成因。需要技术检查时使用 `validate-hydrological-topology`；需要修复数据时回到对应 spatial-analysis Skill。
