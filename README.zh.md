<div align="center">

# Case Failure Analyzer

**面向科学计算与 MLIP 基准用例的证据优先因果失败分析器**

[English](README.md) · [简体中文](README.zh.md)

`Python 3 · 运行时仅依赖标准库` &nbsp;|&nbsp; `四大并列归因` &nbsp;|&nbsp; `中英双语报告`

</div>

适配 `CP2K` · `Quantum ESPRESSO` · `VASP/ABACUS` · `ORCA/Gaussian/PySCF` · `LAMMPS` · `GROMACS/AMBER/OpenMM` · `MLIP (MACE/NequIP/DeePMD/CHGNet)` · `ASE` · `xTB` · `RDKit` · `SciPy/NumPy/JAX/PyTorch/FEniCS/OpenFOAM`。

---

## 目录

- [核心亮点](#核心亮点)
- [快速开始](#快速开始)
- [命令行参考](#命令行参考)
- [架构流程](#架构流程)
- [仓库结构](#仓库结构)
- [开发与测试](#开发与测试)
- [说明文档索引](#说明文档索引)

---

## 核心亮点

| 你得到 | 呈现方式 |
|---|---|
| **错误一眼明了** | 「错误矩阵」：失败检查项 · 错误性质 · 交付值 vs 复算/期望值 · 偏差/容差（超标倍数）· 来源 |
| **证据人话化** | 将 `sci:…`、`sig:…`、`art:…` 等内部引用解析为带文件/行号指向的自然语言条目 |
| **可信度透明** | 计分收据（`points / denominator`）、逐项加减分因子，以及提分所缺证据 |
| **四大并列裁决** | `agent` · `verifier` · `case` · `infra`，各自标注主因 / 伴随 / 已排除并附理由 |
| **全局看板** | `INDEX.md`：通过率、占未通过比例的大类分布（总和 100%）、任务自然排序、直达链接 |

---

## 快速开始

```bash
# 按任务号审计单个基准任务（自动选最新 trial 并输出结论卡）
python3 scripts/cfa.py audit task208 --jobs-root <benchmark checkout> --lang zh

# 批量审计基准目录下全部任务
python3 scripts/cfa.py audit --all --jobs-root <benchmark checkout>

# 只读检视结果（自动脱敏凭据与家目录路径）
python3 scripts/cfa.py inspect task208 --section errors
```

报告落在 `<benchmark>/failure-report/<task_id>/`，同级自动维护 `INDEX.md`。

---

## 命令行参考

### `cfa.py` 子命令

| 命令 | 用途 |
|---|---|
| `audit <task>` / `audit --all` | 解析 trial、执行完整流水线并输出结论卡 |
| `inspect <task> --section <name> [--raw]` | 对已完成审计做只读检视 |
| `index --report-root <dir>` | 原子重建全局 `INDEX.md` |

### `inspect` 视图

| 视图 | 内容 |
|---|---|
| `verdict` | 根因速览卡片（`summary.md`） |
| `errors` | 裁决、主因、验证器可诊断性，及每个具名失败检查的性质与偏差 |
| `confidence` | 置信度、证据强度、`points / denominator`、带符号因子、提分缺口 |
| `evidence` | 运行态、失败检查数、因果锚点、科学观测与行为信号的紧凑摘要 |
| `--raw` | 输出所选视图的原生无格式 JSON |

### 路径模式（`analyze_case.py`）

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --lang bilingual
```

| 参数 | 说明 |
|---|---|
| `--job` | 作业目录或具体 Trial 目录 **（必选）** |
| `--output` | 输出目录 **（必选）**；严格保障输入只读，禁止与输入路径重叠 |
| `--task` | 任务定义目录（可选，省略时自动发现） |
| `--trial` | Trial 名称或 `all`（默认 `all`） |
| `--lang` | `bilingual`（默认）· `zh` · `en` |
| `--format` | `both`（默认）· `json` · `markdown` |
| `--phase` | `all`（流水线 + 保守归因 + 报告）· `collect`（仅采集证据与候选假设） |

**输出文件**（位于 `<output-dir>/<case_id>/`）：

| 文件 | 内容 |
|---|---|
| `evidence.json` | 客观产物清单、`runtime_state`、标准化轨迹、契约审计、科学软件错误观测 |
| `candidate-hypotheses.json` | 带启发式证据评分的竞争根因假设 |
| `analysis.json` | 因果归因结果（`failure-analysis-v1`）；`--phase all` 下为保守确定性草稿 |
| `report.md` / `report.zh.md` / `report.en.md` | 含 4 个核心板块的审计报告（通过案例精简为 30 行内轻量视图） |
| `skill-prescription*.md` | 领域技能处方，仅在判定为可复用 Agent 能力缺口时生成 |
| `job_summary.md` / `.json` | 多 Trial 批处理看板（相对标识符，无本机绝对路径） |

**仅采集证据**：在上述命令后追加 `--phase collect`。

**模型介入裁决**：先校验模型撰写的归因，再渲染报告：

```bash
python3 "$SKILL_DIR/scripts/validate_analysis.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --report   <output-dir>/<case_id>/report.md

python3 "$SKILL_DIR/scripts/render_report.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --output-report <output-dir>/<case_id>/report.md \
  --output-prescription <output-dir>/<case_id>/skill-prescription.md \
  --lang bilingual
```

### 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 分析成功（包括合法的 Job 级启动前基础设施失败） |
| `2` | 无效输入：路径不存在、`--trial` 不存在、空作业目录且无 Job 级证据，或请求了未实现的 `--replay` |

入口脚本在写出任何报告**之前**快速失败校验，避免错误路径产出看似合理的误导性报告。

---

## 架构流程

```text
产物与状态采集    discover_artifacts · runtime_state · extract_runtime_errors
        ↓
客观证据数据      evidence.json
        ↓
竞争假设生成      generate_hypotheses → candidate-hypotheses.json
        ↓
因果归因引擎      analyze_case + attribution-protocol → analysis.json
        ↓
Schema 与硬规则校验   validate_analysis
        ↓
双语审计报告渲染  render_report → report.md · report.zh.md · report.en.md · skill-prescription.md
```

因果判定位于 `scripts/attribution/`：每个门（gate）一个函数，由 `engine.py` 按固定顺序执行，最先命中者生效。

```text
gate0_passed → gate1a_infra_prestartup → gate1b_unknown_no_evidence
             → gate2_case_definition → gate3_verifier_defect
             → gate4_numerical_divergence → gate5_insufficient_positive_evidence
             → gate6_agent_primary
```

- 每个门要么放弃，要么返回完整的 15 键归因结果；`schema.py` 在构造时校验键契约。
- 门的顺序属于关键契约，由测试锁定。
- `analysis.json` 含可选的 `decision_trace`：被评估的门、各门放弃原因及最终命中的门。
- `evidence.json` 含可选的 `diagnostics` 列表：非空表示某次可恢复失败降低了证据质量（如 `tests/verify.py` 无法解析）。同样的记录会以 `ANALYSIS DIAGNOSTIC: {...}` 输出到 stderr；stdout 与退出码不受影响。
- `tests/test_attribution_characterization.py` 将十种原型 fixture 的输出与已提交基线比对；如需有意更新，使用 `UPDATE_BASELINE=1` 重新生成。

---

## 仓库结构

```text
case-failure-analyzer/
├── SKILL.md                 # Skill 入口与运行时契约（面向 Agent，仅英文）
├── README.md / README.zh.md # 项目概览与命令行用法
├── Makefile                 # test、lint、smoke、check-docs、export-skill、clean
├── pyproject.toml           # 包元数据与可选 [dev] 依赖
├── agents/openai.yaml       # codex 运行时接口元数据
├── docs/{en,zh}/            # references/ 的人类可读镜像（由 make check-docs 守护）
├── references/              # Agent 读取文档的唯一真相源
│   ├── *.md                 # taxonomy、归因协议、schema、处方策略
│   ├── error-families.json  # 统一 error-family 注册表
│   └── software/            # 11 个分软件知识库
├── scripts/                 # 确定性证据流水线与报告渲染
│   ├── analyze_case.py      # 入口：collect → attribute → render
│   ├── cfa.py               # 统一 CLI：audit / inspect / index
│   ├── staging.py           # 暂存区、文件锁、输入隔离、清单事务发布
│   ├── validate_analysis.py # Schema 与硬规则校验器
│   ├── render_report.py     # 报告与技能处方渲染器
│   └── attribution/         # 归因引擎（engine、schema、context）+ gates/gate0..gate6
└── tests/                   # unittest 套件、fixture、归因基线
```

本地产物（`.venv/`、`__pycache__/`、`*.egg-info/`、`.ruff_cache/`、`.dev/`）及 bench 侧 `failure-report/` 看板已被 `.gitignore` 排除。实际分发的 skill 只含 `SKILL.md`、`agents/`、`scripts/` 与 `references/`。

---

## 开发与测试

运行时仅依赖标准库；测试工具需额外安装，但 `make test` 无需安装任何依赖。

| 命令 | 作用 |
|---|---|
| `make test` | `python3 -m unittest discover -s tests -t .` |
| `make check` | lint + 测试 + 文档镜像检查 + 格式检查 |
| `make compile` | 对 `scripts/` 与 `tests/` 做语法检查 |
| `make smoke` | 针对已提交双 trial fixture 的端到端 CLI 运行 |
| `make dev` / `make test-pytest` | 可选：安装 `[dev]` 依赖 / 使用 pytest 运行 |
| `make export-skill [DEST=…]` | 导出纯净技能目录 |
| `make help` | 列出全部目标 |

### 导出生产技能

`make export-skill` 导出 `SKILL.md`、`agents/`、`scripts/`、`references/`，自动排除 `tests/`、`docs/` 及开发配置（`Makefile`、`pyproject.toml`、`uv.lock`、`.ruff_cache`、`.venv`、`.git`、`.dev/`、旧版 `failure-analysis/`）。

```bash
make export-skill                                  # 默认：$CODEX_HOME/skills/case-failure-analyzer
make export-skill DEST=/path/to/case-failure-analyzer
```

`scripts/export_skill.py` 拒绝不安全的目标路径（`/`、`$HOME`、源码仓库根目录），并以非破坏方式同步：保留用户自有文件，通过 `.cfa_export_manifest.json` 追踪受管产物。

---

## 说明文档索引

| 文档 | 中文版 | 英文版 |
|---|---|---|
| 项目概览与命令行指南 | [README.zh.md](README.zh.md) | [README.md](README.md) |
| 系统工作流与归因总规范 | [guide](docs/zh/guide.md) | [guide](docs/en/guide.md) |
| 失败分类学（`failure-analysis-v1`） | [taxonomy](docs/zh/taxonomy.md) | [taxonomy](docs/en/taxonomy.md) |
| 逆向因果归因协议 | [protocol](docs/zh/attribution-protocol.md) | [protocol](docs/en/attribution-protocol.md) |
| 证据与归因 JSON 模式 | [schema](docs/zh/evidence-schema.md) | [schema](docs/en/evidence-schema.md) |
| 审计报告结构规范 | [report](docs/zh/report-schema.md) | [report](docs/en/report-schema.md) |
| 领域技能处方生成策略 | [policy](docs/zh/skill-prescription-policy.md) | [policy](docs/en/skill-prescription-policy.md) |

> `SKILL.md` 与 `references/` 专供 Agent 运行时读取，统一保留纯英文；人类开发者请阅读上表的 `docs/` 双语版本。
