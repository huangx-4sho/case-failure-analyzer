# 使用指南

**[English](../en/guide.md) | [简体中文](guide.md)** · [项目首页](../../README.zh.md) · [使用指南](guide.md)

<a id="case-failure-analyzer--系统工作流与归因总规范"></a>

**本页：** [安装与调用](#安装与调用) · [输入参数](#输入参数) · [默认行为与边界](#默认行为与边界) · [分析流程](#分析流程) · [归因约束](#归因约束) · [科学软件错误签名](#科学软件错误签名) · [开发与导出](#开发与导出) · [Harbor 定位与结果复核](#harbor-定位与结果复核)

本指南说明如何安装、调用和复核 `case-failure-analyzer`。智能体按 `SKILL.md` 组织取证与回答；Python 脚本负责提取证据、运行归因规则和生成报告。

## 安装与调用

<a id="安装到-codex"></a>

### 安装到不同客户端

运行脚本需要 Python 3.9+，无需第三方运行时包。技能主体是 `SKILL.md`、`scripts/` 和 `references/`；`agents/openai.yaml` 只提供 Codex 的显示信息，不是其他客户端的运行依赖。

| 客户端 | 常见本地安装目录 | 调用方式 |
| --- | --- | --- |
| Codex | `~/.agents/skills/case-failure-analyzer/` | `$case-failure-analyzer` |
| Claude Code | `~/.claude/skills/case-failure-analyzer/` 或项目 `.claude/skills/` | `/case-failure-analyzer` |
| Cursor | `~/.cursor/skills/case-failure-analyzer/` 或项目 `.cursor/skills/` | 在 `/` 菜单选择技能，或明确说明技能名 |
| GitHub Copilot | 项目 `.github/skills/case-failure-analyzer/`；支持的本地界面也可用 `~/.copilot/skills/` | 按界面的技能入口或自然语言请求调用 |
| 其他能读文件、运行 Python 的 agent | 客户端可访问的任意导出目录 | 明确要求读取该目录的 `SKILL.md` 后执行流程 |

安装位置按各客户端当前文档列出：[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[Cursor](https://cursor.com/docs/skills)、[GitHub Copilot](https://docs.github.com/en/copilot/concepts/customizations/agent-skills)。云端、远程会话和受组织策略管理的界面不一定读取本机用户目录，应使用相应客户端支持的位置。

从本源码仓库根目录导出，例如：

```bash
python3 scripts/export_skill.py --dest "$HOME/.claude/skills/case-failure-analyzer"
```

将 `--dest` 换成所用客户端的目录即可。导出包含 `SKILL.md`、`agents/`、`scripts/`、`references/`，不包含测试、本地报告或文档页面。项目级安装应导出到目标基准仓库，不能导出到本源码仓库内部；导出器会检查路径边界。`make export-skill` 的历史默认值仍是 `$CODEX_HOME/skills/case-failure-analyzer`，建议显式指定目标目录。

### 在聊天中调用

明确说明技能名、任务引用和可访问的基准仓库路径。下面的自然语言请求不依赖某一种 harness；原生技能前缀按上表选择。

```text
使用 case-failure-analyzer 分析 task208，基准仓库是 /path/to/benchmark。
请用中文回答主根因、四类原因的判断依据，并给出完整报告链接。
```

只写任务编号时，默认选择匹配任务中修改时间最新的试次。锁定某次运行时，提供完整试次名：

```text
使用 case-failure-analyzer 分析试次 "task 208__jaekojA"，
基准仓库是 /path/to/benchmark。保留原始输入，输出中文归因报告。
```

也可以直接给作业和题目目录：

```text
使用 case-failure-analyzer 分析试次 /path/to/benchmark/jobs/run-001/task208__abc，
题目目录为 /path/to/benchmark/tasks/task208，
报告写入 /path/to/benchmark/failure-report/run-001/task208。
```

智能体可根据自然语言请求匹配技能；显式点名便于确认使用哪个工作流。批量分析可以写：

```text
使用 case-failure-analyzer 分析 /path/to/benchmark 下的全部任务，
生成中文报告并更新 failure-report/INDEX.md。
```

任务 ID 模式会在聊天中给出归因摘要，并链接到 `failure-report/<job>/<task_id>/<task_id>.md`。各输出文件的用途见[报告规范](report-schema.md)。

总索引在 `failure-report/INDEX.md`，每个作业也有自己的 `failure-report/<job>/INDEX.md`。同一任务出现在多个作业时，可在 `audit` 或 `inspect` 中用 `--job run-001` 指定作业。跨作业比较可用 `cfa.py diff task208 task208 --job-a run-001 --job-b run-002`。

![报告阅读路径：摘要、完整报告与原始证据](../images/cfa-reports.zh.svg)

### 更新与排查

更新源码后，重新执行同一条导出命令即可更新已管理文件。默认导出不删除目标目录中的其他文件；需要清理旧版本的已管理文件时，可以先用 `--clean --dry-run` 查看，再使用 `--clean`，清理范围受导出清单限制。

| 现象 | 检查位置 |
| --- | --- |
| 技能未出现 | 安装目录是否包含 `SKILL.md`、`scripts/` 和 `references/`；客户端是否已发现更新 |
| 同名技能重复 | 是否在用户目录和项目目录同时安装了相同技能 |
| 找不到任务 | 提供绝对基准路径；核对 `jobs/`、任务编号或完整试次名 |
| 能运行脚本但未按技能回答 | 在聊天中明确点名技能，或要求先读取导出目录的 `SKILL.md`，并要求返回归因摘要和报告链接 |
| `diff` 缺少证据 | 先生成审计报告；需要完整试次证据时在审计阶段使用 `--archive-evidence` |

<a id="输入参数-inputs"></a>

## 输入参数

需要或自动发现以下路径：

- `--job`：作业目录（`jobs/<job_name>` 或 `jobs_failed_backup/<job_name>`）或单个 Trial 目录
- `--task`：对应的任务定义目录（`tasks/<task_name>`，可省略并自动解析）

> **输入校验**：`--job` / `--task` 路径必须存在，`--trial` 必须在作业下真实存在，否则入口脚本以退出码 `2` 快速失败，不产出任何报告。仅当路径存在、无 Trial 目录但存在 job 级证据（`result.json` 或 `job.log`）时，才按“启动前 Job 级失败”继续分析。

<a id="默认行为与边界-default-behavior--boundaries"></a>

## 默认行为与边界

- **默认严格只读**：不修改 `tasks/`、`jobs/` 或 `tests/verify.py`。
- **确定性取证 + 模型因果推理**：确定性脚本负责生成客观 `evidence.json` 与候选假设 `candidate-hypotheses.json`；默认 `--phase all` 会输出保守的确定性草稿 `analysis.json`，可由 Agent/模型依据 [attribution-protocol.md](attribution-protocol.md) 覆盖式改写并核验。
- **证据优先归因**：不凭单一关键词直接定性根因；区分**直接失败现象 (`failure_manifestation`)**、**暴露阶段 (`detection_stage`)** 与**根本原因 (`primary_root_cause`)**。
- **指责 Agent 必须具备正面证据**：不采用“排除其他门控后默认归因 Agent”的兜底逻辑。归因 `agent` 必须引用明确的正面证据（如 `behavioral_signals`、`agent_mismatch` 契约违约、或 Agent 操作引发的 `scientific_observations`）。
- 如现有记录无法区分责任，主根因记为 `unknown`（`UNKNOWN_INSUFFICIENT_EVIDENCE`）。评分拒绝和现有证据仍可查阅。
- **报告语言**。使用 `--lang bilingual` 时，路径模式生成 `report.md`、`report.zh.md` 和 `report.en.md`，任务 ID 模式生成 `<task_id>.md` 和 `<task_id>.en.md`。

<a id="核心工作流-workflow"></a>

## 分析流程

### 1. 运行分析

使用统一 CLI（`scripts/cfa.py`）执行任务 ID 审计、只读脱敏检视、试次对比、调查包切片和复核汇总，或对已知路径调用 `scripts/analyze_case.py`（详见 [CLI 命令流程](../../references/cli.md)）：

```bash
SKILL_DIR="/path/to/skills/case-failure-analyzer"
BENCH_ROOT="/path/to/benchmark"
python3 "$SKILL_DIR/scripts/cfa.py" audit task208 --jobs-root "$BENCH_ROOT" --lang zh
python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --jobs-root "$BENCH_ROOT" --section artifacts
python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --jobs-root "$BENCH_ROOT" --section step --step 43
python3 "$SKILL_DIR/scripts/cfa.py" diff task207 task208 --jobs-root "$BENCH_ROOT"
python3 "$SKILL_DIR/scripts/cfa.py" dossier task208 --jobs-root "$BENCH_ROOT" --output /path/to/dossiers/task208
python3 "$SKILL_DIR/scripts/cfa.py" review summary --audit-root "$BENCH_ROOT/failure-report" --review-root /path/to/reviewed --dossier-root /path/to/dossiers --output /path/to/review-summary
# 或按显式目录路径运行：
python3 "$SKILL_DIR/scripts/analyze_case.py" \
  --job "$BENCH_ROOT/jobs/run-001/task208__abc" \
  --task "$BENCH_ROOT/tasks/task208" \
  --output "$BENCH_ROOT/failure-report/run-001/task208"
```

`cfa.py inspect` 支持 `--section verdict|report|errors|confidence|evidence|anchors|artifacts|step|data`（默认自动脱敏，支持 `--raw` 与 `--no-redact`）；`cfa.py diff <target_a> [<target_b>] [--trials <t1> <t2>]` 读取已有审计报告，对比两个任务或同一任务两个试次的裁决、主因、失败检查与因果锚点差异。自动选择的“最近两次”按 `trials/` 归档报告文件的修改时间排序；轻量归档缺少的证据不会自动补齐。

### 2. 确认运行阶段

脚本：`scripts/runtime_state.py` · `scripts/extract_runtime_errors.py` · `scripts/attribution/runtime_triage.py`

对照 `runtime.agent_started`、`runtime.verifier_started`、执行与验证状态和 `error_observations`。模型服务配额或超时可能先中止运行，评分器随后仍会对缺少的交付物打分；`runtime_triage` 会单独整理超时、输出上限或上下文压缩循环下的分诊观测与线索（`configured_budget_vs_workload`、`reasoning_token_runaway`、`context_overflow_by_large_read`），不直接替代上游归因。两项事件和原始分数分别保留。单个试次通过后，作业级错误数不影响它的结论。

### 3. 重建轨迹与因果锚点

脚本：`scripts/normalize_trajectory.py` · `scripts/trace_causal_anchors.py` · `scripts/self_check_execution.py` · `scripts/trajectory_source.py`

将 `ATIF-v1.7`、`.jsonl` 及 OpenAI/Anthropic `tool_use`/`tool_result` 消息流跨步折叠标准化为时间线事件与行为信号（`behavioral_signals`），结合保留中段高显著性错误/指标行的截断策略与两阶段脚本输出目标图（`script_targets`）推断交付物写出路径（仍需核对已采集文件）；`trace_causal_anchors.py` 对关联失败产物的脚本执行/写入事件给予反向溯源加权，并提取关键科学参数的 `parameter_evolution` 演化轨迹。`trajectory_source.py` 从完整写入、无歧义编辑与带引号 heredoc 还原脚本源码；`self_check_execution.py` 将自检命令、脚本版本与结果记录在 `causal_anchors.self_check_executions`。

审查自检时，在 `causal_anchors.self_check_executions` 中找到实际运行的命令、脚本版本和输出。要认定漏检，还需指出该自检漏过了哪条公开要求，以及哪条评分记录确认了后果。[自检证据](evidence-review.md#具体失败位置与自检证据)

### 4. 核对契约与交付物

脚本：`scripts/audit_contract.py` · `scripts/deliverable_audit.py` · `scripts/contract_review.py` · `scripts/verifier_source.py` · `scripts/attribution/failure_location.py`

对照任务要求、验证器规则与智能体交付物，检查 `deliverable_audit`、`contract_observations`、`deliverable_diagnostics`（检测空表、`NaN`/`Inf` 数值与 `checked_keys` 缺失）与 `verifier_observations`（`failure_binding: direct | indirect | none`、`verifier_internal_crash`、`dependency_preflight_failed`），区分智能体契约违约、直接因果绑定的验证器解析缺陷和验证器内部崩溃。`verifier_source.py` 支持多文件 `tests/` 与打包只读的 `tests/refs.json` 验证器，`failure_location.py` 在可唯一匹配时于 `contract_review.failure_locations` 记录对应评分行和提交值。

### 5. 提取科学错误

脚本：`scripts/extract_scientific_errors.py`

基于 `references/error-families.json` 匹配 11 类科学计算与机器学习势套件的 60 类 `error_family`（支持 `.jsonl` 轨迹印证、不设数值偏差上限的具名指标配对，以及 `scale_or_sign_hint` 单位倍率/符号提示与 `stale_deliverable_sync` 待核实的未同步线索），并结合 `references/software/<software>.md` 判别证据要求。提示的触发条件、扫描限额和误读风险见[证据结构](evidence-schema.md#4-附加交叉核对交付物完整性与因果溯源扩展)。

### 6. 定位首次不可恢复偏离

记录 `{"status": "identified", "event_ref": "...", "summary": "..."}`；若因日志缺失无法定位，显式记录 `{"status": "not_identified", "event_ref": null, "summary": "..."}`。

### 7. 比较候选假设

脚本：`scripts/generate_hypotheses.py` · `scripts/confidence.py`

对候选假设（`H1`、`H2`...）列出支持证据 (`evidence_for`)、反对证据 (`evidence_against`)、缺失证据 (`missing_evidence`) 与反事实检验 (`counterfactual_test`)，并计算启发式证据强度评分 (`confidence_kind="heuristic_evidence_score"`，`evidence_strength="high|medium|low"`)。

### 8. 校验与生成报告

脚本：`scripts/validate_analysis.py` · `scripts/render_report.py` · `scripts/dossier.py` · `scripts/review_cause.py` · `scripts/review_summary.py`

校验 `evidence.json`、`analysis.json` 与 Markdown 报告。需要追查具体失败试次时，用 `cfa.py dossier` 提取调查包，按证据填写 `review-plan.json`（可选配[智能体机制标签](../../references/agent-mechanisms.md)），再运行 `cfa.py review apply` 和 `cfa.py review summary` 输出汇总看板。单次试次审计中的 `skill_prescription` 为 `null`。使用 `cfa.py review capability` 对照独立任务的证据，复核建议的指引能否复用。[已有证据审查](evidence-review.md#能力判断)

<a id="归因硬性约束-attribution-hard-rules"></a>

## 归因约束

1. **不凭单一关键词定根因**：日志中出现 `chaotic` 等词汇而无轨迹或系综统计对比数据时，不判定为数值发散容差问题（缺少对比数据时归因 `unknown`）。
2. **区分失败现象、检测阶段与责任根因**。
3. **Agent 未启动规则 (`runtime.agent_started == false`)**：
   - 若 `agent_started == false` 且存在致命基础设施错误证据，归因 `infra`；
   - 若 `agent_started == false` 且没有任何异常或构建日志证据，归因 `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`)；
   - 若 `agent_started == false`，不得归因 `agent`。
4. **归因 `agent` 必须具备正面证据**：仅有 `reward=0` 或 `verify.log` 报错不构成 Agent 正面证据。
5. **静态解析缺陷须完成因果绑定 (`failure_binding == "direct"`)**：若 `verify.log` 仅在参考值中打印 `1.23D+03` 而 Agent 实际输出了错误数值 `9.99E+02`，不得判为 Verifier 正则缺陷；反之，若 Verifier 因自身临时文件缺失（如 `FileNotFoundError: /tmp/verify_tmp/refs.json`）崩溃，归因 `verifier` (`VERIFIER_RECOMPUTE_DEFECT`)。
6. **证据不足或竞争假设无法区分时输出 `unknown`**。
7. **可复用的 Agent 能力改进指引需经过独立的跨任务复核。** [技能处方规则](skill-prescription-policy.md)

## 科学软件错误签名

注册表包含 11 组软件、60 类错误家族。这里列出的是日志匹配规则和判别证据指南，不代表对所列软件进行了完整兼容性验证。

| 套件 | 适用领域与软件 | 错误家族数 | 参考文档 |
| :--- | :--- | :---: | :--- |
| `cp2k` | 混合高斯/平面波 DFT 与从头算分子动力学 (`CP2K`) | 8 | [cp2k.md](../../references/software/cp2k.md) |
| `quantum-espresso` | 平面波 DFT / DFPT (`pw.x`, `bands.x`, `dos.x`, `ph.x`) | 6 | [quantum-espresso.md](../../references/software/quantum-espresso.md) |
| `vasp-abacus` | 固态平面波与局域基 DFT (`VASP`, `ABACUS`, `GPAW`, `Siesta`, `FHI-aims`) | 5 | [vasp-abacus.md](../../references/software/vasp-abacus.md) |
| `orca-gaussian-pyscf` | 分子量子化学 (`ORCA`, `Gaussian`, `PySCF`, `Psi4`, `Q-Chem`, `NWChem`) | 5 | [orca-gaussian-pyscf.md](../../references/software/orca-gaussian-pyscf.md) |
| `lammps` | 经典与反应力场分子动力学 (`LAMMPS`) | 8 | [lammps.md](../../references/software/lammps.md) |
| `gromacs-amber-openmm` | 生物分子动力学 (`GROMACS`, `AMBER`, `OpenMM`, `NAMD`, `CHARMM`) | 4 | [gromacs-amber-openmm.md](../../references/software/gromacs-amber-openmm.md) |
| `mlip` | 机器学习原子间势 (`MACE`, `NequIP`, `Allegro`, `DeePMD-kit`, `CHGNet`, `SevenNet`, `Orb`) | 5 | [mlip.md](../../references/software/mlip.md) |
| `ase` | 原子模拟工作流与计算器 (`ASE`, `pymatgen`) | 5 | [ase.md](../../references/software/ase.md) |
| `xtb` | 半经验量子化学与构象搜索 (`xTB`, `CREST`) | 5 | [xtb.md](../../references/software/xtb.md) |
| `rdkit` | 化学信息学与构象生成 (`RDKit`) | 5 | [rdkit.md](../../references/software/rdkit.md) |
| `generic-scientific` | 数值求解器与偏微分方程 (`SciPy`, `NumPy`, `JAX`, `PyTorch`, `FEniCS`, `OpenFOAM`) | 4 | [generic-scientific.md](../../references/software/generic-scientific.md) |

## Harbor 定位与结果复核

任务号解析也接受 `136-task136`、`136-task136__trial` 这类带数字前缀的名称。需要独立中英文报告时使用 `--lang bilingual`，不要把无语言后缀的主报告误当成英文版。评分契约复核会区分语义误拒、未公开格式与定义歧义；它们不等于评分已被推翻。

缺失产物的分级核验、已有复算记录和结构化因果审查见[已有证据审查](evidence-review.md)。

任务编号模式先匹配常见 Harbor 目录，再在未命中时进行有深度限制、跳过重型目录的搜索。支持平铺的 `jobs/<job>/task208__<trial>` 和按套件组织的 `<suite>/jobs/<job>/task208__<trial>`；题目定义可放在套件根目录或 `tasks/` 下。同号任务优先绑定所选试次所在套件的定义；仍有多个候选时会报错，不按目录字母序猜测。

分析会读取 job/trial 的 `result.json` 与日志、`agent/trajectory.json` 或可识别的 JSONL 轨迹、`verifier/` 判定（包括 CTRF 结果、`score_breakdown.json` 与 MLRS 失败记录）、题目说明和验证脚本（支持多文件 `tests/` 与打包只读的 `tests/refs.json`）。分析会检查 `task.toml` 声明路径在 `artifacts/` 下的采集结果，并对超时或 SDK 上限停机记录 `failure_manifestation.runtime_triage` 分诊线索而不自动上推归责。数字任务编号是本项目的快捷约定；任意名称任务可通过 `--job`、`--task` 和 `--output` 显式指定。

### 支持范围

- 交付物核验读取字符串、字符串数组和带 `source` / `service` 的声明。没有明确服务映射依据时，service 产物保留为无法判定，不会按主容器路径猜测。
- 数值核对主要处理 JSON/CSV/TSV 和可识别日志。文件扫描、字节数和轨迹读取均有限额，不是对任意二进制科学产物的完整验证。
- 分析读取本地已保存记录，不会自动下载 Harbor Hub 作业，也不执行独立科学复算。规则分数表示证据强度，不是根因正确的概率。

### 复核顺序

1. 核对试次与题目路径。默认“最新”取修改时间，必要时使用完整试次名，避免把另一轮运行的产物混入分析。
2. 检查 `diagnostics`、`missing_deliverables` 和具名失败检查，先确认哪些证据缺失或解析失败。`result.json` 是运行器记录，不等同于智能体提交的计算结果。
3. 从报告的证据引用回到原始文件、工具调用和输出，检查单位、字段对应关系以及执行先后顺序；静态提示需要实证支持。
4. 运行结构与约束校验。以下命令针对任务 ID 模式，`REPORT_DIR` 应为实际报告目录：

```bash
python3 "$SKILL_DIR/scripts/validate_analysis.py" \
  --evidence "$REPORT_DIR/data/evidence.json" \
  --analysis "$REPORT_DIR/data/analysis.json" \
  --report "$REPORT_DIR/task208.md"
```

校验会检查字段、证据标识、归因约束和报告章节，但不能证明物理结论或因果判断必然正确。优先检查测评已保存的复算过程、字段和覆盖范围；本 skill 不追加计算。材料不足时保留候选或 `unknown`，并说明需要找回的既有记录。

## 开发与导出

| 文件或目录 | 用途 | 随技能导出 |
| --- | --- | --- |
| `SKILL.md` | 智能体执行流程与回答要求 | 是 |
| `agents/openai.yaml` | 可选的 Codex 显示名称、简介和默认提示词 | 是 |
| `scripts/` | 分析、校验、报告生成与导出脚本 | 是 |
| `references/` | 归因协议、字段定义与软件规则 | 是 |
| `docs/`、`README*` | 面向读者的说明与配图 | 否 |
| `tests/`、`Makefile` | 测试夹具与开发命令 | 否 |
| `pyproject.toml` | Python 版本、开发依赖、Ruff 与 pytest 配置 | 否 |

直接运行分析脚本不需要安装开发依赖。`agents/openai.yaml` 提供技能界面配置；`pyproject.toml` 服务于仓库开发与检查，两者用途不同。

```bash
make compile       # Python 语法检查
make test          # 标准库 unittest
make check-docs    # 英文文档镜像及指南语言链接检查
make smoke         # 内置两试次夹具
make check         # lint、测试、文档与格式检查（需要 ruff）
make export-skill DEST=/path/to/skills/case-failure-analyzer
```
