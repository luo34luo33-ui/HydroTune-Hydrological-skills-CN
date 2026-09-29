# 使用指南

安装后在任意目录运行（示例为 Skill 自身 `examples` 下的脚本）：

```powershell
python examples/derive_topmodel_terrain_inputs.py --stream-result PATH/stream/result.json --topology-result PATH/hydrobase/result.json --topology-qc-result PATH/qc/result.json --classes 30 --distance-bins 20 --minimum-slope 0.0001 --output-dir PATH/terrain
```

`--classes` 至少 2，`--distance-bins` 至少 1，`--minimum-slope` 是无量纲坡降。仅在明确重跑时用 `--overwrite`；只替换本 Skill 声明的产物。输出 `topographic_index.tif`、`topographic_index_classes.csv`、`distance_area.csv` 和 `result.json`。运行需 `numpy`、`rasterio`。输入错误退出 1，科学 QC 错误退出 2。

需先运行 `extract-dem-stream-network`、`build-hydrological-topology` 和 `validate-hydrological-topology --expected-outlets 1`。不要把示例命令中的类数、分箱和最小坡度当作流域默认值。
