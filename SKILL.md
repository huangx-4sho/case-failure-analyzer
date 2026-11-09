---
name: case-failure-analyzer
description: Analyze failed scientific-computing benchmark cases from job artifacts, agent trajectories, task definitions, and verifier code; attribute failures across four peer-level categories (Agent decisions, Verifier rules, Case design, or Infrastructure environment). Output concise bilingual (Chinese & English) causal audit reports with evidence file pointers.
metadata:
  short-description: Audit scientific benchmark case failures across 4 peer categories with concise reports
---

# case failure analyzer

Diagnose why scientific-computing benchmark cases (`jobs/<job_name>` + `tasks/<task_name>`) failed via evidence-backed causal attribution across four peer-level root cause categories:
1. **`agent` (Agent Decisions & Execution)**: Method selection, parameter/cutoff settings, task contract comprehension, workflow orchestration, tool calling, code syntax, or post-processing errors.
2. **`verifier` (Verifier Rules & Scoring)**: Overly strict tolerance, type incompatibility, regex/AST parsing defect, hidden private contract, or verifier recompute crash.
3. **`case` (Benchmark & Task Design)**: Missing/damaged assets, task ambiguity, contradictory reference truth, or unreasonable timeout/compute quota.
4. **`infra` (Runtime & Infrastructure)**: Container/pre-startup failure, missing system dependencies, network timeout, or hardware OOM.

## Task-ID Quick Answer (Recommended)

When invoked with a **task number** — e.g. `/case failure analyzer task208`, `task 220`, or a full trial name `task 208__jaekojA` — do NOT make the user open a file. Run one command and **answer in the conversation with the verdict card it prints to stdout**.

```bash
python3 scripts/cfa.py audit task208 --jobs-root <benchmark checkout> --lang zh
# or equivalently:
python3 scripts/analyze_case.py --task-id task208 --jobs-root <benchmark checkout> --lang zh
```

- `--jobs-root` is optional: it falls back to `$CFA_JOBS_ROOT`, then the current directory, then its parent. Pass it explicitly when the benchmark checkout is elsewhere.
- The resolver finds the newest trial for that task and the task definition folder (`task.toml`). `--report-dir <path>` overrides the output location; `--no-verdict-stdout` suppresses the printed answer.
- **Output landing**: reports land in `<bench_root>/failure-report/<task_id>/`, with an auto-updated global board `<bench_root>/failure-report/INDEX.md`:

  ```text
  failure-report/
  ├── INDEX.md                          # 全局看板（三态评测统计、占未通过比例归一化、自然排序、置信度指标与直达链接）
  ├── task207/
  │   ├── task207.md                    # 主归因报告（干净 Markdown，单语模式无重复 .zh.md）
  │   ├── summary.md                    # 根因速览卡片与关键因果链锚点（与 stdout 完全一致）
  │   ├── skill-prescription.md         # 领域技能处方（仅当检出 Agent 能力盲区时按需生成）
  │   └── data/                         # 机器数据下沉，保持顶层整洁
  │       ├── evidence.json             # 规整化证据、轨迹事件流、验证器日志
  │       ├── analysis.json             # 结构化归因结论与判定门控
  │       ├── summary.json              # 结构化速览卡片元数据
  │       └── candidate-hypotheses.json # 候选假设及证据支持度
  └── task208/
      ├── task208.md
      ├── summary.md
      └── data/
          ├── evidence.json
          ├── analysis.json
          ├── summary.json
          └── candidate-hypotheses.json
  ```

  - **顶层保持 Clean Markdown**：仅保留主报告 `{task_id}.md`、速览卡片 `summary.md`（按需包含 `skill-prescription.md`）；
  - **消除单语重复**：请求 `zh` 仅输出中文主报告，不再产生字节完全相同的 `.zh.md`；
  - **多试次轻量归档**：多试次输出到 `trials/<trial_name>/`，仅存储 `summary.md`、`analysis.json` 与 `summary.json`，避免复制 200KB+ 的全量证据；可通过 `--archive-evidence` 按需开启全量保存；
  - **只读脱敏检视**：使用 `python3 scripts/cfa.py inspect <task_id> [--section verdict|report|errors|confidence|evidence|anchors|data] [--raw]` 安全检视结果，默认自动脱敏家目录路径与敏感凭据：
    - `--section errors`：直观展示裁决、主因、验证器诊断性与具名失败检查明细（层级、偏差、比较与复算散度）；
    - `--section confidence`：展示置信度、证据强度、`points/denominator` 动态分值、正负因子收据与待提升项；
    - `--section evidence`：输出紧凑人读摘要（运行态、检查计数、因果锚点、科学观测与行为信号）；
    - `--raw`：直接输出对应段落的无格式原生 JSON / 文本；
  - **全量用例自动发现与批量审计**：`python3 scripts/cfa.py audit --all --jobs-root <benchmark checkout>` 自动发现全量用例，按自然数序（`task98` < `task194`）依序审计并原子生成 `<bench_root>/failure-report/INDEX.md` 全局看板。

### Required Answer Shape (answer order is load-bearing)

The stdout verdict card is the answer. Relay it directly, in this order:

1. **Most important root cause and why it failed** — one line: verdict, primary peer category, `subtype`, `code`, confidence, and the concrete failure reason (named failing check / deviation value, not a generic message).
2. **Four peer categories** — the `## 1. 四大类裁决` table: `agent` / `verifier` / `case` / `infra` each with its status (`主因` / `伴随因素` / `已排除` / `未选为主因`) and an evidence-backed reason. This establishes that the other three were considered before drilling down.
3. **Subcategory under the winning category** — the `## 2. 主因大类下的小分类` table: `subtype` → `code` → confidence → basis, plus contributing/candidate subcategories, named failing checks, evidence refs, and recommended actions.
4. **Link the report** — e.g. `[task208.md](<REPORT_DIR>/task208.md)` using the `REPORT_DIR:` line printed after the card.

Do not add a fifth category: `unknown` is an auxiliary "insufficient evidence" bucket, not a peer cause. Do not present a subcategory before the four-category table. If the card shows `unknown` as primary, say plainly that the evidence cannot isolate a cause and show what is missing.

### CLI & Input Contract

Entrypoint: `python3 scripts/analyze_case.py`
- Required (choose one mode):
  - **Task-ID mode**: `--task-id task208` (locates job/trial/task automatically).
  - **Path mode**: `--job <path>` (job directory `jobs/<job_name>` or a specific trial directory) **and** `--output <path>` (isolated; inputs are strictly read-only and must not overlap with `--output`).
- Optional:
  - `--task-id <ref>` / `--jobs-root <path>` / `--report-dir <path>` / `--no-verdict-stdout`: see Task-ID Quick Answer above.
  - `--task <path>`: Task definition directory (`tasks/<task_name>`); auto-discovered if omitted.
  - `--trial <name|all>`: Target specific trial name or all trials (default: `all`).
  - `--lang {bilingual,zh,en}`: Report language edition (default: `bilingual`).
  - `--format {both,json,markdown}`: Output format (default: `both`).
  - `--phase {all,collect}`: `all` for full pipeline + attribution + reports (default); `collect` for evidence & candidate hypotheses only.
  - `--replay {none}`: Post-hoc audit mode (default: `none`).
  - `--max-log-bytes <n>`: Maximum bytes per log file read; must be a positive integer (default: `120000`).
  - `--include-session-files`: Include agent session files in inventory.

### Output Standards

- **Single-Trial Artifacts (path mode)**:
  - `evidence.json`: Normalized runtime state, trajectory timeline, contract audit, and scientific error observations.
  - `candidate-hypotheses.json`: Competing causal hypotheses scored by evidence strength.
  - `analysis.json`: Structured attribution (`failure-analysis-v1`) with root cause code, failure stage, and evidence pointers.
  - `report.md` (bilingual), `report.zh.md` (Chinese), `report.en.md` (English): High-signal audit reports with evidence file pointers (lightweight 2-section view < 30 lines for passed trials).
  - `skill-prescription.md`: Bilingual domain skill prescription (generated **only** for reusable Agent capability gaps).
- **Task-ID artifacts**: `<bench_root>/failure-report/<task_id>/` holds clean top-level Markdown (`<task_id>.md`, `summary.md`, optional `skill-prescription.md`) plus all machine JSON under `data/` (see Task-ID Quick Answer). The verdict card printed to stdout is `summary.md`.
- **Multi-Trial Batch Board**:
  - `job_summary.md`: Markdown summary dashboard table showing verdicts, categories, root causes, and failure stages across all trials.
  - `job_summary.json`: Batch aggregated statistics and per-trial summary records.

### Harbor Protocol Specifics

For harbor jobs (`jobs/<timestamp>/<trial>/` + sibling `task.toml`, `artifacts/`, `verifier/`), the analyzer adds:

- **Declared deliverables**: `task.toml` → `artifacts` (e.g. `["/app/final"]`) is the authoritative output contract, not a hardcoded `results.json`. `sig:premature_completion` fires only when a declared path is genuinely missing, reconciled against harbor's collected `<trial>/artifacts/app/<path>` tree. Evidence exposes `metadata.declared_deliverables` and `missing_deliverables`.
- **Not-scored trials**: harbor `docker cp` runs *after* the agent and *before* the verifier. A missing declared path raises `Engine cp failed ... Could not find the file /app/...` and aborts the trial with `reward=None` (`verifier_started == false`). Gate 1c attributes these to `infra` (`INFRA_ARTIFACT_COLLECTION_CRASH`, `INFRA_RUNNER_INTERRUPTED`, or causal `INFRA_AGENT_TIMEOUT` with priority over the downstream collection crash), keeping the agent's incomplete deliverables as a contributing factor — **never** `AGENT_PREMATURE_TERMINATION`.
- **Three-state batch board**: `job_summary.md` reports Scored Passed / Scored Failed / Not Scored plus a **scored pass rate** (`passed / scored`), alongside the overall rate (`passed / total`). Not-scored trials are not agent failures.
- **Verifier report normalization**: the five harbor verifier JSON families (layered `core/verify.json`, `scientific/verify.json`, `raw-verifier.json` `criteria[]`, `science.json` `checks[]`, `verify.json` envelopes with `failure_origin`) are normalized into `evidence.verifier_report` with named `failed_checks`, `core_failures`, `failure_origin`, and `diagnosability`. `diagnosability == "low"` flags a terse generic message with no named check or numeric deviation. Report Section 2 renders this block.

### 3-Step Core Workflow

1. **Run Analysis**: For a task number use the one-line Task-ID command; for an explicit path use path mode:
   ```bash
   python3 scripts/cfa.py audit task208 --jobs-root <benchmark checkout>
   # or via analyze_case:
   python3 scripts/analyze_case.py --task-id task208 --jobs-root <benchmark checkout>
   python3 scripts/analyze_case.py --job jobs/<job_name> --task tasks/<task_name> --output <output-dir>/<case_id>
   ```
2. **Answer / Review Reports**: In Task-ID mode, relay the stdout verdict card as the answer (see Required Answer Shape). Otherwise inspect `report.md` (or `job_summary.md` for batch jobs), then referenced evidence files via file pointers.
3. **Validate & Refine (Optional Model-in-the-Loop)**: When modifying hypotheses or refining attributions, edit `analysis.json` and validate machine-checkable hard rules:
   ```bash
   python3 scripts/validate_analysis.py --evidence <dir>/evidence.json --analysis <dir>/analysis.json [--report <dir>/report.md]
   python3 scripts/render_report.py --evidence <dir>/evidence.json --analysis <dir>/analysis.json --output-report <dir>/report.md --lang bilingual
   ```

### Core Attribution Hard Rules

- **Pre-startup Boundary**: When `runtime.agent_started == false`, attribution MUST be `infra` (if fatal setup error) or `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`). Blaming `agent` is **STRICTLY FORBIDDEN**.
- **Verifier-Not-Run Boundary**: When the verifier never started (`verifier_started == false` / `reward == null`), a `sig:premature_completion` trajectory heuristic alone is NOT positive agent evidence. A post-agent, pre-verifier runner fault (Gate 1c) MUST be `infra` primary with the missing deliverable as a contributing factor; a causal `INFRA_AGENT_TIMEOUT` outranks the downstream collection crash. Never report "verifier ran to completion" when `verifier_started == false`.
- **Deliverable Contract**: On harbor tasks, judge completion against `task.toml` `artifacts`, not `results.json`. A named failing verifier check outranks a generic completion heuristic in Gate 6. A confirmed remote-delivery interruption can still be attributed to premature termination when a successful final scheduler query shows running jobs, the agent returns `end_turn` without a trial exception, and both the public contract and the verifier identify the same missing file under a collected directory.
- **Verifier Crash Priority**: An unhandled verifier crash/exception (`FileNotFoundError`, syntax error, recompute crash) MUST be attributed to `verifier` (`VERIFIER_RECOMPUTE_DEFECT`), never `agent`. This also covers structured `verify.json` reports with `status: ERROR` / `ENV_INVALID`, `scorable: false` and `reward: null` (Harbor then raises `RewardFileNotFoundError` because no `reward.txt` is written); CFA normalizes these into a `ver:internal_crash` observation so Gate 3 fires.
- **Positive Agent Evidence Requirement**: Attributing failure to `agent` requires explicit positive deviation (`behavioral_signals`, contract violation, unrecovered trajectory scientific error). `reward == 0` or verifier `FAIL` alone is insufficient.
- **Closed-Loop Corroboration**: Numerical divergence claims require closed-loop cross-corroboration between prompt requirements, verifier AST rules, and runtime error logs; uncorroborated claims abstain to `unknown`.
- **Delta Divergence Integrity**: A verifier delta (`differs from ... by <delta> beyond bound <tol>`) is a deviation, **not** the agent's delivered value. Bind `agent_reported_value` only from a structured deliverable record (`artifacts/**/*.csv|tsv|json`) whose row carries every identifier named in the verifier message; corroboration/FUD may point only to tool calls, file writes, or command outputs — never `final_response` / `agent_thought`. With no unique delivered value, abstain instead of reusing the verifier's delta.
- **Self-check Coverage Gap**: When the agent's own verification script passes but the verifier names failing checks, emit a `self_check` causal anchor that records `checked_failed_checks` / `uncovered_failed_checks`, and attribute `agent` / `self_check_gap` / `AGENT_SELF_CHECK_GAP` in Gate 6.
- **Causal Anchor FUD**: For Agent attributions, `first_unrecovered_deviation.event_ref` must be an execution anchor (`deliverable_generation` or `algorithm_or_param_definition`), never a final-answer step.
- **Skill Prescription Scope**: `skill_prescription` is **FORBIDDEN** unless `primary_root_cause.category` is `agent`.
- **Evidence Pointer Integrity**: All `evidence_refs` in `analysis.json` must resolve to valid observation IDs in `evidence.json`.

### References (Progressive Disclosure)

Consult when refining complex attributions or checking software-specific error signatures:
- **Protocols & Schemas**: [taxonomy.md](references/taxonomy.md) | [attribution-protocol.md](references/attribution-protocol.md) | [evidence-schema.md](references/evidence-schema.md) | [report-schema.md](references/report-schema.md) | [skill-prescription-policy.md](references/skill-prescription-policy.md) | [error-families.json](references/error-families.json)
- **Scientific Software Knowledge Bases (`references/software/` - 11 suites / 60 error families)**:
  - [cp2k.md](references/software/cp2k.md) (`CP2K` mixed Gaussian/plane-wave DFT & AIMD)
  - [quantum-espresso.md](references/software/quantum-espresso.md) (`Quantum ESPRESSO` plane-wave DFT / DFPT)
  - [vasp-abacus.md](references/software/vasp-abacus.md) (`VASP`, `ABACUS`, `GPAW`, `Siesta`, `FHI-aims` solid-state DFT)
  - [orca-gaussian-pyscf.md](references/software/orca-gaussian-pyscf.md) (`ORCA`, `Gaussian`, `PySCF`, `Psi4`, `Q-Chem`, `NWChem` molecular quantum chemistry)
  - [lammps.md](references/software/lammps.md) (`LAMMPS` classical & reactive MD)
  - [gromacs-amber-openmm.md](references/software/gromacs-amber-openmm.md) (`GROMACS`, `AMBER`, `OpenMM`, `NAMD`, `CHARMM` biomolecular MD)
  - [mlip.md](references/software/mlip.md) (`MACE`, `NequIP`, `Allegro`, `DeePMD-kit`, `CHGNet`, `SevenNet` ML interatomic potentials)
  - [ase.md](references/software/ase.md) (`ASE` / `pymatgen` workflows & calculators)
  - [xtb.md](references/software/xtb.md) (`xTB` / `CREST` semiempirical quantum chemistry)
  - [rdkit.md](references/software/rdkit.md) (`RDKit` cheminformatics & conformer generation)
  - [generic-scientific.md](references/software/generic-scientific.md) (`SciPy`, `NumPy`, `JAX`, `PyTorch`, `FEniCS`, `OpenFOAM` numerics & PDE)
