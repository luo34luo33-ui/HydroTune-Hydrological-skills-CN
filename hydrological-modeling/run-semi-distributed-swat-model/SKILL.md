---
name: run-semi-distributed-swat-model
description: 适用于基于已独立验证的 HydroBase 子流域与河段拓扑、按显式 HRU 属性表运行 SWAT/SWAT+ 派生日尺度降雨径流前向模拟；不适用于空间拓扑构建、HRU 自动划分、参数率定、亚日尺度洪水演算或正式制图。
metadata:
  category: hydrological-modeling
  domains:
    - hydrological-modeling
    - spatial-analysis
  tool_type: python
  primary_tool: pandas
  related_skills:
    - spatial-analysis/build-hydrological-topology
    - spatial-analysis/validate-hydrological-topology
    - spatial-analysis/aggregate-forcing-to-model-units
    - hydrological-modeling/run-semi-distributed-xaj-model
    - hydrological-modeling/route-muskingum-channel
---

# 半分布式 SWAT 日尺度前向模拟

给每个显式 HRU 计算一次 SCS-CN 产流、冠层截留、三层 ET、土壤水再分配、渗漏/侧向流与含水层基流，再按子流域面积汇总注入唯一承接河段，沿经验证的河段 DAG 演算至唯一出口，并给出四级水量平衡审计。命令见[使用指南](usage-guide.md)，字段见[数据契约](data-contract.yaml)。数值内核 `examples/_swat_core.py` 是 MiniSWAT-RR 0.1.0（SWAT+ `97ca231e`，LGPL-2.1）的派生实现，许可与准确定性声明同时记录在其文件头与技能根目录的 `THIRD_PARTY_NOTICES.md`（该文件的可分发副本位于 `examples/THIRD_PARTY_NOTICES.md`，安装后随代码进入 `scripts/`）。

## Use When

- 已有 `build-hydrological-topology` 及 `validate-hydrological-topology` 的可追溯结果，预期出口数为一。
- 已准备好显式 HRU 属性表（面积、CN2、土壤分层、含水层参数），且每个子流域内 HRU 面积和等于 `subbasins.csv` 的 `area_km2`。
- 每个子流域都有完整、已确认单位和时间含义的**日尺度**降雨与蒸发能力序列，或愿意显式 opt-in 日聚合。
- 需要水源分解（地表/侧向/基流）与四级水量平衡证据的日尺度前向结果。

## Do Not Use When

- 需要从 DEM 提取河网、划分子流域或修复失败拓扑；先用 `spatial-analysis`。
- 需要由土地利用/土壤栅格自动划分 HRU；本 Skill 只消费显式 HRU 表，不做空间叠加。
- 需要进行场次洪水或亚日尺度演进；本 Skill 仅日尺度，河道演进请用 `hydrological-modeling/route-muskingum-channel`。
- 需要自动率定参数、计算观测误差指标或制作正式图册。
- 流域过程的融雪、冻土、灌溉、农业管理、作物动态、水库调度、取水、城市不透水、裂隙流、稻田湿地、泥沙养分占主导时；这些过程**未实现**。

## Governing Principle

- 派生自 MiniSWAT-RR 0.1.0（SWAT+ tag `62.0.1`），保留移植过来的 SCS-CN、动态曲线数、土壤水 slug 循环、渗漏/侧向流、滞后储库、含水层指数退水与河段演算方程及执行顺序；1 mm × 1 km² = 1000 m³ 的换算不得改写。
- **不许宣称与 SWAT+ 数值等价**：源工程未做 Fortran/Python 数值对照，未验证项一律 `NOT VERIFIED`，该状态随 `result.json` 传播。
- 面积必须来自有哈希证据的 `subbasins.csv`；每个子流域的局地/边界入流只在其唯一承接河段注入一次。
- 时间步、时区、时间戳含义、HRU 属性、逐河段河道参数、初值、预热期和模拟期间都必须显式配置；缺测不得填零或插值。
- 预热语义采用「序列前 N 步 + `is_warmup` 标记」，与源工程「整段重复跑 N 遍」不同，这是有意改变。
- 非空输出目录默认拒绝；覆盖只删除本 Skill 声明的文件。输入 artifact 永不修改。

## QC 与停止条件

- 独立拓扑 QC 有 FAIL、引用哈希不一致、非单出口、子流域承接河段不唯一、拓扑环或单元映射不完整时停止。
- HRU 表缺列、area 之和与 `subbasins.csv` 不一致（相对 1e-6）、引用未知子流域或有子流域无 HRU 时停止。
- forcing 有重复/缺失单元时步、非有限或负值、时间轴不规则、`step_seconds != 86400` 且未 opt-in 聚合、或聚合日不完整时停止。
- 参数键名未知/`cn_froz` 被覆盖/越界时停止；`parameter_overrides` 是**全局广播**，不能逐对象微调。
- 四级水量平衡（HRU `1e-6` mm、河段 `1e-6` m³、子流域 `1e-5` m³、流域相对 `1e-9`）任一超限即停止（退出码 2）；状态或出流非有限、出流为负同样停止。
- 日尺度 Muskingum 在 `musk_k_hr < 24/(2(1-x))`（x=0.2 时约 15 hr）时 `C3<0`，会给出 WARN 而不是静默修正。

## 结果解释

`outlet_flow.csv` 是拓扑驱动的流域出口日平均流量（m³/s），由当日河段出流除以 86400 s 得到。`hru_process.csv` 给出逐 HRU 的水源分解与四级残差；`reach_process.csv` 给出逐河段入流、出流、损失与末端储量。预热行保留并标记 `is_warmup`。来自上游拓扑 QC 的 warning 会继续出现在运行结果中。含水层深部漏失 `gw_seep_frac` 与河道 `trans_loss/evap` 默认 0，干旱区可能高估径流。
