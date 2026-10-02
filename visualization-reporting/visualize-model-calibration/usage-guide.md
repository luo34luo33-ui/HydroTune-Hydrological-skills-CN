# 使用指南

```text
python scripts/render_model_calibration_atlas.py \
  --calibration-result calibration-output/result.json \
  --language zh --output-dir calibration-atlas
```

语言必须显式选择 `zh` 或 `en`。事件集合默认逐场绘制全部 validation 事件；可用 `--max-events N` 按上游首次出现顺序限制图数，该选择只影响显示且会写入 `figure.json`。

每张图固定输出 `2400×1600 px` PNG、同布局 SVG 和独立 JSON。缺少可选降水列时仍可绘制过程图，但运行状态为 warning。

## 论文绘图与多过程对比

所有图件使用白底，去除主副标题，轴标题和刻度放大，中文宋体、数字英文 Times New Roman。过程图实测为黑色实线，模拟依次使用蓝、朱红、绿、紫等不同颜色。默认一条模拟过程线；通过 `--simulation-label 新安江` 命名主模拟线。图名只保留在机器元数据中。

可追加 `--comparison-manifest comparisons.json`，叠加其他水文模型或后处理过程。清单路径相对清单自身解析，例如：

```json
{
  "schema_version": "1.0",
  "join_on": ["event_id", "time"],
  "series": [
    {"label": "后处理", "path": "postprocessed.csv", "column": "Q_corrected", "unit": "m3/s", "split": "validation"},
    {"label": "HBV", "path": "hbv.csv", "column": "simulated", "unit": "m3/s", "split": "validation"}
  ]
}
```

连续模式使用 `join_on: ["time"]`；事件表只有步号时使用 `["event_id", "step"]`，横轴明确标为时间步。比较 CSV 必须包含关联键和指定模拟列；同一单位、唯一关联键、完全相同的验证事件和时间覆盖，允许行顺序不同，不插值、不补齐、不转换单位。观测与 scored 掩码统一使用主率定成果。最多八条模拟线（含主模拟线），保证颜色各异。单位从原始率定 problem 的 variables 读取；旧包没有单位证据时使用 `--flow-unit` 显式提供。

图例显示各模拟线本场洪水 NSE，保留三位小数。NSE 使用本图内 `scored=true` 样本，排除 warm-up；连续图计算本图验证时段 NSE。观测常量、评分样本少于两个时显示不可用。该值是绘图诊断，不修改或替代上游汇总指标；元数据记录数值、有效样本数、事件范围和公式。散点图仍仅使用评分样本，并按相同颜色显示各模拟序列。

## 连续模拟的逐场验证图

在原 continuous 率定成果上追加 `--event-manifest events.json`。继续生成总览、连续过程和散点图，额外绘制逐场过程，不启动模型、搜索参数或重置状态。

```json
{
  "schema_version": "1.0",
  "step_seconds": 3600,
  "events": {"path": "validation-events.csv", "sha256": "填写实际文件的64位SHA-256"}
}
```

事件表为 CSV，固定列 event_id,start,end；时间必须有时区，ID 非空且唯一，完整事件必须位于验证序列覆盖范围，端点落在真实评分样本上。清单路径相对 events.json 解析。step_seconds 为原连续序列步长，拒绝重复时间、缺失时间步和未对齐端点。不会自动裁切越界事件。--max-events 仅限制绘制数量，整个清单仍先校验。

```text
python scripts/render_model_calibration_atlas.py --calibration-result calibration/result.json --event-manifest events.json --language zh --output-dir atlas
```

叠加过程沿用 --comparison-manifest：先按时间绑定到完整验证序列，再截取所有模拟的同一事件范围。保留模拟值、观测值和评分标记。每图 JSON 的 event_slice 保存真实事件 ID、时间范围、评分样本数、原验证序列以及事件清单的路径和哈希。图内 NSE 只代表本场评分样本，不能替代上游连续指标。event_collection 仍走原接口，不接受额外 --event-manifest。
