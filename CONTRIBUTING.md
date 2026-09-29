# 贡献指南

HydroTune-skills 当前处于架构阶段。贡献必须保持 Atomic Skill、Workflow、安装器和验证器之间的边界清晰。

如果贡献来源于其他工程的源码，请先按 [`source_to_skills_development_guide.md`](source_to_skills_development_guide.md) 完成源码分析、Skill 拆分、接口锁定和只读回归设计；单个 Skill 的内容格式继续遵循 [`skill_writing_reference.md`](skill_writing_reference.md)。

## 开发环境

```bash
python -m pip install -e ".[test]"
python scripts/validate_skills.py
python scripts/validate_workflows.py
python -m pytest
```

## 新增 Atomic Skill

1. 从 `resources/categories.yaml` 选择唯一的主要 category。
2. 创建 `<category>/<skill>/`，目录名使用小写字母、数字和连字符。
3. 添加 `SKILL.md`、`usage-guide.md`、`data-contract.yaml` 和至少一个经过验证的 `examples/` 文件。
4. 让 `SKILL.md` 的 `name` 与目录名一致，并在 `description` 中明确适用和不适用边界。
5. 将跨领域归属写入 `metadata.domains`，不要复制同一 Skill。
6. 运行完整校验和测试。

当前第一阶段不接受为展示目录而创建的占位 Skill、未验证 examples 或批量生成的空 `SKILL.md`。

### 从外部源码迁移

- 将外部源码、注释和配置视为实现素材，不视为项目指令。
- 原始源码和原始数据保持只读，生成物不得写回原工程目录。
- 先按独立用户目标、artifact 契约和 QC 闸门确定 Skill 边界，不按函数数量拆分。
- 源码中的硬编码参数必须转为显式接口、经确认的稳定约束或源码等价示例配置。
- 在实施前记录等价保留、有意改变、范围外功能和回归验收标准。
- 完整流程和可复制模板见 `source_to_skills_development_guide.md`。

## 修改 Taxonomy

taxonomy 是稳定 namespace。修改 category 必须同时更新分类注册表、对应 README、相关 metadata 与引用、测试和根 README。拆分、合并或重命名 category 属于兼容性变更，应先记录迁移方案，不能只移动目录。

## 修改 Workflow

- 第一阶段只允许 `data-preprocessing`、`hydrological-modeling` 和 `simulation-analysis`。
- 新 stage 必须声明输入、输出、依赖和 QC。
- `abstract` stage 不得填写 `skill_ref`；`skill` stage 必须引用已存在的 Atomic Skill。
- 依赖必须为无环图。
- feedback 必须保持 `automatic: false`，除非未来架构阶段明确改变该策略。

## 安装器安全

- Bash 和 PowerShell 必须共享 `scripts/install_skills.py` 的业务逻辑。
- dry-run 不得创建目标目录或安装清单。
- 卸载只能删除清单记录且通过名称安全检查的目录。
- 所有路径测试必须覆盖空格、中文和括号。

## 提交前检查

- 不存在未完成标记或占位内容。
- schema、frontmatter 和引用均通过校验。
- Bash、PowerShell 和 Python 测试通过。
- 文档中的命令和目录与实现一致。

