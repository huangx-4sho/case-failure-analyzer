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
            if isinstance(rewards, dict) and isinstance(rewards.get("reward"), (int, float)):
                reward_val = float(rewards["reward"])
            elif isinstance(vr.get("reward"), (int, float)):
                reward_val = float(vr["reward"])
        if reward_val is None and isinstance(trial_result.get("reward"), (int, float)):
            reward_val = float(trial_result["reward"])

    return reward_val


def _safe_int(val: Any) -> int:
    if isinstance(val, bool):
        return 0
    try:
        return int(val)
    except Exception:
        return 0


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

    reward_val = extract_trial_reward(trial_res, trial_dir, diagnostics)
    exc_info = trial_res.get("exception_info")
    has_trial_exception = bool(
        exc_info is not None or exception_txt_exists or job_abort_without_trial_dir
    )

    state_conflicts: List[str] = []
    if reward_val is not None and reward_val >= 1.0 and has_trial_exception:
        state_conflicts.append(
            "Trial has reward >= 1.0 (passed verification) alongside a trial exception record."
        )
    if verifier_started and not agent_started:
        state_conflicts.append(
            "Verifier artifacts exist while agent_started was inferred as False."
        )

    # Verification status strictly reflects trial verification outcome
    if reward_val is not None and reward_val >= 1.0:
        verification_status = "passed"
    elif reward_val is not None and reward_val < 1.0:
        verification_status = "failed"
    elif verifier_started and verify_log_exists:
        verification_status = "failed"
    else:
        verification_status = "not_run"

    # Execution status reflects runner/agent execution lifecycle for THIS trial only
    if verification_status == "passed":
        execution_status = "completed"
        verdict = "passed"
        exit_status = "completed"
    elif has_trial_exception and verification_status == "not_run":
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
        "diagnostics": diagnostics,
        "agent_started": agent_started,
        "verifier_started": verifier_started,
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
