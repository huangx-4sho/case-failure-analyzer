# Case Failure Analyzer

[English](README.md) · 简体中文 · [技能定义](SKILL.md) · [使用指南](docs/zh/guide.md)

CFA 用于事后分析科学计算基准测试中的失败试次。它读取运行日志、智能体轨迹、任务说明、验证器代码和已采集交付物，按规则生成归因报告，并附上证据位置，供人工或智能体复核。

根因分为四类：智能体决策与执行（`agent`）、验证器规则（`verifier`）、题目设计（`case`）和运行环境（`infra`）。证据不足时返回 `unknown`，通过的试次标记为 `none`。报告支持中文、英文和双语。

运行需要 Python 3.9+，无第三方运行时依赖。分析不重跑科学计算，输入目录保持只读，报告写入独立目录。归因和置信度使用规则与启发式评分；分数不表示根因正确的概率。

## 快速开始

以下命令从本仓库根目录运行。将 `BENCH_ROOT` 设为包含 `jobs/` 与 `tasks/` 的基准仓库路径：

```bash
BENCH_ROOT="/path/to/benchmark"
python3 scripts/cfa.py audit task208 --jobs-root "$BENCH_ROOT" --lang zh
```

命令默认选择该任务修改时间最新的试次，在终端打印归因摘要，并将报告写入 `<bench_root>/failure-report/task208/`。用完整试次名（如 `"task 208__jaekojA"`）可以指定试次；用 `--report-dir` 指定输出位置。省略 `--jobs-root` 时依次查找 `$CFA_JOBS_ROOT`、当前目录及其父目录。

没有自己的作业数据时，可以先运行仓库内的回归夹具：

```bash
REPORT_DIR="$(mktemp -d)"
python3 scripts/analyze_case.py \
  --job tests/fixtures/regressions/mixed-job-success-trial/job \
  --output "$REPORT_DIR" \
  --lang zh
cat "$REPORT_DIR/job_summary.md"
```

该夹具的实际输出包含 1 个已评分通过、0 个已评分失败和 1 个未评分试次。整体通过率为 50.0%，已评分通过率为 100.0%；未评分试次的归因代码为 `UNKNOWN_INSUFFICIENT_EVIDENCE`。这些数字描述夹具中的试次状态，不是归因准确率。

## 查看与对比结果

`inspect` 和 `diff` 读取已经生成的报告。比较两个任务前，先分别运行 `audit`：

```bash
python3 scripts/cfa.py inspect task208 --jobs-root "$BENCH_ROOT" --section errors
python3 scripts/cfa.py inspect task208 --jobs-root "$BENCH_ROOT" --section artifacts
python3 scripts/cfa.py inspect task208 --jobs-root "$BENCH_ROOT" --section step --step 43

python3 scripts/cfa.py audit task207 --jobs-root "$BENCH_ROOT" --lang zh
python3 scripts/cfa.py diff task207 task208 --jobs-root "$BENCH_ROOT"

# 批量审计，并更新 failure-report/INDEX.md
python3 scripts/cfa.py audit --all --jobs-root "$BENCH_ROOT" --lang zh
```

`inspect --section` 还支持 `verdict`、`report`、`confidence`、`evidence`、`anchors` 和 `data`。`step` 省略 `--step` 时显示最近 5 步的索引。`inspect` 与 `diff` 默认遮盖家目录及部分常见凭据格式；`--raw` 只改变输出格式，`--no-redact` 才关闭脱敏。脱敏规则不保证覆盖所有敏感内容。

同一任务的试次对比使用 `diff task208 --trials <t1> <t2>`，需要 `trials/` 下已有审计归档。省略 `--trials` 时按归档报告文件的修改时间选择最近两份，不按原始作业启动时间选择。轻量归档未保存的证据不会被自动补齐；需要完整证据时，在审计时加 `--archive-evidence`。

### 报告目录

任务 ID 模式（`--lang zh --format both`）的典型输出：

```text
failure-report/
├── INDEX.md                      # 汇总已评分通过、已评分失败与未评分试次
└── task208/
    ├── task208.md                # 完整归因报告及证据引用
    ├── summary.md                # 终端打印的归因摘要
    ├── skill-prescription.md     # 仅在 agent 主因且有可复用能力缺口时生成
    ├── trials/                   # 多试次归档（有历史试次时）
    └── data/
        ├── evidence.json
        ├── candidate-hypotheses.json
        ├── analysis.json         # 包含 decision_trace
        └── summary.json
```

显式目录模式使用 `analyze_case.py --job ... --output ...`，输出 `report.md`、`analysis.json` 等文件；多试次作业另有 `job_summary.md` 和 `job_summary.json`。语言、输出格式和文件布局详见[使用指南](docs/zh/guide.md)。

如需单独重建汇总页，可运行 `python3 scripts/cfa.py index --report-root <failure-report目录>`。手动修改 `analysis.json` 后，用 `scripts/validate_analysis.py` 核对规则，再用 `scripts/render_report.py` 重新生成报告。

## 分析方法与限制

![从输入证据，经规则归因，到报告复核的流程](docs/images/cfa_architecture.svg)

CFA 将轨迹中的工具调用与结果关联起来，再对照交付物、任务要求和验证器检查。它支持 ATIF JSON，以及可识别的 OpenAI/Anthropic JSON 或 JSONL 消息结构；文件后缀相同不代表任意轨迹格式都能解析。较长观测被截断时，会尝试保留中部的报错与指标行。

数值核对和因果锚点提供以下线索，复核时需要检查其适用条件：

- 具名指标配对不再因数值偏差过大而被丢弃，但仍依赖字段名匹配；无指标名时仍使用数值邻近阈值。
- `scale_or_sign_hint` 根据数值比例提示常见单位换算、十进制倍率或反号，未读取物理单位元数据，不能单独证明量纲错误。
- `stale_deliverable_sync` 表示执行相关文本中出现了接近复算值的数字。即使缺少交付文件也可能触发，需要继续核对文件、输出和执行顺序。
- 脚本输出路径与 `parameter_evolution` 来自静态提取。参数变化按名称比较首末值，可能跨文件；锚点分数用于排序，不能单独确认因果关系。
- `deliverable_diagnostics` 在扫描限额内检查 JSON/CSV/TSV 的空数据与非有限值；缺键检查只针对已找到的 `results.json`，不等于完整的交付物验证。

字段、阈值和扫描上限见[证据结构](docs/zh/evidence-schema.md)。

### 归因顺序

引擎按固定优先级运行 Gate 0–6，返回首个命中的结论，并记录 `decision_trace`。这是规则顺序，不是每个试次都能被证明的因果时间线。

| 门控 | 检查内容 | 可能结果 |
| :--- | :--- | :--- |
| 0 | 已通过验证或 reward 达到通过阈值 | `none` |
| 1a / 1b | 智能体未启动时的环境错误或证据缺失 | `infra` / `unknown` |
| 1c | 智能体结束后、验证器启动前的运行器与产物采集故障 | `infra` |
| 2 | 题目资产或定义缺陷 | `case` |
| 3 | 验证器崩溃或与失败直接关联的解析缺陷 | `verifier` |
| 4 | 有印证的复算差异，或系综统计一致但瞬时轨迹容差过严 | `agent` / `verifier` |
| 5 | 缺乏智能体行为偏离的正面证据 | `unknown` |
| 6 | 根据行为、契约与科学错误证据细分智能体原因 | `agent` |

仅有 `reward == 0` 不足以归因到 `agent`；智能体未启动时也不能归因到它。验证器未运行的试次单列为未评分。验证器给出的差值不能当作交付值，智能体最终回复中的数字也不能替代执行证据。完整条件与例外见[归因协议](docs/zh/attribution-protocol.md)和[技能定义](SKILL.md)。

## 软件规则与开发

[错误注册表](references/error-families.json)包含 11 组软件、60 类错误家族，涵盖量子化学、分子动力学、机器学习原子间势和通用数值计算。这里的覆盖指日志匹配规则及判别证据指南，不代表各软件全部版本与错误都已验证。软件清单见[使用指南](docs/zh/guide.md#科学软件错误签名)。

`SKILL.md` 和 `agents/` 定义技能入口，`scripts/` 保存分析与报告代码，`references/` 保存协议和字段说明，`docs/` 提供中英文文档，`tests/` 包含单元测试与回归夹具。

```bash
make compile       # Python 语法检查
make test          # 标准库 unittest
make check-docs    # 英文文档镜像及指南语言链接检查
make smoke         # 内置两试次夹具
make check         # lint、测试、文档与格式检查（需要 ruff）
make export-skill DEST=/path/to/skills/case-failure-analyzer
```

## 文档索引

| 主题 | 简体中文 | English |
| :--- | :--- | :--- |
| 工作流与使用方法 | [使用指南](docs/zh/guide.md) | [Guide](docs/en/guide.md) |
| 根因类别 | [根因分类](docs/zh/taxonomy.md) | [Taxonomy](docs/en/taxonomy.md) |
| 判定顺序与证据要求 | [归因协议](docs/zh/attribution-protocol.md) | [Attribution protocol](docs/en/attribution-protocol.md) |
| 机器可读字段 | [证据结构](docs/zh/evidence-schema.md) | [Evidence schema](docs/en/evidence-schema.md) |
| Markdown 产物 | [报告规范](docs/zh/report-schema.md) | [Report schema](docs/en/report-schema.md) |
| 可复用的智能体能力缺口 | [技能处方策略](docs/zh/skill-prescription-policy.md) | [Skill prescription policy](docs/en/skill-prescription-policy.md) |
