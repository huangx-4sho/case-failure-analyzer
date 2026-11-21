# Causal Attribution Protocol

**[English](attribution-protocol.md) | [简体中文](../zh/attribution-protocol.md)** · [Project home](../../README.md) · [Guide](guide.md)

**On this page:** [Backward trace](#1-backward-causal-trace-order) · [Gate order](#2-causal-precedence-timeline--gate-evaluation-order) · [Verifier vs agent](#3-distinguishing-verifier-defects-vs-agent-errors) · [Numerical evidence](#4-numerical-attribution--competing-hypothesis-rules) · [Publication](#5-artifact-ownership--transactional-publication)

Use this trace when the recorded failure needs a deeper causal explanation. A quick audit can report a well-localized rejection or interruption at its recorded evidence level.

## 1. Backward Causal Trace Order

For every failed trial:

1. **Identify the Final Failure Manifestation**
   - Use the per-trial outcome and `score_produced`. Name the terminal runner event, failed check, or both. An exception can coexist with a passing score. Job-level `stats.n_errored_trials` cannot determine a trial's outcome.
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
   - Record `{"status": "identified", "event_ref": "<valid_id>", "summary": "..."}` or `{"status": "not_identified", "event_ref": null, "summary": "..."}` when logs are incomplete. An Agent decision requires an execution event, such as a tool call, file write, or command result. Thoughts and final responses provide context. Every `evidence_refs` entry must resolve in `evidence.json`.

## 2. Causal Precedence Timeline & Gate Evaluation Order

The engine records each rule it evaluates in `decision_trace` and returns the first match. The order gives earlier runtime faults priority when they explain a later rejection.

1. `gate0_review_pending` handles a recorded request for independent review when no final score is available. `gate0_passed` returns `category = "none"` for a passing trial.
2. `gate1a_infra_prestartup` identifies a fatal failure before agent startup. The actual build error can distinguish a missing local tool from a registry access or token request failure. `gate1b_unknown_no_evidence` leaves responsibility open when the startup cause is missing.
3. `gate1c_runner_artifact_collection` checks for collection failures, runner interruptions, agent timeouts and terminal model-service quota errors. A causal timeout can precede a collection failure. If a verifier subsequently ran, its rejection and any score remain visible. A recovered 429 leaves no terminal cutoff to attribute. An independent verifier crash that prevents scoring is reviewed separately.
4. `gate2_case_definition` checks for missing task assets and setup defects. An asset that the image build restores from declared, available shards may be absent as a standalone file in the checkout.
5. `gate3_verifier_defect` checks for an internal verifier crash or a parser defect directly tied to the rejection.
6. `gate4_numerical_divergence` uses corroborated numerical comparisons. A discrepancy tied to submitted data can support an agent candidate; matching ensemble statistics can support a verifier tolerance candidate when the contract permits them.
7. `gate5_insufficient_positive_evidence` returns `unknown` when a trial failed and the available records cannot implicate an agent decision.
8. `gate6_agent_primary` identifies a candidate agent error from the remaining evidence. A named rejection takes priority over a generic early-completion signal. To review `AGENT_SELF_CHECK_GAP`, link the actual self-check and the version it ran to a public criterion and a recorded rejection. [Self-check evidence](evidence-review.md#failure-location-and-self-check-evidence)

<details>
<summary>Gate overview table</summary>

| Precedence Stage | Gate | Scope / Trigger | Required Category | Forbidden Defaults |
| :--- | :--- | :--- | :--- | :--- |
| Pending review | Gate 0 | Recorded request for independent review with no final score | `none` | Preserve `pending` |
| **Stage 0: Passed Trial** | Gate 0 | `verification_status == "passed"` or `reward >= 1.0` | `none` (no failure to attribute) | No failure attribution |
| **Stage 1: Pre-Startup Infra** | Gate 1a | `agent_started == false` with fatal infra error | `infra` | Never `agent` |
| **Stage 1b: Pre-Startup Silent**| Gate 1b | `agent_started == false` with no infra evidence | `unknown` | Never `agent` |
| **Stage 1c: Runner or model service** | Gate 1c | Collection failure, runner interruption, agent timeout or terminal API quota error | `infra` | Check whether the verifier subsequently ran and produced a score |
| **Stage 2: Case Assets/Setup** | Gate 2 | Missing prompt assets or broken container setup | `case` | Never `agent` |
| **Stage 3: Verifier Crash / Defect**| Gate 3 | Internal verifier crash or direct-bound parser defect | `verifier` | Never `agent` or `unknown` (isolated crash) |
| **Stage 4: Numerical evidence** | Gate 4 | Corroborated recompute divergence, or verified ensemble agreement with excessive instantaneous tolerance | `agent` / `verifier` | No attribution from an uncorroborated numerical difference |
| **Stage 5: Insufficient Evidence** | Gate 5 | Failed trial with no positive agent evidence | `unknown` | Never `agent` without positive evidence |
| **Stage 6: Agent Decision** | Gate 6 | Positive agent behavioral/trajectory error | `agent` | Never assign without positive evidence |

</details>

### Necessary Gate Conditions for Blaming the Agent

Use these checks to support an Agent cause during review. Automatic classifications are candidates. Keep the conclusion at that level when a competing explanation remains unresolved.

1. **Task Completeness**: `instruction.md` provided sufficient specification, and required assets exist in the container.
2. **Infrastructure Integrity**: `runtime.agent_started == true`, and no fatal container/network/API/timeout failure blocked normal execution.
3. **Verifier Contract Alignment**: The failing verifier check corresponds to an explicit requirement in `instruction.md` and is neither a causally bound verifier parser defect (`failure_binding == "direct"`) nor a verifier internal crash (`verifier_internal_crash`).
4. **Positive Agent Evidence**: At least one positive agent signal is cited in `evidence_refs` (`behavioral_signals`, `agent_mismatch` contract, agent trajectory decision event, or agent-caused `scientific_observations`). Bare `reward=0` or `FAIL` in `verify.log` is NOT sufficient.
5. **Causal Link**: Fixing the agent's identified decision/action would directly prevent the final failure manifestation.

> **Hard Rule on Pre-Startup Failures (`runtime.agent_started == false`)**:
> - If fatal infrastructure evidence exists (`causal_candidate == true`), use `infra`. Without such evidence, preserve `unknown` (`UNKNOWN_INSUFFICIENT_EVIDENCE`).
> - Whenever `runtime.agent_started == false`, attributing `primary_root_cause.category = "agent"` (`false-agent-blame`) is **strictly forbidden**.

> **Hard Rule on Verifier-Not-Run Trials (`runtime.verifier_started == false` or `runtime.verdict in ("unknown", "not_run")`)**:
> - When the verifier never started, a `sig:premature_completion` trajectory heuristic alone is **not** positive agent evidence (`VERIFIER_NOT_RUN_PREMATURE_BLAME`). Attributing `agent` requires a non-completion observation (unrecovered scientific error, `agent_mismatch` contract, `wrong_output_path`, etc.) cited in `evidence_refs`.
> - Harbor's declared deliverables come from `task.toml` `artifacts`, not a hardcoded `results.json`. A `premature_completion` signal is only valid when a *declared* path is genuinely absent, reconciled against the collected `<trial>/artifacts/app/...` tree.
> - A runner-side collection fault in the post-agent / pre-verifier window keeps `infra` primary. Include a missing deliverable as a contributing factor when the runner actually identifies it. A causal timeout can take precedence over the collection stop. The trial stays unscored when the verifier never ran.
> - A confirmed remote-delivery interruption can support premature termination when a successful final scheduler query shows running jobs, the Agent returned `end_turn` without a trial exception, and the public contract and verifier both identify the same missing file under a collected directory.

## 3. Distinguishing Verifier Defects vs Agent Errors

When `verifier/verify.log` outputs an error or `FAIL: <msg>`:

- **Check 1 — Verifier interruption.**
  Follow the terminal receipt to the earliest demonstrated stop. A verified scoring-environment guard can support an infrastructure candidate. A failure in packaged-input preflight can support a verifier dependency candidate. An isolated internal verifier exception supports `VERIFIER_RECOMPUTE_DEFECT`. When a submission triggers the exception, review the public contract before assigning upstream responsibility. `RewardFileNotFoundError` records the absence of a reward and can follow any of these stops.

- **Check 2 — Causally Bound Parser/Regex Soundness (`failure_binding == "direct"`)**:
  Static parser hazards in `verify.py` (such as Fortran `D+03` exponent omission or namelist `/` truncation) may ONLY be assigned as `primary_root_cause.category = "verifier"` when `failure_binding == "direct"` (i.e., the failing line/field actually failed parsing due to that regex). If `1.23D+03` merely appears as a printed reference number while the agent wrote `9.99E+02`, `failure_binding` is `"none"` and `verifier` must NOT be blamed.

- **Check 3 — Hidden Column/Format Assumptions (`VERIFIER_HIDDEN_CONTRACT`)**:
  If `verify.py` indexes positional `.split()` columns without parsing `Step` header names and `instruction.md` did not specify column order while the agent's `results.json` already passed numerical reference checks, classify as `verifier` (`VERIFIER_HIDDEN_CONTRACT`).

## 4. Numerical Attribution & Competing Hypothesis Rules

- **Closed-Loop Numerical Verification**: Numerical trajectory drift requires a strict three-way closed-loop between `instruction.md`, `tests/verify.py`, and `verifier/verify.log`:
  1. **Prompt Contract Verification**: `instruction.md` must be present and must NOT have mandated pointwise exact trajectory reproduction. If `instruction.md` is missing, the contract is incomplete; abstain conservatively to `unknown`.
  2. **Executable Verifier AST Inspection**: `tests/verify.py` must actually evaluate instantaneous trajectory/coordinates in condition or numerical comparison expressions (e.g. `node.test` in `ast.Assert`, numerical comparison operands in `ast.Compare`, or assertion call values). Keywords appearing only inside comments (`# trajectory_rmsd`), assertion error messages (`node.msg`), or string membership tests (`"..." in log`) do not establish a verifier defect; if executable numerical condition checks are absent, abstain conservatively to `unknown`.
  3. **Runtime Causal Binding**: `verifier/verify.log` must directly bind the failing check to instantaneous trajectory/coordinate RMSD while ensemble averages or conserved quantities agree within tolerance. Non-numerical runtime failures (such as missing output files or KeyErrors) and pytest test function names must never be misbound as numerical tolerance defects.
  4. When all 3 conditions hold, classify as `verifier` (`VERIFIER_TOLERANCE_TOO_STRICT`).
- **Competing Hypotheses**: Every failed/errored case MUST populate `competing_hypotheses` (`candidate-hypotheses.json`) and `first_unrecovered_deviation`.
- **Calibrated Confidence**: Confidence scores (`confidence_kind = "heuristic_evidence_score"`, `evidence_strength = "high" | "medium" | "low"`) are computed from evidence counts, multi-source corroboration, and competing hypothesis separation (`scripts/confidence.py`).

## 5. Artifact Ownership & Transactional Publication

- **Manifest Ownership & Path Isolation**: Artifact ownership is tracked by `.cfa_manifest.json` in `--output`. Manifest paths are strictly validated to reject directory traversal (`..`), absolute paths, and any path escaping the resolved output root.
- **Preservation of User Files**: Without a valid manifest, CFA assumes ownership of zero pre-existing files. User-authored files and directories (e.g. `user_dir/report.md`, `custom_notes.txt`) are never presumed to be CFA artifacts and are strictly preserved across single- and multi-trial layout switches.
- **Atomic File Replacement & Process Locking**: Staged artifacts are published using hidden temporary files in destination directories followed by atomic replacement (`os.replace`), preventing concurrent readers from observing half-written files. Concurrent CLI processes targeting the same output directory are serialized via an advisory process lock, and abandoned temporary files from crashed runs are safely cleaned.
- **Best-Effort Transactional Rollback**: Before modifying or publishing to `--output`, files to be touched are backed up. If an error occurs during publication, best-effort rollback restores backed up items and removes newly created files, catching and logging secondary I/O errors without masking the primary failure.
