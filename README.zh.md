# Case Failure Analyzer

[English](README.md) · 简体中文

CFA 是一个可由不同 agent 调用的科学计算基准失败分析 skill。它读取运行日志、智能体轨迹、任务说明、验证器和交付物，从智能体、验证器、题目设计及运行环境四类原因中进行归因，生成带证据引用的报告。证据不足时保留未知结论。

![分析流程：读取材料、核对证据、规则归因与报告复核](docs/images/cfa-workflow.zh.svg)

需要 Python 3.9+，无第三方运行时依赖。只核验已有产物及测评验证记录，不追加复算；报告支持中文、英文和双语。

## 使用

从本仓库根目录导出到所用客户端的技能目录（[安装位置](docs/zh/guide.md#安装与调用)）：

```bash
python3 scripts/export_skill.py --dest /path/to/skills/case-failure-analyzer
```

在智能体聊天中调用；不支持技能发现的客户端可直接读取导出目录中的 `SKILL.md`：

```text
使用 case-failure-analyzer 分析 task208，基准仓库是 /path/to/benchmark。
请用中文核验缺失产物、关联已有验证记录，说明候选原因和证据缺口。
```

也可以直接在终端运行：

```bash
python3 scripts/cfa.py audit task208 --jobs-root /path/to/benchmark --lang zh
```

需要独立英文版时使用 `--lang bilingual`：任务号模式输出 `<task_id>.en.md`，显式路径模式输出 `report.en.md`；无语言后缀的文件是主报告，不代表英文版。

将示例路径换成包含 `jobs/` 与 `tasks/` 的基准仓库路径。任务编号默认匹配修改时间最新的试次。指定试次、批量分析、结果检视、对比与更新方法见[使用指南](docs/zh/guide.md)。

## 输出

任务 ID 模式默认写入 `<benchmark>/failure-report/<job>/<task_id>/`，并在终端或聊天中返回归因摘要。可按下面的顺序阅读结果：

![报告阅读路径：摘要、完整报告与原始证据](docs/images/cfa-reports.zh.svg)

## 文档

[安装与调用](docs/zh/guide.md#安装与调用) · [根因分类](docs/zh/taxonomy.md) · [归因规则](docs/zh/attribution-protocol.md)

[证据字段](docs/zh/evidence-schema.md) · [报告格式](docs/zh/report-schema.md) · [技能处方](docs/zh/skill-prescription-policy.md) · [已有证据审查](docs/zh/evidence-review.md) · [技能定义](SKILL.md)
