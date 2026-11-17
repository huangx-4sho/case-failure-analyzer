# case failure analyzer — System Guide & Workflow Specification

**[English](guide.md) | [简体中文](../zh/guide.md)**

Use `case-failure-analyzer` to diagnose why a scientific-computing benchmark case (`jobs/<job_name>` + `tasks/<task_name>`) failed.

It performs an end-to-end, evidence-backed causal audit rather than naive log keyword matching:

**Artifact Collection → Unified Runtime State → Timeline Reconstruction → Contract Audit → Scientific Error Diagnosis → Candidate Hypothesis Competition → Causal Attribution → Bilingual Report & Skill Prescription**

---

## Inputs

Require or automatically discover:
- `--job`: Job directory (`jobs/<job_name>` or `jobs_failed_backup/<job_name>`) or a single trial directory
- `--task`: Corresponding task definition directory (`tasks/<task_name>`, optional if resolvable from job metadata)

> **Input Validation**: `--job` and `--task` paths must exist, and `--trial` must exist under the job directory; otherwise the entrypoint exits immediately with code `2` without writing any report. Only when a job directory exists with no trial subdirectories **and** contains job-level evidence (`result.json` or `job.log`) does it proceed as a pre-startup job-level failure analysis.

---

## Default Behavior & Boundaries

- **Read-only by default**: Never modifies `tasks/`, `jobs/`, or `tests/verify.py`.
- **Deterministic Evidence Extraction + Model Causal Reasoning**: Deterministic scripts extract `evidence.json` and `candidate-hypotheses.json`; `--phase all` emits a conservative deterministic draft `analysis.json` that can be overridden and validated by an Agent/model per [attribution-protocol.md](attribution-protocol.md).
- **Evidence-first attribution**: Never assigns a root cause from a single keyword match. Strictly separates **failure manifestation (symptom)**, **detection stage**, and **primary root cause**.
- **Positive-evidence gate for Agent blame**: Never defaults to `agent` when other gates do not fire. Requires positive agent evidence (`behavioral_signals`, `agent_mismatch` contract violation, or agent-caused `scientific_observations`).
- **Calibrated uncertainty**: Outputs `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`) when evidence cannot distinguish competing hypotheses.
- **Bilingual Report Output**: Produces `report.md` (combined bilingual), `report.zh.md` (standalone Chinese), `report.en.md` (standalone English), plus `skill-prescription.md` when eligible.

---

## Workflow

1. **Run Evidence Collection & Candidate Hypothesis Generation**
   Use the unified CLI (`scripts/cfa.py`) for task-ID auditing, read-only inspection, and cross-case diffing, or invoke `scripts/analyze_case.py` directly for path-based jobs:
   ```bash
   SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/case-failure-analyzer"
   [ -d "$SKILL_DIR" ] || SKILL_DIR="$HOME/.agents/skills/case-failure-analyzer"
   python3 "$SKILL_DIR/scripts/cfa.py" audit task208 --jobs-root <benchmark checkout> --lang en
   python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --section artifacts
   python3 "$SKILL_DIR/scripts/cfa.py" inspect task208 --section step --step 43
   python3 "$SKILL_DIR/scripts/cfa.py" diff task207 task208
   # Or by explicit job/task directory:
   python3 "$SKILL_DIR/scripts/analyze_case.py" \
     --job jobs/<job_name> \
     --task tasks/<task_name> \
     --output failure-analysis/<case_id>
   ```
   `cfa.py inspect` supports `--section verdict|report|errors|confidence|evidence|anchors|artifacts|step|data` (with `--raw` and automatic redaction unless `--no-redact` is passed), and `cfa.py diff <target_a> [<target_b>] [--trials <t1> <t2>]` compares verdicts, root causes, failed checks, and causal anchors across two tasks or two trials of the same task.
2. **Gate on Per-Trial Startup & Infrastructure State (`scripts/runtime_state.py` + `scripts/extract_runtime_errors.py`)**
   Determine from `evidence.json` (`runtime.agent_started`, `runtime.verifier_started`, `runtime.execution_status`, `runtime.verification_status`, `error_observations`) whether the trial passed (`reward >= 1.0`) or whether the container/agent failed prior to startup. Job-level `stats.n_errored_trials` is never used to mark an individual passing trial as failed.
3. **Reconstruct the Unified Execution Timeline & Causal Anchors (`scripts/normalize_trajectory.py` + `scripts/trace_causal_anchors.py`)**
   Trace chronological events across runner setup, agent steps (`ATIF-v1.7`, `.jsonl`, OpenAI/Anthropic `tool_use`/`tool_result` folding), tool calls, file writes, two-phase script output target tracking (`script_targets`), salience-preserving truncation, and verifier execution. `trace_causal_anchors.py` applies backward provenance scoring for scripts producing implicated failed artifacts and extracts `parameter_evolution` across steps.
4. **Audit the Three-Way Contract & Deliverable Integrity (`scripts/audit_contract.py`)**
   Inspect `contract_observations`, `deliverable_diagnostics` (empty tables, `NaN`/`Inf` values, missing `checked_keys` in `artifacts/`), and `verifier_observations` (`failure_binding: direct | indirect | none`, `verifier_internal_crash`) to distinguish genuine agent contract violations from causally bound verifier parser defects or internal verifier crashes.
5. **Extract Scientific Software Error & Cross-Check Events (`scripts/extract_scientific_errors.py`)**
   Review `scientific_observations` backed by `references/error-families.json` (plus `.jsonl` trajectory corroboration, unbounded metric-matched artifact pairing, `scale_or_sign_hint`, and `stale_deliverable_sync`) and consult `references/software/<software>.md` for discriminating evidence requirements.
6. **Identify the First Unrecovered Deviation (`first_unrecovered_deviation`)**
   Record `{"status": "identified", "event_ref": "...", "summary": "..."}` or `{"status": "not_identified", "event_ref": null, "summary": "..."}` when logs are incomplete.
7. **Evaluate Competing Root-Cause Hypotheses (`scripts/generate_hypotheses.py` & `scripts/confidence.py`)**
   Compare structured hypotheses (`H1`, `H2`, ...) with `evidence_for`, `evidence_against`, `missing_evidence`, `counterfactual_test`, and heuristic evidence scores.
8. **Validate & Render Outputs (`scripts/validate_analysis.py` & `scripts/render_report.py`)**
   Validate `evidence.json`, `analysis.json`, and `report.md`, then render `report.md`, `report.zh.md`, `report.en.md`, and optional `skill-prescription.md`.

---

## Attribution Constraints (Hard Rules)

1. **Do not classify solely from an error string or bare keyword.** A `Connection failed`, `SCF NOT CONVERGED`, or bare `chaotic` string is only causal if supported by structured evidence and unrecovered downstream impact.
2. **Distinguish symptom, detection point, and root cause.**
3. **Pre-startup rule (`runtime.agent_started == false`):**
   - If `runtime.agent_started == false` AND fatal infrastructure evidence exists -> `primary_root_cause.category` MUST be `infra`.
   - If `runtime.agent_started == false` AND no infrastructure/exception logs exist -> `primary_root_cause.category` MUST be `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`).
   - If `runtime.agent_started == false` -> `primary_root_cause.category` is **STRICTLY FORBIDDEN** from being `agent`.
4. **Positive evidence is required for `agent` attribution.** `reward == 0` or `FAIL` in `verify.log` alone is NOT positive agent evidence.
5. **A static verifier hazard must be causally bound (`failure_binding == "direct"`).**
6. **Use `unknown` when evidence cannot distinguish competing hypotheses.**
7. **Generate `skill-prescription.md` ONLY for reusable, generalizable agent capability gaps.**
