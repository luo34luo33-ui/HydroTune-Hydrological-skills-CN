# data-processing（数据处理）

本分类承载进入水文建模前的原始水文气象数据处理，包括读取、时间对齐、单位与语义核对、质量控制、缺测与异常识别，以及降雨和洪水事件识别。

## 边界

- 模拟输出的事件切片、统计聚合和派生变量归入 `post-processing`。
- 观测与模拟差异的误差解释归入 `evaluation-diagnostics`。
- Skill 可以在 metadata 中声明与上述分类交叉，但物理目录只保留一个主要归属。

## 当前 Skills

```text
prepare-discharge-timeseries
  -> separate-baseflow-eckhardt
  -> extract-flood-events
  -> visualize-flood-event-atlas（visualization-reporting）
```

- `prepare-discharge-timeseries`：读取 CSV、Parquet 或 XLSX，显式确认时间列、流量列、单位与时区，统一输出 `discharge_m3_s` 并执行时间轴 QC。
- `separate-baseflow-eckhardt`：使用显式 `bfi_max` 和显式或有证据估计的 `alpha`，生成基流、直接径流和分量比例。
- `extract-flood-events`：按版本化配置执行洪峰识别、边界搜索、复峰合并、边界收紧、规模过滤、事件指标与机器可读 QC。
- `prepare-model-forcing-timeseries`：把降雨或已有 PET/E0 站点序列按显式单位、时区和原始值语义整理为 mm/步长表。
- `derive-potential-evapotranspiration`：从完整日或小时气象量计算 FAO-56 ETo，只有显式映射时另输出 PET/E0。

缺测默认停止。只有显式选择时才允许受最大缺口约束的内部插值或首尾裁切；不会外推，也不会根据附加列名猜测雨量、蒸发或上下游语义。事件配置中的数值必须全部出现，仓库所带配置只用于复现参考源码，不是通用科学默认值。

## 示例安装

```bash
bash ./install-codex.sh --categories data-processing
```

```powershell
./install-codex.ps1 --categories data-processing
```
