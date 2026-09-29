# HydroTune-skills：Codex 项目架构设计提示词

## 0. 你的角色

你现在是 `HydroTune-skills` 项目的架构设计者。

项目名称：

# HydroTune-skills

这是一个面向水文模拟领域 AI Coding Agent 的 Agent Skills 项目。

项目的整体组织方式、Skill 文件布局、安装机制、验证机制和多 Agent 适配方式，应当**高度参考 GPTomics/bioSkills 的工程架构**。

但是：

- 不复制 bioSkills 的生物信息学内容；
- 不照搬其领域 taxonomy；
- 所有水文领域分类必须重新设计；
- 当前阶段只负责项目架构；
- **当前阶段不要正式创建具体 Skill 内容。**

---

# 1. 当前任务边界

当前只执行：

> HydroTune-skills 的仓库架构设计。

不要进入具体 Skill 编写阶段。

当前阶段允许：

- 创建项目骨架；
- 创建安装系统；
- 创建 Skill 编写规范；
- 创建 workflow 骨架；
- 创建 validator 骨架；
- 创建 README；
- 设计 Skill category 候选方案；
- 设计未来 Skill 的标准目录格式。

当前阶段禁止：

- 批量创建具体 Skill；
- 编写具体水文算法 Skill；
- 自行决定最终 Skill category；
- 大规模填充领域知识；
- 为了展示效果创建大量占位 Skill。

---

# 2. 最重要的交互要求

## Skill Category 必须经过用户确认

你可以分析水文模拟领域，并提出一套推荐的根目录 Skill category。

例如可以考虑：

```text
data-processing
rainfall-runoff
model-calibration
model-evaluation
uncertainty-analysis
geospatial-analysis
...
```

但这些只是候选。

你必须：

1. 提出 category 设计方案；
2. 解释每个 category 的边界；
3. 说明为什么这样划分；
4. 指出可能存在交叉的 category；
5. 向用户展示候选 taxonomy；
6. **停止继续创建 category 目录；**
7. **等待用户明确确认或修改。**

在用户确认之前：

```text
不得正式创建 Skill category 目录。
不得生成对应 Skill。
不得假设 taxonomy 已经确定。
```

这是强制要求。

---

# 3. HydroTune-skills 的核心定位

HydroTune-skills 不应该只是：

```text
水文软件命令合集
```

也不应该只是：

```text
Python / R 示例代码合集
```

它最终应该成为：

> 将水文模拟领域专家的分析决策、建模经验、数据约束、模型诊断、参数分析和结果解释显式包装为 Agent Skills 的知识库。

最终每一个 Skill 应该帮助 Agent 理解：

```text
什么时候使用某个方法
什么时候不能使用
输入需要满足什么条件
数据可能有什么问题
模型假设是什么
参数应该怎样理解
应该进行什么 QC
失败可能是什么原因
结果应该怎样解释
下一步应该进入哪个 Skill
```

但是当前阶段**只为这种未来能力设计架构**。

---

# 4. 总体架构原则

HydroTune-skills 应尽量继承 bioSkills 的以下思想：

```text
根目录
    ↓
领域 Category
    ↓
具体 Skill
    ↓
SKILL.md
usage-guide.md
examples/
```

同时增加更加明确的数据接口层。

未来单个 Skill 的标准结构暂定为：

```text
<category>/
└── <skill-name>/
    ├── SKILL.md
    ├── usage-guide.md
    ├── data-contract.yaml
    └── examples/
```

其中：

### `SKILL.md`

负责：

```text
Agent 的专家决策逻辑
```

### `usage-guide.md`

负责：

```text
用户使用说明
扩展解释
典型使用方式
```

### `data-contract.yaml`

负责：

```text
Skill 输入输出的数据语义契约
```

### `examples/`

负责：

```text
经过验证的参考实现
```

但当前阶段只需要设计这一规范，不需要批量填充。

---

# 5. 仓库一级结构

优先采用下面的总体布局：

```text
HydroTune-skills/
│
├── <未来用户确认后的 Skill categories>
│
├── workflows/
│   ├── data-preprocessing/
│   ├── hydrological-modeling/
│   └── simulation-analysis/
│
├── resources/
│
├── scripts/
│
├── tests/
│
├── install-common.sh
├── install-claude.sh
├── install-codex.sh
├── install-opencode.sh
│
├── skill_writing_reference.md
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

注意：

```text
<未来用户确认后的 Skill categories>
```

当前不要展开。

必须先向用户确认。

---

# 6. Workflow 与普通 Skill Category 分开

`workflows/` 不是普通领域分类。

它表示：

> 多个 Skill 的组合与编排。

普通 Skill 解决：

```text
一个具体分析问题
```

Workflow 解决：

```text
一个完整水文模拟阶段如何组合多个 Skill
```

因此架构必须明确区分：

```text
Atomic Skills
```

和：

```text
Workflow Skills
```

---

# 7. 当前固定的三个 Workflow

当前只设计以下三个 workflow。

不要自行增加第四个 workflow。

---

## 7.1 Workflow 1：数据预处理

目录：

```text
workflows/
└── data-preprocessing/
```

中文概念：

# 数据预处理 Workflow

该 workflow 负责水文模拟进入模型之前的数据组织与结构分析。

必须明确包含两个核心部分：

```text
时序洪水识别
+
空间拓扑分析
```

总体概念流程：

```text
原始水文/气象/空间数据
        ↓
数据读取与基础检查
        ↓
时间轴统一
        ↓
缺失值与异常值检查
        ↓
时序洪水识别
        ↓
洪水事件划分
        ↓
事件属性提取
        ↓
空间数据处理
        ↓
河网 / 子流域 / 站点拓扑分析
        ↓
空间连接关系建立
        ↓
形成模型可消费的数据结构
```

### 7.1.1 时序洪水识别

该 workflow 未来应能够组合相关 Skills，例如：

```text
时间序列对齐
降雨事件识别
洪水事件识别
事件起止点判断
峰值识别
事件分割
前期湿润状态
事件属性计算
```

当前不要创建这些 Skill。

只在 workflow 架构中预留依赖关系。

### 7.1.2 空间拓扑分析

该部分未来用于表达：

```text
站点
子流域
河段
河网
汇流关系
上下游关系
模型单元
```

之间的空间拓扑。

概念结构包括：

```text
DEM / 河网 / 子流域
        ↓
空间预处理
        ↓
拓扑检查
        ↓
上下游关系
        ↓
汇流顺序
        ↓
子流域连接关系
        ↓
模型拓扑结构
```

重点是：

> 不只处理 GIS 文件，还要明确水文拓扑语义。

---

## 7.2 Workflow 2：集总式与半分布式水文模型构建

目录：

```text
workflows/
└── hydrological-modeling/
```

中文概念：

# 水文模型构建 Workflow

当前只考虑：

```text
集总式水文模型
+
半分布式水文模型
```

暂时不把以下内容纳入这个 workflow：

```text
完全分布式模型
二维水动力模型
地下水模型
```

除非用户以后明确扩展。

### 7.2.1 模型构建总体流程

建议架构表达：

```text
预处理数据
     ↓
确定建模空间结构
     ↓
选择模型类型
     ↓
┌───────────────────┐
│                   │
▼                   ▼
集总式             半分布式
│                   │
▼                   ▼
单流域参数          子流域划分
│                   │
│                   ↓
│                 拓扑关系
│                   │
│                   ↓
│                 参数区域化
│                   │
└─────────┬─────────┘
          ↓
      模型状态初始化
          ↓
      参数体系建立
          ↓
      warm-up
          ↓
      simulation
          ↓
      输出标准化
```

### 7.2.2 集总式模型

架构必须允许未来支持：

```text
一个流域
一个整体模型单元
一套或少量参数组
```

例如未来可以扩展：

```text
HBV
GR4J
XAJ
SAC-SMA
自定义概念模型
```

但当前不要创建具体模型 Skill。

### 7.2.3 半分布式模型

架构必须允许表达：

```text
流域
 ↓
多个子流域 / HRU / response units
 ↓
局地产流
 ↓
局部汇流
 ↓
河网拓扑连接
 ↓
出口流量
```

半分布式模型必须能和：

```text
Workflow 1 的空间拓扑分析
```

发生明确的数据依赖关系。

---

## 7.3 Workflow 3：模拟结果智能分析

目录：

```text
workflows/
└── simulation-analysis/
```

中文概念：

# 模拟结果智能分析 Workflow

这个 workflow 不只是：

```text
计算 NSE / KGE
```

而应该设计成：

> Agent 对模型模拟结果进行系统诊断和解释。

总体结构：

```text
simulation results
        +
observations
        ↓
基础性能评价
        ↓
时间序列诊断
        ↓
洪水事件诊断
        ↓
高流量 / 中流量 / 低流量诊断
        ↓
季节性诊断
        ↓
水量平衡诊断
        ↓
空间诊断
        ↓
参数行为分析
        ↓
误差模式识别
        ↓
可能原因推断
        ↓
形成结构化分析报告
```

### 7.3.1 “智能分析”的含义

不要把智能分析设计成：

```text
把所有指标算一遍
```

而应该能够支持未来这样的 Agent reasoning：

```text
现象：
整体 NSE 很高

但是：
洪峰系统性偏低

同时：
洪峰时间基本正确

可能说明：
产流强度不足
而不是汇流时滞错误
```

或者：

```text
现象：
低流量持续偏高

可能相关因素：
基流参数
地下水释放
蒸散
模型状态初始化
```

也就是说未来的 Skills 应支持：

```text
Observation
↓
Pattern
↓
Hydrological hypothesis
↓
Diagnostic evidence
↓
Possible cause
↓
Suggested next analysis
```

当前阶段只设计 workflow 接口。

---

# 8. 三个 Workflow 的关系

架构必须明确表现：

```text
┌──────────────────────────────┐
│ Workflow 1                   │
│ 数据预处理                   │
│                              │
│ 时序洪水识别                 │
│ 空间拓扑分析                 │
└──────────────┬───────────────┘
               │
               │ 标准化数据
               ▼
┌──────────────────────────────┐
│ Workflow 2                   │
│ 水文模型构建                 │
│                              │
│ 集总式模型                   │
│ 半分布式模型                 │
└──────────────┬───────────────┘
               │
               │ 模拟结果
               ▼
┌──────────────────────────────┐
│ Workflow 3                   │
│ 模拟结果智能分析             │
│                              │
│ 指标                         │
│ 事件                         │
│ 时序                         │
│ 空间                         │
│ 诊断                         │
└──────────────────────────────┘
```

未来应该允许形成反馈：

```text
模拟结果分析
      ↓
发现模型问题
      ↓
重新检查：

数据
模型结构
参数
空间划分
事件识别
```

即：

```text
Workflow 3
    ↓
可以反馈到
Workflow 1 / Workflow 2
```

但当前不要实现自动迭代。

---

# 9. Workflow Skill 的文件结构

三个 workflow 都采用：

```text
workflows/
└── <workflow-name>/
    ├── SKILL.md
    ├── usage-guide.md
    └── workflow.yaml
```

其中：

### `SKILL.md`

解释：

```text
workflow 的目标
适用条件
总体分析逻辑
QC 节点
```

### `usage-guide.md`

解释：

```text
用户怎样使用这个 workflow
```

### `workflow.yaml`

负责未来机器可读的编排关系。

当前可以只建立 schema 和骨架。

不要绑定尚未确认的具体 Skill ID。

可以用抽象节点。

例如：

```yaml
workflow: data-preprocessing

stages:

  - id: temporal_processing
    description: 时间序列整理

  - id: flood_event_identification
    description: 时序洪水识别

  - id: spatial_topology
    description: 空间拓扑分析

  - id: output_validation
    description: 输出数据检查
```

---

# 10. Skill Category 的架构要求

虽然 category 具体内容还没有确认，但必须先确定 category 设计原则。

根目录 category 应当满足：

```text
1. 一个 category 表示一个相对稳定的水文专业领域。

2. category 不应该只是软件名称。

3. category 不应该过细。

4. category 内部未来可以容纳多个 task-level Skill。

5. category 名称应该适合成为稳定 namespace。

6. category 之间允许存在科学交叉。

7. 一个 Skill 未来可以通过 metadata 表示属于多个 domain。

8. 物理目录只表示主要归属。

9. workflow 不属于普通 category。

10. category taxonomy 必须经用户确认。
```

---

# 11. Skill 的未来标准结构

当前只定义规范。

暂不创建具体 Skill。

未来：

```text
<category>/
└── <skill>/
    ├── SKILL.md
    ├── usage-guide.md
    ├── data-contract.yaml
    └── examples/
```

---

# 12. SKILL.md 的未来职责

未来的 `SKILL.md` 主要承担：

```text
Use When
Do Not Use When
Governing Principle
Scientific Assumptions
Input Requirements
Decision Rules
Method Selection
QC
Failure Modes
Interpretation
Related Skills
```

重点是：

> 专家决策逻辑。

不要把它变成长篇教材。

---

# 13. usage-guide.md 的未来职责

未来：

```text
usage-guide.md
```

负责：

```text
Overview
Prerequisites
Typical User Requests
Quick Start
What the Agent Will Do
Inputs
Outputs
Common Mistakes
```

它是扩展说明。

不是主要决策规则来源。

---

# 14. data-contract.yaml 的未来职责

HydroTune-skills 相比 bioSkills，应增加明确的数据契约。

未来需要描述：

```text
数据是什么
单位是什么
时间分辨率是什么
空间意义是什么
缺失值代表什么
输入需要满足什么条件
输出是什么
哪些条件必须保持
```

重点包括水文领域特别重要的：

```text
units
time_step
timezone
calendar
catchment_area
coordinate_reference_system
spatial_unit
variable_semantics
state_or_flux
accumulated_or_instantaneous
missing_value_semantics
warmup_period
simulation_period
```

当前只定义 schema。

不要正式创建具体 Skill contract。

---

# 15. 安装机制：高度模仿 bioSkills

这一部分是强制要求。

HydroTune-skills 的安装机制应尽可能保持与 bioSkills 相同的设计思路。

核心结构：

```text
install-common.sh
        ↑
        │
 ┌──────┼────────┐
 │      │        │
Claude Codex  OpenCode
```

---

# 16. install-common.sh

创建：

```text
install-common.sh
```

它应该承担绝大多数通用安装逻辑。

Agent-specific installer 不应该重复实现主要逻辑。

`install-common.sh` 未来负责：

```text
参数解析
Skill 扫描
category 筛选
Skill 名称生成
目标目录检查
安装
卸载
更新
dry-run
validation
list
日志
错误处理
```

尽量保持 bioSkills 的 thin-adapter 架构。

---

# 17. install-claude.sh

创建：

```text
install-claude.sh
```

尽量模仿 bioSkills 的 Claude 安装器。

核心只定义：

```text
TOOL_NAME
DEFAULT_TARGET_DIR
PROJECT_SUBDIR
copy_skill_files()
print_usage()
```

默认目标：

```text
~/.claude/skills/
```

项目级：

```text
<project>/.claude/skills/
```

然后：

```bash
source install-common.sh
```

---

# 18. install-codex.sh

创建：

```text
install-codex.sh
```

默认安装目标尽量保持 bioSkills / Agent Skills 的约定：

```text
~/.agents/skills/
```

项目级：

```text
<project>/.agents/skills/
```

安装时建议映射：

```text
SKILL.md
→ SKILL.md

usage-guide.md
→ references/usage-guide.md

data-contract.yaml
→ references/data-contract.yaml

examples/
→ scripts/
```

这部分要尽量保持 bioSkills Codex installer 的设计风格。

---

# 19. install-opencode.sh

创建：

```text
install-opencode.sh
```

也应作为 thin adapter。

不要重新实现通用 installer。

尽量复用：

```text
install-common.sh
```

---

# 20. Skill 安装后的命名

参考 bioSkills：

```text
bio-<category>-<skill>
```

HydroTune-skills 应使用：

```text
hydro-<category>-<skill>
```

例如未来可能：

```text
hydro-model-calibration-objective-selection
```

注意：

当前不要创建这个 Skill。

这里只规定命名规则。

---

# 21. Installer 应支持的参数

尽量与 bioSkills 保持一致。

至少设计：

```text
--global
--project
--categories
--list
--validate
--update
--uninstall
--dry-run
--verbose
--force
--help
```

语义也尽量一致。

---

# 22. --categories

虽然当前 category 尚未确定，但 installer 必须从一开始支持：

```bash
./install-codex.sh --categories "xxx,yyy"
```

也就是说：

> category 是安装系统中的一级选择单位。

用户确认 taxonomy 后，不应该再重构 installer。

---

# 23. Category 发现机制

尽量模仿 bioSkills：

```text
root category
    ↓
second-level skill directory
    ↓
SKILL.md
```

只有满足：

```text
<category>/<skill>/SKILL.md
```

的目录才被识别为正式 Skill。

排除：

```text
workflows
resources
scripts
tests
```

等非普通 category。

Workflow 可以有独立识别逻辑。

---

# 24. install-common.sh 不承担领域逻辑

安装器只能负责：

```text
发现
验证
复制
转换布局
```

不能理解：

```text
什么叫洪水
什么叫子流域
什么叫 KGE
什么叫半分布式
```

科学知识必须留在 Skill / Workflow 层。

---

# 25. Validation 架构

创建：

```text
scripts/
└── validate_skills.py
```

当前先搭建 framework。

未来至少检查：

```text
SKILL.md 是否存在
YAML frontmatter 是否有效
name 是否唯一
description 是否存在
category 是否有效
related skill 是否存在
data-contract YAML 是否有效
workflow dependency 是否存在
命名规范是否满足
```

但：

> 不要在 category 未确认前写死 category 枚举。

---

# 26. Workflow Validator

额外考虑：

```text
scripts/validate_workflows.py
```

检查：

```text
workflow.yaml 是否有效
stage id 是否唯一
dependency 是否合法
引用的 Skill 是否存在
是否存在明显循环依赖
QC 节点是否合法
```

当前因为 Skill taxonomy 未确认：

```text
允许 workflow 使用抽象 stage
```

---

# 27. README.md

根 README 当前主要负责解释：

```text
HydroTune-skills 是什么
为什么存在
它和 bioSkills 的关系是什么
整体 architecture
Skill / Workflow 区别
安装机制
未来 category 如何组织
```

不要在 README 中声称：

```text
已经包含多少 Skills
```

因为当前还没有正式确定 Skill taxonomy。

可以写：

```text
Skill taxonomy: under design
```

或者：

```text
等待领域分类确认
```

---

# 28. skill_writing_reference.md

现在可以先创建：

```text
skill_writing_reference.md
```

用于定义以后所有 Skill 的编写规范。

但不要加入具体 Skill 内容。

至少定义：

```text
目录结构
命名规范
frontmatter
Use When
Do Not Use When
Governing Principle
Decision Rules
Data Contract
Failure Modes
QC
Related Skills
examples
Workflow 与 Atomic Skill 的区别
```

---

# 29. 建议的根目录最终形态

当前阶段完成后，项目应该更接近：

```text
HydroTune-skills/
│
├── workflows/
│   │
│   ├── data-preprocessing/
│   │   ├── SKILL.md
│   │   ├── usage-guide.md
│   │   └── workflow.yaml
│   │
│   ├── hydrological-modeling/
│   │   ├── SKILL.md
│   │   ├── usage-guide.md
│   │   └── workflow.yaml
│   │
│   └── simulation-analysis/
│       ├── SKILL.md
│       ├── usage-guide.md
│       └── workflow.yaml
│
├── resources/
│
├── scripts/
│   ├── validate_skills.py
│   └── validate_workflows.py
│
├── tests/
│
├── install-common.sh
├── install-claude.sh
├── install-codex.sh
├── install-opencode.sh
│
├── skill_writing_reference.md
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

注意：

**此时还不应该出现正式的领域 Skill category。**

---

# 30. 本阶段结束条件

完成以下工作后停止：

## A. 仓库骨架

完成基础目录与基础文件。

## B. 安装系统

完成：

```text
install-common.sh
install-claude.sh
install-codex.sh
install-opencode.sh
```

整体架构。

## C. 三个 Workflow

建立：

```text
data-preprocessing
hydrological-modeling
simulation-analysis
```

的架构骨架。

## D. Skill 标准

建立：

```text
skill_writing_reference.md
```

## E. Validator 骨架

建立 validator。

## F. Skill Category 提案

最后提出：

```text
HydroTune-skills 第一版 Skill Category 候选方案
```

要求：

- category 数量不要过多；
- 每个 category 给中文说明；
- 每个 category 给英文目录名；
- 解释边界；
- 指出可能的交叉；
- 不创建目录。

然后停止。

---

# 31. 必须向用户提出的问题

完成架构后，最后必须明确询问用户：

> 请确认 HydroTune-skills 第一版应该包含哪些根目录 Skill Category。

使用类似表格：

| 英文目录名 | 中文名称 | 主要职责 | 是否保留 |
|---|---|---|---|
| 待提出 | 待提出 | 待提出 | 待用户确认 |

然后等待用户：

```text
确认
删除
合并
拆分
重命名
增加
```

---

# 32. 用户确认前禁止执行

用户没有确认 taxonomy 前，禁止：

```text
创建正式 category
创建具体 Skill
生成几十个 SKILL.md
填充 examples
构建 skills-registry
假设 category 已确定
```

---

# 33. 架构设计原则

始终保持：

```text
Repository
    │
    ├── Categories
    │      ↓
    │   Atomic Skills
    │
    ├── Workflows
    │      ↓
    │   Skill orchestration
    │
    ├── Installer
    │      ↓
    │   Agent adaptation
    │
    └── Validator
           ↓
        Quality control
```

未来：

```text
Category
    ↓
找到 Skill

SKILL.md
    ↓
专家判断

data-contract.yaml
    ↓
数据验证

examples/
    ↓
执行

QC
    ↓
结果检查

Workflow
    ↓
连接下一个 Skill
```

---

# 34. HydroTune-skills 与 bioSkills 的关系

架构层面高度参考 bioSkills：

```text
bioSkills
Category
  ↓
Skill
  ↓
SKILL.md
  ↓
Installer
```

HydroTune-skills 在此基础上进一步考虑：

```text
Data Contract
+
Workflow Schema
+
水文空间拓扑
+
水文时间事件结构
+
模拟结果诊断
```

因此：

```text
bioSkills architecture
        ↓
借鉴
        ↓
HydroTune-skills
        ↓
针对水文模拟重新设计
```

不要修改成完全不同的插件体系或 package manager。

安装机制尤其应该尽量保持与 bioSkills 接近。

---

# 35. 当前最重要的原则

当前任务不是：

> 把整个水文学写成几百个 Markdown。

而是：

> 先建立一个稳定、清晰、可以长期扩展的 HydroTune-skills 骨架。

本轮工作的成功标准不是 Skill 数量。

而是下面四件事情是否设计清楚：

```text
1. Skill 放在哪里？

2. Workflow 如何组织 Skill？

3. Skill 如何安装到不同 Agent？

4. Category 如何在得到用户确认后扩展？
```

完成这些以后：

# 停止。

不要继续进入 Skill 内容开发。

等待用户确认 Skill Category taxonomy。
