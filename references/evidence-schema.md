# Evidence & Analysis JSON Schemas (`failure-analysis-v1`)

**On this page:** [Evidence](#1-evidencejson-schema) · [Analysis](#2-analysisjson-schema) · [Harbor](#3-harbor-protocol-extensions-additive-optional-keys) · [Cross-checks](#4-additive-cross-check-deliverable--provenance-extensions) · [Evidence review](#5-existing-evidence-review-fields) · [Scoring review](#6-scoring-contract-and-runtime-review)

This page describes evidence, attribution, and optional extension fields. Expand examples as needed; identifiers, keys, and enum values retain their code spelling.

| Data | Purpose |
| --- | --- |
| `evidence.json` | Runtime state, trajectory, checks, and evidence locations |
| `analysis.json` | Primary cause, hypotheses, actions, and decision records |
| Optional extensions | Harbor collection, numerical comparisons, and provenance hints |

## 1. `evidence.json` Schema

`evidence.json` stores purely objective observations extracted by deterministic scripts (`discover_artifacts.py`, `runtime_state.py`, `normalize_trajectory.py`, `extract_runtime_errors.py`, `audit_contract.py`, `extract_scientific_errors.py`). It must never contain subjective root-cause labels.

<details>
<summary>Expand the evidence.json example</summary>

```json
{
  "case_id": "string (e.g. ccb-cp2k-aimd-water or trial_name)",
  "job_dir": "string",
  "task_dir": "string or null",
  "trial_name": "string",
  "trial_dir": "string or null",
  "artifacts": [
    {
      "artifact_id": "art:trial_result",
      "rel_path": "result.json",
      "scope": "trial|job|task",
      "exists": true,
      "size_bytes": 3869,
      "sha256": "sha256:..."
    }
  ],
  "runtime": {
    "started_at": "ISO-8601 or null",
    "finished_at": "ISO-8601 or null",
    "execution_status": "completed|errored|not_started|unknown",
    "verification_status": "passed|failed|pending|errored|not_run",
    "verdict": "passed|failed|pending|errored|unknown",
    "exit_status": "completed|failed|errored|unknown",
    "reward": 0.0,
    "agent_started": true,
    "verifier_started": true,
    "state_conflicts": [],
    "stages": {
      "environment_setup": {"started_at": "...", "finished_at": "..."},
      "agent_setup": {"started_at": "...", "finished_at": "..."},
      "agent_execution": {"started_at": "...", "finished_at": "..."},
      "verifier": {"started_at": "...", "finished_at": "..."}
    }
  },
  "metadata": {
    "job_stats": {"n_total_trials": 2, "n_errored_trials": 1},
    "job_result_summary": {"n_total_trials": 2, "n_errored_trials": 1},
    "agent_info": "object or null (raw runner agent metadata)",
    "task_toml": "object or null (parsed task.toml)"
  },
  "timeline": [
    {
      "event_id": "trajectory:step:1",
      "timestamp": "2026-09-19T05:45:50.652Z",
      "actor": "user|agent|runner|verifier",
      "event_type": "user_instruction|agent_reasoning|tool_call|tool_output|shell_error|file_read|file_write|repeated_command|final_response|verifier_result|runtime_exception",
      "tool_name": "Bash|Read|Write|Edit|null",
      "command": "string or null",
      "observation": "truncated string or null",
      "observation_sha256": "sha256:... or null",
      "exit_code": 0,
      "source_file": "agent/trajectory.json",
      "source_pointer": "/steps/0"
    }
  ],
  "behavioral_signals": [
    {
      "signal_id": "sig:repeated_fail:2:0",
      "signal_type": "repeated_failed_action|ignored_error|missing_log_inspection|unverified_output|premature_completion|wrong_output_path|asset_modified|dependency_search_attempted|scientific_parameter_changed|fallback_attempted",
      "description": "string",
      "event_ref": "trajectory:step:2:tool:0"
    }
  ],
  "error_observations": [
    {
      "error_id": "err:1",
      "code": "INFRA_CONTAINER_BUILD",
      "subtype": "container_build",
      "stage": "environment_build",
      "matched_text": "string",
      "causal_candidate": true,
      "transient": false,
      "source_file": "result.json",
      "source_pointer": "/exception_info/exception_message"
    }
  ],
  "contract_observations": [
    {
      "contract_id": "contract:1",
      "item": "results.json",
      "prompt_requirement": "required",
      "verifier_requirement": "required",
      "agent_output_status": "present",
      "alignment": "consistent|implicit_consistent|agent_missing_output|agent_schema_mismatch|verifier_hidden_requirement|verifier_schema_mismatch|verifier_defect|case_defect",
      "details": "string",
      "source_refs": ["task:instruction.md", "task:tests/verify.py"]
    }
  ],
  "verifier_observations": [
    {
      "obs_id": "ver:hazard:1",
      "type": "verifier_fail_message|parser_hazard|implicit_column_assumption|verifier_internal_crash",
      "summary": "string",
      "matched_text": "string",
      "hazard_detected": true,
      "failure_binding": "direct|indirect|none",
      "binding_evidence": ["ver:fail_log"],
      "affected_input": "string or null",
      "affected_parser": "string or null",
      "triggered": true,
      "source_file": "tests/verify.py",
      "source_pointer": "AST"
    }
  ],
  "scientific_observations": [
    {
      "sci_id": "sci:1",
      "software": "cp2k|quantum-espresso|lammps|xtb|ase|rdkit|vasp|gaussian|orca|gromacs|pyscf",
      "error_family": "scf_nonconvergence",
      "aliases": [],
      "reference_anchor": "references/software/cp2k.md#scf_nonconvergence",
      "matched_text": "string",
      "source_ref": "trajectory:step:8",
      "candidate_causes": ["bad_initial_guess", "insufficient_scf_iterations"],
      "required_discriminating_evidence": ["SCF iteration history", "MAX_SCF setting"]
    }
  ],
  "diagnostics": [
    {
      "diagnostic_id": "diag:verifier_check_stats:VERIFIER_CHECK_STATS_BUILD_FAILED",
      "schema_version": "diagnostics-v1",
      "stage": "verifier_check_stats",
      "code": "VERIFIER_CHECK_STATS_BUILD_FAILED",
      "severity": "info|warning|error",
      "message": "Verifier check statistics could not be built: ValueError",
      "source_ref": "verifier/verify.log or null",
      "error_type": "ValueError or null",
      "error_message": "string or null",
      "impact": "what the analyzer can no longer conclude because of this recovery",
      "context": {"max_log_bytes": 120000}
    }
  ],
  "verifier_check_stats": {
    "total_checks": 2,
    "passed_checks": 1,
    "failed_checks": 1,
    "failed_check_summaries": ["reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)"],
    "single_failure": true,
    "widespread_failure": false,
    "counterfactual_candidate": true
  },
  "missing_artifacts": [
    "agent/trajectory.json"
  ]
}
```

</details>

**Evidence integrity fields:**

- `trial_dir` / `task_dir` are absolute input paths resolved at collection time; reports render only relative anchors and never expose them.
- `metadata` preserves raw runner metadata (`job_stats`, `agent_info`, parsed `task.toml`) for audit; it is not interpreted as evidence.
- `diagnostics` is a list of structured recovery records (`diagnostics-v1`). `severity="warning"` means evidence was degraded and a conclusion may rest on less than it appears; `severity="error"` means a required input could not be read. Warning/error records surface in `report.md` Section 1 and are itemized in Section 2; summary rows and `job_summary.json` aggregate their counts.
- `verifier_check_stats` is **optional** and present only when a verifier log exists. `total_checks` is a lower bound for fail-fast verifiers, whose passing checks print no marker. Only `single_failure` counts as check localization. `counterfactual_candidate` (exactly one failed check) is a *candidate* only: it cannot distinguish "the only check that failed" from "the only check that ran", so gates must independently bind it to metric-level evidence before awarding counterfactual confidence.

## 2. `analysis.json` Schema

`analysis.json` stores the structured causal attribution and must pass `scripts/validate_analysis.py`.

<details>
<summary>Expand the analysis.json example</summary>

```json
{
  "schema_version": "failure-analysis-v1",
  "case_id": "cp2k-aimd-water",
  "trial_name": "cp2k-aimd-water__kGRrYBg",
  "verdict": "failed|errored|passed",
  "failure_stage": "environment_build|environment_runtime|agent_setup|agent_execution|verifier_execution|none|unknown",
  "detection_stage": "runner|verifier_execution|agent_execution",
  "first_unrecovered_deviation": {
    "status": "identified|not_identified",
    "event_ref": "err:1 or null",
    "timestamp": "2026-09-18T21:45:37.672525",
    "summary": "Docker image pull/build failed during environment setup before agent startup"
  },
  "failure_manifestation": {
    "type": "dependency_install_failure|verifier_check_failed|verifier_internal_crash|...",
    "summary": "Human-readable description of the final failure symptom"
  },
  "primary_root_cause": {
    "category": "infra|case|agent|verifier|unknown|none",
    "subtype": "external_network",
    "code": "INFRA_EXTERNAL_NETWORK",
    "confidence": 0.86,
    "confidence_kind": "heuristic_evidence_score",
    "evidence_strength": "high|medium|low",
    "summary": "Explanation of why this is the singular primary root cause"
  },
  "contributing_factors": [],
  "competing_hypotheses": [
    {
      "hypothesis_id": "H1",
      "category": "infra",
      "subtype": "external_network",
      "claim": "Docker build failed due to network timeout/unreachability before agent started",
      "evidence_for": ["err:1"],
      "evidence_against": [],
      "missing_evidence": [],
      "counterfactual_test": "Pre-pull image or build with working network mirror",
      "confidence": 0.86,
      "confidence_kind": "heuristic_evidence_score",
      "evidence_strength": "high"
    }
  ],
  "evidence_refs": ["err:1", "art:trial_result"],
  "excluded_hypotheses": [
    {
      "hypothesis_id": "H2",
      "category": "agent",
      "reason": "Agent never started (runtime.agent_started == false); missing trajectory is a consequence of pre-startup build failure."
    }
  ],
  "recommended_actions": [
    {
      "owner": "Infra|Case|Verifier|Agent Policy",
      "action": "Concrete remediation step"
    }
  ],
  "skill_prescription": null,
  "decision_trace": {
    "schema_version": "decision-trace-v1",
    "engine": "attribution-engine-v1",
    "gate_order": ["gate0_passed", "gate1a_infra_prestartup", "..."],
    "evaluated": [
      {
        "gate_id": "gate0_passed",
        "matched": false,
        "reason": "trial did not pass verification",
        "checks": {"verification_status": "not_run", "reward": null}
      }
    ],
    "selected": {
      "gate_id": "gate1b_unknown_no_evidence",
      "category": "unknown",
      "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
      "verdict": "errored",
      "failure_stage": "unknown"
    },
    "reconciled_competing_hypotheses": true
  }
}
```

</details>

`decision_trace` is an additive audit key: it records every evaluated gate, why each abstained, and which gate was selected. `checks` holds scalars only (no log text), and nothing time-dependent is recorded, so `analysis.json` stays byte-deterministic. `subcase_id` appears only when a gate selects a documented subcase.

## 3. Harbor Protocol Extensions (additive, optional keys)

Harbor jobs (`jobs/<timestamp>/<trial>/` with `result.json`, `agent/trajectory.json`, `verifier/`, `artifacts/`, and a sibling `task.toml`) produce additional observation keys. All are **optional**: non-harbor inputs keep the legacy shape and the validator does not require them.

<details>
<summary>Expand the Harbor extension example</summary>

```json
{
  "metadata": {
    "task_toml": {"artifacts": ["/app/final", "/app/results.json"]},
    "declared_deliverables": ["/app/final", "/app/results.json"]
  },
  "missing_deliverables": ["/app/final"],
  "verifier_report": {
    "schema_family": "envelope|envelope+layered|checks|criteria",
    "primary_report": "verify.json",
    "source_files": [{"rel_path": "core/verify.json", "role": "nested"}],
    "verdict": "fail",
    "scorable": true,
    "reward": 0.0,
    "status": "SCORED",
    "scientific_status": "FAIL",
    "failure_origin": {"role": "candidate", "reason_code": "SCIENTIFIC_CRITERIA_FAILED"},
    "core_failures": ["coordination_distances_match_derived_reference"],
    "messages": [],
    "checks": [{"layer": "L3", "name": "...", "ok": false, "detail": "...", "core": false, "source": "core/verify.json"}],
    "failed_checks": [{"layer": "L3", "name": "...", "ok": false, "detail": "...", "core": false, "source": "core/verify.json"}],
    "failed_check_summaries": ["[L3] ..."],
    "diagnosability": "normal|low",
    "diagnosability_reasons": [],
    "summary": "[L3] ..."
  }
}
```

</details>

- **`metadata.task_toml.artifacts`** is the authoritative declared-deliverable set. `metadata.declared_deliverables` mirrors it as a flat list. `normalize_trajectory(..., expected_deliverables=[...])` uses it to decide whether `sig:premature_completion` is real: the signal fires only when a *declared* path is absent from the trajectory, never because a hardcoded `results.json` was not written. When `task.toml` is unavailable the list is empty and the legacy `results.json` heuristic applies.
- **`missing_deliverables`** is the reconciled list of declared paths that were never produced. Trajectory matching is heuristic, so `collect_case_evidence.py` cross-checks `<trial>/artifacts/app/<declared path>` (harbor's `docker cp` output, which cannot be imagined) and drops any path that was actually collected. A `premature_completion` signal carries `declared_deliverables` / `missing_deliverables` arrays; when all declared paths were collected, the signal is removed entirely.
- **`verifier_report`** normalizes the five harbor verifier JSON families (layered `core/verify.json`, `scientific/verify.json` with `ACADEMIC_INTEGRITY_VETO`, `raw-verifier.json` `criteria[]`, `science.json` `checks[]`, and `verify.json` envelopes with `failure_origin`) into one shape: named `failed_checks` with `layer`, `name`, `detail`, `core`, `source`; `core_failures`; `failure_origin`; `scorable`; `reward`; and a `diagnosability` flag. `diagnosability == "low"` means the verifier emitted only a terse generic message with no named check or numeric deviation — the report surfaces this explicitly instead of pretending the evidence is complete. When a report is present, `build_check_stats_from_report()` also feeds `verifier_check_stats` so check localization and confidence scoring work on harbor reports too.
- **`error_observations`** may carry harbor runner-stage entries with `stage: "runner"`, `runner_phase: "post_agent_execution"`, `scoring_reached`, and `missing_deliverable`. Codes: `INFRA_ARTIFACT_COLLECTION_CRASH` (`Engine cp failed ... Could not find the file /app/...`), `INFRA_RUNNER_INTERRUPTED` (log ends at `Collecting main service artifacts`), and causal `INFRA_AGENT_TIMEOUT`. These are temporally *after* agent execution but *before* verification, so they must never be staged as `environment_build` / `container_build`. A benign `Cannot estimate Claude cost ... litellm.BadRequestError: LLM Provider NOT provided` line is telemetry present in successful trials and is deliberately not an error rule.

## 4. Additive Cross-Check, Deliverable & Provenance Extensions

All keys below are additive and optional in `evidence.json` / `summary.json`:

The following is a partial field illustration, not a captured run.

<details>
<summary>Expand the cross-check and provenance example</summary>

```json
{
  "deliverable_diagnostics": {
    "scanned_files": ["artifacts/app/results.json", "artifacts/app/summary.csv"],
    "empty_deliverables": [],
    "nan_or_inf_values": [
      {"file": "artifacts/app/summary.csv", "key": "force_max", "value": "NaN", "row": 3}
    ],
    "missing_checked_keys": ["converged_energy"]
  },
  "scientific_observations": [
    {
      "sci_id": "sci:recompute_div:1",
      "error_family": "verifier_recompute_divergence",
      "metric": "total_energy",
      "agent_reported_value": -15.42,
      "recomputed_value": -419.60,
      "scale_or_sign_hint": {
        "kind": "unit_conversion",
        "factor": 0.036749,
        "label": "Hartree/eV (÷27.211386)",
        "unit_token_corroborated": false
      },
      "stale_deliverable_sync": true,
      "recomputed_match_in_trajectory_ref": "trajectory:step:18"
    }
  ],
  "causal_anchors": {
    "algorithm_or_param_definition": {
      "event_id": "trajectory:step:9",
      "parameter_evolution": [
        {
          "param": "ecutwfc",
          "file": "/app/code/solve.py",
          "initial": "30",
          "final": "80",
          "from_event": "trajectory:step:4",
          "to_event": "trajectory:step:9"
        }
      ]
    }
  }
}
```

</details>

- **Multi-format trajectories**: `resolve_trajectory_path` and `_convert_messages_to_steps` parse recognized ATIF, OpenAI, and Anthropic JSON/JSONL structures, linking calls and results by tool-call ID. `truncate_with_head_tail` can retain up to 6 diagnostic/metric lines from the middle. `script_targets` statically extracts output paths and adds them to `written_files` when a matching shell invocation has no detected error; it does not prove that the files were created.
- **Named-metric pairing**: `pair_verifier_evidence_with_artifacts` uses exact, normalized, or substring field-name matches without a numerical-deviation ceiling for named candidates. Unnamed values still require relative deviation below `0.05` or absolute deviation below `0.05`. Similar field names still require semantic review.
- **`scale_or_sign_hint`**: Matches ratios of two finite, nonzero numbers to preset unit factors (`27.211386` Hartree/eV, `627.5095` Hartree/kcal/mol, `23.06055` eV/kcal/mol, `96.4853` eV/kJ/mol, `4.184` kcal/kJ, `0.529177` Bohr/Angstrom) or powers of ten (`10`, `100`, `1000`) within 1.5% relative error; sign flips allow 2% magnitude error. `unit_token_corroborated` indicates whether an expected unit token appeared in `metric` or the verifier text. `factor` expresses the delivered value relative to the recomputed value, not a correction to apply directly to the delivered value. Its output remains a lead to verify.
- **`stale_deliverable_sync` & `recomputed_match_in_trajectory_ref`**: For non-delta divergences, triggers only when `trial_dir` has an on-disk delivered artifact matching `metric` and `reported_value` (without already matching `recomputed_value`), and an error-free execution `observation` (excluding `tool_calls` input code) contains `metric` plus a near-recomputed numeric token (>=4 significant digits within `max(1e-9, abs(recomputed_value) * 5e-4)`). It still does not prove timestamp ordering between calculation and file write and remains a lead to verify.
- **`deliverable_diagnostics`**: Scans JSON/CSV/TSV under `artifacts/`, returning `scanned_files`, `empty_deliverables`, `nan_or_inf_values`, `results_json_files`, `results_json_keys`, and `missing_checked_keys`. Limits are 200 files, 8,000,000 bytes per file, 200,000 rows per table, and 20,000 nodes per JSON file. Missing-key checks use the union of keys in discovered `results.json` files; no such file means no missing-key entries. Unparseable or oversized content may be skipped, so empty diagnostics do not establish completeness. Related `contract_observations` also require corresponding failure text in `verify.log`.
- **`parameter_evolution`**: Scoped per target script file (`file`), tracking recognized scientific parameter assignments (`cutoff`, `eta`, `alpha`, `tol`, `tolerance`, `ecutwfc`, `ecutrho`, `dt`, `timestep`, `kpoints`, `conv_thr`, `mixing_beta`, `nsteps`) across writes, edits, heredocs, and commands referencing that script, recording `initial`, `final`, `from_event`, and `to_event` when the first and last values differ, and filtering to `algo_anchor.target_path` on `causal_anchors.algorithm_or_param_definition`. It does not retain all intermediate changes or prove effective runtime values. Candidates associated with implicated artifacts separately receive a `+15` generation-ranking bonus; both features are localization aids.

## 5. Existing-evidence review fields

| Field | Meaning |
| --- | --- |
| `evidence.deliverable_audit` | Declarations, mappings, capture states, scan coverage, and explicit saved missing-output statements; no inference of non-production |
| `evidence.trajectory_normalization` | Format, coverage, diagnostics, and source-pointer space |
| `analysis.causal_assessment.level` | `not_applicable`, `observed_failure`, `candidate_cause`, or `evidence_supported_cause` |
| `contract_review.failure_locations` | Recorded failed checks or log excerpts, their source locations, and a matching scorer condition or submitted field when it can be identified uniquely |
| `causal_assessment.failure_detail` | The runtime interruption and scoring rejection selected for the report. Either or both can be present |
| `causal_anchors.self_check_executions` | Actual agent self-check commands, their script sources and observed results. A partial result can include both passing and failing subtests |
| `causal_assessment.capability.diagnosis` | The dimension, decision evidence and support level for a failed trial attributed to the agent |
| `causal_assessment.existing_verification` | Saved checks/numerical records, declared coverage and limits; `new_calculation_performed` is false |
| `causal_assessment.decision` | Specific decision, execution, effective inputs/outputs, applicable basis, terminal observation, and reasoning |
| `causal_assessment.alternatives` | Status, rationale, and evidence for alternatives; unresolved items remain explicit |
| `causal_assessment.capability` | This-trial suspected guidance; not independent confirmation of model incapacity |

Review records bind input JSON and saved-source digests; see [existing-evidence review](evidence-review.md). These fields record evidence and reviewer judgments, not a guarantee of scientific correctness.

## 6. Scoring contract and runtime review

| Field | Purpose |
| --- | --- |
| `runtime.score_produced` | Whether a recognizable score was actually produced; verifier startup is insufficient |
| `runtime.reward_metrics` | Original named numeric metrics, such as `submission_checks`; not relabeled as scientific accuracy |
| `contract_review.review_items` | `semantic_rejection`, `hidden_format_requirement`, and `definition_ambiguity`, with public requirements, submissions, failed rules, and coverage limits |
| `contract_review.execution_scope_findings` | Differences between checking original capture and isolated replay outputs |
| `contract_review.requirement_coverage_findings` | Direct label keys, nested occurrences, and quoted verification status; avoids inferring absent coverage from shallow structure alone |
| `scientific_contract_review.findings` | Candidate scientific-definition differences bound to saved source, public requirements, execution, and failed checks |

A started verifier that errors without producing a score has `verification_status="errored"`. A runtime exception followed by a zero score retains both facts without counting the trial twice. Contract review never changes an original failure to a pass; checks after the first rejection may not have executed.
