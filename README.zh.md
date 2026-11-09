<div align="center">

# Case Failure Analyzer · 用例失败归因

**科学计算基准的证据归因与失败审计**

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Runtime · stdlib](https://img.shields.io/badge/Runtime-stdlib-15803D?style=flat-square)](pyproject.toml)
[![Reports · 中文 / EN](https://img.shields.io/badge/Reports-中文%20%2F%20EN-7C3AED?style=flat-square)](SKILL.md)

[English](README.md) · [技能定义](SKILL.md) · [使用指南](docs/zh/guide.md)

[快速开始](#快速开始) · [归因流程](#归因流程) · [报告产物](#报告产物) · [开发](#仓库结构与开发) · [文档](#文档索引)

</div>

---

Case Failure Analyzer（CFA）用于审计科学计算基准试次。它读取已有的作业与试次日志、智能体轨迹、任务定义、验证器结果及代码、已采集的交付产物，生成带证据引用的裁决、Markdown 报告和结构化数据。

> **运行要求** · Python 3.9+ · 运行时仅使用标准库
>
> 审计读取已有产物，并将结果写到独立的报告目录。对仿真和验证器代码的分析基于静态证据。

任务 ID 模式生成的核心产物

| 归因裁决 | 证据追溯 | 批量总览 |
| :--- | :--- | :--- |
| 比较四类根因，给出主因及依据 | 定位失败检查、日志与执行步骤 | 汇总评分状态、通过率与任务报告 |
| `summary.md` | `data/evidence.json` | `INDEX.md` |

---

## 快速开始

在 CFA 仓库中执行以下命令，将 `BENCH_ROOT` 设为包含作业与任务定义的基准仓库路径。

### 1. 审计任务

```bash
BENCH_ROOT="/path/to/benchmark"
python3 scripts/cfa.py audit task208 --jobs-root "$BENCH_ROOT" --lang zh
```

### 2. 查看失败检查

```bash
python3 scripts/cfa.py inspect task208 --jobs-root "$BENCH_ROOT" --section errors
```

### 3. 批量审计

```bash
python3 scripts/cfa.py audit --all --jobs-root "$BENCH_ROOT" --lang zh
```

<details>
<summary><strong>任务选择、裁决阅读与检视选项</strong></summary>

- **审计单个任务。** `task208` 默认选择按修改时间排序的最新匹配试次。用引号包住完整名称（如 `"task 208__jaekojA"`）可指定试次。省略根目录时，优先使用 `CFA_JOBS_ROOT`，未设置时依次查找当前目录及其父目录。
- **阅读裁决。** 单任务命令在终端打印速览卡及 `REPORT_DIR:`。卡片先给出裁决和主因，再比较四类归因，最后展开主因子类、证据及可获得的具名失败检查。作为技能调用时，在对话中直接给出卡片并附上报告链接。
- **批量审计。** `audit --all` 自动发现任务编号，按自然数序处理并重建全局索引。加上 `--verdict-stdout` 可逐项打印速览卡。
- **检视已有结果。** `inspect` 支持 `verdict`、`report`、`errors`、`confidence`、`evidence`、`anchors` 和 `data`。默认遮盖用户主目录路径和符合已知模式的凭据。适用时可通过 `--raw` 查看原始 JSON 或段落文本。

报告根目录由基准仓库的布局决定。重建索引时，将实际的 `failure-report/` 路径传给 `python3 scripts/cfa.py index --report-root /path/to/failure-report`。

</details>

<details>
<summary><strong>按目录审计 · 可运行的回归示例</strong></summary>

### 按作业或试次目录审计

已有目录路径时，使用 `scripts/analyze_case.py --job … --output …`。输出目录须与输入隔离。可用 `--task` 指定任务定义，或用 `--trial` 从作业中选取一个试次。

以下示例直接使用仓库内的回归测试夹具。

```bash
REPORT_DIR="$(mktemp -d)"
python3 scripts/analyze_case.py \
  --job tests/fixtures/regressions/mixed-job-success-trial/job \
  --output "$REPORT_DIR" \
  --lang zh
cat "$REPORT_DIR/job_summary.md"
```

夹具包含 **1 个已评分通过、0 个已评分失败和 1 个未评分试次**。报告中的**整体通过率为 50.0%**，**已评分通过率为 100.0%**。未评分试次的归因代码为 `UNKNOWN_INSUFFICIENT_EVIDENCE`。

</details>

---

## 归因流程

```mermaid
flowchart LR
    A["日志与任务产物"]
    B["证据整理"]
    C["候选假设与规则判定"]
    D["裁决与报告"]
    A --> B --> C --> D
    style A fill:#f1f5f9,stroke:#64748b,color:#0f172a
    style B fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a
    style C fill:#f5f3ff,stroke:#8b5cf6,color:#4c1d95
    style D fill:#ecfdf5,stroke:#10b981,color:#064e3b
```

CFA 将运行状态、轨迹事件、任务契约、验证器检查及科学计算错误特征整理成统一证据，记录候选假设，再按固定顺序评估判定规则。首条适用规则生成 `failure-analysis-v1` 结构化结论和判定轨迹。证据引用可回查观测记录，并在信息可用时定位到源文件或执行步骤。

| 并列归因类别 | 审查内容 |
| --- | --- |
| `agent` · 智能体决策与执行 | 方法、参数、工作流、任务契约履行情况及交付结果自检 |
| `verifier` · 验证器规则与评分 | 解析与比较缺陷、容差规则及验证器内部复算异常 |
| `case` · 基准与题目设计 | 资产缺失、需求歧义及参考依据不一致 |
| `infra` · 运行环境与基础设施 | 启动、容器、网络及运行器故障，包括产物采集异常 |

判定顺序依次覆盖已通过试次、启动与运行器边界、题目缺陷、验证器缺陷、数值偏差、证据不足和智能体执行问题。`unknown` 用于记录证据不足。归因于智能体需要可观测的具体偏离。数值偏差需要任务要求、验证器规则及运行证据互相印证。置信度明细列出影响评分的证据因素。

### Harbor 作业处理

- 以 `task.toml` 声明的交付路径为准，在采集后的产物目录中核对。
- 智能体启动后、验证器运行前发生的运行器故障可能导致试次未评分。命中产物采集规则时，基础设施是主因，缺失的交付物可记录为伴随因素。
- 对已支持的验证器 JSON 结构提取具名检查和可诊断性信息。批量汇总区分已评分通过、已评分失败和未评分三种状态。

### 科学软件覆盖

注册表包含 11 组软件与工作流下的 60 类已实现错误特征，覆盖 CP2K、Quantum ESPRESSO、LAMMPS、ASE 和机器学习原子间势等场景。这些模式用于从现有日志及产物中识别异常观测。[错误模式注册表](references/error-families.json) · [软件参考](references/software/)

---

## 报告产物

下表对应完整审计和默认的 `--format both`。可用 `--lang zh`、`en` 或 `bilingual`（默认）选择语言，或用 `--phase collect` 仅收集证据并生成候选假设。

| 入口 | 默认输出 |
| --- | --- |
| 任务 ID · `cfa.py audit task208` | `failure-report/task208/task208.md` 与 `summary.md`，证据、假设、归因和卡片数据放在 `data/`。全局 `failure-report/INDEX.md` 汇总已审计任务的评分和归因。 |
| 单试次路径 · `analyze_case.py --job … --output …` | 输出目录下直接生成 `report.md` 及对应语言版本，以及 `evidence.json`、`candidate-hypotheses.json`、`analysis.json`。 |
| 多试次作业路径 | 输出目录下生成 `job_summary.md` 和 `job_summary.json`，各试次的报告和 JSON 放在对应名称的子目录中。 |

任务 ID 模式的典型目录（`--lang zh --format both`）

```text
failure-report/
├── INDEX.md
└── task208/
    ├── task208.md
    ├── summary.md
    ├── skill-prescription.md    # 按需生成
    └── data/
        ├── evidence.json
        ├── candidate-hypotheses.json
        ├── analysis.json
        └── summary.json
```

任务 ID 模式可用 `--report-dir` 指定报告目录。只有主因为 `agent` 且发现可复用的能力缺口时，才生成 `skill-prescription.md`。如需调整 `analysis.json`，可用 `scripts/validate_analysis.py` 对照证据校验，再用 `scripts/render_report.py` 重新渲染报告。

---

## 仓库结构与开发

| 路径 | 用途 |
| --- | --- |
| [`SKILL.md`](SKILL.md) · [`agents/openai.yaml`](agents/openai.yaml) | 技能执行说明及界面元数据 |
| [`scripts/`](scripts/) · [`scripts/attribution/`](scripts/attribution/) | CLI、证据采集、归因、校验及渲染 |
| [`references/`](references/) · [`docs/`](docs/) | 技能参考资料及中英文文档 |
| [`tests/`](tests/) | CLI、归因、报告测试及回归夹具 |

```bash
make test
make check-docs
make smoke
make export-skill DEST=/path/to/skills/case-failure-analyzer
```

`make test` 使用 `unittest`。`make check` 还会执行 Ruff 检查，需要开发工具。`make export-skill` 打包技能说明、界面元数据、脚本和参考资料，默认导出至 `$CODEX_HOME/skills/case-failure-analyzer`，其中 `CODEX_HOME` 默认为 `~/.codex`。

---

## 文档索引

| 主题 | 简体中文 | English |
| --- | --- | --- |
| 工作流与使用方法 | [使用指南](docs/zh/guide.md) | [Guide](docs/en/guide.md) |
| 根因类别 | [根因分类](docs/zh/taxonomy.md) | [Taxonomy](docs/en/taxonomy.md) |
| 判定顺序与证据要求 | [归因协议](docs/zh/attribution-protocol.md) | [Attribution protocol](docs/en/attribution-protocol.md) |
| 机器可读字段 | [证据结构](docs/zh/evidence-schema.md) | [Evidence schema](docs/en/evidence-schema.md) |
| Markdown 产物 | [报告规范](docs/zh/report-schema.md) | [Report schema](docs/en/report-schema.md) |
| 可复用的智能体能力缺口 | [技能处方策略](docs/zh/skill-prescription-policy.md) | [Skill prescription policy](docs/en/skill-prescription-policy.md) |
