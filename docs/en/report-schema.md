# Report Schema (`report.md`)

Every generated `report.md` MUST include the following **4 core sections** in exact order with start-of-line headings (`## <N>. <Title>`) and non-empty body text so human reviewers and automated validators (`scripts/validate_analysis.py`) can audit the report consistently.

Following user and reviewer experience principles, reports adhere to the **Compact Attribute Table & Clean Focal Summary (紧凑属性表与聚焦高亮)** standard:
- **层级显式化**：在归因结论中，**首先以 Markdown 属性表明确评测结论、主因归属**（顶层大类 `agent` / `case` / `infra` / `verifier` ➔ 二级子类 `subtype` (`CODE`)）、阶段与置信度。
- **直击痛点**：紧接着以高亮引用卡片呈现【失败表现 (Failure manifestation)】与【归因判断 (Attribution)】：前者说明评测在哪里失败，后者说明失败由什么机制导致。
- **门控降噪**：隐藏正常启动下的 `agent_started=True, verifier_started=True` 底层门控标记，仅在发生启动前失败或容器异常中断时才在表下附带 Warning 门控警示。
- **路径去噪与分级收敛**：精简证据文件索引，使用统一基准目录相对路径锚点；当日志全部缺失时合并为单行概括；对缺失产物分级过滤，仅展示核心输入与计算产物，收敛内部临时/中间文件。
- **自然文本表述**：消除 Python 列表原生 `repr` 语法泄露，将支持/反驳证据转换为自然文本。
- **诊断透明化**：证据采集发生降级（`diagnostics` 中 `severity` 为 `warning` 或 `error`）时，报告必须在第 1 节给出醒目警示，并在第 2 节逐条列出降级诊断；不得让降级结果看起来与“证据干净”完全一致。

Every invocation of `scripts/render_report.py` / `scripts/analyze_case.py` generates:
- `report.md`: Compact Chinese-English bilingual report with parallel bilingual labels (4 core sections, no duplicated Part II English copy).
- `report.zh.md`: Clean standalone Chinese edition (4 core sections).
- `report.en.md`: Clean standalone English edition (4 core sections).

---

## Required Sections & Visual Structure

### 1. `## 1. 归因结论` (or `## 1. Root Cause Verdict`)
以紧凑 Markdown 属性表（`项目 (Field) | 结论 (Finding)`）配合高亮引用呈现：
- **评测结论 (Verdict)**：`failed` / `passed` / `errored`（包含执行状态、验证状态、reward）
- **主因 (Primary cause)**：【大类】➔ 【二级子类】(`CODE`)
- **阶段 (Stages)**：发生阶段 ➔ 检出阶段
- **置信度 (Confidence)**：数值、判定方式与证据强度
- **运行门控警示 (Gate Warning)**：仅在未正常启动或容器异常中断时附带警示块，正常启动时不展示冗余门控状态。
- **证据完整性警示 (Evidence Integrity Warning)**：仅当 `diagnostics` 中存在 `warning` / `error` 记录时出现，说明证据已降级及受影响结论，并指向第 2 节的明细。
- **高亮结论卡片**：
  - **失败表现 (Failure manifestation)**：验证器具名失败检查或数值偏差等直接失败现象。
  - **归因判断 (Attribution)**：大类 · 子类层面的因果机制（模型盲区、契约歧义、判分缺陷或环境故障）。

### 2. `## 2. 失败证据链` (or `## 2. Failure Evidence Chain`)
- **关键失败报错摘要**：提炼最核心的 1~3 行验证器错误日志。
- **首次不可恢复偏离点 (`first_unrecovered_deviation`)**：记录偏离事件 ref、时间戳与发生动作。
- **核心证据引用代号 (`evidence_refs`)**。
- **证据降级诊断 (Evidence Degradation Diagnostics)**：仅当存在 `warning` / `error` 诊断时出现，逐条列出 `code`、`stage`、`severity`、`message`、`impact` 与 `source_ref`；这些记录同样写入 `evidence.json` 并在 `job_summary.json` 中聚合计数。
- **证据链来源文件索引 (Evidence File Pointers)**：
  - **相对上下文锚点**：统一标明 `case`、`trial` 与 `task` 相对标识，消除长绝对路径折行与泄露噪音。
  - **核心日志与产物**：交互轨迹（`agent/trajectory.json`）、评测日志（`verifier/verify.log`）、任务规范（`instruction.md`）、计算产物（`results.json`）。全缺时单行合并概括。
  - **全量证据矩阵索引**：指引至 `evidence.json` 与 `analysis.json`。

### 3. `## 3. 假设检验与排除` (or `## 3. Hypothesis Audit`)
- **竞争假设仲裁**：美化为自然文本的候选假设支持证据与排除假设反驳理由，杜绝 Python 列表语法泄露。
- **伴随因素与异常信号**：次要伴随影响与行为信号条数统计（详细条目索引至 `evidence.json`）。

### 4. `## 4. 改进建议` (or `## 4. Recommended Actions`)
- **针对性整改行动 (Action Items)**：按责任主体（Case/Infra/Verifier/Agent）给出明确对策。
- **领域 Skill 处方**：针对智能体能力盲区推荐的领域协议技能。
- **核心缺失产物清单**：仅展示关键核心输入与计算产物，收敛内部临时/中间文件。

---

## Lightweight View for Passed Trials (通过案例精简版)

当评测判定为通过 (`verdict == "passed"`) 时，报告自动切换为轻量精简视图（严格预算控制在 30 行以内），仅保留通过裁定与验证证据，彻底移除失败根因、竞争失败假设、缺失产物清单与修复行动等无关噪音：

### 1. `## 1. 通过结论` (or `## 1. Verdict & Pass Summary`)
- **通过属性表**：评测结论（`passed` · reward）、阶段（执行与验证均通过）、置信度（`1.00` · 规则判定）。
- **通过依据**：无降级时标注评测规则全部通过，未检测到未恢复异常或规格违规。若存在 `warning` / `error` 证据降级诊断，则本节改述为“判定通过但证据采集降级”，并在通过依据之后输出证据完整性警示；逐条诊断仍放在第 2 节，不得继续声称“无任何偏离”。

### 2. `## 2. 验证证据` (or `## 2. Verification Evidence`)
- **任务与试次**：`task`, `trial`, `case` 相对标识，不输出任何机器绝对路径。
- **已验证产物记录**：仅展示实际存在的验证产物文件，不列出失败产物缺失项。
- **全量验证矩阵索引**：指引至 `evidence.json` 与 `analysis.json`（若使用 `--format markdown` 则展示对应说明）。

---

## Batch Summary (`job_summary.md` / `job_summary.json`)

Multi-trial jobs additionally emit `job_summary.md` (bilingual), `job_summary.zh.md`, `job_summary.en.md`, and `job_summary.json`.

- **`job_summary.md`**: overview table, root-cause category distribution, and a per-trial diagnostics table. When at least one trial has `warning` / `error` diagnostics, a batch-level evidence degradation notice is emitted above Section 1, and the per-trial table carries a `Degraded Evidence` column showing `⚠️ <warning+error count>` (or `-` for clean rows). A clean batch emits neither the notice nor a misleading zero row.
- **`job_summary.json`**:
  - `job_dir`: batch identifier (reports render only its basename).
  - `batch_statistics`: `total_trials`, `agent_started_count`, `verifier_started_count`, `scored_trial_count`, `not_scored_trial_count`, `scored_pass_rate`, `low_diagnosability_trial_count`, `verdict_counts`, `failure_stage_counts`, `category_counts`, `root_cause_code_counts`, `degraded_evidence_trial_count`, `diagnostic_severity_counts` (`{"warning": N, "error": N}`).
  - `trials[]`: per-trial summary rows with `trial_name`, `verdict`, `agent_started`, `verifier_started`, `failure_stage`, `detection_stage`, `category`, `code`, `confidence`, `diagnostic_counts`, `degraded_evidence`, `verifier_diagnosability` (`"normal"|"low"|null`). Collect-phase rows may also carry `phase: "collect"`.

`degraded_evidence_trial_count` counts trials whose evidence collection emitted at least one `warning` or `error` diagnostic; it is **not** a failure count and must not be folded into `verdict_counts`.

### Three-State Scoring Coverage (`### 1.1`)

`job_summary.md` renders an additive `### 1.1` subsection under Section 1 with four rows: **Scored Passed**, **Scored Failed**, **Not Scored** (runner aborted before the verifier, `reward=None`), and **Scored Pass Rate**. A note distinguishes the overall rate (`passed / total_trials`, retained in the Section 1 table) from the scored rate (`passed / scored_trials`). When `low_diagnosability_trial_count > 0`, a batch-level notice warns that some verifier conclusions are terse generic messages with no named check or numeric deviation. This subsection is additive: the four mandatory `## <N>.` sections of `report.md` and the existing Section 1 table are unchanged.

### Verifier Report Block (single-trial `report.md`)

When `evidence.verifier_report` is present, Section 2 includes the normalized verifier-report block: `schema_family`, `primary_report`, named `failed_checks` with layer/name/detail, `core_failures`, `failure_origin`, and `summary`. If `diagnosability == "low"`, the block explicitly says the verifier message is too terse to localize a failing check and lists `diagnosability_reasons`, instead of silently presenting an empty check list as complete evidence.

---

## Task-ID Quick Answer (`--task-id task208`)

Invoking the skill with a task reference (e.g. `/case failure analyzer task208`) uses task-ID mode. `scripts/task_ref.py` resolves the reference to the newest matching trial, its job directory, and the task definition directory containing `task.toml`. `--jobs-root` (or `$CFA_JOBS_ROOT`, then the current directory and its parent) bounds the search; numeric matching is exact, so `task208` never resolves to `task2080`.

**Output landing**: reports land in `<bench_root>/failure-report/<task_id>/`, next to the benchmark's case folders. `bench_root` is derived from the resolved trial (`<bench>/<case>/jobs/<job>/<trial>` → `<bench>`; flat `<bench>/jobs/...` → `<bench>`), and the bench-level folder contains only one subfolder per analyzed task:

```text
<bench_root>/failure-report/
├── task207/
│   ├── task207.md / task207.zh.md / task207.en.md
│   ├── task207.summary.md / task207.summary.json
│   ├── task207.evidence.json / task207.analysis.json / task207.candidate-hypotheses.json
│   └── .cfa_manifest.json
└── task208/
    └── ...
```

The per-task folder sits outside every declared input root (job directory, task definition, trial directory), so the isolation check in `scripts/staging.py` still rejects output equal to / inside / a parent of an input. Each task publishes through its own `.cfa_manifest.json`, so re-analysing one task never touches another task's folder or unmanaged user files. `--report-dir` overrides the exact landing folder, `--no-verdict-stdout` suppresses the printed answer.

All artifacts are prefixed with the canonical task label:

| File | Contents |
|---|---|
| `task208.md`, `task208.zh.md`, `task208.en.md` | Full bilingual causal audit with the 4 mandatory `## <N>.` sections (unchanged structure) |
| `task208.summary.md` | The verdict card, byte-identical to stdout |
| `task208.summary.json` | Machine-readable verdict card (`cfa-verdict-card-v1`) |
| `task208.evidence.json`, `task208.analysis.json`, `task208.candidate-hypotheses.json` | Structured evidence / attribution |
| `task208.skill-prescription*.md` | Only when an agent capability gap was found |
| `.cfa_manifest.json` | Publish manifest inside the task folder; scopes cleanup to this task's files |

Path mode (`--job` + `--output`) keeps the legacy unprefixed names.

### Verdict Card (`task208.summary.md` / stdout)

The card **is** the direct answer; the CLI prints it to stdout followed by `REPORT_DIR: <path>`, so a caller can answer without opening a file. Its order is load-bearing:

1. **Primary root cause and why it failed** — one line with verdict, primary peer category, `subtype`, `code`, confidence, and the concrete failure reason (named failing check / deviation value). Embedded raw verifier JSON blobs are stripped.
2. **Four peer categories** (`## 1. 四大类裁决`) — `agent`, `verifier`, `case`, `infra`, each with a status (`主因` / `伴随因素` / `已排除` / `未选为主因` / `候选假设`) and an evidence-backed reason. `unknown` is an auxiliary bucket, not a fifth peer category.
3. **Subcategory under the winning category** (`## 2. 主因大类下的小分类`) — `subtype` → `code` → confidence → basis, plus contributing/candidate subcategories, named failing checks, `evidence_refs`, and `recommended_actions`.

When the primary category is `unknown`, the card says plainly that evidence cannot isolate a cause and shows what is missing; it never invents a subcategory. The card is bilingual-labelled and follows `--lang` (Chinese by default, English with `--lang en`).

