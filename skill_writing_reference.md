# HydroTune-skills Skill 编写规范

本规范定义未来 Atomic Skill 和 Workflow Skill 的稳定作者接口。Skill 应保存会改变 Agent 决策的水文专业约束，而不是写成长篇教材或软件命令清单。

## 1. Atomic Skill 目录

```text
<category>/<skill>/
├── SKILL.md
├── usage-guide.md
├── data-contract.yaml
└── examples/
```

- category 必须来自 `resources/categories.yaml`。
- skill 名称使用小写字母、数字和连字符。
- 同一 Skill 只有一个物理主归属；其他领域写入 `metadata.domains`。
- `examples/` 必须包含经过验证、可解释关键参数来源的文件，不允许空目录或占位脚本。

## 2. SKILL.md

### Frontmatter

```yaml
---
name: skill-name
description: Use when ...; do not use when ...
metadata:
  category: category-name
  domains:
    - category-name
  tool_type: instruction
  primary_tool: agent-reasoning
  related_skills: []
---
```

要求：

- `name` 必须等于目录名；安装器会将其转换为 `hydro-<category>-<skill>`。
- `description` 必须简洁说明能力、正向触发条件和容易混淆的不适用边界。
- `tool_type` 只能是 `instruction`、`python`、`r`、`cli` 或 `mixed`。
- `primary_tool` 是单个字符串，不使用逗号拼接工具列表。
- `related_skills` 使用 `category/skill` 完整引用，且目标必须存在。

### 正文职责

按任务实际需要组织，但必须覆盖会影响正确性的内容：

1. Use When 与 Do Not Use When。
2. Governing Principle 和科学假设。
3. 输入要求、用户确认闸门和停止条件。
4. 方法选择与决策规则。
5. QC、失败模式和可观察症状。
6. 结果解释边界与下一步 Skill。

只把共同且必要的规则放入入口文件。较长的操作说明放入 `usage-guide.md`；确定性重复操作才放入 `examples/`，安装后会成为 `scripts/`。

## 3. usage-guide.md

建议包含 Overview、Prerequisites、Typical User Requests、Quick Start、What the Agent Will Do、Inputs、Outputs、Common Mistakes 和 Related Skills。它用于扩展说明，不应与 `SKILL.md` 重复同一套决策规则。

## 4. data-contract.yaml

数据契约必须通过 `resources/schemas/data-contract.schema.json`，并至少声明：

- `schema_version` 与 `skill`；
- 输入和输出 artifact；
- 变量语义、单位和缺测语义；
- 适用时的 time step、timezone、calendar、CRS、catchment area 和 spatial unit；
- 状态/通量、累积/瞬时语义；
- warm-up、simulation period 和不可破坏的不变量。

未知值必须记录为未确认或 unavailable，不得写入经验默认值并伪装为事实。

## 5. Workflow Skill

Workflow 使用：

```text
workflows/<workflow>/
├── SKILL.md
├── usage-guide.md
└── workflow.yaml
```

`workflow.yaml` 的每个 stage 都必须有 `id`、`type`、`description`、`depends_on`、`inputs`、`outputs` 和 `qc`。

- `abstract` 表示架构阶段的能力节点，不绑定 Skill。
- `skill` 必须通过 `skill_ref` 引用已存在的 `category/skill`。
- 依赖必须无环，QC 失败时必须保留停止或降级语义。
- Workflow 负责编排，不能复制 Atomic Skill 的完整专家内容。

## 6. 证据与解释规则

Skill 必须区分确定性 artifact 事实、runtime 证据与推荐、用户确认、工程推断以及未知或 unavailable。只有确定性事实和用户确认可作为 confirmed 输入。不得把列名推断、经验惯例、相关性、单个指标或排名直接写成已确认元数据或因果结论。

## 7. 安装映射

安装器会重写 frontmatter `name`，并执行：

```text
usage-guide.md       -> references/usage-guide.md
data-contract.yaml   -> references/data-contract.yaml
workflow.yaml        -> references/workflow.yaml
examples/            -> scripts/
assets/              -> assets/
```

`SKILL.md` 中指向前三个源文件的直接 Markdown 链接会在安装时改写到 `references/`。

## 8. 完成标准

- 正反触发边界明确。
- 数据契约和示例与正文一致。
- 所有参数、阈值和 magic number 有来源或理由。
- 失败模式包含触发条件、机制、症状和修正方向。
- 校验器和测试通过，不存在未完成占位标记。

