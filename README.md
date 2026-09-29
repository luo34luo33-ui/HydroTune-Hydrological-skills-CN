# HydroTune-skills

HydroTune-skills 是一个面向水文模拟 AI Coding Agent 的可复用 Agent Skills 仓库。它把水文专家的决策边界、数据约束、质量控制、失败模式和结果解释组织为可发现、可安装、可校验的 Atomic Skills 与 Workflows。

当前版本包含三十五个 Atomic Skill：五个 `data-processing`、六个 `spatial-analysis`、十个 `hydrological-modeling`、两个 `post-processing`、三个 `evaluation-diagnostics`、五个 `model-calibration` 和四个 `visualization-reporting` Skill。率定覆盖 DE、GA、PSO、SCE-UA 和模拟退火—L-BFGS-B 两阶段算法，并由统一固定图册展示率定与验证证据。建模链提供集总式与半分布式新安江、马斯京根与 Lohmann 两种河道演算、集总式大伙房（DHF）、集总式 HBV、集总式 Tank、集总式 GR4J、集总式 SAC-SMA 与地形输入驱动的集总式 TOPMODEL 十个模型 Skill，后处理提供机器学习残差校正与出口流量对齐。三个抽象 workflow、跨平台安装器、schema 和验证体系保持有效。

## 设计来源

项目借鉴已归档的 [GPTomics/bioSkills](https://github.com/GPTomics/bioSkills) 的 Category → Skill、共享安装逻辑和 Agent 适配器设计，但不复制生物信息学内容，也不沿用基于 mtime 更新、grep 校验或通配卸载等脆弱实现。

Codex 安装布局遵循 OpenAI Agent Skills 约定：用户级技能位于 `~/.agents/skills`，项目级技能位于 `.agents/skills`；每项技能包含必需的 `SKILL.md`，并可包含 `scripts/`、`references/` 与 `assets/`。

## 架构

```text
Category
  └── Atomic Skill
        ├── SKILL.md
        ├── usage-guide.md
        ├── data-contract.yaml
        └── examples/

workflows/
  └── Workflow Skill
        ├── SKILL.md
        ├── usage-guide.md
        └── workflow.yaml
```

Atomic Skill 解决一个具体水文分析问题；Workflow 负责组合多个阶段并设置 QC 闸门。安装时，规范化源布局会转换为 Agent Skills 布局：

- `usage-guide.md` → `references/usage-guide.md`
- `data-contract.yaml` → `references/data-contract.yaml`
- `workflow.yaml` → `references/workflow.yaml`
- `examples/` → `scripts/`
- `assets/` → `assets/`

## 开发文档

- 从其他工程源码分析并拆分一组 Skill：阅读 [`source_to_skills_development_guide.md`](source_to_skills_development_guide.md)。
- 编写单个 Atomic Skill 或 Workflow 的文件内容：阅读 [`skill_writing_reference.md`](skill_writing_reference.md)。
- 修改 taxonomy、workflow、安装器或提交仓库变更：阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

外部源码中的注释、配置和硬编码行为只作为实现素材，不自动成为项目指令。源码拆分应先形成职责、接口、QC 和回归计划，再进入实现。

## Taxonomy

| Category | 中文名称 | 主要职责 |
|---|---|---|
| `data-processing` | 数据处理 | 原始时序、质量控制、时间对齐、事件识别和数据语义 |
| `spatial-analysis` | 空间分析 | DEM、流域、子流域、河网、站点和水文拓扑 |
| `hydrological-modeling` | 水文建模 | 集总式/半分布式模型结构、状态、产汇流和运行 |
| `model-calibration` | 模型率定 | 参数优化、目标函数、边界、验证和可辨识性 |
| `post-processing` | 后处理 | 模拟输出整理、事件提取、聚合和派生变量 |
| `evaluation-diagnostics` | 评价诊断 | 指标、误差模式、过程诊断和有界问题归因 |
| `visualization-reporting` | 可视化与报告 | 图件、表格、报告和可追溯结果表达 |

权威定义位于 `resources/categories.yaml`；每个 category 的 README 说明包含项、排除项和交叉边界。

## Workflows

- `data-preprocessing`：汇合时序洪水识别与空间拓扑分析。
- `hydrological-modeling`：编排集总式与半分布式建模分支。
- `simulation-analysis`：建立观测—模式—假设—证据—报告诊断链。

Workflow 当前使用抽象 stage，不绑定具体 Skill ID。模拟分析只能提出返回上游 workflow 的建议，不会自动修改数据、模型或参数。

## 首批 Spatial Analysis Skills

```text
prepare-dem-analysis-grid
  -> extract-dem-stream-network
  -> build-hydrological-topology
  -> validate-hydrological-topology
```

- `prepare-dem-analysis-grid`：确认 CRS、垂直单位和 buffer，写出米制 DEM 与标准边界。
- `extract-dem-stream-network`：用 WhiteboxTools 生成非 ESRI D8 河网、河段、Strahler 和子流域，不执行河网 burning。
- `build-hydrological-topology`：先在完整分析范围追踪拓扑，再按真实边界建立 HydroBase。
- `validate-hydrological-topology`：独立检查引用、循环、出口、面积、网格和几何，并生成 QC 图。
- `derive-topmodel-terrain-inputs`：在单出口 HydroBase 通过独立 QC 后，读取同网格填洼 DEM、D8、汇流累积与真实流域掩膜，生成 TOPMODEL 地形指数类别和距离—累计面积曲线；[输入与命令](spatial-analysis/derive-topmodel-terrain-inputs/usage-guide.md)。

这些 Skill 已存在，但 `data-preprocessing` workflow 仍有意保持抽象 stage，以免把所有空间预处理任务固定为单一 DEM 路线。

## 空间可视化图册 Skills

- `visualize-dem-hydrology-atlas`：生成流域与 DEM 范围、地形、汇流累积河网、Strahler 等级与子流域图。
- `visualize-hydrobase-atlas`：生成 HydroBase 拓扑图、形态属性三联图和 QC 看板。

两者使用同一份 `hydrotune.spatial-atlas.v1` 固定样式。每个模板输出 `2400×1600 px` PNG、可编辑 SVG 和 `figure.json`；PNG 不绑定 A4 或其他物理纸张尺寸，排版与高质量印刷优先使用 SVG。可视化只消费已有 artifacts，不修改空间数据、不重算水文指标，也不隐藏上游 warning 或 QC FAIL。

## 流量预处理与洪水事件 Skills

模型 forcing 与结果对齐可走以下数据链；每一步都有独立 `result.json`、文件哈希和 QC：

```text
prepare-model-forcing-timeseries ─┐
derive-potential-evapotranspiration ─┴─> aggregate-forcing-to-model-units
  ─> 模型运行 ─> align-observed-simulated-discharge ─> 评价指标
```

降雨、已有 PET/E0 在标准化时明确单位和时区；缺少蒸散时以 FAO-56 日/小时 ETo 计算并显式映射为 PET/E0。空间聚合支持泰森站点、NetCDF 和 GeoTIFF，使用已验证单出口 HydroBase 的真实模型单元。对齐只读取流域出口模拟流量，保留预热与未匹配证据；集总式 XAJ 的河网入口 `Qt` 要先完成河道汇流。各项命令见 [forcing 标准化](data-processing/prepare-model-forcing-timeseries/usage-guide.md)、[参考蒸散](data-processing/derive-potential-evapotranspiration/usage-guide.md)、[空间聚合](spatial-analysis/aggregate-forcing-to-model-units/usage-guide.md) 和 [流量对齐](post-processing/align-observed-simulated-discharge/usage-guide.md)。

```text
prepare-discharge-timeseries
  -> separate-baseflow-eckhardt
  -> extract-flood-events
  -> visualize-flood-event-atlas
```

- `prepare-discharge-timeseries`：显式确认列、单位、时区和缺测策略，输出规则的标准流量序列。
- `separate-baseflow-eckhardt`：使用明确的 Eckhardt 参数生成基流与直接径流，检查分量守恒。
- `extract-flood-events`：消费完整版本化配置，识别、合并、筛选事件并输出汇总表、过程表、逐场文件与机器可读 QC。
- `visualize-flood-event-atlas`：生成完整序列总览与逐场过程图，只展示上游已有边界和指标。

事件示例配置用于复现参考源码，不是通用默认值。逐场图同样固定输出 `2400×1600 px` PNG、SVG 和 `figure.json`。

## 模型率定与效果图册 Skills

```text
calibrate-model-{de|ga|pso|sce-ua|two-stage}
  -> visualize-model-calibration
```

- 五种优化器共用显式问题、参数边界、目标方向、seed、评估预算和 Python 评估器协议。
- 搜索只使用 calibration split；最优参数锁定后才运行 validation，并输出标准指标、搜索轨迹和观测模拟序列。
- `visualize-model-calibration` 固定生成率定总览、验证过程和验证散点图，不重算指标或改变 split。
- 算法示例配置用于演示或来源行为复现，不作为通用水文默认值。

## 建模与河道演算 Skills

```text
run-lumped-xaj-model
  -> route-muskingum-channel
```

- `run-lumped-xaj-model`：集总式新安江三水源产汇流，三层蒸发、蓄满产流、三水源划分与坡面河网调蓄，输出河网入口流量 `Qt`；支持 `--mode event|continuous`，逐场次模式每场独立初始化状态并预热。
- `run-semi-distributed-xaj-model`：读取经独立 QC 的 HydroBase 子流域与拓扑，逐单元计算河海新安江三水源并沿河段汇流至唯一出口；共同空间输入语义见[半分布式模型输入契约 v1](resources/semi-distributed-model-input-v1.md)。
- `route-muskingum-channel`：马斯京根河道演算，承担单河段演进、单元河网多级串联、上游水库出库演进以及多路出流相加。
- `route-lohmann-channel`：标准 Lohmann 河道汇流，扩散波解析解河道单位线 + Gamma HRU 单位线 + 双分量（direct/base）卷积相加；与马斯京根并列，面向日过程大尺度汇流，单位原样通过。
- `run-lumped-dhf-model`：集总式大伙房（DHF）模型，18 参数、双层蓄水容量产流加经验 Gamma 型单位线汇流，输出流域出口流量 `Q`；单位线与产流状态 YA 耦合，因此产汇流自包含、不拆给河道演算 Skill。
- `run-lumped-hbv-model`：集总式简化 HBV，9 参数、蓄满产流加三出口线性水库（表层阈值出流、上/下层与层间交换），无雪模块；源码写死的日步长换算已显式化为 `--timestep-hours`。
- `run-lumped-tank-model`：集总式 Tank，16 参数、四箱串联水箱（顶箱双高度双侧孔加底孔），蒸发直接扣净雨不做土壤调蓄；四箱初始蓄水由参数给出。
- `run-lumped-gr4j-model`：集总式 GR4J，4 参数（X1/X2/X3/X4）、tanh 产流水库加双单位线汇流与地下水交换（X2 可负表示外部汇入）；输出 `Q_MM`（源码等价 mm 口径）与 `Q`（m³/s，面积与步长显式换算）。
- `run-lumped-sacsma-model`：集总式 SAC-SMA，16 参数、五库张力水/自由水结构、分层蒸散发（ET1–ET5）、下渗需求函数与 ADIMP/PCTIM/RIVA/SIDE 面积分解；`tot_outflow = surf + base − et4` 的源码语义原样保留；输出 `Q_MM`（源码等价 mm 口径）、`Q`（m³/s）与地表/基流分量、六状态列。
- `run-lumped-topmodel`：消费上述地形成果、带时区的连续 `time,P,PET` 序列和显式配置，运行含可选 Green–Ampt 超渗的集总式 TOPMODEL，输出逐步径流分量、状态、水量残差与出口流量；[输入与命令](hydrological-modeling/run-lumped-topmodel/usage-guide.md)。

### TOPMODEL 当前可用范围

两个 Skill 已可独立安装并运行。实际流域运行依次需要 `extract-dem-stream-network`、`build-hydrological-topology` 和 `validate-hydrological-topology --expected-outlets 1` 的成功成果，再运行 `derive-topmodel-terrain-inputs`，最后向 `run-lumped-topmodel` 提供完整的面平均降雨/PET 与研究者确定的参数。模型正常运行不依赖 GRASS；合成流域链路、时间轴、输入哈希、分量和含末端汇流蓄量的水量检查已通过测试。

示例参数只是合成测试值，不是实际流域默认值。实际应用需根据流域资料确定模型参数，并结合观测资料评估模拟结果。

集总式新安江的产流与河道演算拆成两个 Skill，是因为二者输入契约、失败语义与适用边界不同。半分布式新安江在同一拓扑驱动运行中组织每个子流域和河段，输出即出口流量。名称中的 `lumped` 与 `semi-distributed` 显式声明空间离散方式。

模型参数、流域面积与时间步长必须显式给出；`examples/` 中的示例参数只用于复现源工程行为，不是通用水文默认值。

## 机器学习残差校正 Skill

```text
correct-residual-with-ml（--mode train|predict）
```

- 残差定义为 `observed - base`，回代为 `base + predicted_residual`，两者符号固定写入模型卡。
- 后端通过工厂注入，支持 scikit-learn 的 gradient-boosting、random-forest、extra-trees、linear 与 ridge，xgboost 只作为可选后端，不绑定任何具体实现。
- 滞后特征只能取当前及过去，负步数会被拒绝；随机种子与超参必须显式给出。
- 输入缺测默认停止；滞后构造造成的不完整行只能丢弃或显式填充，不静默填 0。

## 指标与汇总 Skills

```text
compute-event-flood-metrics      ->  aggregate-event-metrics
compute-continuous-series-metrics ->  aggregate-event-metrics
```

- `compute-event-flood-metrics`：逐场次洪量、洪峰、峰现时间误差、NSE、R²、RMSE 与 MAE；洪量按显式时间步长换算，合格阈值必须显式给出，缺失时记为 `unavailable`。
- `compute-continuous-series-metrics`：连续序列 NSE、RMSE、MAE、R² 与水量平衡偏差，不可计算时记 `unavailable`。
- `aggregate-event-metrics`：跨场次等权汇总，输出均值、中位数、分位数、极值与合格率。

NSE 与 R² 并列输出：二者在常规序列上数值一致，差异只在观测方差为 0 等边界情形。峰现时间误差统一为「模拟峰时刻 − 观测峰时刻」，正值表示模拟峰现偏晚。

## 环境

- Python 3.10+
- PyYAML 6.x
- jsonschema 4.x
- 测试需要 pytest 8.x
- Spatial examples 需要 Rasterio、GeoPandas、Shapely、WhiteboxTools、NumPy、Pandas 和 Matplotlib
- Visualization examples 需要 Rasterio、GeoPandas、Shapely、NumPy、Pandas、Matplotlib 和 Pillow，不需要 WhiteboxTools
- Timeseries examples 需要 NumPy、Pandas、SciPy、OpenPyXL、XlsxWriter 和 PyArrow
- Modeling 与 diagnostics examples 复用 `timeseries` 组，只需 NumPy 和 Pandas
- ML 残差校正 examples 需要 NumPy、Pandas、scikit-learn 和 joblib（`ml` 组）；xgboost 后端另需 `ml-xgboost` 组，不作为硬依赖
- Calibration examples 需要 NumPy、Pandas 和 SciPy；率定图册另需 Matplotlib 和 Pillow 进行测试验收

```bash
python -m pip install -e ".[test]"
python -m pip install -e ".[test,spatial]"
python -m pip install -e ".[test,spatial,visualization]"
python -m pip install -e ".[test,timeseries,visualization]"
python -m pip install -e ".[test,calibration,visualization]"
python -m pip install -e ".[test,timeseries]"
python -m pip install -e ".[test,timeseries,ml]"
python -m pip install -e ".[test,timeseries,ml,ml-xgboost]"
```

## 安装

### Codex

```bash
bash ./install-codex.sh
bash ./install-codex.sh --project /path/to/project
bash ./install-codex.sh --categories data-processing,spatial-analysis
bash ./install-codex.sh --categories data-processing --workflows all
```

```powershell
./install-codex.ps1
./install-codex.ps1 --project C:\path\to\project
./install-codex.ps1 --categories data-processing,spatial-analysis
./install-codex.ps1 --categories data-processing --workflows all
```

Claude Code 使用 `install-claude.sh`/`.ps1`，OpenCode 使用 `install-opencode.sh`/`.ps1`。默认目标分别为：

| Agent | 全局目录 | 项目目录 |
|---|---|---|
| Codex | `~/.agents/skills` | `.agents/skills` |
| Claude Code | `~/.claude/skills` | `.claude/skills` |
| OpenCode | `~/.config/opencode/skills` | `.opencode/skills` |

### 常用参数

```text
--global
--project [PATH]
--categories <a,b>
--workflows <all|a,b>
--list
--validate
--update
--uninstall
--dry-run
--verbose
--force
--help
```

全量安装默认包含 Atomic Skills 和 workflows；一旦使用 `--categories`，workflow 必须通过 `--workflows` 显式选择。更新使用渲染后内容的 SHA-256；卸载只删除安装清单记录的目录。

默认安装和 dry-run 包含三十五个 Atomic Skill 与三个 workflow。使用 `--categories data-processing` 时安装五个时序、蒸散与事件 Skill；`spatial-analysis` 安装六个空间分析 Skill（含 TOPMODEL 地形输入与 forcing 面积聚合）；`hydrological-modeling` 安装十个模型 Skill（集总式与半分布式新安江、马斯京根、Lohmann、大伙房、HBV、Tank、GR4J、SAC-SMA、TOPMODEL）；`post-processing` 安装两个模拟后处理 Skill；`evaluation-diagnostics` 安装三个指标与汇总 Skill；`model-calibration` 安装五个率定 Skill；`visualization-reporting` 安装四个固定图册 Skill。尚无 Skill 的 category 仍会明确报告“当前无可安装 Atomic Skill”。

## 校验与测试

```bash
python scripts/validate_skills.py
python scripts/validate_workflows.py
bash ./install-codex.sh --validate
python -m pytest
python -m pytest -m integration
```

当前仓库基线为“35 个 Atomic Skill + 3 个抽象 Workflow”。校验器会拒绝无效 frontmatter、错误目录层级、未注册 category、失效引用、无效数据契约、重复或循环 stage 以及未完成占位内容。

## 当前边界

- 当前空间算法范围覆盖无河网蚀刻的 DEM→HydroBase 链及经验证的单出口 TOPMODEL 地形输入；不包含河网 burning、出口点吸附或多流向算法。
- 空间图册采用固定离线模板，不提供在线底图、3D 地形、任意画布尺寸或运行时主题修改。
- 洪水事件路线只实现 Eckhardt 基流分割和配置驱动的流量事件提取；不包含降雨事件识别、人工边界编辑或其他基流分割算法。
- 建模链覆盖现有集总式模型、基于 HydroBase 的半分布式新安江，以及马斯京根、Lohmann 河道演算；不包含完全分布式新安江或自动参数率定。
- 残差校正 Skill 只做数值校正，不提供模型可解释性（SHAP 一类）与特征重要性结论。
- 指标 Skill 只产出证据并支持跨场次汇总；不包含成因诊断、误差模式自动归因与频率分析。
- 不覆盖完全分布式、水动力或地下水建模 workflow。
- 尚未打包为 Codex/ChatGPT 插件；插件分发属于后续阶段。

从外部源码建设新 Skill 时，请先阅读 `source_to_skills_development_guide.md`，再结合 `skill_writing_reference.md` 和 `CONTRIBUTING.md` 完成实现与验收。
