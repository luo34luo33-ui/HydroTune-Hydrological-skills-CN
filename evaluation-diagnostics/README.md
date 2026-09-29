# evaluation-diagnostics（评价诊断）

本分类承载整体、事件、流量分区、季节、水量平衡和空间性能评价，以及观测—模拟误差模式识别和证据约束的水文诊断。

## 包含项

- 场次洪水过程指标：洪量、洪峰、峰现时间与拟合指标
- 连续序列整体指标与水量平衡偏差
- 跨场次汇总统计与合格率
- 误差模式识别与证据约束的下一步建议

## 排除项

- 指标所需的数据切片、聚合与数值校正归入 `post-processing`
- 参数优化本身归入 `model-calibration`
- 图表排版和报告渲染归入 `visualization-reporting`
- 诊断必须区分观测事实、计算证据、工程推断和待确认假设，不把相关性写成因果事实

## 本分类的 Atomic Skill

| Skill | 职责 | 输出 |
|---|---|---|
| `compute-event-flood-metrics` | 逐场次计算洪量、洪峰、峰现时间误差、NSE、R²、RMSE、MAE；合格阈值必须显式给出 | 一场一行的指标表 |
| `compute-continuous-series-metrics` | 对连续序列计算 NSE、RMSE、MAE、R² 与水量平衡偏差；不可计算时记 `unavailable` | `metric/value/unit/status` 长表 |
| `aggregate-event-metrics` | 跨场次等权汇总：均值、中位数、分位数、极值与合格率 | 分组汇总表 |

### 处理链

```text
compute-event-flood-metrics  ->  aggregate-event-metrics
compute-continuous-series-metrics  ->  aggregate-event-metrics
```

### 本分类的共同约束

- 时间步长必须显式声明并与时间轴一致，不得使用写死的换算系数或隐式 fallback。
- 缺测、长度不一致、非有限值都会导致退出码 2，不填 0 后计算。
- 合格阈值必须由使用者显式给出；缺失时合格列记为 `unavailable`，不套用行业惯例值。
- NSE 与 R² 并列输出：二者在常规序列上数值一致，差异只在观测方差为 0 等边界情形。
- 本分类只产出证据，不做无证据的因果归因。

## 交叉边界

- 与 `post-processing`：被评价的序列由后处理或模型运行产出。
- 与 `visualization-reporting`：指标的表达与排版不属于本分类。
- 与 `model-calibration`：率定与验证划分不属于本分类。
