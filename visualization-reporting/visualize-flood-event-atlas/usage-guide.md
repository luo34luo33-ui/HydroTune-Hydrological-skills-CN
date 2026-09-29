# 使用指南

```text
python scripts/render_flood_event_atlas.py --extract-result events/result.json \
  --language zh --selection all --output-dir event-atlas
```

`first` 或 `largest` 模式必须同时提供 `--max-events`。每张图固定输出 `2400×1600 px` PNG、同布局 SVG 和 `figure.json`。总览使用确定性分箱最小—最大包络，仅改变显示密度。
