# Report schema (`report.md`)

**[English](report-schema.md) | [简体中文](../zh/report-schema.md)** · [Project home](../../README.md) · [Guide](guide.md)

**On this page:** [Failure report](#required-sections--visual-structure) · [Passed trials](#lightweight-view-for-passed-trials) · [Batch summary](#batch-summary-job_summarymd--job_summaryjson) · [Task-ID reports](#task-id-quick-answer---task-id-task208)

Reports first show `causal_assessment.level`: observed failure, candidate cause, or this-trial cause supported by an existing-evidence review. A numerical discrepancy, absence of other rule matches, or a heuristic score is not causal confirmation. Deliverable tables distinguish captured presence, absence from capture, collection faults, and unresolved states. Saved verifier records and coverage limits remain separate. Single-trial reports do not confirm capability prescriptions; see [existing-evidence review](evidence-review.md).

Failure reports use four core sections in the order below. Each report heading starts at the beginning of a line as `## <N>. <Title>` and has a non-empty body, so reviewers and `scripts/validate_analysis.py` can check it. Passed trials use the separate two-section view described below.

`report.md` is the primary edition selected by `--lang`, not the English edition. In path mode, `--lang bilingual` produces separate `report.zh.md` and `report.en.md`; with `--lang zh`, `report.zh.md` is a Chinese alias. Task-ID mode uses `<task_id>.en.md` for English. Generated labels and narrative follow the selected language; untranslated source text is explicitly quoted. Reviewed prose may provide fields such as `summary_en` and `summary_zh`.

Scoring coverage counts produced scores rather than verifier launches. Reports retain original metric names and runtime exceptions, and separately display scoring-contract review, replay scope, and scientific-definition findings in the evidence section.

## Required Sections & Visual Structure

Present the verdict, primary category → subtype (`CODE`), stages, and confidence in a compact Markdown property table. Follow it with the failure manifestation and attribution mechanism. These are different claims: one describes where verification failed; the other explains the proposed cause.

Use relative evidence paths and natural-language evidence summaries instead of raw Python list representations. If all logs are missing, summarize that once. Show core missing inputs and computational outputs, suppressing incidental temporary files. Omit normal `agent_started=True, verifier_started=True` flags; show a gate warning only for startup failures or abnormal container interruptions.

Scoring details are expandable; the main table retains the score, method, and evidence strength. Exact repetitions of the primary explanation are omitted from deviation and hypothesis entries, while event IDs, supporting evidence, and distinct claims remain. JSON links follow the actual output layout. Missing files and runner result metadata must not be described as submitted computational outputs.

Evidence degradation must remain visible: when `diagnostics` includes `warning` or `error`, Section 1 warns about the affected conclusions and Section 2 lists the diagnostics.

Path-mode report editions, selected by the requested language and format:

| File | Edition |
| --- | --- |
| `report.md` | Compact Chinese-English report with parallel labels, without a duplicated English Part II |
| `report.zh.md` | Standalone Chinese report |
| `report.en.md` | Standalone English report |

<a id="1--1-归因结论-or--1-root-cause-verdict"></a>

### 1. Root cause verdict

Report heading: `## 1. 归因结论` or `## 1. Root Cause Verdict`.

| Item | Required content |
| --- | --- |
| Verdict | `failed`, `passed`, or `errored`, including execution status, verification status, and reward |
| Primary cause | Category → subtype (`CODE`) |
| Stages | Failure stage → detection stage |
| Confidence | Score, assessment method, and evidence strength |
| Gate warning | Only for startup failure or abnormal container interruption |
| Evidence integrity warning | Only for `warning` / `error` diagnostics; state the impact and refer to Section 2 |
| Failure manifestation | Named verifier failure or numerical deviation |
| Attribution | The causal mechanism at category/subtype level: agent capability gap, ambiguous contract, scoring defect, or environment fault |

<a id="2--2-失败证据链-or--2-failure-evidence-chain"></a>

### 2. Failure evidence chain

Report heading: `## 2. 失败证据链` or `## 2. Failure Evidence Chain`.

- Summarize the most relevant 1–3 verifier error lines.
- Record the event reference, timestamp, and action for `first_unrecovered_deviation`, along with the core `evidence_refs`.
- When degradation diagnostics exist, list `code`, `stage`, `severity`, `message`, `impact`, and `source_ref`. These records also appear in `evidence.json` and are counted in `job_summary.json`.
- Identify `case`, `trial`, and `task` relatively, avoiding long machine-specific absolute paths. Index core evidence: `agent/trajectory.json`, `verifier/verify.log`, `instruction.md`, and `results.json`. Summarize all-missing logs on one line.
- Link the complete evidence and analysis in `evidence.json` and `analysis.json`.

<a id="3--3-假设检验与排除-or--3-hypothesis-audit"></a>

### 3. Hypothesis audit

Report heading: `## 3. 假设检验与排除` or `## 3. Hypothesis Audit`.

Present supporting evidence for candidate hypotheses and reasons for excluding alternatives in natural language, without Python list syntax. Include contributing factors and behavioral-signal counts; refer to `evidence.json` for individual records.

<a id="4--4-改进建议-or--4-recommended-actions"></a>

### 4. Recommended actions

Report heading: `## 4. 改进建议` or `## 4. Recommended Actions`.

Give actions for the responsible category: case, infrastructure, verifier, or agent. Include domain skill prescriptions for eligible agent capability gaps and list core missing inputs and computational outputs, omitting incidental temporary files.

<a id="lightweight-view-for-passed-trials-通过案例精简版"></a>

## Lightweight View for Passed Trials

When `verdict == "passed"`, use a lightweight view with a budget of fewer than 30 lines. Keep the pass verdict and verification evidence; omit failure causes, competing failure hypotheses, missing-artifact lists, and remediation actions.

<a id="1--1-通过结论-or--1-verdict--pass-summary"></a>

### 1. Verdict and pass summary

Report heading: `## 1. 通过结论` or `## 1. Verdict & Pass Summary`.

Show `passed` and reward, execution/verification stages, and rule-based confidence `1.00`. With no degradation, state that verification passed and no unrecovered anomaly or contract violation was detected. With `warning` / `error` diagnostics, state that verification passed but evidence collection degraded, add the integrity warning, and list details in Section 2; do not claim there were no deviations.

<a id="2--2-验证证据-or--2-verification-evidence"></a>

### 2. Verification evidence

Report heading: `## 2. 验证证据` or `## 2. Verification Evidence`.

Show relative `task`, `trial`, and `case` identifiers, with no machine-specific absolute paths. List only verification artifacts that exist, omitting missing-failure-artifact lists. Link `evidence.json` and `analysis.json`, or explain their absence when `--format markdown` was requested.

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

## Task-ID Quick Answer (`--task-id task208`)

Invoking the skill with a task reference (e.g. `$case-failure-analyzer task208`) uses task-ID mode. `scripts/task_ref.py` resolves the reference to the newest matching trial, its job directory, and the task definition directory containing `task.toml`. `--jobs-root` (or `$CFA_JOBS_ROOT`, then the current directory and its parent) bounds the search; numeric matching is exact, so `task208` never resolves to `task2080`.

**Output landing**: reports land in `<bench_root>/failure-report/<task_id>/`, next to the benchmark's case folders. `bench_root` is derived from the resolved trial (`<bench>/<case>/jobs/<job>/<trial>` → `<bench>`; flat `<bench>/jobs/...` → `<bench>`). The bench-level folder holds one global board and one subfolder per analyzed task:

```text
<bench_root>/failure-report/
├── INDEX.md                          # Global board: three-state stats, normalized category distribution, natural-sorted manifest
├── task207/
│   ├── task207.md                    # Main causal audit (clean Markdown)
│   ├── task207.en.md                 # English edition only when --lang bilingual
│   ├── summary.md                    # Verdict card, identical to stdout
│   ├── data/
│   │   ├── evidence.json
│   │   ├── analysis.json
│   │   ├── summary.json
│   │   └── candidate-hypotheses.json
│   ├── trials/<trial_name>/          # Multi-trial archive (lightweight conclusions)
│   └── .cfa_manifest.json
└── task208/
    └── ...
```

The per-task folder sits outside every declared input root (job directory, task definition, trial directory), so the isolation check in `scripts/staging.py` still rejects output equal to / inside / a parent of an input. Each task publishes through its own `.cfa_manifest.json`, so re-analysing one task never touches another task's folder or unmanaged user files. `--report-dir` overrides the exact landing folder, `--no-verdict-stdout` suppresses the printed answer. `cfa index` rebuilds `INDEX.md` atomically.

Machine-readable JSON is sunk into `data/`; the top level keeps only clean Markdown. Hierarchical mode never emits a redundant `.zh.md` copy (a `zh` request produces only `task207.md`), and `cfa inspect` reads `data/summary.json` / `data/evidence.json` / `data/analysis.json`.

| File | Contents |
|---|---|
| `task208.md`, `task208.en.md` | Full causal audit with the 4 mandatory `## <N>.` sections (bilingual mode adds the English edition) |
| `summary.md` | The verdict card, byte-identical to stdout |
| `data/summary.json` | Machine-readable verdict card (`cfa-verdict-card-v1`) |
| `data/evidence.json`, `data/analysis.json`, `data/candidate-hypotheses.json` | Structured evidence / attribution / hypotheses |
| `.cfa_manifest.json` | Publish manifest inside the task folder; scopes cleanup to this task's files |

Path mode (`--job` + `--output`) keeps the legacy unprefixed names (`report.md`, `evidence.json`, ...).

### Global Index Board (`INDEX.md`)

The bench-level global board aggregates root-cause attributions across all analyzed tasks:

- **Three-state Trial Statistics**: Header callout tracks `总用例 / Total`, `通过 / Passed`, `已评分失败 / Failed`, `未评分或异常 / Errored & Unscored`, and **可评分通过率 / Scored Pass Rate** (`passed / (passed + failed)`).
- **Normalized Category Distribution**: Proportions in Section 1 are computed against non-passed cases (`non_passed = sum(cat_counts)`), guaranteeing that the 5 failure categories (`agent`, `verifier`, `case`, `infra`, `unknown`) always sum to `100.0%`.
- **Natural Numeric Ordering**: Case manifests are sorted naturally (`task98` before `task194` before `task207`), avoiding lexicographical misordering.
- **Visual Confidence Indicators**: Quick-glance confidence signals: `🟢` for `>= 0.65`, `🟡` for `0.40..0.64`, `🔴` for `< 0.40`, and `✅ 1.00` for passed trials.
- **Crisp Actionable Links**: Manifest table provides direct navigation links: `[速览]` (`summary.md`), `[报告]` (`{task_id}.md`), `[数据]` (`data/`); historical reports may also link an existing prescription, while new capability guidance is reviewed separately.

### At-a-Glance Error Matrix & Resolved Evidence Inspection

`scripts/cfa.py inspect` provides focused, read-only inspection with automatic credential and user-home redaction:

- **At-a-Glance Error Matrix** (`cfa inspect <task_id> --section errors`): Terminal-friendly Markdown summary of verdict, primary root cause, verifier diagnosability, and every named failed check (`[<layer>] <name>`, `failure_kind`, `detail`, plus artifact comparison or recompute divergence deltas). Add `--raw` for unformatted JSON.
- **Confidence Breakdown Receipt** (`cfa inspect <task_id> --section confidence`): Pretty-prints heuristic confidence, evidence strength, dynamic `points / denominator` scale, signed `+`/`-` evidence factors, and `missing_to_raise` items needed to elevate confidence, alongside competing hypotheses scores. Add `--raw` for structured JSON.
- **Resolved Evidence Summary** (`cfa inspect <task_id> --section evidence`): Concise human-readable digest of runtime execution state, failed checks count, causal anchors, scientific observations, behavioral signals count, and core evidence refs without dumping raw JSON. Add `--raw` to dump the original `evidence.json`.
- **Batch Discovery & Audit** (`cfa audit --all --jobs-root <bench_root>`): Automatically scans the benchmark checkout (supporting both Harbor multi-suite and flat layouts), natural-sorts discovered tasks, audits each newest trial with 1-line progress reporting, and updates `<bench_root>/failure-report/INDEX.md`.

### Verdict Card (`summary.md` / stdout)

The card **is** the direct answer; the CLI prints it to stdout followed by `REPORT_DIR: <path>`, so a caller can answer without opening a file. Its order is load-bearing:

1. **Primary root cause and why it failed** — one line with verdict, primary peer category, `subtype`, `code`, confidence, and the concrete failure reason (named failing check / deviation value). Embedded raw verifier JSON blobs are stripped.
2. **Four peer categories** (`## 1. 四大类裁决`) — `agent`, `verifier`, `case`, `infra`, each with a status (`主因` / `伴随因素` / `已排除` / `未选为主因` / `候选假设`) and an evidence-backed reason. `unknown` is an auxiliary bucket, not a fifth peer category.
3. **Subcategory under the winning category** (`## 2. 主因大类下的小分类`) — `subtype` → `code` → confidence → basis, plus contributing/candidate subcategories, named failing checks, `evidence_refs`, and `recommended_actions`.

When the primary category is `unknown`, the card says plainly that evidence cannot isolate a cause and shows what is missing; it never invents a subcategory. The card is bilingual-labelled and follows `--lang` (Chinese by default, English with `--lang en`).
