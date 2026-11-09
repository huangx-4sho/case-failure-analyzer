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
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from render_index import update_failure_report_index
from task_ref import REPORT_DIR_NAME, discover_all_task_ids, resolve_task_ref


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
    """Delegate to analyze_case.py with normalized task-id positional arguments or audit --all."""
    import analyze_case

    if "--all" in argv:
        jobs_root: Optional[Path] = None
        i = 0
        while i < len(argv):
            if argv[i] == "--jobs-root" and i + 1 < len(argv):
                jobs_root = Path(argv[i + 1]).resolve()
                break
            i += 1
        if jobs_root is None:
            roots = _find_benchmark_roots(None)
            if roots:
                jobs_root = roots[0]

        if jobs_root is None or not jobs_root.is_dir():
            print(
                f"ERROR: could not locate benchmark jobs or failure-report root from '{Path.cwd()}'. "
                "Please specify --jobs-root or run within a benchmark repository.",
                file=sys.stderr,
            )
            return 2

        task_ids = discover_all_task_ids(jobs_root)
        if not task_ids:
            print(f"No tasks discovered under '{jobs_root}'.", file=sys.stderr)
            return 0

        show_verdict = "--verdict-stdout" in argv
        shared_args: List[str] = []
        i = 0
        while i < len(argv):
            arg = argv[i]
            if arg == "--all":
                i += 1
                continue
            if arg == "--verdict-stdout":
                i += 1
                continue
            if arg == "--jobs-root":
                shared_args.extend([arg, argv[i + 1]])
                i += 2
                continue
            shared_args.append(arg)
            i += 1

        if not show_verdict and "--no-verdict-stdout" not in shared_args:
            shared_args.append("--no-verdict-stdout")
        if "--jobs-root" not in shared_args:
            shared_args.extend(["--jobs-root", str(jobs_root)])

        total = len(task_ids)
        any_failed = False
        report_root: Optional[Path] = None

        for idx, tid in enumerate(task_ids, start=1):
            print(f"[{idx}/{total}] Auditing {tid}...")
            task_argv = ["--task-id", tid] + shared_args
            orig_argv = sys.argv
            sys.argv = [sys.argv[0]] + task_argv
            try:
                analyze_case.main()
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                if rc != 0:
                    any_failed = True
                    print(f"  FAILED: {tid} exited with code {rc}", file=sys.stderr)
            except Exception as exc:
                any_failed = True
                print(f"  ERROR: {tid} raised exception: {exc}", file=sys.stderr)
            finally:
                sys.argv = orig_argv

            if report_root is None:
                try:
                    ref = resolve_task_ref(tid, jobs_root)
                    report_root = ref.report_root
                except Exception:
                    pass

        if report_root is None:
            cand = jobs_root / REPORT_DIR_NAME
            if cand.is_dir():
                report_root = cand
            elif (jobs_root.parent / REPORT_DIR_NAME).is_dir():
                report_root = jobs_root.parent / REPORT_DIR_NAME
            else:
                report_root = jobs_root / REPORT_DIR_NAME

        index_file = update_failure_report_index(report_root)
        print(f"\nBatch audit complete ({total} tasks). Global index: {index_file}")
        return 1 if any_failed else 0

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


def _format_errors_summary(
    task_id: str,
    ev: Dict[str, Any],
    an: Dict[str, Any],
    raw: bool = False,
) -> str:
    verdict = an.get("verdict") or ev.get("runtime", {}).get("verdict", "unknown")
    prc = an.get("primary_root_cause") or {}
    manifestation = an.get("failure_manifestation") or {}
    vr = ev.get("verifier_report") or {}
    diagnosability = (
        manifestation.get("verifier_diagnosability") or vr.get("diagnosability") or "normal"
    )
    reasons = (
        manifestation.get("verifier_diagnosability_reasons")
        or vr.get("diagnosability_reasons")
        or []
    )

    failed_checks = list(manifestation.get("named_failed_checks") or vr.get("failed_checks") or [])
    sci_obs = ev.get("scientific_observations") or []
    divergences = [
        o
        for o in sci_obs
        if isinstance(o, dict) and o.get("error_family") == "verifier_recompute_divergence"
    ]

    if raw:
        payload = {
            "task_id": task_id,
            "verdict": verdict,
            "primary_root_cause": prc,
            "verifier_diagnosability": diagnosability,
            "verifier_diagnosability_reasons": reasons,
            "failed_checks": failed_checks,
            "recompute_divergences": divergences,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    lines = [
        f"# Error & Verifier Summary: `{task_id}`",
        "",
        f"- **Verdict**: `{verdict}`",
        (
            f"- **Primary Root Cause**: `{prc.get('category', 'unknown')}` ➔ "
            f"`{prc.get('subtype', '—')}` (`{prc.get('code', '—')}`)"
        ),
        f"  - *Summary*: {prc.get('summary', '—')}",
        (
            f"- **Verifier Diagnosability**: `{diagnosability}`"
            + (f" ({', '.join(reasons)})" if reasons else "")
        ),
        "",
    ]

    lines.append(f"## Named Failed Checks ({len(failed_checks)})")
    lines.append("")
    if not failed_checks:
        lines.append("*(No named failed checks recorded)*")
        lines.append("")
    else:
        for idx, fc in enumerate(failed_checks, start=1):
            name = fc.get("name", "unnamed")
            layer = fc.get("layer", "—")
            detail = fc.get("detail", "—")
            kind = fc.get("failure_kind") or "unspecified"
            lines.append(f"### {idx}. `[{layer}]` **`{name}`** (`{kind}`)")
            lines.append(f"- **Detail**: {detail}")
            check_ev = fc.get("evidence")
            if isinstance(check_ev, dict) and check_ev:
                lines.append(f"- **Evidence**: `{json.dumps(check_ev, ensure_ascii=False)}`")
            art_cmp = fc.get("artifact_comparison")
            if isinstance(art_cmp, dict) and art_cmp:
                lines.append(
                    f"- **Artifact Comparison**: `{json.dumps(art_cmp, ensure_ascii=False)}`"
                )
            recomp = fc.get("recompute_divergence")
            if isinstance(recomp, dict) and recomp:
                lines.append(f"- **Recompute Delta**: `{json.dumps(recomp, ensure_ascii=False)}`")
            lines.append("")

    if divergences:
        lines.append(f"## Recompute Divergences ({len(divergences)})")
        lines.append("")
        for d in divergences:
            sci_id = d.get("sci_id", "sci:recompute")
            match = d.get("matched_text", "")
            lines.append(f"- **`{sci_id}`**: {match}")
        lines.append("")

    return "\n".join(lines).strip()


def _format_confidence_summary(
    task_id: str,
    an: Dict[str, Any],
    raw: bool = False,
) -> str:
    prc = an.get("primary_root_cause") or {}
    conf = prc.get("confidence")
    strength = prc.get("evidence_strength") or "low"
    breakdown = prc.get("confidence_breakdown") or {}
    hyps = an.get("competing_hypotheses") or []

    if raw:
        payload = {
            "task_id": task_id,
            "confidence": conf,
            "evidence_strength": strength,
            "confidence_breakdown": breakdown,
            "competing_hypotheses": hyps,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    points = breakdown.get("points", "—")
    denom = breakdown.get("denominator", "—")
    factors = breakdown.get("factors") or []
    missing = breakdown.get("missing_to_raise") or []

    conf_display = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else str(conf or "—")

    lines = [
        f"# Confidence & Evidence Breakdown: `{task_id}`",
        "",
        f"- **Confidence Score**: `{conf_display}`",
        f"- **Evidence Strength**: `{strength}`",
        f"- **Score Scale**: `{points} / {denom}` points",
        "",
    ]

    if factors:
        lines.append("## Evidence Factors Breakdown")
        lines.append("")
        for f in factors:
            delta = f.get("delta", 0)
            sign = f"+{delta}" if delta > 0 else str(delta)
            lbl_zh = f.get("label_zh", "")
            lbl_en = f.get("label_en", "")
            lbl = (
                f"{lbl_en} ({lbl_zh})"
                if lbl_zh and lbl_en
                else (lbl_en or lbl_zh or f.get("key", "factor"))
            )
            lines.append(f"- `[{sign}]` {lbl}")
        lines.append("")

    if missing:
        lines.append("## Missing Evidence to Raise Confidence")
        lines.append("")
        for m in missing:
            lines.append(f"- ⚠️ {m}")
        lines.append("")

    if hyps:
        lines.append(f"## Competing Hypotheses Scores ({len(hyps)})")
        lines.append("")
        for h in hyps:
            hid = h.get("hypothesis_id", "H")
            cat = h.get("category", "unknown")
            sub = h.get("subtype", "—")
            h_conf = h.get("confidence")
            h_conf_str = (
                f"{float(h_conf):.2f}" if isinstance(h_conf, (int, float)) else str(h_conf or "—")
            )
            h_str = h.get("evidence_strength") or "low"
            claim = h.get("claim", "—")
            lines.append(f"- **`{hid}`** (`{cat}` ➔ `{sub}`): `{h_conf_str}` (`{h_str}`) · {claim}")
        lines.append("")

    return "\n".join(lines).strip()


def _format_evidence_digest(
    task_id: str,
    ev: Dict[str, Any],
    an: Optional[Dict[str, Any]] = None,
) -> str:
    runtime = ev.get("runtime") or {}
    vr = ev.get("verifier_report") or {}
    failed_checks = vr.get("failed_checks") or []
    causal_anchors = ev.get("causal_anchors") or {}
    sci_obs = ev.get("scientific_observations") or []
    behavioral = ev.get("behavioral_signals") or []
    evidence_refs = []
    if an and isinstance(an, dict):
        evidence_refs = an.get("evidence_refs") or []
    if not evidence_refs and "evidence_refs" in ev:
        evidence_refs = ev.get("evidence_refs") or []

    lines = [
        f"# Evidence Digest: `{task_id}`",
        "",
        "## Runtime State",
        f"- **Execution Status**: `{runtime.get('execution_status', 'unknown')}`",
        f"- **Verification Status**: `{runtime.get('verification_status', 'unknown')}`",
        f"- **Verdict**: `{runtime.get('verdict', 'unknown')}` (Reward: `{runtime.get('reward')}`)",
        (
            f"- **Gates**: `agent_started={runtime.get('agent_started')}`, "
            f"`verifier_started={runtime.get('verifier_started')}`"
        ),
        "",
        "## Counts & Signal Summary",
        f"- **Failed Verifier Checks**: `{len(failed_checks)}`",
        f"- **Scientific Observations**: `{len(sci_obs)}`",
        f"- **Behavioral Signals**: `{len(behavioral)}`",
        "",
    ]

    if causal_anchors:
        lines.append("## Causal Anchors")
        lines.append("```json")
        lines.append(json.dumps(causal_anchors, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    if sci_obs:
        lines.append(f"## Scientific Observations ({len(sci_obs)})")
        for so in sci_obs[:5]:
            sid = so.get("sci_id", "sci")
            sw = so.get("software", "general")
            ef = so.get("error_family", "unknown")
            lines.append(f"- **`{sid}`** (`{sw}` · `{ef}`): {so.get('matched_text', '')[:120]}")
        if len(sci_obs) > 5:
            lines.append(f"- *... and {len(sci_obs) - 5} more observations*")
        lines.append("")

    if evidence_refs:
        lines.append(f"## Core Evidence Refs ({len(evidence_refs)})")
        lines.append(f"`{', '.join(str(r) for r in evidence_refs)}`")
        lines.append("")

    return "\n".join(lines).strip()


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
    task_ref = None
    report_dir = None
    report_basename = args.task_id
    task_id = args.task_id
    last_error = None

    if args.report_dir and Path(args.report_dir).is_dir():
        report_dir = Path(args.report_dir).resolve()
    else:
        roots = _find_benchmark_roots(args.jobs_root)
        if not roots:
            print(
                f"ERROR: could not locate benchmark jobs or failure-report root from '{Path.cwd()}'. "
                "Please specify --jobs-root or run within a benchmark repository.",
                file=sys.stderr,
            )
            return 2
        for r in roots:
            try:
                task_ref = resolve_task_ref(args.task_id, r, report_dir_override=args.report_dir)
                report_dir = task_ref.report_dir
                report_basename = task_ref.report_basename
                task_id = task_ref.task_id
                break
            except Exception as exc:
                last_error = exc
            cand = r / REPORT_DIR_NAME / args.task_id
            if cand.is_dir():
                report_dir = cand
                break

    if report_dir is None or not report_dir.is_dir():
        if last_error:
            print(f"ERROR: could not resolve task '{args.task_id}': {last_error}", file=sys.stderr)
        else:
            print(
                f"ERROR: report directory not found for '{args.task_id}'. Run 'cfa audit {args.task_id}' first.",
                file=sys.stderr,
            )
        return 2

    # Locate summary or report file
    section = args.section
    output_text = ""

    def _find_file(candidates: List[Path]) -> Optional[Path]:
        for c in candidates:
            if c.is_file():
                return c
        return None

    if section == "verdict":
        if args.raw:
            sum_json = _find_file(
                [
                    report_dir / "data" / "summary.json",
                    report_dir / f"{report_basename}.summary.json",
                    report_dir / "summary.json",
                ]
            )
            if sum_json:
                output_text = sum_json.read_text(encoding="utf-8")
            else:
                sum_md = _find_file(
                    [
                        report_dir / "summary.md",
                        report_dir / f"{report_basename}.summary.md",
                    ]
                )
                output_text = (
                    sum_md.read_text(encoding="utf-8")
                    if sum_md
                    else f"(No summary found under {report_dir})"
                )
        else:
            sum_md = _find_file(
                [
                    report_dir / "summary.md",
                    report_dir / f"{report_basename}.summary.md",
                ]
            )
            if sum_md:
                output_text = sum_md.read_text(encoding="utf-8")
            else:
                output_text = f"(No summary.md found under {report_dir})"
    elif section == "report":
        rep_file = _find_file(
            [
                report_dir / f"{report_basename}.md",
                report_dir / "report.md",
            ]
        )
        if rep_file:
            output_text = rep_file.read_text(encoding="utf-8")
        else:
            output_text = f"(No report.md found under {report_dir})"
    elif section == "data":
        ev_file = _find_file(
            [
                report_dir / "data" / "evidence.json",
                report_dir / f"{report_basename}.evidence.json",
                report_dir / "evidence.json",
            ]
        )
        if ev_file:
            output_text = ev_file.read_text(encoding="utf-8")
        else:
            output_text = f"(No evidence.json found under {report_dir})"
    elif section == "evidence":
        ev_file = _find_file(
            [
                report_dir / "data" / "evidence.json",
                report_dir / f"{report_basename}.evidence.json",
                report_dir / "evidence.json",
            ]
        )
        if not ev_file:
            output_text = f"(No evidence.json found under {report_dir})"
        elif args.raw:
            output_text = ev_file.read_text(encoding="utf-8")
        else:
            try:
                ev = json.loads(ev_file.read_text(encoding="utf-8"))
                an_file = _find_file(
                    [
                        report_dir / "data" / "analysis.json",
                        report_dir / f"{report_basename}.analysis.json",
                        report_dir / "analysis.json",
                    ]
                )
                an = json.loads(an_file.read_text(encoding="utf-8")) if an_file else None
                output_text = _format_evidence_digest(task_id, ev, an)
            except Exception as exc:
                output_text = f"(Failed formatting evidence digest: {exc})"
    elif section == "errors":
        ev_file = _find_file(
            [
                report_dir / "data" / "evidence.json",
                report_dir / f"{report_basename}.evidence.json",
                report_dir / "evidence.json",
            ]
        )
        an_file = _find_file(
            [
                report_dir / "data" / "analysis.json",
                report_dir / f"{report_basename}.analysis.json",
                report_dir / "analysis.json",
            ]
        )
        ev = json.loads(ev_file.read_text(encoding="utf-8")) if ev_file else {}
        an = json.loads(an_file.read_text(encoding="utf-8")) if an_file else {}
        output_text = _format_errors_summary(task_id, ev, an, raw=args.raw)
    elif section == "confidence":
        an_file = _find_file(
            [
                report_dir / "data" / "analysis.json",
                report_dir / f"{report_basename}.analysis.json",
                report_dir / "analysis.json",
            ]
        )
        if not an_file:
            output_text = f"(No analysis.json found under {report_dir})"
        else:
            try:
                an = json.loads(an_file.read_text(encoding="utf-8"))
                output_text = _format_confidence_summary(task_id, an, raw=args.raw)
            except Exception as exc:
                output_text = f"(Failed formatting confidence summary: {exc})"
    elif section == "anchors":
        sum_json = _find_file(
            [
                report_dir / "data" / "summary.json",
                report_dir / f"{report_basename}.summary.json",
                report_dir / "summary.json",
            ]
        )
        if sum_json:
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
        choices=["verdict", "report", "errors", "confidence", "evidence", "anchors", "data"],
        default="verdict",
        help="Report section or data to inspect (default: verdict)",
    )
    inspect_parser.add_argument(
        "--raw", action="store_true", help="Output raw JSON or unformatted section data"
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
