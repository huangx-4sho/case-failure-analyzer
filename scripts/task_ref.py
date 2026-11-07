#!/usr/bin/env python3
"""
Task-ID invocation resolver (`scripts/task_ref.py`)

Turns a short task reference such as `task208`, `208`, `task 208`, or a full
trial name `task 208__jaekojA` into the concrete harbor directories needed by
`analyze_case.py`: the trial directory, its job directory, and the task
definition directory (`task.toml`). It also derives the default report landing
directory `<task definition dir>/failure-report`.

The resolver is deliberately read-only and conservative:

- It never scans outside the supplied jobs root.
- It only accepts directory names whose numeric task part matches exactly
  (so `task208` cannot silently resolve to `task2080`).
- When a task has several trials (retries / regrades), the newest one by
  `result.json` modification time wins, and all candidates are returned so the
  caller can disclose the choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

#: Canonical report directory name created at the benchmark root.
REPORT_DIR_NAME = "failure-report"

_TASK_REF_RE = re.compile(r"^\s*(?:task[\s_-]*)?0*(\d+)\s*$", re.IGNORECASE)
_TRIAL_DIR_RE = re.compile(r"^task[\s_-]*0*(\d+)(?:__|[-_])", re.IGNORECASE)
_TASK_DEF_DIR_RE = re.compile(r"^task[\s_-]*0*(\d+)\s*$", re.IGNORECASE)
#: Directories that are never descended into when searching for task folders.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "artifacts",
    "verifier",
    "agent",
    "failure-report",
    ".dev",
    "failure-analysis",  # legacy dev-output root, kept for older checkouts
}


class TaskRefError(ValueError):
    """Raised when a task reference cannot be resolved unambiguously."""


@dataclass(frozen=True)
class TaskRef:
    """Resolved task reference. All paths are absolute."""

    raw: str
    task_id: str  # canonical label, e.g. "task208"
    number: str  # numeric part, e.g. "208"
    report_basename: str  # e.g. "task208"
    trial_dir: Path
    job_dir: Path
    task_def_dir: Optional[Path]
    bench_root: Path  # benchmark checkout holding the case folders
    report_root: Path  # <bench_root>/failure-report (one subfolder per task)
    report_dir: Path  # <bench_root>/failure-report/<task_id>
    candidates: List[Path]


def _natural_sort_key(s: str) -> tuple:
    parts = re.split(r"(\d+)", str(s or ""))
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def discover_all_task_ids(jobs_root: Path) -> List[str]:
    """
    Find all distinct tasks that have at least one trial directory under jobs_root.

    Supports both Harbor multi-suite layout (e.g. `29-metals-mlp/jobs/<ts>/task 194__xxx`)
    and flat `jobs/` layout. Returns normalized task IDs sorted in natural numeric order
    (e.g. `['task98', 'task189', 'task190']`).
    """
    root = Path(jobs_root).resolve()
    if not root.is_dir():
        return []

    discovered: Set[str] = set()
    for path, _depth in _walk_dirs(root, max_depth=8):
        if not _is_trial_dir(path):
            continue
        m = _TRIAL_DIR_RE.match(path.name)
        if m:
            discovered.add(f"task{int(m.group(1))}")
        else:
            m2 = _TASK_DEF_DIR_RE.match(path.name)
            if m2:
                discovered.add(f"task{int(m2.group(1))}")

    return sorted(discovered, key=_natural_sort_key)


def normalize_task_ref(raw: str) -> str:
    """
    Normalize a task reference to its canonical `task<number>` label.

    Accepts `task208`, `208`, `task 208`, `Task-208`, and a full harbor trial
    name such as `task 208__jaekojA` (the suffix is ignored for the label).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TaskRefError("task reference must be a non-empty string")
    text = raw.strip()
    m = _TASK_REF_RE.match(text)
    if m:
        number = str(int(m.group(1)))
    else:
        m = _TRIAL_DIR_RE.match(text)
        if not m:
            raise TaskRefError(
                f"cannot parse task reference {raw!r}; expected e.g. 'task208', '208', "
                "or a harbor trial name like 'task 208__jaekojA'"
            )
        number = str(int(m.group(1)))
    return f"task{number}"


def _trial_number(name: str, number: str) -> bool:
    m = _TRIAL_DIR_RE.match(name)
    return bool(m and str(int(m.group(1))) == number)


def _task_def_number(name: str, number: str) -> bool:
    m = _TASK_DEF_DIR_RE.match(name)
    return bool(m and str(int(m.group(1))) == number)


def _walk_dirs(root: Path, max_depth: int = 6):
    """Yield directories under root up to max_depth, pruning known heavy trees."""
    root = root.resolve()
    stack: List[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            yield child, depth + 1
            if child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            stack.append((child, depth + 1))


def _is_trial_dir(path: Path) -> bool:
    return (path / "result.json").is_file() or (path / "trial.log").is_file()


def _is_task_def_dir(path: Path) -> bool:
    return (path / "task.toml").is_file()


def _fast_candidates(root: Path, number: str) -> tuple[List[Path], List[Path]]:
    """
    Fixed-depth globs for the common harbor layout. Avoids walking every sibling
    repository when the caller passes a broad `--jobs-root` (e.g. a workspace
    root). Returns `(trial_dirs, task_def_dirs)`; empty lists mean "no fast hit".
    """
    trial_globs = (
        f"*/jobs/*/task {number}__*",
        f"*/jobs/*/task{number}__*",
        f"*/jobs/*/task {number}-*",
        f"*/jobs/*/task{number}-*",
        f"*/*/jobs/*/task {number}__*",
        f"*/*/jobs/*/task{number}__*",
        f"jobs/*/task {number}__*",
        f"*/task {number}__*",
    )
    task_def_globs = (
        f"*/task {number}",
        f"*/task{number}",
        f"*/*/task {number}",
        f"*/*/task{number}",
        f"task {number}",
    )
    trials: List[Path] = []
    task_defs: List[Path] = []
    for pattern in trial_globs:
        for path in root.glob(pattern):
            if path.is_dir() and not path.is_symlink() and _trial_number(path.name, number):
                if _is_trial_dir(path):
                    trials.append(path.resolve())
    for pattern in task_def_globs:
        for path in root.glob(pattern):
            if path.is_dir() and not path.is_symlink() and _task_def_number(path.name, number):
                if _is_task_def_dir(path):
                    task_defs.append(path.resolve())
    return list(dict.fromkeys(trials)), list(dict.fromkeys(task_defs))


def _count_task_defs(path: Path, limit: int = 2) -> int:
    """Count `task*` definition folders directly under `path` (early exit at `limit`)."""
    count = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        if child.is_dir() and not child.is_symlink() and (child / "task.toml").is_file():
            count += 1
            if count >= limit:
                break
    return count


def derive_bench_root(jobs_root: Path, trial_dir: Path, case_root: Path) -> Path:
    """
    Find the benchmark checkout that owns `failure-report`.

    Three layouts are supported:

    - `bench/caseA/task 207/` + `bench/caseA/jobs/...` (CompChemWorld style):
      the report belongs at `bench/failure-report`.
    - `bench/task 207/` + `bench/jobs/...` (flat style): the report belongs at
      `bench/failure-report`.
    - `--jobs-root` pointing straight at a `jobs/` tree: resolved from the
      trial path instead of the argument.
    """
    root = Path(jobs_root).resolve()
    trial = Path(trial_dir).resolve()

    # A `--jobs-root` that does not contain the resolved trial cannot describe
    # this checkout; trust the trial's own ancestry instead.
    try:
        trial.relative_to(root)
    except ValueError:
        root = Path(case_root).resolve()

    # Prefer the argument when it already looks like a checkout root.
    if (root / "jobs").is_dir():
        # A case folder: one level up only when it groups several task defs.
        return (root.parent if _count_task_defs(root) >= 2 else root).resolve()
    if root.name == "jobs":
        case = root.parent
        return (case.parent if _count_task_defs(case) >= 2 else case).resolve()
    if _count_task_defs(root) >= 2:
        return root

    # Unknown layout: fall back to the trial's own ancestry (<case>/jobs/<job>/<trial>).
    case = Path(case_root).resolve()
    return (case.parent if _count_task_defs(case) >= 2 else case).resolve()


def resolve_task_ref(
    raw: str,
    jobs_root: Path,
    report_dir_override: Optional[Path] = None,
) -> TaskRef:
    """
    Resolve `raw` under `jobs_root` into a `TaskRef`.

    Search order is deterministic: task definition folders are collected first
    (to anchor the report directory), then trial directories. Trials are ranked
    by `result.json` mtime (falling back to directory mtime), newest first.
    """
    task_id = normalize_task_ref(raw)
    number = task_id[4:]
    root = Path(jobs_root).resolve()
    if not root.is_dir():
        raise TaskRefError(f"jobs root does not exist or is not a directory: {root}")

    # Optional explicit trial suffix: `task 208__jaekojA` selects that trial.
    explicit_trial = "__" in raw
    explicit_norm = re.sub(r"[\s_-]", "", raw.strip().lower()) if explicit_trial else ""

    fast_trials, fast_task_defs = _fast_candidates(root, number)
    task_defs: List[Path] = []
    trials: List[Path] = []
    if fast_trials and fast_task_defs:
        # Fast path hit both sides; no broad walk needed.
        task_defs = list(fast_task_defs)
        if explicit_trial:
            trials = [
                p for p in fast_trials if re.sub(r"[\s_-]", "", p.name.lower()) == explicit_norm
            ]
        else:
            trials = list(fast_trials)
    if not trials:
        task_defs = list(fast_task_defs)
        trials = []
        for path, _depth in _walk_dirs(root):
            name = path.name
            if _task_def_number(name, number) and _is_task_def_dir(path):
                if path.resolve() not in task_defs:
                    task_defs.append(path.resolve())
            if _trial_number(name, number) and _is_trial_dir(path):
                if explicit_trial and re.sub(r"[\s_-]", "", name.lower()) != explicit_norm:
                    continue
                if path.resolve() not in trials:
                    trials.append(path.resolve())

    if not trials:
        raise TaskRefError(
            f"no harbor trial directory for {task_id} found under {root}. "
            "Pass --jobs-root pointing at the benchmark checkout that contains "
            "the case folders' jobs/ trees."
        )

    def _rank(p: Path) -> tuple[float, str]:
        res = p / "result.json"
        target = res if res.is_file() else p
        try:
            mtime = target.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (mtime, str(p))

    ranked = sorted(trials, key=_rank, reverse=True)
    chosen = ranked[0]

    # A trial's job dir is the directory holding trial.log / job.log above it.
    job_dir = chosen.parent.resolve()

    # The folder that hosts the jobs/ tree; usually the case folder that also
    # holds this task's definition.
    case_root = job_dir.parent.parent if job_dir.parent.name == "jobs" else job_dir.parent

    # Prefer a task definition folder whose parent is the case root containing
    # this job tree; fall back to any matching definition folder.
    task_def_dir: Optional[Path] = None
    if task_defs:
        for cand in sorted(task_defs, key=str):
            if cand.parent == case_root:
                task_def_dir = cand
                break
        if task_def_dir is None:
            task_def_dir = sorted(task_defs, key=str)[0]

    bench_root = derive_bench_root(root, chosen, case_root)
    report_root = (bench_root / REPORT_DIR_NAME).resolve()
    if report_dir_override is not None:
        # An explicit --report-dir is used verbatim as the report folder.
        report_dir = Path(report_dir_override).resolve()
    else:
        # One folder per task keeps `failure-report/` browsable: the bench level
        # shows task folders only, never a flat pile of per-trial artifacts.
        report_dir = (report_root / task_id).resolve()

    return TaskRef(
        raw=raw,
        task_id=task_id,
        number=number,
        report_basename=task_id,
        trial_dir=chosen,
        job_dir=job_dir,
        task_def_dir=task_def_dir,
        bench_root=bench_root,
        report_root=report_root,
        report_dir=report_dir,
        candidates=ranked,
    )
