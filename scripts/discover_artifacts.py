#!/usr/bin/env python3
"""
Phase 1: Artifact Discovery (`scripts/discover_artifacts.py`)

Discovers job-level, trial-level, and task-level artifacts, computes SHA256
hashes, parses JSON/TOML metadata, determines whether the agent and verifier
actually started, and lists missing artifacts without failing on partial runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_state import (
    compute_runtime_state,
    resolve_trajectory_path,
    resolve_verifier_script_path,
)


_SHA256_CACHE: Dict[Tuple[str, int, int, Optional[int]], str] = {}
_JSON_CACHE: Dict[Tuple[str, int, int], Optional[Dict[str, Any]]] = {}


def extract_declared_deliverables(task_toml: Optional[Dict[str, Any]]) -> List[str]:
    """
    Declared deliverable paths from a harbor `task.toml` `artifacts` array.

    Harbor collects these container paths (`docker cp`) after the agent phase and
    before verification, so they are the task's authoritative deliverable set -
    unlike a hardcoded `results.json`, which many harbor tasks never produce.

    Returns the paths exactly as declared (e.g. `["/app/final"]`). A missing,
    unreadable, or empty `artifacts` declaration yields `[]`, which callers read
    as "declared deliverables unknown" and fall back to legacy heuristics.
    """
    if not isinstance(task_toml, dict):
        return []
    raw = task_toml.get("artifacts")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    declared: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            cleaned = item.strip()
            if cleaned not in declared:
                declared.append(cleaned)
    return declared


def compute_sha256(path: Path, max_bytes: Optional[int] = None) -> str:
    try:
        st = path.stat()
        cache_key = (str(path.resolve()), st.st_mtime_ns, st.st_size, max_bytes)
        if cache_key in _SHA256_CACHE:
            return _SHA256_CACHE[cache_key]
    except OSError:
        cache_key = None

    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = max_bytes
        while True:
            chunk_size = 65536 if remaining is None else min(65536, remaining)
            if chunk_size <= 0:
                break
            data = f.read(chunk_size)
            if not data:
                break
            h.update(data)
            if remaining is not None:
                remaining -= len(data)
    digest = f"sha256:{h.hexdigest()}"
    if cache_key is not None:
        _SHA256_CACHE[cache_key] = digest
    return digest


def safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        st = path.stat()
        cache_key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
        if cache_key in _JSON_CACHE:
            return _JSON_CACHE[cache_key]
    except OSError:
        cache_key = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
            parsed: Optional[Dict[str, Any]] = data if isinstance(data, dict) else {"_value": data}
    except Exception as exc:
        parsed = {"_parse_error": str(exc)}
    if cache_key is not None:
        _JSON_CACHE[cache_key] = parsed
    return parsed


def safe_load_toml(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        import tomllib  # Python 3.11+

        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        # Minimal fallback key=value parser
        result: Dict[str, Any] = {}
        try:
            section = result
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    sec_name = line[1:-1].strip()
                    section = result.setdefault(sec_name, {})
                elif "=" in line:
                    k, v = line.split("=", 1)
                    section[k.strip()] = v.strip().strip("'\"")
        except Exception as exc:
            return {"_parse_error": str(exc)}
        return result


def is_trial_dir(path: Path) -> bool:
    """Determine if a directory is a trial directory inside a job."""
    if not path.is_dir() or path.name.startswith("."):
        return False
    # A job directory containing job.log is a job root, not a single trial directory
    if (path / "job.log").is_file():
        return False
    res = safe_load_json(path / "result.json")
    # If result.json has job-level keys like stats or n_total_trials without trial_name, it's a job dir
    if res and ("n_total_trials" in res or "stats" in res) and "trial_name" not in res:
        return False
    # If any immediate subdirectory has its own trial.log or trial_name in result.json, `path` is a job root
    for child in path.iterdir():
        if child.is_dir() and child.name not in (
            "agent",
            "verifier",
            "artifacts",
            "environment",
            "tests",
        ):
            child_res = safe_load_json(child / "result.json")
            if (child / "trial.log").is_file() or (child_res and "trial_name" in child_res):
                return False
    if res and ("trial_name" in res or "agent_execution" in res):
        return True
    trial_markers = ("trial.log", "agent", "verifier", "exception.txt")
    if any((path / m).exists() for m in trial_markers):
        return True
    return False


def discover_trials(job_path: Path, trial_filter: str = "all") -> List[Path]:
    """Return sorted list of trial directories for a given job_path."""
    if is_trial_dir(job_path):
        return [job_path]
    trials: List[Path] = []
    if job_path.is_dir():
        for child in sorted(job_path.iterdir()):
            if is_trial_dir(child):
                if trial_filter in ("all", child.name):
                    trials.append(child)
    return trials


def make_artifact_entry(
    artifact_id: str,
    base_dir: Path,
    rel_path: str,
    scope: str,
) -> Dict[str, Any]:
    full_path = base_dir / rel_path
    exists = full_path.is_file()
    entry: Dict[str, Any] = {
        "artifact_id": artifact_id,
        "rel_path": rel_path,
        "abs_path": str(full_path.resolve()) if exists else str(full_path),
        "scope": scope,
        "exists": exists,
        "size_bytes": full_path.stat().st_size if exists else 0,
        "sha256": compute_sha256(full_path) if exists else None,
    }
    return entry


def build_trial_inventory(
    job_dir: Path,
    task_dir: Optional[Path],
    trial_dir: Optional[Path],
    include_session_files: bool = False,
) -> Dict[str, Any]:
    artifacts: List[Dict[str, Any]] = []
    missing_artifacts: List[str] = []

    # 1. Job-level files
    job_files = [
        ("art:job_config", "config.json"),
        ("art:job_log", "job.log"),
        ("art:job_lock", "lock.json"),
        ("art:job_result", "result.json"),
    ]
    for aid, rel in job_files:
        entry = make_artifact_entry(aid, job_dir, rel, "job")
        artifacts.append(entry)
        if not entry["exists"]:
            missing_artifacts.append(f"job:{rel}")

    # 2. Trial-level files
    resolved_traj = resolve_trajectory_path(trial_dir)
    traj_rel = (
        str(resolved_traj.relative_to(trial_dir))
        if (trial_dir and resolved_traj and resolved_traj.is_file())
        else "agent/trajectory.json"
    )
    trial_files = [
        ("art:trial_config", "config.json"),
        ("art:trial_lock", "lock.json"),
        ("art:trial_result", "result.json"),
        ("art:exception_txt", "exception.txt"),
        ("art:trial_log", "trial.log"),
        ("art:manifest_json", "artifacts/manifest.json"),
        ("art:trajectory_json", traj_rel),
        ("art:claude_code_txt", "agent/claude-code.txt"),
        ("art:verify_log", "verifier/verify.log"),
        ("art:test_stdout", "verifier/test-stdout.txt"),
        ("art:reward_txt", "verifier/reward.txt"),
    ]
    if trial_dir is not None and trial_dir.is_dir():
        for aid, rel in trial_files:
            entry = make_artifact_entry(aid, trial_dir, rel, "trial")
            artifacts.append(entry)
            # Note: exception.txt is only expected on exceptions; others are tracked if missing
            if not entry["exists"] and rel != "exception.txt":
                missing_artifacts.append(rel)

        if include_session_files:
            sessions_dir = trial_dir / "agent" / "sessions"
            if sessions_dir.is_dir():
                for idx, sf in enumerate(sorted(sessions_dir.rglob("*"))):
                    if sf.is_file():
                        rel = str(sf.relative_to(trial_dir))
                        artifacts.append(
                            make_artifact_entry(f"art:session_{idx}", trial_dir, rel, "trial")
                        )
    else:
        for _, rel in trial_files:
            if rel != "exception.txt":
                missing_artifacts.append(rel)

    # 3. Auto-resolve task_dir from config/result if not supplied
    job_result = safe_load_json(job_dir / "result.json") or {}
    trial_result = (
        safe_load_json(trial_dir / "result.json") if (trial_dir and trial_dir.is_dir()) else None
    ) or {}
    if task_dir is None:
        raw_task_id = trial_result.get("task_id")
        raw_config = trial_result.get("config")
        raw_config_task = raw_config.get("task") if isinstance(raw_config, dict) else None
        task_rel = (raw_task_id.get("path") if isinstance(raw_task_id, dict) else None) or (
            raw_config_task.get("path") if isinstance(raw_config_task, dict) else None
        )
        if task_rel:
            candidate = job_dir.parent.parent / task_rel
            if candidate.is_dir():
                task_dir = candidate

    # 4. Task-level files
    if task_dir is not None and task_dir.is_dir():
        resolved_verify = resolve_verifier_script_path(task_dir)
        verify_rel = (
            str(resolved_verify.relative_to(task_dir))
            if (resolved_verify and resolved_verify.is_relative_to(task_dir))
            else "tests/verify.py"
        )
        task_files = [
            ("art:instruction_md", "instruction.md"),
            ("art:task_toml", "task.toml"),
            ("art:dockerfile", "environment/Dockerfile"),
            ("art:verify_py", verify_rel),
            ("art:test_sh", "tests/test.sh"),
            ("art:refs_json", "tests/refs.json"),
        ]
        for aid, rel in task_files:
            entry = make_artifact_entry(aid, task_dir, rel, "task")
            artifacts.append(entry)
            if not entry["exists"]:
                missing_artifacts.append(f"task:{rel}")

        assets_dir = task_dir / "environment" / "assets"
        if assets_dir.is_dir():
            for idx, af in enumerate(sorted(assets_dir.rglob("*"))):
                if af.is_file() and not af.name.startswith("."):
                    rel = str(af.relative_to(task_dir))
                    artifacts.append(make_artifact_entry(f"art:asset_{idx}", task_dir, rel, "task"))

    # 5. Determine runtime status and stage gates via shared runtime_state module
    rt_state = compute_runtime_state(
        job_result=job_result,
        trial_result=trial_result,
        trial_dir=trial_dir,
    )
    job_stats = rt_state.pop("job_stats", {})

    case_id = trial_result.get("task_name") or (task_dir.name if task_dir else None) or job_dir.name
    trial_name = trial_result.get("trial_name") or (trial_dir.name if trial_dir else job_dir.name)

    return {
        "case_id": case_id,
        "trial_name": trial_name,
        "job_dir": str(job_dir.resolve()),
        "trial_dir": str(trial_dir.resolve()) if trial_dir else None,
        "task_dir": str(task_dir.resolve()) if task_dir and task_dir.is_dir() else None,
        "artifacts": artifacts,
        "runtime": rt_state,
        "metadata": {
            "job_stats": job_stats,
            "job_result_summary": job_stats,
            "agent_info": trial_result.get("agent_info"),
            "task_toml": safe_load_toml(task_dir / "task.toml")
            if (task_dir and task_dir.is_dir())
            else None,
        },
        "missing_artifacts": missing_artifacts,
    }


def discover_all(
    job_path: Path,
    task_path: Optional[Path] = None,
    trial_filter: str = "all",
    include_session_files: bool = False,
) -> Dict[str, Any]:
    trials = discover_trials(job_path, trial_filter=trial_filter)
    job_dir = job_path.parent if is_trial_dir(job_path) else job_path
    if not trials:
        # Job-level failure before any trial directory was created
        inv = build_trial_inventory(
            job_dir=job_dir,
            task_dir=task_path,
            trial_dir=None,
            include_session_files=include_session_files,
        )
        return {
            "job_dir": str(job_dir.resolve()),
            "task_dir": inv.get("task_dir"),
            "trials": [inv],
        }

    trial_inventories = [
        build_trial_inventory(
            job_dir=job_dir,
            task_dir=task_path,
            trial_dir=t,
            include_session_files=include_session_files,
        )
        for t in trials
    ]
    resolved_task_dir = next(
        (inv["task_dir"] for inv in trial_inventories if inv.get("task_dir")), None
    )
    return {
        "job_dir": str(job_dir.resolve()),
        "task_dir": resolved_task_dir,
        "trials": trial_inventories,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover job, trial, and task artifacts.")
    parser.add_argument("--job", required=True, type=Path, help="Path to job or trial directory")
    parser.add_argument(
        "--task", required=False, type=Path, default=None, help="Path to task directory"
    )
    parser.add_argument("--trial", default="all", help="Trial name or 'all'")
    parser.add_argument(
        "--include-session-files", action="store_true", help="Include agent session files"
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON file path")
    args = parser.parse_args()

    if not args.job.exists():
        print(f"ERROR: --job path does not exist: {args.job}", file=sys.stderr)
        sys.exit(2)
    if args.task is not None and not args.task.exists():
        print(f"ERROR: --task path does not exist: {args.task}", file=sys.stderr)
        sys.exit(2)

    result = discover_all(
        job_path=args.job,
        task_path=args.task,
        trial_filter=args.trial,
        include_session_files=args.include_session_files,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
