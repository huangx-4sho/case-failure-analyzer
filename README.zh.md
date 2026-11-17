# Case Failure Analyzer

[English](README.md) · 简体中文

CFA 是一个用于科学计算基准失败分析的 skill。它读取运行日志、智能体轨迹、任务说明、验证器和交付物，从智能体、验证器、题目设计及运行环境四类原因中进行归因，生成带证据引用的报告。证据不足时保留未知结论。

![分析流程：读取材料、核对证据、规则归因与报告复核](docs/images/cfa-workflow.zh.svg)

需要 Python 3.9+，无第三方运行时依赖。报告支持中文、英文和双语。

## 使用

从本仓库根目录导出到 Codex 的用户技能目录：

```bash
python3 scripts/export_skill.py --dest "$HOME/.agents/skills/case-failure-analyzer"
```

然后在 Codex 聊天中输入：

```text
$case-failure-analyzer 分析 task208，基准仓库是 /path/to/benchmark。
请用中文说明主因、四类原因的判断依据，并给出报告链接。
```

也可以直接在终端运行：

```bash
python3 scripts/cfa.py audit task208 --jobs-root /path/to/benchmark --lang zh
```

将示例路径换成包含 `jobs/` 与 `tasks/` 的基准仓库路径。任务编号默认匹配修改时间最新的试次。指定试次、批量分析、结果检视、对比与更新方法见[使用指南](docs/zh/guide.md)。

## 输出

任务 ID 模式默认写入 `<benchmark>/failure-report/<task_id>/`，并在终端或聊天中返回归因摘要。可按下面的顺序阅读结果：

![报告阅读路径：摘要、完整报告与原始证据](docs/images/cfa-reports.zh.svg)

## 文档

[安装与调用](docs/zh/guide.md#安装与调用) · [根因分类](docs/zh/taxonomy.md) · [归因规则](docs/zh/attribution-protocol.md)

[证据字段](docs/zh/evidence-schema.md) · [报告格式](docs/zh/report-schema.md) · [技能处方](docs/zh/skill-prescription-policy.md) · [技能定义](SKILL.md)
