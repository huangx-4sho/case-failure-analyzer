#!/usr/bin/env python3
"""
Unified Per-Trial Runtime State Model (`scripts/runtime_state.py`)

Provides a single source of truth for per-trial execution state, stage gates
(`agent_started`, `verifier_started`), `execution_status`, `verification_status`,
`verdict`, and legacy `exit_status`. Never leaks job-level aggregate error counts
(`stats.n_errored_trials`) into individual trial verdicts.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
import math
from typing import Any, Dict, List, Optional

from diagnostics import WARNING, make_diagnostic

SCHEMA_VERSION = "failure-analysis-v1"
SUPPORTED_ATIF_VERSIONS = ("ATIF-v1.7", "ATIF-v1.6", "ATIF-v1.5")

TRAJECTORY_REL_PATHS = (
    "agent/trajectory.json",
    "agent/trajectory.jsonl",
    "agent/messages.json",
    "agent/events.jsonl",
    "trajectory.json",
    "trajectory.jsonl",
    "messages.json",
)

VERIFIER_SCRIPT_REL_PATHS = (
    "tests/verify.py",
    "tests/test_outputs.py",
    "tests/test_solution.py",
    "tests/evaluate.py",
    "verify.py",
)

COMPLETENESS_REWARD_SCOPE = "evidence_completeness_only"


def score_was_produced(record: Dict[str, Any]) -> bool:
    """Starting a verifier is not evidence that it returned a score."""
    if "score_produced" in record:
        return record["score_produced"] is True
    if "scored" in record:
        return record["scored"] is True
    reward = record.get("reward")
    if reward is not None:
        return (
            not isinstance(reward, bool)
            and isinstance(reward, (int, float))
            and math.isfinite(reward)
        )
    return record.get("verdict") in ("passed", "failed")


def resolve_trajectory_path(trial_dir: Optional[Path]) -> Optional[Path]:
    """Return the first existing trajectory file in `trial_dir`, or the default `agent/trajectory.json` path."""
    if trial_dir is None:
        return None
    for rel in TRAJECTORY_REL_PATHS:
        cand = trial_dir / rel
        if cand.is_file():
            return cand
    # Only documented rollout filenames in the trial or agent directory.
    for directory in (trial_dir / "agent", trial_dir):
        for cand in sorted(directory.glob("rollout*.jsonl")):
            if cand.is_file() and not cand.is_symlink():
                return cand
    return trial_dir / "agent" / "trajectory.json"


def resolve_verifier_script_path(task_dir: Optional[Path]) -> Optional[Path]:
    """Return the first existing Python verifier script in `task_dir`, or the default `tests/verify.py` path."""
    if task_dir is None:
        return None
    for rel in VERIFIER_SCRIPT_REL_PATHS:
        cand = task_dir / rel
        if cand.is_file():
            return cand
    return task_dir / "tests" / "verify.py"


def extract_trial_reward(
    trial_result: Dict[str, Any],
    trial_dir: Optional[Path],
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Optional[float]:
    reward_txt_path = trial_dir / "verifier" / "reward.txt" if trial_dir else None
    reward_val: Optional[float] = None
    if reward_txt_path and reward_txt_path.is_file():
        try:
            raw_text = reward_txt_path.read_text(encoding="utf-8", errors="replace").strip()
            if raw_text:
                reward_val = float(raw_text.splitlines()[0].strip())
                if not math.isfinite(reward_val):
                    raise ValueError("non-finite reward")
        except Exception as exc:
            # Recovery is correct (the caller falls back to result.json), but an
            # unparseable reward silently changes Gate 0's pass detection, so the
            # downgrade is recorded rather than swallowed.
            reward_val = None
            if diagnostics is not None:
                diagnostics.append(
                    make_diagnostic(
                        stage="runtime_state.extract_trial_reward",
                        code="REWARD_PARSE_FAILED",
                        severity=WARNING,
                        message="Could not parse verifier/reward.txt as a float.",
                        source_ref="art:verifier_reward",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        impact=(
                            "`runtime.reward` falls back to result.json metadata; if that is "
                            "absent the reward stays null and Gate 0 can no longer detect a "
                            "passing trial from the reward value."
                        ),
                    )
                )

    if reward_val is None and isinstance(trial_result, dict):
        vr = trial_result.get("verifier_result")
        if isinstance(vr, dict):
            rewards = vr.get("rewards")
            if isinstance(rewards, dict) and _is_numeric_reward(rewards.get("reward")):
                reward_val = float(rewards["reward"])
            elif _is_numeric_reward(vr.get("reward")):
                reward_val = float(vr["reward"])
        if reward_val is None and _is_numeric_reward(trial_result.get("reward")):
            reward_val = float(trial_result["reward"])

    return reward_val


def _is_numeric_reward(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _safe_int(val: Any) -> int:
    if isinstance(val, bool):
        return 0
    try:
        return int(val)
    except Exception:
        return 0


def _verifier_receipts(trial_dir):
    """Only top-level saved verifier status, never nested review criteria.

    Canonical verifier envelopes take precedence over stale transport stdout.
    """
    receipts = []
    if not trial_dir:
        return receipts
    for rel in ("verifier/verify.json", "verifier/verification.json", "verifier/test-stdout.txt"):
        path = trial_dir / rel
        try:
            with path.open("rb") as handle:
                raw = handle.read(8 * 1024 * 1024 + 1)
        except OSError:
            continue
        if len(raw) > 8 * 1024 * 1024:
            continue
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            report = json.loads(text)
        except ValueError:
            report = None
        report = report if isinstance(report, dict) else {}
        status = str(report.get("status") or "").upper()
        if rel.endswith(".json") and status == "ENV_INVALID":
            return [{"source_file": rel, "source_pointer": "/status", "status": status}]
        final_statuses = {"FAIL", "FAILED", "PASS", "PASSED", "ERROR", "SCORED"}
        if rel.endswith(".json") and status in final_statuses:
            return []
        if status in {"REVIEW_REQUIRED", "REQUIRE_REVIEW"} or re.match(
            r"^(?:REVIEW_REQUIRED|REQUIRE_REVIEW)(?:\s*:|$)", text
        ):
            scoped_reward = {}
            reason = report.get("reason") or report.get("error") or report.get("summary")
            diagnostics = report.get("diagnostics")
            review = report.get("review")
            review_reason = review.get("reason") if isinstance(review, dict) else None
            checks = (diagnostics if isinstance(diagnostics, dict) else report).get("checks")
            all_passed = (
                isinstance(checks, list)
                and bool(checks)
                and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
            )
            review_confirmed = (
                report.get("reward_status") == "PENDING_REVIEW"
                and (checks is None or all_passed)
                or (
                    isinstance(diagnostics, dict)
                    and diagnostics.get("status") == "SCIENTIFIC_REVIEW_REQUIRED"
                    and isinstance(review_reason, str)
                    and all_passed
                )
            )
            if rel.endswith(".json") and review_confirmed:
                scoped_reward = {"reward_scope": "pending_review_placeholder", "reward": 0}
                reason = report.get("review_note") or review_reason or reason
                if isinstance(review_reason, str) and "review-error.json" in review_reason:
                    try:
                        sidecar = path.with_name("review-error.json")
                        if not sidecar.is_symlink() and sidecar.stat().st_size <= 65536:
                            error = json.loads(sidecar.read_text(encoding="utf-8"))
                            if (
                                isinstance(error, dict)
                                and error.get("status") == status
                                and isinstance(error.get("error_code"), str)
                            ):
                                reason = f"{error['error_code']} (review-error.json) · {reason}"
                    except (OSError, ValueError):
                        pass
            if (
                report.get("reward_scope") == COMPLETENESS_REWARD_SCOPE
                and "scientific_score" in report
                and report["scientific_score"] is None
                and _is_numeric_reward(report.get("reward"))
            ):
                scoped_reward = {
                    "reward_scope": COMPLETENESS_REWARD_SCOPE,
                    "reward": report["reward"],
                }
            receipts.append(
                {
                    "source_file": rel,
                    "source_pointer": "/status" if report else "L1",
                    "status": status or "REVIEW_REQUIRED",
                    "reason": str(reason or "")[:500] if report else text[:500],
                    **scoped_reward,
                }
            )
            if rel.endswith(".json"):
                break
    return receipts


def compute_runtime_state(
    job_result: Optional[Dict[str, Any]],
    trial_result: Optional[Dict[str, Any]],
    trial_dir: Optional[Path],
) -> Dict[str, Any]:
    job_res = job_result if isinstance(job_result, dict) else {}
    trial_res = trial_result if isinstance(trial_result, dict) else {}
    diagnostics: List[Dict[str, Any]] = []

    traj_exists = bool(
        trial_dir and any((trial_dir / rel).is_file() for rel in TRAJECTORY_REL_PATHS)
    )
    claude_txt_exists = bool(
        trial_dir
        and any(
            (trial_dir / rel).is_file() and (trial_dir / rel).stat().st_size > 0
            for rel in (
                "agent/claude-code.txt",
                "agent/codex.txt",
                "agent/agent.log",
                "agent/stdout.txt",
                "agent/output.log",
            )
        )
    )
    verify_log_exists = bool(
        trial_dir
        and any(
            (trial_dir / rel).is_file()
            for rel in (
                "verifier/verify.log",
                "verifier/pytest.log",
                "verifier/verifier.log",
                "verify.log",
            )
        )
    )
    test_stdout_exists = bool(
        trial_dir
        and (trial_dir / "verifier" / "test-stdout.txt").is_file()
        and (trial_dir / "verifier" / "test-stdout.txt").stat().st_size > 0
    )
    reward_txt_exists = bool(
        trial_dir
        and (
            (trial_dir / "verifier" / "reward.txt").is_file()
            or (trial_dir / "reward.txt").is_file()
        )
    )
    exception_txt_exists = bool(trial_dir and (trial_dir / "exception.txt").is_file())

    agent_exec_stage = trial_res.get("agent_execution") or {}
    verifier_stage = trial_res.get("verifier") or {}
    env_setup_stage = trial_res.get("environment_setup") or {}
    agent_setup_stage = trial_res.get("agent_setup") or {}

    agent_started = bool(
        traj_exists
        or claude_txt_exists
        or (isinstance(agent_exec_stage, dict) and agent_exec_stage.get("started_at"))
        or trial_res.get("agent_result") is not None
    )
    verifier_started = bool(
        verify_log_exists
        or test_stdout_exists
        or reward_txt_exists
        or (isinstance(verifier_stage, dict) and verifier_stage.get("started_at"))
        or trial_res.get("verifier_result") is not None
    )

    stats = job_res.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    job_abort_without_trial_dir = bool(
        trial_dir is None and _safe_int(stats.get("n_errored_trials", 0)) > 0
    )

    verifier_result = trial_res.get("verifier_result")
    if not isinstance(verifier_result, dict):
        verifier_result = {}
    raw_metrics = verifier_result.get("rewards")
    reward_metrics = (
        {str(k): v for k, v in list(raw_metrics.items())[:32] if _is_numeric_reward(v)}
        if isinstance(raw_metrics, dict)
        else {}
    )
    reward_val = extract_trial_reward(trial_res, trial_dir, diagnostics)
    # When Harbor saved only a named submission metric, reward.txt is diagnostic
    # output. A conventional scalar remains valid for jobs without that metric.
    submission_only = (
        "submission_checks" in reward_metrics
        and not _is_numeric_reward(raw_metrics.get("reward"))
        and not _is_numeric_reward(verifier_result.get("reward"))
        and not _is_numeric_reward(trial_res.get("reward"))
    )
    if submission_only:
        reward_val = None
    exc_info = trial_res.get("exception_info")
    has_trial_exception = bool(
        exc_info is not None or exception_txt_exists or job_abort_without_trial_dir
    )

    receipts = _verifier_receipts(trial_dir)
    invalid_evaluation = bool(receipts and receipts[0]["status"] == "ENV_INVALID")
    review_receipts = [] if invalid_evaluation else receipts
    reward_scope = "submission_checks_only" if submission_only else None
    if invalid_evaluation and reward_val in (None, 0):
        reward_scope = "invalid_evaluation"
        if reward_val is not None:
            reward_metrics.setdefault("reward", reward_val)
        reward_val = None
    if review_receipts:
        receipt = review_receipts[0]
        scoped_value = receipt.get("reward")
        if scoped_value is not None and reward_val in (None, scoped_value):
            reward_scope = receipt["reward_scope"]
            if reward_val is not None or receipt["reward_scope"] == COMPLETENESS_REWARD_SCOPE:
                reward_metrics.setdefault("reward", scoped_value)
            reward_val = None
    pending_review = bool(review_receipts and reward_val is None)
    verifier_started = verifier_started or bool(receipts)
    state_conflicts: List[str] = []
    if invalid_evaluation and reward_val is not None:
        state_conflicts.append("ENV_INVALID receipt conflicts with a nonzero recorded reward.")
    if review_receipts and reward_val is not None:
        state_conflicts.append(
            "REVIEW_REQUIRED receipt conflicts with a numeric reward; preserve the recorded scored outcome for review."
        )
    if reward_val is not None and reward_val >= 1.0 and has_trial_exception:
        state_conflicts.append(
            "Trial has reward >= 1.0 (passed verification) alongside a trial exception record."
        )
    if verifier_started and not agent_started:
        state_conflicts.append(
            "Verifier artifacts exist while agent_started was inferred as False."
        )

    # Verification status strictly reflects trial verification outcome
    if pending_review:
        verification_status = "pending"
    elif reward_scope == "invalid_evaluation":
        verification_status = "errored"
    elif reward_val is not None and reward_val >= 1.0:
        verification_status = "passed"
    elif reward_val is not None and reward_val < 1.0:
        verification_status = "failed"
    elif submission_only and reward_metrics["submission_checks"] == 0:
        verification_status = "failed"
    elif verifier_started and has_trial_exception:
        verification_status = "errored"
    elif verifier_started and verify_log_exists:
        verification_status = "failed"
    else:
        verification_status = "not_run"

    # Execution status reflects runner/agent execution lifecycle for THIS trial only
    if pending_review:
        agent_exception = isinstance(exc_info, dict) and exc_info.get("exception_type") not in (
            None,
            "RewardFileNotFoundError",
        )
        execution_status = "errored" if agent_exception else "completed"
        verdict = "pending"
        exit_status = "pending"
    elif reward_scope == "invalid_evaluation":
        execution_status = "errored"
        verdict = exit_status = "errored"
    elif verification_status == "passed":
        execution_status = "completed"
        verdict = "passed"
        exit_status = "completed"
    elif has_trial_exception and verification_status in ("not_run", "errored"):
        execution_status = "errored"
        verdict = "errored"
        exit_status = "errored"
    elif not agent_started and not verifier_started:
        execution_status = "not_started"
        verdict = "errored"
        exit_status = "errored" if has_trial_exception else "unknown"
    elif verification_status == "failed":
        execution_status = "errored" if has_trial_exception else "completed"
        verdict = "failed"
        exit_status = "errored" if has_trial_exception else "failed"
    else:
        execution_status = "unknown"
        verdict = "unknown"
        exit_status = "unknown"

    stats = job_res.get("stats") or {}
    if not isinstance(stats, dict):
        stats = {}

    return {
        "started_at": trial_res.get("started_at")
        or (agent_exec_stage.get("started_at") if isinstance(agent_exec_stage, dict) else None)
        or job_res.get("started_at"),
        "finished_at": trial_res.get("finished_at")
        or (verifier_stage.get("finished_at") if isinstance(verifier_stage, dict) else None)
        or job_res.get("finished_at"),
        "execution_status": execution_status,
        "verification_status": verification_status,
        "verdict": verdict,
        "exit_status": exit_status,  # Deprecated alias kept for schema compatibility
        "reward": reward_val,
        **({"reward_scope": reward_scope} if reward_scope else {}),
        "reward_metrics": reward_metrics,
        "diagnostics": diagnostics,
        "agent_started": agent_started,
        "verifier_started": verifier_started,
        "score_produced": reward_val is not None,
        "review_required": bool(review_receipts),
        "review_receipts": review_receipts,
        "has_trial_exception": has_trial_exception,
        "state_conflicts": state_conflicts,
        "stages": {
            "environment_setup": env_setup_stage if isinstance(env_setup_stage, dict) else {},
            "agent_setup": agent_setup_stage if isinstance(agent_setup_stage, dict) else {},
            "agent_execution": agent_exec_stage if isinstance(agent_exec_stage, dict) else {},
            "verifier": verifier_stage if isinstance(verifier_stage, dict) else {},
        },
        "job_stats": {
            "n_total_trials": job_res.get("n_total_trials") or stats.get("n_total_trials"),
            "n_errored_trials": stats.get("n_errored_trials", 0),
        },
    }
