# 使用指南

## 安装依赖

在仓库开发环境中安装 `visualization` 可选依赖；安装后的 Skill 自身不需要 WhiteboxTools。

## 命令

```text
python scripts/render_dem_hydrology_atlas.py \
  --prepare-result PATH \
  [--extract-result PATH] \
  [--language zh|en] \
  --output-dir PATH \
  [--overwrite]
```

`--language` 默认为 `zh`。输出目录非空时默认拒绝写入；`--overwrite` 只替换本 Skill 声明的图件和结果文件。

## 固定模板

- `dem-basin-context`：标准 DEM、真实流域边界与分析范围。
- `dem-terrain`：分析 DEM 分层设色和 hillshade。
- `flow-accumulation-network`：`log1p` 显示的汇流累积、地形阴影和提取河网。
- `stream-order-subbasins`：柔和子流域分区及 Strahler 分级河网。

只有前两张依赖 prepare result；后两张要求有效 extract result。每张图生成同名 `.png`、`.svg` 和 `.figure.json`。

## 解释边界

图册用于表达已有证据，不用于判断阈值是否科学、修正流域边界、重新划分子流域或解释水文过程原因。上游 warning 会原样进入图册状态与图内标识。
