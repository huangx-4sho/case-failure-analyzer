#!/usr/bin/env python3
"""
Unified Entrypoint, Evidence Collector & Conservative Causal Attribution Engine (`scripts/analyze_case.py`)

Pipeline:
  1. Artifact Discovery (`discover_artifacts.py` + `runtime_state.py`)
  2. Trajectory Normalization (`normalize_trajectory.py`)
  3. Runtime & Infra Error Extraction (`extract_runtime_errors.py`)
  4. Prompt / Task / Verifier Contract Audit (`audit_contract.py`)
  5. Scientific Software Error Extraction (`extract_scientific_errors.py`)
  6. Candidate Hypothesis Generation (`generate_hypotheses.py`)
  7. Conservative Causal Attribution (`analysis.json`)
  8. Schema & Attribution Constraint Validation (`validate_analysis.py`)
  9. Bilingual Report & Skill Prescription Rendering (`render_report.py`)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import staging
from audit_contract import audit_contract
from cli_args import positive_int
from discover_artifacts import discover_all, discover_trials, extract_declared_deliverables
from extract_runtime_errors import extract_runtime_errors, read_text_head_tail
from extract_scientific_errors import extract_scientific_errors
from generate_hypotheses import generate_candidate_hypotheses
from normalize_trajectory import normalize_trajectory
from normalize_verifier_report import build_check_stats_from_report, build_verifier_report
from render_report import (
    render_job_summary_markdown,
    render_report_markdown,
    render_skill_prescription_markdown,
)
from render_verdict import build_verdict_card, render_verdict_card_markdown
from runtime_state import resolve_trajectory_path
from task_ref import TaskRef, TaskRefError, REPORT_DIR_NAME, resolve_task_ref
from trace_causal_anchors import trace_causal_anchors
from staging import (
    acquire_publish_lock,
    cleanup_orphaned_cfa_tmp_files,
    get_publish_lock_path,
    is_pid_alive,
    is_safe_manifest_relpath,
    paths_overlap,
    publish_staging_to_output,
    publish_staging_to_output_locked,
    validate_input_output_isolation,
    validate_output_writable,
)
from validate_analysis import validate_all
from attribution.engine import run_attribution_engine
from diagnostics import WARNING, format_diagnostic_line, make_diagnostic, merge_diagnostics

# Re-exported public constants and helpers from staging
MANIFEST_FILENAME = staging.MANIFEST_FILENAME
acquire_advisory_process_lock = staging.acquire_advisory_process_lock
release_advisory_process_lock = staging.release_advisory_process_lock
clean_tier2_lock_dir = staging.clean_tier2_lock_dir

# Re-exported aliases for backwards compatibility with tests and consumers
_paths_overlap = paths_overlap
_validate_input_output_isolation = validate_input_output_isolation
_validate_output_writable = validate_output_writable
_is_safe_manifest_relpath = is_safe_manifest_relpath
_is_pid_alive = is_pid_alive
_cleanup_orphaned_cfa_tmp_files = cleanup_orphaned_cfa_tmp_files
_get_publish_lock_path = get_publish_lock_path
_acquire_publish_lock = acquire_publish_lock
_publish_staging_to_output = publish_staging_to_output
_publish_staging_to_output_locked = publish_staging_to_output_locked

try:
    from verify_check_stats import build_check_stats
except ImportError:
    # `verify_check_stats.py` is created by the parallel confidence-upgrade
    # workstream; until it lands, the additive `verifier_check_stats` evidence
    # key is simply omitted and every other behavior is unchanged.
    build_check_stats = None  # type: ignore[assignment]

#: Candidate verifier-log paths, mirroring the discovery order in
#: `audit_contract.audit_contract`.
_VERIFIER_LOG_RELPATHS = (
    "verifier/verify.log",
    "verifier/test-stdout.txt",
    "verifier/pytest.log",
    "verifier/verifier.log",
    "verify.log",
)


def _read_verifier_log_text(trial_dir: Optional[Path], max_log_bytes: int) -> str:
    """Read the trial's verifier log with the shared `--max-log-bytes` head/tail
    truncation. Returns "" when no verifier log exists or is non-empty."""
    if trial_dir is None:
        return ""
    for rel_path in _VERIFIER_LOG_RELPATHS:
        text = read_text_head_tail(trial_dir / rel_path, max_log_bytes)
        if text.strip():
            return text
    return ""


def _record_check_stats_failure(
    diagnostics: Optional[List[Dict[str, Any]]],
    *,
    code: str,
    message: str,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a degradation record when check-stat collection cannot complete."""
    if diagnostics is None:
        return
    diagnostics.append(
        make_diagnostic(
            stage="verifier_check_stats",
            code=code,
            severity=WARNING,
            message=message,
            error_type=error_type,
            error_message=error_message,
            impact=(
                "check_localization and counterfactual confidence inputs are "
                "unavailable; attribution confidence falls back to the base "
                "evidence score."
            ),
            context=context,
        )
    )


def _build_verifier_check_stats(
    trial_dir: Optional[Path],
    max_log_bytes: int,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Compute the verifier check statistics stored on evidence as the additive
    `verifier_check_stats` key (consumed by the attribution confidence upgrade
    via `AttributionContext.check_localization`). Returns None when the stats
    builder is unavailable, no verifier log text exists, or the builder fails —
    evidence collection must never crash on an optional additive key.

    Failures are NOT silent: a warning diagnostic is appended so reports and
    batch summaries can show that attribution confidence ran with degraded
    localization evidence. A missing verifier log is normal and adds nothing.
    """
    if build_check_stats is None:
        _record_check_stats_failure(
            diagnostics,
            code="VERIFIER_CHECK_STATS_BUILDER_UNAVAILABLE",
            message="Verifier check statistics builder is unavailable.",
        )
        return None
    verify_log_text = _read_verifier_log_text(trial_dir, max_log_bytes)
    if not verify_log_text.strip():
        return None
    try:
        stats = build_check_stats(verify_log_text)
    except Exception as exc:  # evidence collection must never crash on an optional key
        _record_check_stats_failure(
            diagnostics,
            code="VERIFIER_CHECK_STATS_BUILD_FAILED",
            message="Verifier check statistics could not be built from the verifier log.",
            error_type=type(exc).__name__,
            error_message=str(exc),
            context={"max_log_bytes": max_log_bytes},
        )
        return None
    if not isinstance(stats, dict):
        _record_check_stats_failure(
            diagnostics,
            code="VERIFIER_CHECK_STATS_INVALID_RESULT",
            message=(
                "Verifier check statistics builder returned a non-object result; "
                "localization evidence was discarded."
            ),
            error_type=type(stats).__name__,
            context={"max_log_bytes": max_log_bytes},
        )
        return None
    return stats


class ReplayNotSupportedError(RuntimeError):
    """Raised when `--replay` is requested without a supported isolated container runtime."""


def run_isolated_verifier_replay(
    task_dir: Optional[Path], trial_dir: Optional[Path], timeout_sec: int = 30
) -> Dict[str, Any]:
    raise ReplayNotSupportedError(
        "Verifier/simulation replay is not implemented for offline static task analysis. "
        "Use `--replay none` (default) for read-only forensic analysis."
    )


def _confirm_delivered_on_disk(
    trial_dir: Optional[Path],
    declared_deliverables: List[str],
) -> Set[str]:
    """
    Declared deliverables already confirmed inside the trial's collected
    artifacts (`<trial>/artifacts/app/<declared path>`), i.e. paths harbor's
    post-agent `docker cp` successfully retrieved.

    This is authoritative for "the file existed in the container": the
    trajectory matcher in `normalize_trajectory` is heuristic and can miss
    script-generated paths, while a collected artifact cannot be imagined.
    """
    if trial_dir is None or not declared_deliverables:
        return set()
    art_root = trial_dir / "artifacts"
    if not art_root.is_dir():
        return set()
    confirmed: Set[str] = set()
    for declared in declared_deliverables:
        rel = declared.strip().lstrip("/")
        if rel and (art_root / rel).exists():
            confirmed.add(declared)
    return confirmed


def _reconcile_delivered_deliverables(
    norm_traj: Dict[str, Any],
    trial_dir: Optional[Path],
    declared_deliverables: List[str],
) -> List[str]:
    """
    Drop declared deliverables that were actually collected on disk from the
    trajectory-derived `missing_deliverables`, updating the `premature_completion`
    signal in place so gates and reports see the reconciled set.

    Returns the reconciled missing list (empty when nothing is missing).
    """
    missing = list(norm_traj.get("missing_deliverables") or [])
    if not missing or not declared_deliverables:
        return missing
    confirmed = _confirm_delivered_on_disk(trial_dir, declared_deliverables)
    if not confirmed:
        return missing
    reconciled = [d for d in missing if d not in confirmed]
    norm_traj["missing_deliverables"] = reconciled
    if reconciled:
        for sig in norm_traj.get("behavioral_signals") or []:
            if sig.get("signal_type") != "premature_completion":
                continue
            sig["missing_deliverables"] = reconciled
            sig["description"] = (
                "Agent trajectory ended without producing declared deliverable(s): "
                f"{', '.join(reconciled)} (task.toml `artifacts`)."
            )
    else:
        # Every declared deliverable was collected on disk: the completion signal
        # was a trajectory-matching miss, so it must not reach the gates at all.
        norm_traj["behavioral_signals"] = [
            s
            for s in (norm_traj.get("behavioral_signals") or [])
            if s.get("signal_type") != "premature_completion"
        ]
    return reconciled


def _verifier_fault_message(report: Dict[str, Any]) -> str:
    """Human-readable evaluator fault text from a normalized verifier report."""
    for message in report.get("messages") or []:
        if isinstance(message, str) and message.strip():
            return message.strip()[:500]
    summary = str(report.get("summary") or "").strip()
    if summary:
        return summary[:500]
    return str(report.get("status") or "ERROR")


def _is_unscored_evaluator_fault(report: Dict[str, Any]) -> bool:
    """True for a verifier that aborted unscored with an evaluator-side fault.

    Harbor graders write `status: ERROR` / `ENV_INVALID` with `reward: null`
    and deliberately emit no `reward.txt`. `audit_contract.py` only detects
    crashes from a plain `verify.log`; layered `verify.json` reports need this
    structured path so Gate 3 can attribute a verifier defect instead of
    falling through to `unknown`.
    """
    status = str(report.get("status") or "").strip().upper()
    scientific_status = str(report.get("scientific_status") or "").strip().upper()
    if (
        status not in {"ERROR", "ENV_INVALID", "INCONCLUSIVE"}
        and scientific_status != "INCONCLUSIVE"
    ):
        return False
    if report.get("scorable") is True or report.get("eligible_for_success_ranking") is True:
        return False
    if report.get("reward") is not None:
        return False
    origin = report.get("failure_origin")
    if isinstance(origin, dict):
        code = str(origin.get("reason_code") or "").lower()
    elif isinstance(origin, str):
        code = origin.lower()
    else:
        code = ""
    if "evaluator" in code or "infrastructure" in code:
        return True
    return status in {"ERROR", "ENV_INVALID"}


def _attach_unscored_verifier_fault(
    report: Optional[Dict[str, Any]],
    verifier_observations: List[Dict[str, Any]],
) -> None:
    """Bind an unscored structured verifier error to a crash observation."""
    if not isinstance(report, dict) or not _is_unscored_evaluator_fault(report):
        return
    message = _verifier_fault_message(report)
    fail_log = next(
        (obs for obs in verifier_observations if obs.get("obs_id") == "ver:fail_log"), None
    )
    if fail_log is None:
        verifier_observations.append(
            {
                "obs_id": "ver:fail_log",
                "type": "verifier_fail_message",
                "summary": message[:240],
                "matched_text": message[:500],
                "source_file": "verifier/verify.json",
                "source_pointer": "status",
            }
        )
    else:
        # `test-stdout.txt` often starts with library warnings; the structured
        # report carries the actual evaluator exception.
        fail_log["summary"] = message[:240]
        fail_log["matched_text"] = message[:500]
    if any(obs.get("obs_id") == "ver:internal_crash" for obs in verifier_observations):
        return
    verifier_observations.append(
        {
            "obs_id": "ver:internal_crash",
            "type": "verifier_internal_crash",
            "subtype": "recompute_defect",
            "code": "VERIFIER_RECOMPUTE_DEFECT",
            "summary": f"Verifier crashed internally: {message[:200]}",
            "matched_text": message[:400],
            "hazard_detected": True,
            "failure_binding": "direct",
            "binding_evidence": ["ver:fail_log"],
            "triggered": True,
            "source_file": "verifier/verify.json",
            "source_pointer": "status",
        }
    )


def collect_case_evidence(
    trial_inv: Dict[str, Any],
    job_dir: Path,
    max_log_bytes: int = 120000,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    trial_dir = Path(trial_inv["trial_dir"]) if trial_inv.get("trial_dir") else None
    task_dir = Path(trial_inv["task_dir"]) if trial_inv.get("task_dir") else None

    traj_path = resolve_trajectory_path(trial_dir)
    declared_deliverables = extract_declared_deliverables(
        (trial_inv.get("metadata") or {}).get("task_toml")
    )
    norm_traj = normalize_trajectory(
        traj_path,
        max_obs_bytes=max(1500, max_log_bytes // 40),
        expected_deliverables=declared_deliverables,
    )
    missing_deliverables = _reconcile_delivered_deliverables(
        norm_traj, trial_dir, declared_deliverables
    )

    runtime_err_res = extract_runtime_errors(
        job_dir=job_dir,
        trial_dir=trial_dir,
        max_log_bytes=max_log_bytes,
    )

    contract_res = audit_contract(
        task_dir=task_dir,
        trial_dir=trial_dir,
        normalized_traj=norm_traj,
    )

    verifier_report = build_verifier_report(trial_dir)
    if verifier_report is not None:
        _attach_unscored_verifier_fault(verifier_report, contract_res["verifier_observations"])

    sci_res = extract_scientific_errors(
        trial_dir=trial_dir,
        normalized_traj=norm_traj,
        verifier_report=verifier_report,
    )

    # Additive top-level key: recoveries that degraded evidence on this trial.
    diagnostics = merge_diagnostics(
        (trial_inv.get("runtime") or {}).get("diagnostics"),
        contract_res.get("diagnostics"),
    )
    evidence: Dict[str, Any] = {
        "case_id": trial_inv["case_id"],
        "trial_name": trial_inv["trial_name"],
        "job_dir": trial_inv["job_dir"],
        "trial_dir": trial_inv["trial_dir"],
        "task_dir": trial_inv["task_dir"],
        "artifacts": trial_inv["artifacts"],
        "runtime": trial_inv["runtime"],
        # Additive key: harbor's declared deliverable set (task.toml `artifacts`),
        # empty when the task declaration is unavailable.
        "metadata": {
            **(trial_inv.get("metadata") or {}),
            "declared_deliverables": declared_deliverables,
        },
        "timeline": norm_traj["events"],
        "behavioral_signals": norm_traj["behavioral_signals"],
        "error_observations": runtime_err_res["error_observations"],
        "contract_observations": contract_res["contract_observations"],
        "verifier_observations": contract_res["verifier_observations"],
        "scientific_observations": sci_res["scientific_observations"],
        "missing_artifacts": trial_inv["missing_artifacts"],
        # Additive top-level key: declared deliverables (task.toml `artifacts`)
        # that were never produced. Empty for tasks with no declaration.
        "missing_deliverables": missing_deliverables,
        "diagnostics": diagnostics,
    }
    # Additive top-level key: normalized verifier report. Harbor tasks use at
    # least five different verifier JSON schemas; this is the only place where
    # named failed checks, per-check deviation values, `core_failures` and
    # `failure_origin` become visible to gates, reports and the batch board.
    if verifier_report is not None:
        evidence["verifier_report"] = verifier_report

    # Additive top-level key: verifier check statistics for the confidence
    # upgrade (`check_localization` measurement). When there is no verify.log,
    # derive the same shape from the structured report instead of losing every
    # named failed check.
    verifier_check_stats = _build_verifier_check_stats(trial_dir, max_log_bytes, diagnostics)
    if verifier_report is not None:
        if verifier_check_stats is None or verifier_check_stats.get("total_checks", 0) == 0:
            if (
                verifier_report.get("checks")
                or verifier_report.get("failed_checks")
                or verifier_report.get("failed_check_count")
            ):
                verifier_check_stats = build_check_stats_from_report(verifier_report)
        elif verifier_report.get("failed_check_summaries"):
            summaries = list(verifier_check_stats.get("failed_check_summaries") or [])
            for item in verifier_report["failed_check_summaries"]:
                if item not in summaries:
                    summaries.append(item)
            verifier_check_stats["failed_check_summaries"] = summaries
            verifier_check_stats["verifier_report_checks"] = verifier_report.get(
                "failed_check_count", len(verifier_report.get("failed_checks") or [])
            )
    if verifier_check_stats is not None:
        evidence["verifier_check_stats"] = verifier_check_stats
    anchors = trace_causal_anchors(evidence)
    if anchors:
        evidence["causal_anchors"] = anchors
    cand_hyps = generate_candidate_hypotheses(evidence)
    return evidence, norm_traj, contract_res, cand_hyps


def _reconcile_competing_hypotheses(
    branch_hyps: List[Dict[str, Any]],
    cand_hyps: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merges branch-specific hypotheses with all candidate hypotheses produced by
    `generate_candidate_hypotheses(evidence)` so every gate's `competing_hypotheses`
    is driven by the unified candidate hypothesis set.
    """
    merged: List[Dict[str, Any]] = [dict(h) for h in (branch_hyps or [])]
    seen_keys = {(h.get("category"), h.get("subtype")) for h in merged}
    raw_candidates = (cand_hyps or {}).get("candidate_hypotheses") or []
    for cand in raw_candidates:
        if not isinstance(cand, dict):
            continue
        key = (cand.get("category"), cand.get("subtype"))
        if key in seen_keys:
            for existing in merged:
                if (existing.get("category"), existing.get("subtype")) == key:
                    merged_for = list(
                        dict.fromkeys(
                            (existing.get("evidence_for") or []) + (cand.get("evidence_for") or [])
                        )
                    )
                    existing["evidence_for"] = merged_for
                    break
            continue
        seen_keys.add(key)
        cloned = dict(cand)
        cloned["hypothesis_id"] = f"H{len(merged) + 1}"
        merged.append(cloned)
    return merged


def _enrich_with_verifier_report(
    result: Dict[str, Any], evidence: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Put named verifier checks (and their deviation values) into the failure
    manifestation, and correct a premature-termination verdict that the
    structured verifier report contradicts.

    Harbor graders emit layered JSON reports with no `verifier/verify.log`.
    Without this step the report says "agent wrote results.json without sanity
    validation" while the real failure is e.g. `L3
    coordination_distances_match_derived_reference` (5/25 distances within
    tolerance, worst deviation 3.326 A).
    """
    report = evidence.get("verifier_report")
    if not isinstance(report, dict):
        return result
    failed = report.get("failed_checks") or []
    summary = str(report.get("summary") or "").strip()
    if not failed and not summary:
        return result

    manifestation = result.get("failure_manifestation")
    if not isinstance(manifestation, dict):
        manifestation = {}
        result["failure_manifestation"] = manifestation
    if summary:
        manifestation["summary"] = summary[:700]
    if failed:
        manifestation["type"] = "verifier_check_failed"
        manifestation["named_failed_checks"] = [
            {
                "layer": c.get("layer"),
                "name": c.get("name"),
                "detail": str(c.get("detail") or "")[:300],
                "core": bool(c.get("core")),
                "source": c.get("source"),
            }
            for c in failed[:5]
        ]
    if report.get("diagnosability") == "low":
        manifestation["verifier_diagnosability"] = "low"
        manifestation["verifier_diagnosability_reasons"] = list(
            report.get("diagnosability_reasons") or []
        )

    prc = result.get("primary_root_cause")
    if not (isinstance(prc, dict) and prc.get("code") == "AGENT_PREMATURE_TERMINATION" and failed):
        return result

    replacement = (
        f"Named verifier checks failed: {summary[:420]}"
        if summary
        else ("Named verifier checks failed; the trial was scored, not terminated early.")
    )
    prc["code"] = "AGENT_RESULT_VALIDATION"
    prc["subtype"] = "result_validation"
    prc["summary"] = replacement
    for hyp in result.get("competing_hypotheses") or []:
        if (
            isinstance(hyp, dict)
            and hyp.get("category") == "agent"
            and hyp.get("hypothesis_id") == "H1"
        ):
            hyp["claim"] = replacement
    for action in result.get("recommended_actions") or []:
        if isinstance(action, dict) and "premature" in str(action.get("action", "")).lower():
            action["action"] = replacement
    fud = result.get("first_unrecovered_deviation")
    if isinstance(fud, dict):
        fud["summary"] = replacement
    trace = result.get("decision_trace")
    if isinstance(trace, dict):
        trace["verifier_report_correction"] = (
            "AGENT_PREMATURE_TERMINATION -> AGENT_RESULT_VALIDATION"
        )
    return result


def build_causal_attribution(
    evidence: Dict[str, Any],
    norm_traj: Dict[str, Any],
    contract_res: Dict[str, Any],
    cand_hyps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if cand_hyps is None:
        cand_hyps = generate_candidate_hypotheses(evidence)
    result = _build_raw_causal_attribution(evidence, norm_traj, contract_res, cand_hyps)
    if result.get("verdict") != "passed":
        result["competing_hypotheses"] = _reconcile_competing_hypotheses(
            result.get("competing_hypotheses") or [],
            cand_hyps,
        )
        # Record that the gate's hypothesis list was post-processed after it
        # returned, so the trace does not imply the gate emitted it directly.
        trace = result.get("decision_trace")
        if isinstance(trace, dict):
            trace["reconciled_competing_hypotheses"] = True
        result = _enrich_with_verifier_report(result, evidence)
        prc = result.get("primary_root_cause") or {}
        fud = result.get("first_unrecovered_deviation")
        if (
            isinstance(fud, dict)
            and fud.get("status") != "identified"
            and prc.get("category") == "agent"
        ):
            anchors = evidence.get("causal_anchors") or {}
            algo_anchor = anchors.get("algorithm_or_param_definition")
            deliv_anchor = anchors.get("deliverable_generation")
            chosen_anchor = algo_anchor or deliv_anchor
            if chosen_anchor:
                fud["status"] = "identified"
                fud["event_ref"] = chosen_anchor.get("event_id")
                fud["summary"] = (
                    f"Agent formulated computational parameters/logic at `{chosen_anchor.get('step_id')}` "
                    f"({chosen_anchor.get('target_path', 'code')}) leading to subsequent verification failure."
                    if chosen_anchor is algo_anchor
                    else f"Agent executed deliverable generation at `{chosen_anchor.get('step_id')}` "
                    f"resulting in verification failure."
                )
    return result


def _build_raw_causal_attribution(
    evidence: Dict[str, Any],
    norm_traj: Dict[str, Any],
    contract_res: Dict[str, Any],
    cand_hyps: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deprecated shim retained for signature stability; delegates to the gate engine.

    The decision logic now lives in `scripts/attribution/` as one function per
    gate. `norm_traj` and `contract_res` were never read by the original
    implementation (the caller passes them positionally), so they remain accepted
    but unused.
    """
    return run_attribution_engine(evidence, cand_hyps)


def analyze_single_trial(
    trial_inv: Dict[str, Any],
    job_dir: Path,
    max_log_bytes: int = 120000,
    replay_mode: str = "none",
    lang: str = "bilingual",
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    if replay_mode != "none":
        run_isolated_verifier_replay(
            Path(trial_inv["task_dir"]) if trial_inv.get("task_dir") else None,
            Path(trial_inv["trial_dir"]) if trial_inv.get("trial_dir") else None,
        )

    evidence, norm_traj, contract_res, cand_hyps = collect_case_evidence(
        trial_inv=trial_inv,
        job_dir=job_dir,
        max_log_bytes=max_log_bytes,
    )

    analysis = build_causal_attribution(evidence, norm_traj, contract_res, cand_hyps)
    report_md = render_report_markdown(evidence, analysis, lang=lang)

    ok, val_errors = validate_all(evidence, analysis, report_md)
    if not ok:
        raise RuntimeError(f"Generated analysis failed schema/constraint validation: {val_errors}")

    skill_md = None
    if analysis.get("skill_prescription"):
        skill_md = render_skill_prescription_markdown(analysis["skill_prescription"], lang=lang)

    return evidence, analysis, report_md, skill_md


def _report_diagnostics(evidence: Dict[str, Any]) -> None:
    """Emit one line per degraded-evidence record to stderr (never stdout)."""
    for record in evidence.get("diagnostics") or []:
        print(f"ANALYSIS DIAGNOSTIC: {format_diagnostic_line(record)}", file=sys.stderr)


KNOWN_TRIAL_ARTIFACTS = {
    "evidence.json",
    "candidate-hypotheses.json",
    "analysis.json",
    "report.md",
    "report.zh.md",
    "report.en.md",
    "skill-prescription.md",
    "skill-prescription.zh.md",
    "skill-prescription.en.md",
}
KNOWN_ROOT_ARTIFACTS = {
    "job_summary.json",
    "job_summary.md",
    "job_summary.zh.md",
    "job_summary.en.md",
}


def _artifact_name(logical: str, basename: Optional[str]) -> str:
    """
    Map a canonical artifact name to its on-disk name.

    With `basename=None` (flat path mode) names are unchanged. A non-None
    `basename` yields the legacy prefixed names (`task208.md`,
    `task208.analysis.json`, ...). Hierarchical task-ID mode no longer writes
    those prefixed files — it uses `data/*.json` plus clean top-level Markdown —
    but the mapping is retained for cleanup and manifest compatibility.
    """
    if not basename:
        return logical
    if logical == "report.md":
        return f"{basename}.md"
    if logical.startswith("report."):
        return f"{basename}.{logical[len('report.') :]}"
    return f"{basename}.{logical}"


def write_outputs(
    out_dir: Path,
    evidence: Dict[str, Any],
    analysis: Optional[Dict[str, Any]],
    report_md: Optional[str],
    skill_md: Optional[str],
    cand_hyps: Optional[Dict[str, Any]] = None,
    fmt: str = "both",
    lang: str = "bilingual",
    basename: Optional[str] = None,
    verdict_card: Optional[Dict[str, Any]] = None,
    verdict_md: Optional[str] = None,
    archive_evidence: bool = True,
) -> None:
    """
    Write trial artifacts.
    In task-ID mode (basename is provided), enforces a clean hierarchical layout:
      - Top-level: clean markdown (e.g. task208.md, summary.md, skill-prescription.md)
      - data/: machine JSON (evidence.json, analysis.json, summary.json, candidate-hypotheses.json)
      - Eliminates redundant single-language .zh.md copies.
    In flat mode (basename=None), outputs directly to out_dir for compatibility.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    is_hierarchical = basename is not None
    if is_hierarchical:
        (out_dir / "data").mkdir(parents=True, exist_ok=True)

    def name(logical: str) -> str:
        return _artifact_name(logical, basename)

    # 1. Determine planned artifacts for the current invocation
    planned_files: Set[str] = set()
    effective_cand_hyps = cand_hyps
    if fmt in ("json", "both"):
        ev_file = "data/evidence.json" if is_hierarchical else name("evidence.json")
        if archive_evidence:
            planned_files.add(ev_file)
        if effective_cand_hyps is None and analysis is not None:
            effective_cand_hyps = {
                "case_id": evidence.get("case_id"),
                "trial_name": evidence.get("trial_name"),
                "candidate_hypotheses": analysis.get("competing_hypotheses") or [],
            }
        if effective_cand_hyps is not None:
            cand_file = (
                "data/candidate-hypotheses.json"
                if is_hierarchical
                else name("candidate-hypotheses.json")
            )
            planned_files.add(cand_file)
        if analysis is not None:
            an_file = "data/analysis.json" if is_hierarchical else name("analysis.json")
            planned_files.add(an_file)
        if verdict_card is not None:
            sum_json = "data/summary.json" if is_hierarchical else name("summary.json")
            planned_files.add(sum_json)

    has_markdown = fmt in ("markdown", "both") and report_md is not None and analysis is not None
    if has_markdown:
        sp = analysis.get("skill_prescription")
        main_report = f"{basename}.md" if is_hierarchical else name("report.md")
        planned_files.add(main_report)
        if lang == "bilingual":
            en_report = f"{basename}.en.md" if is_hierarchical else name("report.en.md")
            planned_files.add(en_report)
            if not is_hierarchical:
                planned_files.add(name("report.zh.md"))
        if skill_md and sp:
            main_skill = (
                "skill-prescription.md" if is_hierarchical else name("skill-prescription.md")
            )
            planned_files.add(main_skill)
            if lang == "bilingual":
                en_skill = (
                    "skill-prescription.en.md"
                    if is_hierarchical
                    else name("skill-prescription.en.md")
                )
                planned_files.add(en_skill)
                if not is_hierarchical:
                    planned_files.add(name("skill-prescription.zh.md"))
        if verdict_md is not None:
            sum_md = "summary.md" if is_hierarchical else name("summary.md")
            planned_files.add(sum_md)

    # 2. Lifecycle cleanup: purge stale artifacts from prior runs
    known_names = {name(logical) for logical in KNOWN_TRIAL_ARTIFACTS}
    known_names.update(
        {
            name("summary.json"),
            name("summary.md"),
            "summary.md",
            "summary.json",
            "data/evidence.json",
            "data/analysis.json",
            "data/summary.json",
            "data/candidate-hypotheses.json",
        }
    )
    if is_hierarchical:
        known_names.update(
            {
                f"{basename}.zh.md",
                f"{basename}.en.md",
                f"{basename}.evidence.json",
                f"{basename}.analysis.json",
                f"{basename}.summary.json",
                f"{basename}.summary.md",
                f"{basename}.candidate-hypotheses.json",
            }
        )
    for stale_file in known_names - planned_files:
        stale_path = out_dir / stale_file
        if stale_path.is_file():
            stale_path.unlink()

    # 3. Write planned artifacts
    ev_target = (
        (out_dir / "data" / "evidence.json")
        if is_hierarchical
        else (out_dir / name("evidence.json"))
    )
    if (
        "data/evidence.json" in planned_files or name("evidence.json") in planned_files
    ) and archive_evidence:
        ev_target.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    cand_target = (
        (out_dir / "data" / "candidate-hypotheses.json")
        if is_hierarchical
        else (out_dir / name("candidate-hypotheses.json"))
    )
    if (
        "data/candidate-hypotheses.json" in planned_files
        or name("candidate-hypotheses.json") in planned_files
    ) and effective_cand_hyps is not None:
        cand_target.write_text(
            json.dumps(effective_cand_hyps, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    an_target = (
        (out_dir / "data" / "analysis.json")
        if is_hierarchical
        else (out_dir / name("analysis.json"))
    )
    if (
        "data/analysis.json" in planned_files or name("analysis.json") in planned_files
    ) and analysis is not None:
        an_target.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")

    sum_json_target = (
        (out_dir / "data" / "summary.json") if is_hierarchical else (out_dir / name("summary.json"))
    )
    if verdict_card is not None and (
        "data/summary.json" in planned_files or name("summary.json") in planned_files
    ):
        sum_json_target.write_text(
            json.dumps(verdict_card, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    sum_md_target = (out_dir / "summary.md") if is_hierarchical else (out_dir / name("summary.md"))
    if verdict_md is not None and (
        "summary.md" in planned_files or name("summary.md") in planned_files
    ):
        sum_md_target.write_text(verdict_md, encoding="utf-8")

    if has_markdown and analysis is not None:
        sp = analysis.get("skill_prescription")
        main_report_target = (
            (out_dir / f"{basename}.md") if is_hierarchical else (out_dir / name("report.md"))
        )
        main_skill_target = (
            (out_dir / "skill-prescription.md")
            if is_hierarchical
            else (out_dir / name("skill-prescription.md"))
        )

        if lang == "zh":
            zh_report = render_report_markdown(evidence, analysis, lang="zh", output_format=fmt)
            main_report_target.write_text(zh_report, encoding="utf-8")
            if not is_hierarchical:
                (out_dir / name("report.zh.md")).write_text(zh_report, encoding="utf-8")
            if skill_md and sp:
                zh_skill = render_skill_prescription_markdown(sp, lang="zh")
                main_skill_target.write_text(zh_skill, encoding="utf-8")
                if not is_hierarchical:
                    (out_dir / name("skill-prescription.zh.md")).write_text(
                        zh_skill, encoding="utf-8"
                    )
        elif lang == "en":
            en_report = render_report_markdown(evidence, analysis, lang="en", output_format=fmt)
            main_report_target.write_text(en_report, encoding="utf-8")
            if not is_hierarchical:
                (out_dir / name("report.en.md")).write_text(en_report, encoding="utf-8")
            if skill_md and sp:
                en_skill = render_skill_prescription_markdown(sp, lang="en")
                main_skill_target.write_text(en_skill, encoding="utf-8")
                if not is_hierarchical:
                    (out_dir / name("skill-prescription.en.md")).write_text(
                        en_skill, encoding="utf-8"
                    )
        else:  # bilingual
            bi_report = render_report_markdown(
                evidence, analysis, lang="bilingual", output_format=fmt
            )
            main_report_target.write_text(bi_report, encoding="utf-8")
            en_report_target = (
                (out_dir / f"{basename}.en.md")
                if is_hierarchical
                else (out_dir / name("report.en.md"))
            )
            en_report_target.write_text(
                render_report_markdown(evidence, analysis, lang="en", output_format=fmt),
                encoding="utf-8",
            )
            if not is_hierarchical:
                (out_dir / name("report.zh.md")).write_text(
                    render_report_markdown(evidence, analysis, lang="zh", output_format=fmt),
                    encoding="utf-8",
                )
            if skill_md and sp:
                main_skill_target.write_text(
                    render_skill_prescription_markdown(sp, lang="bilingual"), encoding="utf-8"
                )
                en_skill_target = (
                    (out_dir / "skill-prescription.en.md")
                    if is_hierarchical
                    else (out_dir / name("skill-prescription.en.md"))
                )
                en_skill_target.write_text(
                    render_skill_prescription_markdown(sp, lang="en"), encoding="utf-8"
                )
                if not is_hierarchical:
                    (out_dir / name("skill-prescription.zh.md")).write_text(
                        render_skill_prescription_markdown(sp, lang="zh"), encoding="utf-8"
                    )


def _degraded_diagnostic_counts(evidence: Dict[str, Any]) -> Dict[str, int]:
    """Count warning/error diagnostics on one trial's evidence."""
    counts = {"warning": 0, "error": 0}
    for record in evidence.get("diagnostics") or []:
        if not isinstance(record, dict):
            continue
        severity = record.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _summary_diagnostic_fields(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Summary-row fields that make degraded evidence visible in batch output."""
    counts = _degraded_diagnostic_counts(evidence)
    report = evidence.get("verifier_report") or {}
    return {
        "diagnostic_counts": counts,
        "degraded_evidence": bool(counts["warning"] or counts["error"]),
        # `low` means the verifier's failure reason is a terse generic message
        # with no named check or numeric deviation (see normalize_verifier_report).
        "verifier_diagnosability": (
            report.get("diagnosability") if isinstance(report, dict) else None
        ),
    }


def compute_batch_statistics(summary_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute Stage 10 batch-level aggregation statistics across trials."""
    verdict_counts: Dict[str, int] = {}
    stage_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    code_counts: Dict[str, int] = {}
    diagnostic_counts = {"warning": 0, "error": 0}
    degraded_trial_count = 0
    agent_started_count = 0
    verifier_started_count = 0

    for row in summary_rows:
        v = str(row.get("verdict") or "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        if row.get("agent_started"):
            agent_started_count += 1
        if row.get("verifier_started"):
            verifier_started_count += 1
        st = row.get("failure_stage")
        if st:
            stage_counts[st] = stage_counts.get(st, 0) + 1
        cat = row.get("category")
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        code = row.get("code")
        if code:
            code_counts[code] = code_counts.get(code, 0) + 1
        row_diagnostics = row.get("diagnostic_counts") or {}
        row_warnings = int(row_diagnostics.get("warning", 0) or 0)
        row_errors = int(row_diagnostics.get("error", 0) or 0)
        diagnostic_counts["warning"] += row_warnings
        diagnostic_counts["error"] += row_errors
        if row_warnings or row_errors:
            degraded_trial_count += 1

    low_diagnosability_count = sum(
        1 for row in summary_rows if row.get("verifier_diagnosability") == "low"
    )
    scored_trial_count = verifier_started_count
    not_scored_trial_count = max(0, len(summary_rows) - scored_trial_count)
    passed_count = int(verdict_counts.get("passed", 0))
    scored_pass_rate = (
        round(passed_count / scored_trial_count * 100.0, 1) if scored_trial_count > 0 else 0.0
    )

    return {
        "total_trials": len(summary_rows),
        "agent_started_count": agent_started_count,
        "verifier_started_count": verifier_started_count,
        # Three-state scoring coverage: trials whose verifier never ran are NOT
        # scored failures and must not be counted as agent failures in the rate.
        "scored_trial_count": scored_trial_count,
        "not_scored_trial_count": not_scored_trial_count,
        "scored_pass_rate": scored_pass_rate,
        "low_diagnosability_trial_count": low_diagnosability_count,
        "verdict_counts": verdict_counts,
        "failure_stage_counts": stage_counts,
        "category_counts": category_counts,
        "root_cause_code_counts": dict(sorted(code_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "degraded_evidence_trial_count": degraded_trial_count,
        "diagnostic_severity_counts": diagnostic_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze scientific-computing benchmark case failures (case failure analyzer)."
    )
    parser.add_argument(
        "--job", required=False, type=Path, default=None, help="Job or trial directory path"
    )
    parser.add_argument(
        "--task-id",
        required=False,
        default=None,
        help=(
            "Short task reference (e.g. 'task208', '208', 'task 208__jaekojA'). "
            "Resolves the trial under --jobs-root and writes task<id>.* reports "
            "into the task's failure-report/ folder."
        ),
    )
    parser.add_argument(
        "--jobs-root",
        required=False,
        type=Path,
        default=None,
        help=(
            "Benchmark checkout root containing case folders' jobs/ trees. "
            "Defaults to $CFA_JOBS_ROOT, then the current working directory."
        ),
    )
    parser.add_argument(
        "--report-dir",
        required=False,
        type=Path,
        default=None,
        help="Override the default <task>/failure-report output directory",
    )
    parser.add_argument(
        "--task", required=False, type=Path, default=None, help="Task definition directory path"
    )
    parser.add_argument("--trial", default="all", help="Specific trial name or 'all'")
    parser.add_argument(
        "--output",
        required=False,
        type=Path,
        default=None,
        help=(
            "Output directory. Optional in --task-id mode (defaults to the task's "
            "failure-report/ folder); required otherwise."
        ),
    )
    parser.add_argument(
        "--no-verdict-stdout",
        action="store_true",
        help="Suppress the direct-answer verdict card printed to stdout in --task-id mode",
    )
    parser.add_argument(
        "--phase",
        choices=["all", "collect"],
        default="all",
        help=(
            "Execution phase: 'collect' (evidence + candidate-hypotheses only) "
            "or 'all' (default: full evidence pipeline + conservative attribution + bilingual report)"
        ),
    )
    parser.add_argument(
        "--replay",
        choices=["none", "verifier", "safe"],
        default="none",
        help="Verifier/simulation replay mode (only 'none' is supported for static read-only analysis)",
    )
    parser.add_argument(
        "--max-log-bytes",
        type=positive_int,
        default=120000,
        help="Maximum bytes per log file read (positive integer)",
    )
    parser.add_argument(
        "--include-session-files",
        action="store_true",
        help="Include agent session files in inventory",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="both",
        help="Output format (default: both)",
    )
    parser.add_argument(
        "--lang",
        choices=["bilingual", "zh", "en"],
        default="bilingual",
        help="Report language edition (default: bilingual; options: bilingual, zh, en)",
    )
    parser.add_argument(
        "--archive-evidence",
        action="store_true",
        help="In multi-trial mode, also archive full evidence.json inside trials/<trial_name>/ (default: lightweight archiving)",
    )
    args = parser.parse_args()

    # ── Task-ID quick invocation (e.g. `/case failure analyzer task208`) ──────
    task_ref: Optional[TaskRef] = None
    basename: Optional[str] = None
    if args.task_id:
        explicit_root = args.jobs_root or os.environ.get("CFA_JOBS_ROOT")
        cwd = Path.cwd()
        roots = [Path(explicit_root)] if explicit_root else [cwd, cwd.parent]
        task_ref = None
        last_error: Optional[TaskRefError] = None
        for candidate_root in roots:
            try:
                task_ref = resolve_task_ref(
                    args.task_id, candidate_root, report_dir_override=args.report_dir
                )
                break
            except TaskRefError as exc:
                last_error = exc
        if task_ref is None:
            print(f"ERROR: {last_error}", file=sys.stderr)
            sys.exit(2)
        args.job = task_ref.trial_dir
        args.task = task_ref.task_def_dir or args.task
        args.trial = task_ref.trial_dir.name
        if args.output is None:
            args.output = task_ref.report_dir
        basename = task_ref.report_basename
    else:
        if args.job is None:
            print("ERROR: --job is required when --task-id is not used.", file=sys.stderr)
            sys.exit(2)
        if args.output is None:
            print("ERROR: --output is required when --task-id is not used.", file=sys.stderr)
            sys.exit(2)
        if args.jobs_root is not None or args.report_dir is not None:
            print(
                "ERROR: --jobs-root and --report-dir are only valid with --task-id.",
                file=sys.stderr,
            )
            sys.exit(2)

    if args.phase == "collect" and args.format == "markdown":
        print(
            "ERROR: --phase collect does not produce markdown reports. "
            "Use --format json or --format both.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Fail fast on bad input paths or unsupported replay modes before writing any output.
    if args.replay != "none":
        try:
            run_isolated_verifier_replay(args.task, args.job)
        except ReplayNotSupportedError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
    if not args.job.exists():
        print(f"ERROR: --job path does not exist: {args.job}", file=sys.stderr)
        sys.exit(2)
    if not args.job.is_dir():
        print(f"ERROR: --job path exists and is not a directory: {args.job}", file=sys.stderr)
        sys.exit(2)
    if args.task is not None:
        if not args.task.exists():
            print(f"ERROR: --task path does not exist: {args.task}", file=sys.stderr)
            sys.exit(2)
        if not args.task.is_dir():
            print(f"ERROR: --task path exists and is not a directory: {args.task}", file=sys.stderr)
            sys.exit(2)

    # The default landing (`<bench>/failure-report/<task>`) is outside every
    # input. An explicit `--report-dir` may still target a `failure-report`
    # folder inside a case directory; that exact folder is the only sanctioned
    # output inside an input tree.
    sanctioned_report_dir: Optional[Path] = None
    if task_ref is not None and args.output.resolve() == task_ref.report_dir:
        resolved_out = args.output.resolve()
        resolved_inputs = [p.resolve() for p in (args.job, args.task) if p is not None]
        if resolved_out.name == REPORT_DIR_NAME and resolved_out.parent in resolved_inputs:
            sanctioned_report_dir = resolved_out

    _validate_input_output_isolation(
        out_dir=args.output,
        input_roots=[(args.job, "--job"), (args.task, "--task")],
        planned_trial_names=[],
        allowed_report_dir=sanctioned_report_dir,
    )

    all_trials = discover_trials(args.job, trial_filter="all")
    if args.trial != "all":
        available = [t.name for t in all_trials]
        if args.trial not in available:
            print(
                f"ERROR: --trial '{args.trial}' not found under {args.job}. "
                f"Available trials: {available or 'none'}",
                file=sys.stderr,
            )
            sys.exit(2)
    elif not all_trials:
        # A job-level pre-startup failure is legitimate only when job-level evidence exists.
        has_job_level_evidence = (args.job / "result.json").is_file() or (
            args.job / "job.log"
        ).is_file()
        if not has_job_level_evidence:
            print(
                f"ERROR: no trial directories and no job-level result.json/job.log found under {args.job}. "
                "Refusing to analyze an empty input tree.",
                file=sys.stderr,
            )
            sys.exit(2)

    discovery = discover_all(
        job_path=args.job,
        task_path=args.task,
        trial_filter=args.trial,
        include_session_files=args.include_session_files,
    )
    trials = discovery.get("trials") or []
    job_dir = Path(discovery["job_dir"])
    task_dir_path = Path(discovery["task_dir"]) if discovery.get("task_dir") else None

    all_input_roots: List[Tuple[Optional[Path], str]] = [
        (job_dir, "--job"),
        (task_dir_path, "--task"),
    ]
    for inv in trials:
        t_task = inv.get("task_dir")
        if t_task and Path(t_task) != task_dir_path:
            all_input_roots.append((Path(t_task), "--task"))
        t_path = inv.get("trial_dir")
        if t_path:
            all_input_roots.append((Path(t_path), f"input trial '{inv.get('trial_name')}'"))
    for t in all_trials:
        all_input_roots.append((t, f"input trial '{t.name}'"))

    planned_trial_names = [inv["trial_name"] for inv in trials] if len(trials) > 1 else []
    _validate_input_output_isolation(
        out_dir=args.output,
        input_roots=all_input_roots,
        planned_trial_names=planned_trial_names,
        allowed_report_dir=sanctioned_report_dir,
    )

    validate_output_writable(args.output)

    staging_dir = Path(tempfile.mkdtemp(prefix="cfa_staging_"))
    verdict_stdout: Optional[str] = None
    try:
        if len(trials) == 1:
            inv = trials[0]
            if args.phase == "collect":
                ev, _, _, cand_hyps = collect_case_evidence(
                    inv, job_dir=job_dir, max_log_bytes=args.max_log_bytes
                )
                _report_diagnostics(ev)
                write_outputs(
                    staging_dir,
                    ev,
                    None,
                    None,
                    None,
                    cand_hyps=cand_hyps,
                    fmt=args.format,
                    lang=args.lang,
                    basename=basename,
                )
            else:
                ev, an, rep, skill_md = analyze_single_trial(
                    inv,
                    job_dir=job_dir,
                    max_log_bytes=args.max_log_bytes,
                    replay_mode=args.replay,
                    lang=args.lang,
                )
                _report_diagnostics(ev)
                card: Optional[Dict[str, Any]] = None
                card_md: Optional[str] = None
                if task_ref is not None:
                    card = build_verdict_card(
                        ev,
                        an,
                        task_id=task_ref.task_id,
                        report_basename=task_ref.report_basename,
                    )
                    card_lang = "zh" if args.lang != "en" else "en"
                    card_md = render_verdict_card_markdown(card, lang=card_lang)
                    verdict_stdout = card_md
                write_outputs(
                    staging_dir,
                    ev,
                    an,
                    rep,
                    skill_md,
                    fmt=args.format,
                    lang=args.lang,
                    basename=basename,
                    verdict_card=card,
                    verdict_md=card_md,
                )
            # The default task-ID landing is a dedicated per-task folder, so the
            # generic manifest is enough; a shared --report-dir keeps the
            # task-scoped manifest to avoid deleting a sibling task's reports.
            manifest_key = (
                None
                if task_ref is not None and args.output.resolve() == task_ref.report_dir
                else basename
            )
            publish_staging_to_output(
                staging_dir, args.output, is_multi_trial=False, manifest_key=manifest_key
            )
            if task_ref is not None:
                from render_index import update_failure_report_index

                try:
                    update_failure_report_index(task_ref.report_root)
                except Exception as exc:
                    print(f"WARNING: failed to update index: {exc}", file=sys.stderr)
        else:
            summary_rows = []
            for inv in trials:
                t_out = (
                    (staging_dir / "trials" / inv["trial_name"])
                    if task_ref is not None
                    else (staging_dir / inv["trial_name"])
                )
                archive_ev = (
                    args.archive_evidence
                    if (task_ref is not None and args.phase != "collect")
                    else True
                )
                if args.phase == "collect":
                    ev, _, _, cand_hyps = collect_case_evidence(
                        inv, job_dir=job_dir, max_log_bytes=args.max_log_bytes
                    )
                    _report_diagnostics(ev)
                    write_outputs(
                        t_out,
                        ev,
                        None,
                        None,
                        None,
                        cand_hyps=cand_hyps,
                        fmt=args.format,
                        lang=args.lang,
                        basename=None,
                        archive_evidence=True,
                    )
                    summary_rows.append(
                        {
                            "trial_name": inv["trial_name"],
                            "verdict": ev["runtime"].get("verdict"),
                            "agent_started": bool(ev["runtime"].get("agent_started")),
                            "verifier_started": bool(ev["runtime"].get("verifier_started")),
                            "phase": "collect",
                            **_summary_diagnostic_fields(ev),
                        }
                    )
                else:
                    ev, an, rep, skill_md = analyze_single_trial(
                        inv,
                        job_dir=job_dir,
                        max_log_bytes=args.max_log_bytes,
                        replay_mode=args.replay,
                        lang=args.lang,
                    )
                    _report_diagnostics(ev)
                    card = None
                    card_md = None
                    if task_ref is not None:
                        card = build_verdict_card(
                            ev,
                            an,
                            task_id=f"{task_ref.task_id}__{inv['trial_name']}",
                            report_basename=task_ref.report_basename,
                        )
                        card_lang = "zh" if args.lang != "en" else "en"
                        card_md = render_verdict_card_markdown(card, lang=card_lang)
                    write_outputs(
                        t_out,
                        ev,
                        an,
                        rep,
                        skill_md,
                        fmt=args.format,
                        lang=args.lang,
                        basename=None,
                        verdict_card=card,
                        verdict_md=card_md,
                        archive_evidence=archive_ev,
                    )
                    summary_rows.append(
                        {
                            "trial_name": inv["trial_name"],
                            "verdict": an["verdict"],
                            "agent_started": bool(ev["runtime"].get("agent_started")),
                            "verifier_started": bool(ev["runtime"].get("verifier_started")),
                            "failure_stage": an["failure_stage"],
                            "detection_stage": an["detection_stage"],
                            "category": an["primary_root_cause"]["category"],
                            "code": an["primary_root_cause"]["code"],
                            "confidence": an["primary_root_cause"]["confidence"],
                            **_summary_diagnostic_fields(ev),
                        }
                    )
            batch_stats = compute_batch_statistics(summary_rows)
            job_summary_data = {
                "job_dir": str(job_dir),
                "batch_statistics": batch_stats,
                "trials": summary_rows,
            }
            if args.format in ("json", "both"):
                (staging_dir / "job_summary.json").write_text(
                    json.dumps(job_summary_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            if args.format in ("markdown", "both") and args.phase != "collect":
                if args.lang == "zh":
                    zh_summary = render_job_summary_markdown(job_summary_data, lang="zh")
                    (staging_dir / "job_summary.md").write_text(zh_summary, encoding="utf-8")
                    (staging_dir / "job_summary.zh.md").write_text(zh_summary, encoding="utf-8")
                elif args.lang == "en":
                    en_summary = render_job_summary_markdown(job_summary_data, lang="en")
                    (staging_dir / "job_summary.md").write_text(en_summary, encoding="utf-8")
                    (staging_dir / "job_summary.en.md").write_text(en_summary, encoding="utf-8")
                else:  # bilingual
                    (staging_dir / "job_summary.md").write_text(
                        render_job_summary_markdown(job_summary_data, lang="bilingual"),
                        encoding="utf-8",
                    )
                    (staging_dir / "job_summary.zh.md").write_text(
                        render_job_summary_markdown(job_summary_data, lang="zh"),
                        encoding="utf-8",
                    )
                    (staging_dir / "job_summary.en.md").write_text(
                        render_job_summary_markdown(job_summary_data, lang="en"),
                        encoding="utf-8",
                    )
            publish_staging_to_output(staging_dir, args.output, is_multi_trial=True)
            if task_ref is not None:
                from render_index import update_failure_report_index

                try:
                    update_failure_report_index(task_ref.report_root)
                except Exception as exc:
                    print(f"WARNING: failed to update index: {exc}", file=sys.stderr)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    # Direct answer for task-ID invocation: the verdict card goes to stdout so a
    # caller (`/case failure analyzer task208`) can answer without opening files.
    if verdict_stdout and not args.no_verdict_stdout:
        print(verdict_stdout)
        print(f"REPORT_DIR: {args.output}")


if __name__ == "__main__":
    main()
