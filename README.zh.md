# case failure analyzer（科学计算基准用例失败因果分析器）

**[English](README.md) | [简体中文](README.zh.md)**

面向科学计算与机器学习势（MLIP）基准评测（`CP2K`、`Quantum ESPRESSO`、`VASP/ABACUS`、`ORCA/Gaussian/PySCF`、`LAMMPS`、`GROMACS/AMBER/OpenMM`、`MLIP (MACE/NequIP/DeePMD/CHGNet)`、`ASE`、`xTB`、`RDKit`、`SciPy/NumPy/JAX/PyTorch/FEniCS/OpenFOAM`）的证据优先（Evidence-First）因果失败审计 Skill 与工具集。

---

## 说明文档索引

| 文档名称 | 中文版 (`docs/zh/`) | 英文版 (`docs/en/`) |
|---|---|---|
| **项目概览与命令行使用指南** | [README.zh.md](README.zh.md) | [README.md](README.md) |
| **系统工作流与归因总规范** | [docs/zh/guide.md](docs/zh/guide.md) | [docs/en/guide.md](docs/en/guide.md) |
| **失败分类学规范 (`failure-analysis-v1`)** | [docs/zh/taxonomy.md](docs/zh/taxonomy.md) | [docs/en/taxonomy.md](docs/en/taxonomy.md) |
| **逆向因果归因协议** | [docs/zh/attribution-protocol.md](docs/zh/attribution-protocol.md) | [docs/en/attribution-protocol.md](docs/en/attribution-protocol.md) |
| **证据与归因 JSON 模式规范** | [docs/zh/evidence-schema.md](docs/zh/evidence-schema.md) | [docs/en/evidence-schema.md](docs/en/evidence-schema.md) |
| **审计报告结构规范（4 大核心板块）** | [docs/zh/report-schema.md](docs/zh/report-schema.md) | [docs/en/report-schema.md](docs/en/report-schema.md) |
| **领域技能处方生成策略** | [docs/zh/skill-prescription-policy.md](docs/zh/skill-prescription-policy.md) | [docs/en/skill-prescription-policy.md](docs/en/skill-prescription-policy.md) |

> **关于 Agent 专用文件的说明**：根目录下的 `SKILL.md` 及 `references/` 目录（含 `references/*.md`、`references/software/*.md` 与 `references/error-families.json`）专供 Agent 运行时读取，统一保留纯英文版本以节约上下文开销。人类开发者阅读请查阅上表中的 `docs/zh/` 与 `docs/en/` 双版本目录。

---

## 架构流程

```text
产物与状态采集 (discover_artifacts.py + runtime_state.py + extract_runtime_errors.py)
       ↓
客观证据数据 (evidence.json)
       ↓
竞争假设生成 (generate_hypotheses.py -> candidate-hypotheses.json)
       ↓
因果归因引擎 / Agent 裁决协议 (analyze_case.py + attribution-protocol.md -> analysis.json)
       ↓
Schema 与硬规则校验器 (validate_analysis.py)
       ↓
中英双语审计报告渲染 (render_report.py -> report.md, report.zh.md, report.en.md, skill-prescription.md)
```

### 仓库结构

git 跟踪的仓库结构及各目录一句话简注如下：

```text
case-failure-analyzer/
├── .gitignore                   # 本地产物与构建缓存的排除规则
├── SKILL.md                     # Skill 入口与运行时契约（面向 Agent，仅英文）
├── README.md / README.zh.md     # 项目概览与命令行用法（英文 | 简体中文双语）
├── Makefile                     # 工程化入口：test、lint、smoke、check-docs、export-skill、clean
├── pyproject.toml               # 包元数据与可选 [dev] 依赖
├── agents/
│   └── openai.yaml              # codex 运行时接口元数据
├── docs/
│   ├── en/                      # references/ 的人类可读英文镜像（由 make check-docs 守护）
│   └── zh/                      # references/ 的人类可读中文镜像
├── references/                  # Agent 读取文档的唯一真相源
│   ├── *.md                     # taxonomy、归因协议、evidence/report schema、处方策略
│   ├── error-families.json      # 科学计算与 MLIP 套件统一 error-family 注册表
│   └── software/                # 11 个分软件知识库（cp2k、vasp-abacus、lammps、mlip 等）
├── scripts/                     # 确定性证据流水线与报告渲染
│   ├── analyze_case.py          # CLI 入口：collect -> attribute -> render
│   ├── staging.py               # 暂存区生命周期、建议性文件锁、输入隔离与清单事务发布
│   ├── validate_analysis.py     # Schema 与硬规则校验器
│   ├── render_report.py         # 双语报告与技能处方渲染器
│   ├── *.py                     # 证据模块：产物发现、运行态、轨迹标准化、契约审计、
│   │                            #   科学错误提取、假设生成等
│   └── attribution/             # 因果归因引擎（engine.py、schema.py、context.py 等）
│       └── gates/               # 每个门一个模块，按固定顺序求值（gate0..gate6）
└── tests/                       # unittest 测试套件：python3 -m unittest discover -s tests -t .
    ├── fixtures/                # 已提交 fixture：归因基线与回归作业
    └── test_*.py                # 单元、决策表与特征化测试
```

`.venv/`、`__pycache__/`、`*.egg-info/`、`.ruff_cache/`、`.dev/` 为本地构建/运行/IDE 产物，已被 `.gitignore` 排除；bench 侧生成的 `failure-report/` 看板同样不纳入仓库。实际分发的 skill 只包含 `SKILL.md`、`agents/`、`scripts/` 与 `references/`。

---

## 使用方法

### 1. 统一 CFA 命令行工具与任务号模式（推荐）

针对基准评测用例，CFA 提供统一的命令行入口（`scripts/cfa.py`），支持 `audit`、`inspect` 与 `index` 三大核心子命令：

```bash
# 1. 单任务快速审计（自动解析最新 trial 并直接输出结论卡）
python3 scripts/cfa.py audit task208 --jobs-root <benchmark checkout> --lang zh

# 2. 全量用例自动发现与批量审计
python3 scripts/cfa.py audit --all --jobs-root <benchmark checkout>

# 3. 只读安全检视（自动脱敏家目录路径与敏感凭据）
python3 scripts/cfa.py inspect task208 --section verdict       # 根因速览卡片 (summary.md)
python3 scripts/cfa.py inspect task208 --section errors        # 一目了然错误矩阵与具名失败检查
python3 scripts/cfa.py inspect task208 --section confidence    # 置信度收据与 points/denominator 标尺
python3 scripts/cfa.py inspect task208 --section evidence      # 证据规整化人读摘要
python3 scripts/cfa.py inspect task208 --section errors --raw  # 输出原生无格式 JSON 数据

# 4. 原子加锁重新生成全局看板
python3 scripts/cfa.py index --report-root <benchmark checkout>/failure-report
```

- **一目了然错误矩阵** (`--section errors`)：呈现裁决、主因、验证器可诊断性及具名失败检查明细（层级、失败种类、详细报错、产物比对与复算散度数值）。
- **置信度收据与因子明细** (`--section confidence`)：展示启发式置信度、证据强度、动态分值标尺（`points / denominator`）、带正负符号的证据因子明细、提升置信度所需补充证据及竞争假设得分。
- **规整化证据人读摘要** (`--section evidence`)：提供运行态、失败检查计数、因果锚点、科学观测与行为信号计数的紧凑人读摘要，无需向终端倾倒 200KB+ 的全量 JSON。
- **全局看板 (`INDEX.md`)**：追踪三态评测统计（总用例、通过、已评分失败、未评分或异常、可评分通过率），以未通过用例为分母归一化根因大类占比（总和恒为 100.0%），任务编号自然数序排列（`task98` < `task194`），呈现视觉强度指标（`🟢`、`🟡`、`🔴`、`✅`），并提供速览、报告、数据与技能处方直达链接。

### 2. 全流程归因与中英双版报告生成（路径模式）

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --lang bilingual
```

#### 命令行参数说明

- `--job`：作业目录（`jobs/<job_name>`）或具体 Trial 目录（必选）。
- `--output`：证据、分析与报告的输出目录（必选；严格保障输入只读，禁止与输入路径重叠）。
- `--task`：任务定义目录路径（可选；省略时自动发现）。
- `--trial`：指定 Trial 名称或 `all`（默认：`all`）。
- `--lang`：报告语言版本：`bilingual`（默认，中英双语合订）、`zh`（独立纯中文版）或 `en`（独立纯英文版）。
- `--format`：输出格式：`both`（默认）、`json` 或 `markdown`。
- `--phase`：执行阶段：`all`（默认：全流水线+保守归因+报告渲染）或 `collect`（仅采集客观证据与候选假设）。

在 `<output-dir>/<case_id>/` 目录下生成的输出文件：
- `evidence.json`：单 Trial 客观产物清单、统一运行态 `runtime_state`、标准化轨迹时间线、三方契约审计矩阵及科学软件错误观测。
- `candidate-hypotheses.json`：带启发式证据强度评分的竞争根因假设列表。
- `analysis.json`：因果归因结构化结果（`failure-analysis-v1`）——在 `--phase all` 下默认生成保守的确定性草稿。
- `report.md`：**中英双语合订审计报告**（失败案例包含 4 个核心板块；通过案例精简为 < 30 行轻量视图，彻底剔除失败语义）。
- `report.zh.md`：独立纯中文审计报告（通过案例自动切换为 2 章节轻量视图）。
- `report.en.md`：独立纯英文审计报告（通过案例自动切换为 2 章节轻量视图）。
- `skill-prescription.md`（及 `skill-prescription.zh.md`、`skill-prescription.en.md`）：中英双语领域技能处方（**仅**在判定为可复用的 Agent 能力缺口时生成）。
- `job_summary.md`：多 Trial 批处理 Markdown 看板，汇总全部 Trial 的判定结果、归因类别、根因代码与失败阶段（采用相对标识符，杜绝本机绝对路径泄漏）。
- `job_summary.json`：多 Trial 批处理结构化聚合统计与每 Trial 摘要记录。

### 3. 仅执行证据采集与候选假设生成 (`--phase collect`)

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job jobs/<job_name> \
  --task tasks/<task_name> \
  --output <output-dir>/<case_id> \
  --phase collect
```

### 4. 模型介入裁决结果的校验与渲染

`--phase all` 默认产出保守的确定性归因草稿。如需提交由模型编写或修订的归因结论，请先执行校验再渲染报告：

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
[ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
python3 "$SKILL_DIR/scripts/validate_analysis.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --report  <output-dir>/<case_id>/report.md

python3 "$SKILL_DIR/scripts/render_report.py" \
  --evidence <output-dir>/<case_id>/evidence.json \
  --analysis <output-dir>/<case_id>/analysis.json \
  --output-report <output-dir>/<case_id>/report.md \
  --output-prescription <output-dir>/<case_id>/skill-prescription.md \
  --lang bilingual
```

---

## 退出码说明

| 退出码 | 含义 |
|---|---|
| `0` | 分析成功完成（包括合法的 Job 级启动前基础设施失败）。 |
| `2` | 无效输入：`--job` 或 `--task` 路径不存在、指定的 `--trial` 不存在、空作业目录且无 Job 级证据，或请求了未实现的 `--replay` 模式。 |

入口脚本会在写出任何报告文件**之前**执行快速失败校验，避免错误路径产出看似合理的误导性报告。

---

## 运行测试

运行时仅依赖标准库；测试工具则需要额外安装。`make test` 无需安装任何依赖：

```bash
make test          # 规范命令：python3 -m unittest discover -s tests -t .
make compile       # 对 scripts/ 与 tests/ 做语法检查
make smoke         # 针对已提交的双 trial fixture 做端到端 CLI 运行
make export-skill  # 导出纯净生产技能到 DEST（默认: $CODEX_HOME/skills/case-failure-analyzer）
make dev           # 可选：安装 [dev] 依赖（pytest）
make test-pytest   # 可选：使用 pytest 运行
make help          # 列出全部目标
```

不使用 `make` 时的等价命令：

```bash
python3 -m unittest discover -s tests -t .
python3 -m pytest -q          # 仅在执行 `make dev` 之后
```

---

## 导出生产技能

将生产纯净技能目录（`SKILL.md`、`agents/`、`scripts/`、`references/`）同步导出到目标目录，自动排除单元测试与测试固件（`tests/`）、本地文档镜像（`docs/`）以及开发工程配置（`Makefile`, `pyproject.toml`, `uv.lock`, `.ruff_cache`, `.venv`, `.git`, `.dev/`、旧版 `failure-analysis/` 等）：

```bash
# 导出到默认目录（$CODEX_HOME/skills/case-failure-analyzer 或 ~/.codex/skills/case-failure-analyzer）
make export-skill

# 或导出到自定义目标目录
make export-skill DEST=/path/to/target/case-failure-analyzer
```

导出逻辑由 `scripts/export_skill.py` 驱动，内置目标路径安全防护（严禁导出至根目录 `/`、`$HOME` 或源码仓库根目录），并采用非破坏性受控同步（保留目标目录中用户自有的定制文件，通过 `.cfa_export_manifest.json` 追踪受管产物）。

### 交互式诊断

`analysis.json` 包含一个可选的 `decision_trace`：按序记录被评估的各个归因门（gate）、每个门为何放弃（abstain）以及最终命中的门。无需阅读门链即可回答“为何是该根因而非其他”。`tests/test_attribution_decisions.py` 正是针对它做断言。

`evidence.json` 包含一个可选的 `diagnostics` 列表。该列表非空表示某次可恢复的失败降低了证据质量——例如 `tests/verify.py` 无法解析时，由于无法收集基于 AST 的解析器隐患，验证器会「看起来是干净的」。相同记录会以每行一条 JSON 的形式输出到 stderr（`ANALYSIS DIAGNOSTIC: {...}`）；stdout 与退出码不受影响。

### 架构

因果判定位于 `scripts/attribution/`，每个门（gate）一个函数，由 `engine.py` 按固定顺序执行：

```text
gate0_passed → gate1a_infra_prestartup → gate1b_unknown_no_evidence
             → gate2_case_definition → gate3_verifier_defect
             → gate4_numerical_divergence → gate5_insufficient_positive_evidence
             → gate6_agent_primary
```

每个门要么放弃，要么返回完整的 15 键归因结果，最先命中者生效。门的顺序属于关键契约并由测试锁定；`scripts/attribution/schema.py` 在构造时即校验键契约。

`tests/test_attribution_characterization.py` 会将十种原型 fixture 的完整 `analysis` 输出与已提交的基线快照进行比对，因此任何归因行为的变化都会表现为可复核的 diff，而不会变成一份细微不同的报告。如需有意更新基线，使用 `UPDATE_BASELINE=1` 重新生成。
