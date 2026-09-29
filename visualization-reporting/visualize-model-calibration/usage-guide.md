# 使用指南

```text
python scripts/render_model_calibration_atlas.py \
  --calibration-result calibration-output/result.json \
  --language zh --output-dir calibration-atlas
```

语言必须显式选择 `zh` 或 `en`。事件集合默认逐场绘制全部 validation 事件；可用 `--max-events N` 按上游首次出现顺序限制图数，该选择只影响显示且会写入 `figure.json`。

每张图固定输出 `2400×1600 px` PNG、同布局 SVG 和独立 JSON。缺少可选降水列时仍可绘制过程图，但运行状态为 warning。
