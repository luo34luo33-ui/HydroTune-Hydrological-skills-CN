# hydrological-modeling（水文建模）

本分类承载集总式和半分布式降雨径流模型的结构选择、状态初始化、参数体系、warm-up、前向模拟和河道演算组织。

## 包含项

- 建模空间结构与模型类型选择
- 模型状态、参数体系、warm-up 与前向模拟
- 产流计算与坡面、河网调蓄
- 河道演算：单河段演进、单元河网串联、上游来水演进与多路出流合并

## 排除项

- 参数搜索、目标函数和率定验证归入 `model-calibration`
- 空间数据与水文拓扑的生成和检查归入 `spatial-analysis`
- 模拟输出的加工与机器学习校正归入 `post-processing`
- 指标评价与误差诊断归入 `evaluation-diagnostics`
- 图表与报告归入 `visualization-reporting`
- 第一版不覆盖完全分布式模型、二维水动力模型或地下水模型

## 本分类的 Atomic Skill

| Skill | 职责 | 输出 |
|---|---|---|
| `run-lumped-xaj-model` | 集总式新安江三水源产汇流：三层蒸发、蓄满产流、三水源划分、坡面与河网调蓄。支持连续序列与逐场次两种模式 | `Qt`（河网入口流量）与全部状态列 |
| `run-semi-distributed-xaj-model` | HydroBase 子流域逐单元新安江产流，按河段拓扑演算三水源与边界入流 | 子流域、河段过程及唯一出口流量 |
| `run-semi-distributed-swat-model` | HydroBase 子流域内的显式 HRU 逐个跑 SCS-CN 产流、三层 ET、土壤水再分配与含水层基流，按河段 DAG 演算至唯一出口；日尺度，提供四级水量平衡审计 | HRU、子流域、河段过程、唯一出口流量与水量平衡报告 |
| `route-muskingum-channel` | 马斯京根河道演算：单河段演进、单元河网多级串联、上游水库出库演进、多路出流相加 | 演进后出流与合并后的 `Q_total` |
| `run-lumped-dhf-model` | 集总式大伙房（DHF）模型：18 参数、双层蓄水容量产流、蒸发亏缺分配与经验 Gamma 型单位线汇流，产汇流自包含 | 流域出口流量 `Q` 与产流分量、状态列 |
| `run-lumped-hbv-model` | 集总式简化 HBV：9 参数、蓄满产流加三出口线性水库（含层间交换），无雪模块 | 流域出口流量 `Q` 与土壤/响应层状态列 |
| `run-lumped-tank-model` | 集总式 Tank：16 参数、四箱串联水箱（顶箱双高度双侧孔加底孔），蒸发直接扣净雨 | 流域出口流量 `Q` 与四箱蓄水、各孔出流列 |
| `run-lumped-gr4j-model` | 集总式 GR4J：4 参数、tanh 产流水库、非线性下渗、双单位线汇流与地下水交换（X2 可负表示外部汇入） | `Q_MM`（源码等价 mm 口径）、`Q`（m3/s）与产流分量、状态列 |
| `run-lumped-sacsma-model` | 集总式 SAC-SMA：16 参数、五库张力水/自由水结构、分层蒸散发（ET1–ET5）、下渗需求函数、ADIMP/PCTIM/RIVA/SIDE 面积分解；源码为日水量核算，非日步长仅 WARN | `Q_MM`（源码等价 mm 口径）、`Q`（m3/s）与地表/基流分量、六状态列 |
| `run-lumped-topmodel` | 已验证真实流域地形指数与距离面积分布驱动的集总式 TOPMODEL，含可选 Green–Ampt 超渗 | 各产流分量、蓄量、水量残差与出口流量 |
| `route-lohmann-channel` | 标准 Lohmann 河道汇流：扩散波解析解河道单位线 + Gamma HRU 单位线 + 双分量（direct/base）卷积相加；日过程大尺度，与马斯京根并列 | `routed_direct`、`routed_base` 与相加后的 `Q_total`，及导出的单位线 JSON |

### 处理链

```text
run-lumped-xaj-model  ->  route-muskingum-channel  ->  post-processing / evaluation-diagnostics
      Qt                        Q_total
validated HydroBase + subbasin forcing -> run-semi-distributed-xaj-model -> evaluation-diagnostics
                                               outlet Q
validated HydroBase + HRU 表 + daily forcing -> run-semi-distributed-swat-model -> evaluation-diagnostics
                                                        outlet Q, water balance
run-lumped-sacsma-model ->  route-lohmann-channel  --->  evaluation-diagnostics
   SURF / BASE            routed_direct + routed_base = Q_total
run-lumped-dhf-model  --------------------------------->  evaluation-diagnostics
      Q（出口流量）
run-lumped-hbv-model  --------------------------------->  evaluation-diagnostics
      Q（出口流量）
run-lumped-tank-model --------------------------------->  evaluation-diagnostics
      Q（出口流量）
run-lumped-gr4j-model --------------------------------->  evaluation-diagnostics
      Q（出口流量）
run-lumped-sacsma-model（不接汇流时）-------------------->  evaluation-diagnostics
      Q（出口流量）
derive-topmodel-terrain-inputs -> run-lumped-topmodel ----> evaluation-diagnostics
                                    Q（出口流量）
```

产流与河道演算被拆成两个 Skill，是因为二者具有不同的输入契约、失败语义与适用边界：产流的失败来自参数与 forcing，河道演算的失败来自 `K`、`X` 与时间步长的数值稳定条件；分开后河道演算可以独立复用于其它模型的出流。DHF 的经验单位线与产流状态 YA 耦合（TM 依赖 YA+R），拆分会破坏参数耦合，因此 DHF 自包含产汇流、输出即出口流量。

### 命名约定

`run-lumped-xaj-model` 中的 `lumped` 显式声明空间离散方式。半分布式新安江与半分布式 SWAT 是并列 Skill，读取 HydroBase 空间单元与拓扑；半分布式 SWAT 还要求显式 HRU 属性表，不由栅格自动划分 HRU。后续模型共用的 ID、时空单位和证据约定见[半分布式模型输入契约 v1](../resources/semi-distributed-model-input-v1.md)。

## 交叉边界

- 与 `spatial-analysis`：半分布式建模需要子流域与拓扑，本分类不生产这些数据。
- 与 `model-calibration`：本分类只做给定参数下的前向运行，不搜索参数。
- 与 `post-processing`：本分类的出口流量是残差校正等后处理步骤的输入。
