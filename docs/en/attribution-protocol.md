# Causal Attribution Protocol

**[English](attribution-protocol.md) | [简体中文](../zh/attribution-protocol.md)**

Every failure analysis MUST follow this 7-step backward causal trace protocol. Do not jump from an error log line directly to a root cause label.

---

## 1. Backward Causal Trace Order

For every failed trial:

1. **Identify the Final Failure Manifestation**
   - What caused the trial to be marked failed (`reward < 1.0` or `exception_info != null`)? Note: If `reward >= 1.0` (`verification_status == "passed"`), the trial passed and must NEVER be polluted by job-level `stats.n_errored_trials`.
2. **Trace Backward to the Immediate Artifact State**
   - Inspect how the missing/invalid file or value was produced and compare the three-way contract: `instruction.md` (Prompt) vs `tests/verify.py` (Verifier) vs Agent's outputs.
3. **Locate the First Error Decision or Abnormal State**
   - Walk backward through normalized trajectory events (`trajectory:step:N`) to find where the state first diverged from a valid solution path.
4. **Assess Recoverability**
   - Distinguish recoverable container-side issues from unrecoverable defects (e.g., container build failure, missing task asset, or causally bound verifier parser/internal crash).
5. **Check Agent Observation**
   - Did the agent observe the error message or anomalous value in a tool output?
6. **Check Agent Recovery Actions**
   - Did the agent diagnose the error and adjust parameters appropriately, or did it repeat the failed action (`repeated_failed_action`) / ignore the error?
7. **Pinpoint the First Unrecovered Deviation (`first_unrecovered_deviation`)**
   - Record `{"status": "identified", "event_ref": "<valid_id>", "summary": "..."}` or `{"status": "not_identified", "event_ref": null, "summary": "..."}` when logs are incomplete.

---

## 2. Causal Precedence Timeline & Gate Evaluation Order

All causal attribution enforces a strict chronological dependency timeline:
$$\text{Infra (pre-startup)} \longrightarrow \text{Case (assets/setup)} \longrightarrow \text{Verifier (crash/parser/tolerance)} \longrightarrow \text{Agent (positive deviation)}$$

To implement this timeline deterministically, the engine evaluates gates in declared order (the gate sequence `gate0`–`gate6`, 9 decision ids):
1. **Gate 0: Passed Short-Circuit (`gate0_passed`)**: If the trial passed verification (`verification_status == "passed"` or `reward >= 1.0`), there is no failure to attribute; the engine short-circuits with `category = "none"` and never pollutes a passing trial with job-level errors.
2. **Gate 1a: Pre-Startup Infrastructure (`gate1a_infra_prestartup`)**: Fatal infra error when `agent_started == false` attributes to `infra`.
3. **Gate 1b: Pre-Startup Silent (`gate1b_unknown_no_evidence`)**: `agent_started == false` with no logs or errors explaining why attributes conservatively to `unknown`.
4. **Gate 1c: Pre-Verification Runner / Execution Infra (`gate1c_runner_artifact_collection`)**: A post-agent, pre-verifier harbor fault — `INFRA_ARTIFACT_COLLECTION_CRASH` (`Engine cp failed`), `INFRA_RUNNER_INTERRUPTED` (log ends at `Collecting main service artifacts`), or a causal `INFRA_AGENT_TIMEOUT` — attributes to `infra`. An upstream agent timeout outranks a downstream collection crash; the agent's missing declared deliverable is kept as an explicit contributing factor. The verifier never started in most of these trials, so there was **no scored failure** and `agent`/`AGENT_PREMATURE_TERMINATION` is forbidden.
5. **Gate 2: Case Assets & Setup Defect (`gate2_case_definition`)**: Missing prompt assets or broken container setup attributes to `case`.
6. **Gate 3: Verifier Defect (`gate3_verifier_defect`)**: Internal verifier crashes (`VERIFIER_RECOMPUTE_DEFECT`) or causally bound parser hazards (`failure_binding == "direct"`) attribute to `verifier`.
7. **Gate 4: Numerical / Tolerance Defect (`gate4_numerical_divergence`)**: Structured numerical divergence with matching ensemble statistics and proven closed loop attributes to `verifier` (`VERIFIER_TOLERANCE_TOO_STRICT`).
8. **Gate 5: Insufficient Positive Evidence (`gate5_insufficient_positive_evidence`)**: If the trial failed but no positive agent evidence implicates Agent behaviour, abstains conservatively to `unknown` and prevents false agent blame (bare `reward == 0` or `FAIL` is not positive evidence). This is the conservative fallback before any Agent attribution.
9. **Gate 6: Agent Primary Decision Error (`gate6_agent_primary`)**: Validates all 5 necessary gate conditions and positive behavioral signals before attributing to `agent`. A named failing verifier check outranks a generic `premature_completion` heuristic (subcase 5e precedes 5d when checks are localized). This gate always matches, so it terminates the chain.

| Precedence Stage | Gate | Scope / Trigger | Required Category | Forbidden Defaults |
| :--- | :--- | :--- | :--- | :--- |
| **Stage 0: Passed Trial** | Gate 0 | `verification_status == "passed"` or `reward >= 1.0` | `none` (no failure to attribute) | No failure attribution |
| **Stage 1: Pre-Startup Infra** | Gate 1a | `agent_started == false` with fatal infra error | `infra` | Never `agent` |
| **Stage 1b: Pre-Startup Silent**| Gate 1b | `agent_started == false` with no infra evidence | `unknown` | Never `agent` |
| **Stage 1c: Pre-Verification Runner** | Gate 1c | Post-agent artifact-collection crash / silent interruption / agent timeout | `infra` | Never `agent`; never a scored `failed` verdict when the verifier did not start |
| **Stage 2: Case Assets/Setup** | Gate 2 | Missing prompt assets or broken container setup | `case` | Never `agent` |
| **Stage 3: Verifier Crash / Defect**| Gate 3 | Internal verifier crash or direct-bound parser defect | `verifier` | Never `agent` or `unknown` (isolated crash) |
| **Stage 4: Numerical Tolerance** | Gate 4 | Instantaneous trajectory drift with matching ensemble | `verifier` (if verified) | Never `agent` (abstain to `unknown` if unproven)|
| **Stage 5: Insufficient Evidence** | Gate 5 | Failed trial with no positive agent evidence | `unknown` | Never `agent` without positive evidence |
| **Stage 6: Agent Decision** | Gate 6 | Positive agent behavioral/trajectory error | `agent` | Never assign without positive evidence |

### Necessary Gate Conditions for Blaming the Agent

You may assign `primary_root_cause.category = "agent"` **ONLY IF ALL FIVE** of the following conditions hold (plus at least one positive agent evidence reference):

1. **Task Completeness**: `instruction.md` provided sufficient specification, and required assets exist in the container.
2. **Infrastructure Integrity**: `runtime.agent_started == true`, and no fatal container/network/API/timeout failure blocked normal execution.
3. **Verifier Contract Alignment**: The failing verifier check corresponds to an explicit requirement in `instruction.md` and is neither a causally bound verifier parser defect (`failure_binding == "direct"`) nor a verifier internal crash (`verifier_internal_crash`).
4. **Positive Agent Evidence**: At least one positive agent signal is cited in `evidence_refs` (`behavioral_signals`, `agent_mismatch` contract, agent trajectory decision event, or agent-caused `scientific_observations`). Bare `reward=0` or `FAIL` in `verify.log` is NOT sufficient.
5. **Causal Link**: Fixing the agent's identified decision/action would directly prevent the final failure manifestation.

> **Hard Rule on Pre-Startup Failures (`runtime.agent_started == false`)**:
> - If `runtime.agent_started == false` AND fatal infrastructure evidence exists (`causal_candidate == true`) -> `primary_root_cause.category` MUST be `infra` (or `case` if the `Dockerfile` itself is broken).
> - If `runtime.agent_started == false` AND no infrastructure or exception logs exist -> `primary_root_cause.category` MUST be `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`).
> - Whenever `runtime.agent_started == false`, attributing `primary_root_cause.category = "agent"` (`false-agent-blame`) is **strictly forbidden**.

> **Hard Rule on Verifier-Not-Run Trials (`runtime.verifier_started == false` or `runtime.verdict in ("unknown", "not_run")`)**:
> - When the verifier never started, a `sig:premature_completion` trajectory heuristic alone is **not** positive agent evidence (`VERIFIER_NOT_RUN_PREMATURE_BLAME`). Attributing `agent` requires a non-completion observation (unrecovered scientific error, `agent_mismatch` contract, `wrong_output_path`, etc.) cited in `evidence_refs`.
> - Harbor's declared deliverables come from `task.toml` `artifacts`, not a hardcoded `results.json`. A `premature_completion` signal is only valid when a *declared* path is genuinely absent, reconciled against the collected `<trial>/artifacts/app/...` tree.
> - A runner-side fault in the post-agent / pre-verifier window (Gate 1c) keeps `infra` primary; the agent's incomplete deliverables are a contributing factor, not the root cause. The trial is **not scored** (`reward=None`) and must not be counted as a scored failure in batch pass rates.

---

## 3. Distinguishing Verifier Defects vs Agent Errors

When `verifier/verify.log` outputs an error or `FAIL: <msg>`:

- **Check 1 — Verifier Internal Crash (`VERIFIER_RECOMPUTE_DEFECT`)**:
  If `verify.py` crashes on its own internal paths/files (e.g., `FileNotFoundError: /tmp/verify_tmp/refs.json`, unhandled `Traceback` in `verify.py` unrelated to required agent outputs) with no earlier infra or case defects, classify strictly as `verifier` (`VERIFIER_RECOMPUTE_DEFECT`), NEVER `agent` or `unknown`.
- **Check 2 — Causally Bound Parser/Regex Soundness (`failure_binding == "direct"`)**:
  Static parser hazards in `verify.py` (such as Fortran `D+03` exponent omission or namelist `/` truncation) may ONLY be assigned as `primary_root_cause.category = "verifier"` when `failure_binding == "direct"` (i.e., the failing line/field actually failed parsing due to that regex). If `1.23D+03` merely appears as a printed reference number while the agent wrote `9.99E+02`, `failure_binding` is `"none"` and `verifier` must NOT be blamed.
- **Check 3 — Hidden Column/Format Assumptions (`VERIFIER_HIDDEN_CONTRACT`)**:
  If `verify.py` indexes positional `.split()` columns without parsing `Step` header names and `instruction.md` did not specify column order while the agent's `results.json` already passed numerical reference checks, classify as `verifier` (`VERIFIER_HIDDEN_CONTRACT`).

---

## 4. Numerical Attribution & Competing Hypothesis Rules

- **Closed-Loop Numerical Verification**: Numerical trajectory drift requires a strict three-way closed-loop between `instruction.md`, `tests/verify.py`, and `verifier/verify.log`:
  1. **Prompt Contract Verification**: `instruction.md` must be present and must NOT have mandated pointwise exact trajectory reproduction. If `instruction.md` is missing, the contract is incomplete; abstain conservatively to `unknown`.
  2. **Executable Verifier AST Inspection**: `tests/verify.py` must actually evaluate instantaneous trajectory/coordinates in condition or numerical comparison expressions (e.g. `node.test` in `ast.Assert`, numerical comparison operands in `ast.Compare`, or assertion call values). Keywords appearing only inside comments (`# trajectory_rmsd`), assertion error messages (`node.msg`), or string membership tests (`"..." in log`) do not establish a verifier defect; if executable numerical condition checks are absent, abstain conservatively to `unknown`.
  3. **Runtime Causal Binding**: `verifier/verify.log` must directly bind the failing check to instantaneous trajectory/coordinate RMSD while ensemble averages or conserved quantities agree within tolerance. Non-numerical runtime failures (such as missing output files or KeyErrors) and pytest test function names must never be misbound as numerical tolerance defects.
  4. When all 3 conditions hold, classify as `verifier` (`VERIFIER_TOLERANCE_TOO_STRICT`).
- **Competing Hypotheses**: Every failed/errored case MUST populate `competing_hypotheses` (`candidate-hypotheses.json`) and `first_unrecovered_deviation`.
- **Calibrated Confidence**: Confidence scores (`confidence_kind = "heuristic_evidence_score"`, `evidence_strength = "high" | "medium" | "low"`) are computed from evidence counts, multi-source corroboration, and competing hypothesis separation (`scripts/confidence.py`).

---

## 5. Artifact Ownership & Transactional Publication

- **Manifest Ownership & Path Isolation**: Artifact ownership is tracked by `.cfa_manifest.json` in `--output`. Manifest paths are strictly validated to reject directory traversal (`..`), absolute paths, and any path escaping the resolved output root.
- **Preservation of User Files**: Without a valid manifest, CFA assumes ownership of zero pre-existing files. User-authored files and directories (e.g. `user_dir/report.md`, `custom_notes.txt`) are never presumed to be CFA artifacts and are strictly preserved across single- and multi-trial layout switches.
- **Atomic File Replacement & Process Locking**: Staged artifacts are published using hidden temporary files in destination directories followed by atomic replacement (`os.replace`), preventing concurrent readers from observing half-written files. Concurrent CLI processes targeting the same output directory are serialized via an advisory process lock, and abandoned temporary files from crashed runs are safely cleaned.
- **Best-Effort Transactional Rollback**: Before modifying or publishing to `--output`, files to be touched are backed up. If an error occurs during publication, best-effort rollback restores backed up items and removes newly created files, catching and logging secondary I/O errors without masking the primary failure.
