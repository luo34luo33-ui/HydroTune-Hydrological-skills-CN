# 第三方声明（Third-Party Notices）

本文件对应 `hydrological-modeling/run-semi-distributed-swat-model`。

## 1. 衍生来源

| 项 | 值 |
|---|---|
| 直接来源工程 | MiniSWAT-RR 0.1.0（Python 半分布式日尺度降雨—径流模型） |
| 上游工程 | SWAT+，`https://github.com/swat-model/swatplus` |
| 上游 tag | `62.0.1` |
| 上游 commit | `97ca231e22b29356b9891e057238fa3c68809da5`（2026-09-24） |
| 上游许可 | GNU Lesser General Public License v2.1 |
| MiniSWAT-RR 许可 | LGPL-2.1-or-later（按 LGPL-2.1 第 2 条 c 款同样许可） |

本 Skill 的 `examples/_swat_core.py` 是 MiniSWAT-RR 的**派生实现**。仓库根目录 `LICENSE`
为 MIT，仅覆盖 HydroTune-skills 自身的编排、文档与校验代码；被携带的 SWAT 派生数值
代码仍受 LGPL-2.1 约束，使用者须同时遵守该许可。

## 2. 准确定性声明（必须随本 Skill 一同传播）

- MiniSWAT-RR **未做任何 Fortran / Python 数值对照**（其环境无 gfortran / cmake），
  未对照项在源工程中一律标注 `NOT VERIFIED`。
- 因此本 Skill **不得宣称与 SWAT+ 数值等价**；所有涉及"等价""复现 SWAT+"的说法都是
  不被证据支持的。

## 3. 忠实移植 / 改写 / 替换 / 省略清单

| 类别 | 内容 |
|---|---|
| 忠实移植 | SCS-CN 产流 `sq_daycn`；动态 CN `sq_dailycn` + `curno` + `ascrv`；土壤水再分配与渗漏/侧向流 `swr_percmain` / `swr_percmicro`；侧向流滞后 `swr_substor`；土壤蒸发 `et_act`；冠层蒸发；含水层指数退水 `aqu_1d_control`；Muskingum 系数；变储量系数法；mm↔m³ 换算 |
| 改写 | 冠层容量固定化（无 LAI 动态）；坡面滞后去掉上游的 `Max(1e-6, …)` 造水下限；含水层改为每 HRU 一个；Muskingum 的 K 直接输入；拓扑层级向量化；日步保持上游顺序调度 |
| 替换 | 植物蒸腾：由 LAI/覆盖度分配改为固定比例 + 按各层可用水量加权（键名 `simplified_ep_frac`） |
| 省略 | 雪/融雪、冻土闸门、城市不透水修正、裂隙流、Green–Ampt 分支、瓦管流、湿地/稻田、作物生长与农业管理、灌溉、水库调度与取水、泥沙/养分/农药/碳、含水层间侧向交换；河道透水损失与水面蒸发保留为显式输入且默认 0 |

## 4. HydroTune 相对源工程的有意改变

1. 移除包外 `config/parameter_schema.yaml` 的 `parents[2]` 定位与环境变量查找，
   参数 schema 内嵌为 `_swat_core.PARAMETER_SCHEMA`。
2. 预热语义改为「序列前 N 步 + `is_warmup` 标记」；源码的 `warmup_days` 是把整段序列
   重复运行 N 遍。
3. 输入改为 HydroBase 拓扑证据 + 显式 HRU 属性表 + `(time, sub_id)` forcing 长表；
   源码 `Boundary` 与 `P` 轴相反的内部约定在 CLI 层统一为同轴长表。
4. 四级水量平衡容差由源工程的测试断言提升为 `result.json` 中的显式 QC 检查项。

## 5. 已知限制

- 仅日尺度（`step_seconds = 86400`）；非日尺度 forcing 必须先在上游处理，
  或使用本 Skill 显式 opt-in 的日聚合。
- 无雪无冻土、无灌溉、无水库调度：这些流域过程的缺失会系统性改变水量分配。
- 河道透水损失与水面蒸发默认 0，干旱区可能高估径流。
- 日尺度 Muskingum 在 `musk_k_hr < 24/(2(1-x))`（x=0.2 时约 15 hr）时 `C3<0`，
  会出现负流截断与河道储量滞留；因此默认使用 `linear_storage`。
