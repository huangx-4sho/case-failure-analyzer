#!/usr/bin/env python3
"""
Case Failure Analyzer Unified CLI (`scripts/cfa.py`)

A thin, disciplined entry point providing three focused subcommands:
1. `audit`: Normalizes arguments and delegates to `scripts/analyze_case.py`.
2. `inspect`: Strictly read-only inspection of task findings with automatic redaction
   (home directory paths, API tokens, passwords).
3. `index`: Full atomic recomputation and file locking of `<bench_root>/failure-report/INDEX.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from render_index import update_failure_report_index
from task_ref import REPORT_DIR_NAME, resolve_task_ref


_KEY_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[REDACTED_KEY]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[REDACTED_KEY]"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "[REDACTED_TOKEN]"),
    (
        re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]+['\"]"),
        r'\1: "[REDACTED]"',
    ),
]


def redact_sensitive(text: str) -> str:
    """Mask user home paths, API keys, and access tokens for safe inspection."""
    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")
    for pattern, repl in _KEY_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def cmd_audit(argv: List[str]) -> int:
    """Delegate to analyze_case.py with normalized task-id positional arguments."""
    import analyze_case

    # Map positional task_id (e.g. `cfa audit task208`) to `--task-id task208`
    forwarded: List[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if i == 0 and not arg.startswith("-") and not forwarded:
            forwarded.extend(["--task-id", arg])
        else:
            forwarded.append(arg)
        i += 1

    orig_argv = sys.argv
    sys.argv = [sys.argv[0]] + forwarded
    try:
        analyze_case.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv = orig_argv


def _find_benchmark_roots(explicit_root: Optional[Path] = None) -> List[Path]:
    if explicit_root:
        return [Path(explicit_root).resolve()]
    env_root = os.environ.get("CFA_JOBS_ROOT")
    if env_root:
        return [Path(env_root).resolve()]
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        if (p / REPORT_DIR_NAME).is_dir() or (p / ".git").is_dir():
            return [p]
        try:
            if any(c.is_dir() and re.match(r"^\d{2}-", c.name) for c in p.iterdir()):
                return [p]
        except (OSError, PermissionError):
            continue
    return []


def cmd_inspect(args: argparse.Namespace) -> int:
    """Strictly read-only inspection of a case's verdict card or evidence."""
    roots = _find_benchmark_roots(args.jobs_root)
    if not roots:
        print(
            f"ERROR: could not locate benchmark jobs or failure-report root from '{Path.cwd()}'. "
            "Please specify --jobs-root or run within a benchmark repository.",
            file=sys.stderr,
        )
        return 2

    task_ref = None
    last_error = None
    for r in roots:
        try:
            task_ref = resolve_task_ref(args.task_id, r, report_dir_override=args.report_dir)
            break
        except Exception as exc:
            last_error = exc

    if task_ref is None:
        print(f"ERROR: could not resolve task '{args.task_id}': {last_error}", file=sys.stderr)
        return 2

    report_dir = task_ref.report_dir
    if not report_dir.is_dir():
        print(
            f"ERROR: report directory not found: {report_dir}. Run 'cfa audit {args.task_id}' first.",
            file=sys.stderr,
        )
        return 1

    # Locate summary or report file
    section = args.section
    output_text = ""

    if section == "verdict":
        sum_md = report_dir / "summary.md"
        if not sum_md.is_file():
            sum_md = report_dir / f"{task_ref.report_basename}.summary.md"
        if sum_md.is_file():
            output_text = sum_md.read_text(encoding="utf-8")
        else:
            output_text = f"(No summary.md found under {report_dir})"
    elif section == "report":
        rep_file = report_dir / f"{task_ref.report_basename}.md"
        if not rep_file.is_file():
            rep_file = report_dir / "report.md"
        if rep_file.is_file():
            output_text = rep_file.read_text(encoding="utf-8")
        else:
            output_text = f"(No report.md found under {report_dir})"
    elif section in ("data", "evidence"):
        ev_file = report_dir / "data" / "evidence.json"
        if not ev_file.is_file():
            ev_file = report_dir / f"{task_ref.report_basename}.evidence.json"
        if ev_file.is_file():
            output_text = ev_file.read_text(encoding="utf-8")
        else:
            output_text = f"(No evidence.json found under {report_dir})"
    elif section == "anchors":
        sum_json = report_dir / "data" / "summary.json"
        if not sum_json.is_file():
            sum_json = report_dir / f"{task_ref.report_basename}.summary.json"
        if sum_json.is_file():
            try:
                card = json.loads(sum_json.read_text(encoding="utf-8"))
                anchors = card.get("causal_anchors") or {}
                output_text = json.dumps(anchors, indent=2, ensure_ascii=False)
            except Exception as exc:
                output_text = f"(Failed parsing summary.json: {exc})"
        else:
            output_text = f"(No summary.json found under {report_dir})"

    if not args.no_redact:
        output_text = redact_sensitive(output_text)

    print(output_text)
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """Atomic recomputation of failure-report/INDEX.md."""
    report_root = args.report_root
    if report_root is None:
        roots = _find_benchmark_roots(None)
        for r in roots:
            cand = r / REPORT_DIR_NAME
            if cand.is_dir():
                report_root = cand
                break
        if report_root is None:
            print(
                "ERROR: --report-root not specified and no default failure-report directory found. "
                "Please specify --report-root or run within a benchmark repository.",
                file=sys.stderr,
            )
            return 2

    index_path = update_failure_report_index(report_root)
    print(f"Successfully updated index: {index_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="cfa",
        description="Case Failure Analyzer (CFA) - Unified Command Line Interface",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # audit subcommand
    subparsers.add_parser("audit", help="Run case failure audit and generate reports")
    # inspect subcommand
    inspect_parser = subparsers.add_parser(
        "inspect", help="Read-only inspection of audit conclusions and anchors"
    )
    inspect_parser.add_argument("task_id", help="Task ID to inspect (e.g. task208)")
    inspect_parser.add_argument(
        "--section",
        choices=["verdict", "report", "anchors", "data", "evidence"],
        default="verdict",
        help="Report section or data to inspect (default: verdict)",
    )
    inspect_parser.add_argument(
        "--jobs-root", type=Path, default=None, help="Benchmark checkout root"
    )
    inspect_parser.add_argument(
        "--report-dir", type=Path, default=None, help="Report directory override"
    )
    inspect_parser.add_argument(
        "--no-redact", action="store_true", help="Disable automatic sensitive token masking"
    )

    # index subcommand
    index_parser = subparsers.add_parser("index", help="Recompute failure report INDEX.md")
    index_parser.add_argument(
        "--report-root", type=Path, default=None, help="Path to failure-report/ root"
    )

    if not argv or "-h" in argv or "--help" in argv and len(argv) == 1:
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand in ("-h", "--help"):
        parser.print_help()
        return 0
    elif subcommand == "audit":
        return cmd_audit(argv[1:])
    elif subcommand == "inspect":
        args = inspect_parser.parse_args(argv[1:])
        return cmd_inspect(args)
    elif subcommand == "index":
        args = index_parser.parse_args(argv[1:])
        return cmd_index(args)
    elif not subcommand.startswith("-"):
        # Convenience shortcut: `cfa task208` -> `cfa audit task208`
        return cmd_audit(argv)
    else:
        return cmd_audit(argv)


if __name__ == "__main__":
    sys.exit(main())
