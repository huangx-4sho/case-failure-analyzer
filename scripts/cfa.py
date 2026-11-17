#!/usr/bin/env python3
"""
Case Failure Analyzer Unified CLI (`scripts/cfa.py`)

A thin, disciplined entry point providing four focused subcommands:
1. `audit`: Normalizes arguments and delegates to `scripts/analyze_case.py`.
2. `inspect`: Strictly read-only inspection of task findings with automatic redaction
   (home directory paths, API tokens, passwords).
3. `diff`: Side-by-side comparison of two audited tasks or trials.
4. `index`: Full atomic recomputation and file locking of `<bench_root>/failure-report/INDEX.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from render_index import update_failure_report_index
from task_ref import REPORT_DIR_NAME, discover_all_task_ids, normalize_task_ref, resolve_task_ref


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


def _normalize_artifact_comparisons(art_cmp: Any) -> List[Dict[str, Any]]:
    """Normalize a check's artifact_comparison field into a list of dicts."""
    if isinstance(art_cmp, dict):
        return [art_cmp]
    if isinstance(art_cmp, list):
        return [item for item in art_cmp if isinstance(item, dict)]
    return []


def _format_hint_label(hint: Any) -> str:
    """Format a scale_or_sign_hint dict into a readable string."""
    if not isinstance(hint, dict):
        return str(hint)
    kind = hint.get("kind", "unknown")
    factor = hint.get("factor")
    label = hint.get("label") or ""
    parts = [str(kind)]
    if factor is not None:
        parts.append(f"factor={factor}")
    if label:
        parts.append(str(label))
    return ", ".join(parts)


def _format_artifacts_summary(
    task_id: str,
    ev: Dict[str, Any],
    an: Optional[Dict[str, Any]] = None,
    raw: bool = False,
) -> str:
    """Format discovered artifacts, missing deliverables, contract observations, and comparisons."""
    artifacts = list(ev.get("artifacts") or [])
    declared_deliverables = list((ev.get("metadata") or {}).get("declared_deliverables") or [])
    missing_deliverables = list(ev.get("missing_deliverables") or [])
    missing_artifacts = list(ev.get("missing_artifacts") or [])
    contract_observations = list(ev.get("contract_observations") or [])
    deliverable_diagnostics = ev.get("deliverable_diagnostics")

    vr = ev.get("verifier_report") or {}
    failed_checks = list(vr.get("failed_checks") or [])
    if not failed_checks and isinstance(an, dict):
        failed_checks = list(
            (an.get("failure_manifestation") or {}).get("named_failed_checks") or []
        )

    check_comparisons: List[Dict[str, Any]] = []
    scale_or_sign_hints: List[Dict[str, Any]] = []
    stale_deliverable_syncs: List[Dict[str, Any]] = []

    for fc in failed_checks:
        if not isinstance(fc, dict):
            continue
        check_name = fc.get("name", "unnamed")
        layer = fc.get("layer")
        cmp_list = _normalize_artifact_comparisons(fc.get("artifact_comparison"))
        check_hints: List[Dict[str, Any]] = []
        if isinstance(fc.get("scale_or_sign_hint"), dict):
            check_hints.append(fc["scale_or_sign_hint"])
        for item in cmp_list:
            if isinstance(item.get("scale_or_sign_hint"), dict):
                check_hints.append(item["scale_or_sign_hint"])
        stale_val = fc.get("stale_deliverable_sync")
        if not stale_val:
            for item in cmp_list:
                if item.get("stale_deliverable_sync"):
                    stale_val = item.get("stale_deliverable_sync")
                    break

        if cmp_list or check_hints or stale_val:
            check_comparisons.append(
                {
                    "check_name": check_name,
                    "layer": layer,
                    "artifact_comparison": cmp_list,
                    **({"scale_or_sign_hints": check_hints} if check_hints else {}),
                    **({"stale_deliverable_sync": stale_val} if stale_val else {}),
                }
            )
            for h in check_hints:
                scale_or_sign_hints.append(
                    {"source": f"check:{check_name}", "hint": h}
                )
            if stale_val:
                stale_deliverable_syncs.append(
                    {
                        "source": f"check:{check_name}",
                        "stale_deliverable_sync": stale_val,
                        **(
                            {
                                "recomputed_match_in_trajectory_ref": fc.get(
                                    "recomputed_match_in_trajectory_ref"
                                )
                            }
                            if fc.get("recomputed_match_in_trajectory_ref")
                            else {}
                        ),
                    }
                )

    for so in ev.get("scientific_observations") or []:
        if not isinstance(so, dict):
            continue
        sci_id = so.get("sci_id", "sci")
        if isinstance(so.get("scale_or_sign_hint"), dict):
            scale_or_sign_hints.append(
                {
                    "source": sci_id,
                    "metric": so.get("metric"),
                    "agent_artifact_ref": so.get("agent_artifact_ref"),
                    "hint": so["scale_or_sign_hint"],
                }
            )
        if so.get("stale_deliverable_sync"):
            stale_deliverable_syncs.append(
                {
                    "source": sci_id,
                    "metric": so.get("metric"),
                    "agent_artifact_ref": so.get("agent_artifact_ref"),
                    "stale_deliverable_sync": so.get("stale_deliverable_sync"),
                    "recomputed_match_in_trajectory_ref": so.get(
                        "recomputed_match_in_trajectory_ref"
                    ),
                }
            )

    if raw:
        payload: Dict[str, Any] = {
            "task_id": task_id,
            "declared_deliverables": declared_deliverables,
            "artifacts": artifacts,
            "missing_deliverables": missing_deliverables,
            "missing_artifacts": missing_artifacts,
            "contract_observations": contract_observations,
            "check_artifact_comparisons": check_comparisons,
            "scale_or_sign_hints": scale_or_sign_hints,
            "stale_deliverable_syncs": stale_deliverable_syncs,
        }
        if deliverable_diagnostics is not None:
            payload["deliverable_diagnostics"] = deliverable_diagnostics
        return json.dumps(payload, indent=2, ensure_ascii=False)

    lines = [
        f"# Artifacts & Deliverables: `{task_id}`",
        "",
        f"- **Declared Deliverables**: `{len(declared_deliverables)}`",
        f"- **Discovered Artifacts**: `{len(artifacts)}`",
        f"- **Missing Deliverables**: `{len(missing_deliverables)}`",
        f"- **Contract Observations**: `{len(contract_observations)}`",
        "",
    ]

    lines.append("## Declared & Missing Deliverables")
    lines.append("")
    if declared_deliverables:
        lines.append(
            f"- **Declared**: {', '.join(f'`{d}`' for d in declared_deliverables)}"
        )
    if missing_deliverables:
        lines.append(
            f"- **Missing Deliverables**: {', '.join(f'`{d}`' for d in missing_deliverables)}"
        )
    else:
        lines.append("- **Missing Deliverables**: *(none)*")
    if missing_artifacts:
        lines.append(
            f"- **Missing Artifacts**: {', '.join(f'`{a}`' for a in missing_artifacts)}"
        )
    lines.append("")

    lines.append(f"## Discovered Artifacts ({len(artifacts)})")
    lines.append("")
    if not artifacts:
        lines.append("*(No artifacts recorded)*")
        lines.append("")
    else:
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            aid = art.get("artifact_id", "art")
            rel = art.get("rel_path", "—")
            scope = art.get("scope", "—")
            exists = art.get("exists")
            size = art.get("size_bytes")
            size_str = f", {size} bytes" if size is not None else ""
            lines.append(f"- **`{aid}`** (`{scope}`): `{rel}` (exists=`{exists}`{size_str})")
        lines.append("")

    lines.append(f"## Contract Observations ({len(contract_observations)})")
    lines.append("")
    if not contract_observations:
        lines.append("*(No contract observations recorded)*")
        lines.append("")
    else:
        for co in contract_observations:
            if not isinstance(co, dict):
                continue
            cid = co.get("contract_id", "contract")
            align = co.get("alignment", "unknown")
            item = co.get("item", "—")
            status = co.get("agent_output_status", "—")
            details = co.get("details", "")
            lines.append(
                f"- **`{cid}`** (`{align}` · item=`{item}`, status=`{status}`): {details}"
            )
        lines.append("")

    lines.append("## Per-Check Artifact Comparisons & Anomalies")
    lines.append("")
    if not check_comparisons and not scale_or_sign_hints and not stale_deliverable_syncs:
        lines.append("*(No per-check artifact comparisons or anomaly hints recorded)*")
        lines.append("")
    else:
        for rec in check_comparisons:
            layer = rec.get("layer") or "—"
            cname = rec.get("check_name", "unnamed")
            lines.append(f"### `[{layer}]` **`{cname}`**")
            for cmp_item in rec.get("artifact_comparison") or []:
                lines.append(f"- **Comparison**: `{json.dumps(cmp_item, ensure_ascii=False)}`")
                if isinstance(cmp_item.get("scale_or_sign_hint"), dict):
                    lines.append(
                        f"  - **Scale/Sign Hint**: `{_format_hint_label(cmp_item['scale_or_sign_hint'])}`"
                    )
            for h in rec.get("scale_or_sign_hints") or []:
                lines.append(f"- **Scale/Sign Hint**: `{_format_hint_label(h)}`")
            if rec.get("stale_deliverable_sync"):
                lines.append(
                    f"- **Stale Deliverable Sync**: `{rec['stale_deliverable_sync']}`"
                )
            lines.append("")

        obs_hints = [h for h in scale_or_sign_hints if not str(h.get("source", "")).startswith("check:")]
        obs_stale = [s for s in stale_deliverable_syncs if not str(s.get("source", "")).startswith("check:")]
        if obs_hints:
            lines.append("### Scale / Sign Hints")
            lines.append("")
            for item in obs_hints:
                src = item.get("source", "sci")
                metric = item.get("metric") or "—"
                art_ref = item.get("agent_artifact_ref") or "—"
                lines.append(
                    f"- **`{src}`** (metric=`{metric}`, artifact=`{art_ref}`): "
                    f"`{_format_hint_label(item.get('hint'))}`"
                )
            lines.append("")
        if obs_stale:
            lines.append("### Stale Deliverable Sync Records")
            lines.append("")
            for item in obs_stale:
                src = item.get("source", "sci")
                metric = item.get("metric") or "—"
                art_ref = item.get("agent_artifact_ref") or "—"
                traj_ref = item.get("recomputed_match_in_trajectory_ref") or "—"
                lines.append(
                    f"- **`{src}`** (metric=`{metric}`, artifact=`{art_ref}`): "
                    f"`stale_deliverable_sync={item.get('stale_deliverable_sync')}` "
                    f"(trajectory match: `{traj_ref}`)"
                )
            lines.append("")

    if deliverable_diagnostics is not None:
        lines.append("## Deliverable Content Diagnostics")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(deliverable_diagnostics, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).strip()


def _extract_step_key(ev_item: Dict[str, Any]) -> Tuple[str, str]:
    """Extract (step_id, step_prefix) from a timeline event."""
    if ev_item.get("step_id") is not None:
        sid = str(ev_item["step_id"])
        return sid, f"trajectory:step:{sid}"
    eid = str(ev_item.get("event_id") or "")
    m = re.match(r"^(?:trajectory:)?step:([^:]+)", eid)
    if m:
        sid = m.group(1)
        prefix = f"trajectory:step:{sid}" if eid.startswith("trajectory:") else f"step:{sid}"
        return sid, prefix
    return eid or "unknown", eid or "unknown"


def _event_matches_step(ev_item: Dict[str, Any], step_spec: str) -> bool:
    """Return True if timeline event matches a step number or event ID prefix."""
    spec = step_spec.strip()
    if not spec:
        return False
    eid = str(ev_item.get("event_id") or "")
    if ev_item.get("step_id") is not None and str(ev_item["step_id"]) == spec:
        return True
    if re.fullmatch(r"\d+", spec):
        return (
            eid == f"trajectory:step:{spec}"
            or eid.startswith(f"trajectory:step:{spec}:")
            or eid == f"step:{spec}"
            or eid.startswith(f"step:{spec}:")
        )
    if re.fullmatch(r"(?:trajectory:)?step:[^:]+", spec):
        return eid == spec or eid.startswith(spec + ":")
    return eid == spec or eid.startswith(spec + ":") or eid.startswith(spec)


def _fence_block(text: str, lang: str = "text") -> List[str]:
    """Render text inside a Markdown fenced block that will not collide with inner backticks."""
    fence = "````" if "```" in text else "```"
    return [f"{fence}{lang}", text, fence]


def _render_timeline_event_md(ev_item: Dict[str, Any]) -> List[str]:
    """Render a single timeline event into readable Markdown lines."""
    eid = ev_item.get("event_id", "event")
    etype = ev_item.get("event_type", "event")
    actor = ev_item.get("actor", "unknown")
    ts = ev_item.get("timestamp")
    tool = ev_item.get("tool_name")
    cmd = ev_item.get("command")
    exit_code = ev_item.get("exit_code")
    obs = ev_item.get("observation")
    code_payload = ev_item.get("code_payload")
    src_file = ev_item.get("source_file")
    src_ptr = ev_item.get("source_pointer")

    lines = [f"### `{eid}` · `{etype}` (`{actor}`)"]
    if ts:
        lines.append(f"- **Timestamp**: `{ts}`")
    if src_file or src_ptr:
        lines.append(f"- **Source**: `{src_file or ''}{src_ptr or ''}`")
    if tool:
        lines.append(f"- **Tool**: `{tool}`")
    if cmd:
        lines.append(f"- **Command / Target**: `{cmd}`")
    if exit_code is not None:
        lines.append(f"- **Exit Code**: `{exit_code}`")
    if code_payload:
        lines.append("- **Code Payload**:")
        lines.extend(_fence_block(str(code_payload), "text"))
    if obs:
        if etype == "agent_thought":
            obs_label = "Thought"
        elif etype in ("user_instruction", "agent_reasoning", "final_response"):
            obs_label = "Message"
        else:
            obs_label = "Observation"
        lines.append(f"- **{obs_label}**:")
        lines.extend(_fence_block(str(obs), "text"))
    lines.append("")
    return lines


def _format_step_timeline(
    task_id: str,
    ev: Dict[str, Any],
    step_spec: Optional[str] = None,
    raw: bool = False,
) -> str:
    """Format a filtered step or concise step index + last 5 steps from evidence['timeline']."""
    timeline = [item for item in (ev.get("timeline") or []) if isinstance(item, dict)]

    if step_spec is not None:
        matched = [item for item in timeline if _event_matches_step(item, step_spec)]
        if raw:
            return json.dumps(
                {
                    "task_id": task_id,
                    "step_filter": step_spec,
                    "matched_count": len(matched),
                    "events": matched,
                },
                indent=2,
                ensure_ascii=False,
            )
        lines = [f"# Timeline Step: `{task_id}` (`{step_spec}`)", ""]
        if not matched:
            lines.append(
                f"*(No timeline events matched `{step_spec}`; total timeline events: {len(timeline)})*"
            )
            return "\n".join(lines).strip()
        lines.append(f"- **Matched Events**: `{len(matched)}`")
        lines.append("")
        for item in matched:
            lines.extend(_render_timeline_event_md(item))
        return "\n".join(lines).strip()

    # No --step filter: group by step and show concise step index + last 5 steps
    grouped: List[Tuple[str, str, List[Dict[str, Any]]]] = []
    group_pos: Dict[str, int] = {}
    for item in timeline:
        sid, sprefix = _extract_step_key(item)
        if sid not in group_pos:
            group_pos[sid] = len(grouped)
            grouped.append((sid, sprefix, []))
        grouped[group_pos[sid]][2].append(item)

    step_index = []
    for sid, sprefix, evs in grouped:
        etypes = list(dict.fromkeys(str(e.get("event_type") or "event") for e in evs))
        tools = [str(e.get("tool_name")) for e in evs if e.get("tool_name")]
        exit_codes = [e.get("exit_code") for e in evs if e.get("exit_code") is not None]
        commands = [str(e.get("command")) for e in evs if e.get("command")]
        step_index.append(
            {
                "step_id": sid,
                "event_prefix": sprefix,
                "event_count": len(evs),
                "event_types": etypes,
                "tools": tools,
                "exit_codes": exit_codes,
                "commands": commands,
            }
        )

    recent = grouped[-5:]
    if raw:
        return json.dumps(
            {
                "task_id": task_id,
                "step_filter": None,
                "total_steps": len(grouped),
                "total_events": len(timeline),
                "step_index": step_index,
                "recent_steps": [
                    {"step_id": sid, "event_prefix": sprefix, "events": evs}
                    for sid, sprefix, evs in recent
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    lines = [
        f"# Timeline Step Index: `{task_id}`",
        "",
        f"- **Total Steps**: `{len(grouped)}` (`{len(timeline)}` events)",
        "",
    ]
    if not grouped:
        lines.append("*(No timeline events recorded)*")
        return "\n".join(lines).strip()

    lines.append("## Step Index")
    lines.append("")
    for row in step_index:
        summary_parts = [f"{row['event_count']} event(s)", ", ".join(row["event_types"])]
        if row["tools"]:
            summary_parts.append(f"tools={','.join(row['tools'])}")
        if row["exit_codes"]:
            summary_parts.append(f"exit={row['exit_codes']}")
        if row["commands"]:
            cmd_preview = row["commands"][0][:80]
            summary_parts.append(f"cmd=`{cmd_preview}`")
        lines.append(
            f"- **Step `{row['step_id']}`** (`{row['event_prefix']}`): "
            + " · ".join(summary_parts)
        )
    lines.append("")

    lines.append(f"## Last {len(recent)} Step(s)")
    lines.append("")
    for sid, sprefix, evs in recent:
        lines.append(f"### Step `{sid}` (`{sprefix}`)")
        lines.append("")
        for item in evs:
            lines.extend(_render_timeline_event_md(item))

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
    elif section == "artifacts":
        ev_file = _find_file(
            [
                report_dir / "data" / "evidence.json",
                report_dir / f"{report_basename}.evidence.json",
                report_dir / "evidence.json",
            ]
        )
        if not ev_file:
            output_text = f"(No evidence.json found under {report_dir})"
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
                output_text = _format_artifacts_summary(task_id, ev, an, raw=args.raw)
            except Exception as exc:
                output_text = f"(Failed formatting artifacts summary: {exc})"
    elif section == "step":
        ev_file = _find_file(
            [
                report_dir / "data" / "evidence.json",
                report_dir / f"{report_basename}.evidence.json",
                report_dir / "evidence.json",
            ]
        )
        if not ev_file:
            output_text = f"(No evidence.json found under {report_dir})"
        else:
            try:
                ev = json.loads(ev_file.read_text(encoding="utf-8"))
                output_text = _format_step_timeline(
                    task_id, ev, step_spec=getattr(args, "step", None), raw=args.raw
                )
            except Exception as exc:
                output_text = f"(Failed formatting step timeline: {exc})"

    if not args.no_redact:
        output_text = redact_sensitive(output_text)

    print(output_text)
    return 0


def _find_first_file(candidates: List[Path]) -> Optional[Path]:
    for c in candidates:
        if c.is_file():
            return c
    return None


def _is_audited_report_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for rel in (
        "data/analysis.json",
        "analysis.json",
        "data/evidence.json",
        "evidence.json",
        "data/summary.json",
        "summary.json",
    ):
        if (path / rel).is_file():
            return True
    if (
        any(path.glob("*.analysis.json"))
        or any(path.glob("*.evidence.json"))
        or any(path.glob("*.summary.json"))
    ):
        return True
    return False


def _load_report_bundle(report_dir: Path, basename_hint: Optional[str] = None) -> Dict[str, Any]:
    """Load analysis, evidence, and summary JSON dicts from a report directory."""
    def _load_kind(kind: str) -> Dict[str, Any]:
        cands = [report_dir / "data" / f"{kind}.json"]
        if basename_hint:
            cands.append(report_dir / f"{basename_hint}.{kind}.json")
        cands.append(report_dir / f"{kind}.json")
        target = _find_first_file(cands)
        if target is None:
            globbed = sorted(report_dir.glob(f"*.{kind}.json"))
            if globbed:
                target = globbed[0]
        if target is None:
            return {}
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    return {
        "analysis": _load_kind("analysis"),
        "evidence": _load_kind("evidence"),
        "summary": _load_kind("summary"),
    }


def _safe_normalize_task_id(raw: str) -> Optional[str]:
    try:
        return normalize_task_ref(raw)
    except Exception:
        return None


def _resolve_diff_target(
    target: str,
    jobs_root: Optional[Path] = None,
    report_dir: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Resolve a diff target (path, task ID, or trial ref) to (report_dir, label)."""
    cand_path = Path(target)
    if cand_path.is_dir() and _is_audited_report_dir(cand_path):
        resolved = cand_path.resolve()
        return resolved, resolved.name

    norm_id = _safe_normalize_task_id(target)

    if report_dir is not None:
        rdir = Path(report_dir).resolve()
        for name in (target, norm_id):
            if not name:
                continue
            sub = rdir / name
            if _is_audited_report_dir(sub):
                return sub.resolve(), name
            trial_sub = rdir / "trials" / name
            if _is_audited_report_dir(trial_sub):
                return trial_sub.resolve(), name

    roots = _find_benchmark_roots(jobs_root)
    last_error: Optional[Exception] = None
    for root in roots:
        try:
            ref = resolve_task_ref(target, root)
            if "__" in target:
                trial_rep = ref.report_dir / "trials" / ref.trial_dir.name
                if _is_audited_report_dir(trial_rep):
                    return trial_rep.resolve(), ref.trial_dir.name
            if _is_audited_report_dir(ref.report_dir):
                return ref.report_dir.resolve(), ref.task_id
        except Exception as exc:
            last_error = exc

        for name in (target, norm_id):
            if not name:
                continue
            cand = root / REPORT_DIR_NAME / name
            if _is_audited_report_dir(cand):
                return cand.resolve(), name

    if last_error is not None:
        raise ValueError(f"could not resolve audited report for '{target}': {last_error}")
    raise ValueError(
        f"could not resolve audited report directory for '{target}'. Run 'cfa audit {target}' first."
    )


def _resolve_base_report_dir(
    target: str,
    jobs_root: Optional[Path] = None,
    report_dir: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Resolve a task's base report directory (containing a `trials/` folder)."""
    norm_id = _safe_normalize_task_id(target)
    if report_dir is not None:
        rdir = Path(report_dir).resolve()
        if (rdir / "trials").is_dir() or _is_audited_report_dir(rdir):
            return rdir, norm_id or target
        for name in (target, norm_id):
            if name and ((rdir / name / "trials").is_dir() or _is_audited_report_dir(rdir / name)):
                return (rdir / name).resolve(), name
        return rdir, norm_id or target

    cand_path = Path(target)
    if cand_path.is_dir() and ((cand_path / "trials").is_dir() or _is_audited_report_dir(cand_path)):
        resolved = cand_path.resolve()
        return resolved, resolved.name

    roots = _find_benchmark_roots(jobs_root)
    last_error: Optional[Exception] = None
    for root in roots:
        try:
            ref = resolve_task_ref(target, root)
            if (ref.report_dir / "trials").is_dir() or _is_audited_report_dir(ref.report_dir):
                return ref.report_dir.resolve(), ref.task_id
        except Exception as exc:
            last_error = exc
        for name in (target, norm_id):
            if not name:
                continue
            cand = root / REPORT_DIR_NAME / name
            if (cand / "trials").is_dir() or _is_audited_report_dir(cand):
                return cand.resolve(), name

    if last_error is not None:
        raise ValueError(f"could not resolve report directory for '{target}': {last_error}")
    raise ValueError(
        f"could not resolve report directory for '{target}'. Run 'cfa audit {target}' first."
    )


def _trial_report_mtime(trial_dir: Path) -> Tuple[float, str]:
    best_mtime = 0.0
    for rel in (
        "data/analysis.json",
        "analysis.json",
        "data/evidence.json",
        "evidence.json",
        "data/summary.json",
        "summary.json",
    ):
        cand = trial_dir / rel
        if cand.is_file():
            try:
                best_mtime = max(best_mtime, cand.stat().st_mtime)
            except OSError:
                pass
    if best_mtime == 0.0:
        try:
            best_mtime = trial_dir.stat().st_mtime
        except OSError:
            best_mtime = 0.0
    return (best_mtime, trial_dir.name)


def _select_two_latest_trials(trials_root: Path) -> Tuple[Path, Path]:
    """Select the two most recent audited trial directories (older_of_two, newest)."""
    if not trials_root.is_dir():
        raise ValueError(f"no trials directory found under '{trials_root.parent}'.")
    trial_dirs = [
        d for d in sorted(trials_root.iterdir()) if d.is_dir() and _is_audited_report_dir(d)
    ]
    if len(trial_dirs) < 2:
        raise ValueError(
            f"fewer than 2 audited trials found under '{trials_root}' (found {len(trial_dirs)})."
        )
    ranked = sorted(trial_dirs, key=_trial_report_mtime, reverse=True)
    newest, second_newest = ranked[0], ranked[1]
    return second_newest, newest


def _first_float(mapping: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for k in keys:
        val = mapping.get(k)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _extract_comparison_entries(check: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for item in _normalize_artifact_comparisons(check.get("artifact_comparison")):
        metric = str(item.get("metric") or check.get("name") or "value")
        delivered = _first_float(item, ("delivered", "reported", "value", "agent_value"))
        recomputed = _first_float(item, ("recomputed", "expected", "reference", "ref"))
        abs_diff = _first_float(item, ("abs_diff", "diff", "delta"))
        if abs_diff is None and delivered is not None and recomputed is not None:
            abs_diff = abs(delivered - recomputed)
        entries.append(
            {
                "metric": metric,
                "delivered": delivered,
                "recomputed": recomputed,
                "abs_diff": abs_diff,
                "artifact_ref": item.get("artifact_ref"),
                "scale_or_sign_hint": item.get("scale_or_sign_hint"),
            }
        )
    return entries


def _compute_numeric_deltas(
    check_a: Dict[str, Any], check_b: Dict[str, Any]
) -> List[Dict[str, Any]]:
    entries_a = _extract_comparison_entries(check_a)
    entries_b = _extract_comparison_entries(check_b)
    if not entries_a or not entries_b:
        return []

    deltas: List[Dict[str, Any]] = []
    by_metric_b = {e["metric"]: e for e in entries_b}
    for idx, ea in enumerate(entries_a):
        eb = by_metric_b.get(ea["metric"])
        if eb is None and len(entries_a) == 1 and len(entries_b) == 1:
            eb = entries_b[0]
        elif eb is None and idx < len(entries_b) and ea["metric"] == eb["metric"]:
            eb = entries_b[idx]
        if eb is None:
            continue
        deliv_delta = (
            round(eb["delivered"] - ea["delivered"], 12)
            if ea["delivered"] is not None and eb["delivered"] is not None
            else None
        )
        abs_diff_delta = (
            round(eb["abs_diff"] - ea["abs_diff"], 12)
            if ea["abs_diff"] is not None and eb["abs_diff"] is not None
            else None
        )
        deltas.append(
            {
                "metric": ea["metric"],
                "a_delivered": ea["delivered"],
                "b_delivered": eb["delivered"],
                "delivered_delta": deliv_delta,
                "a_recomputed": ea["recomputed"],
                "b_recomputed": eb["recomputed"],
                "a_abs_diff": ea["abs_diff"],
                "b_abs_diff": eb["abs_diff"],
                "abs_diff_delta": abs_diff_delta,
            }
        )
    return deltas


def _extract_failed_checks(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    ev = bundle.get("evidence") or {}
    an = bundle.get("analysis") or {}
    vr = ev.get("verifier_report") or {}
    checks = list(vr.get("failed_checks") or [])
    if not checks:
        checks = list((an.get("failure_manifestation") or {}).get("named_failed_checks") or [])
    return [c for c in checks if isinstance(c, dict)]


def _extract_runtime_and_verdict(bundle: Dict[str, Any]) -> Dict[str, Any]:
    ev = bundle.get("evidence") or {}
    an = bundle.get("analysis") or {}
    card = bundle.get("summary") or {}
    rt = ev.get("runtime") or {}
    vr = ev.get("verifier_report") or {}

    verdict = an.get("verdict") or rt.get("verdict") or card.get("verdict") or "unknown"
    reward = rt.get("reward")
    if reward is None:
        reward = vr.get("reward")
    return {
        "verdict": verdict,
        "reward": reward,
        "execution_status": rt.get("execution_status") or "unknown",
        "verification_status": rt.get("verification_status") or "unknown",
    }


def _extract_primary_root_cause(bundle: Dict[str, Any]) -> Dict[str, Any]:
    an = bundle.get("analysis") or {}
    card = bundle.get("summary") or {}
    prc = an.get("primary_root_cause") or card.get("primary_root_cause") or {}
    return {
        "category": prc.get("category", "unknown"),
        "subtype": prc.get("subtype", "—"),
        "code": prc.get("code", "—"),
        "confidence": prc.get("confidence"),
    }


def _extract_causal_anchors(bundle: Dict[str, Any]) -> Dict[str, Any]:
    ev = bundle.get("evidence") or {}
    card = bundle.get("summary") or {}
    anchors = ev.get("causal_anchors") or card.get("causal_anchors") or {}
    return anchors if isinstance(anchors, dict) else {}


def _anchor_digest(slot: str, anchor: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(anchor, dict) or not anchor:
        return None
    digest: Dict[str, Any] = {
        "event_id": anchor.get("event_id"),
        "step_id": anchor.get("step_id"),
        "tool_name": anchor.get("tool_name"),
    }
    if slot == "algorithm_or_param_definition":
        digest["target_path"] = anchor.get("target_path")
        digest["parameter_tradeoff_detected"] = anchor.get("parameter_tradeoff_detected")
        digest["matched_failed_checks"] = anchor.get("matched_failed_checks") or []
        if "parameter_evolution" in anchor:
            digest["parameter_evolution"] = anchor.get("parameter_evolution") or []
    elif slot == "deliverable_generation":
        digest["command"] = anchor.get("command")
        digest["matched_failed_checks"] = anchor.get("matched_failed_checks") or []
    elif slot == "self_check":
        digest["command"] = anchor.get("command")
        digest["script_path"] = anchor.get("script_path")
        digest["self_check_passed"] = anchor.get("self_check_passed")
        digest["checked_failed_checks"] = anchor.get("checked_failed_checks") or []
        digest["uncovered_failed_checks"] = anchor.get("uncovered_failed_checks") or []
    return digest


def _diff_parameter_evolution(
    evo_a: List[Dict[str, Any]], evo_b: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_param_a = {
        str(item.get("param")): item for item in evo_a if isinstance(item, dict) and item.get("param")
    }
    by_param_b = {
        str(item.get("param")): item for item in evo_b if isinstance(item, dict) and item.get("param")
    }
    all_params = list(dict.fromkeys([*by_param_a.keys(), *by_param_b.keys()]))
    rows: List[Dict[str, Any]] = []
    for p in all_params:
        ia = by_param_a.get(p) or {}
        ib = by_param_b.get(p) or {}
        rows.append(
            {
                "param": p,
                "a_initial": ia.get("initial"),
                "a_final": ia.get("final"),
                "b_initial": ib.get("initial"),
                "b_final": ib.get("final"),
            }
        )
    return rows


def _build_diff_payload(
    label_a: str,
    dir_a: Path,
    bundle_a: Dict[str, Any],
    label_b: str,
    dir_b: Path,
    bundle_b: Dict[str, Any],
) -> Dict[str, Any]:
    rv_a = _extract_runtime_and_verdict(bundle_a)
    rv_b = _extract_runtime_and_verdict(bundle_b)

    prc_a = _extract_primary_root_cause(bundle_a)
    prc_b = _extract_primary_root_cause(bundle_b)
    conf_a = prc_a.get("confidence")
    conf_b = prc_b.get("confidence")
    conf_delta = (
        round(float(conf_b) - float(conf_a), 6)
        if isinstance(conf_a, (int, float)) and isinstance(conf_b, (int, float))
        else None
    )

    checks_a = _extract_failed_checks(bundle_a)
    checks_b = _extract_failed_checks(bundle_b)
    map_a = {str(c.get("name") or f"unnamed_{i}"): c for i, c in enumerate(checks_a)}
    map_b = {str(c.get("name") or f"unnamed_{i}"): c for i, c in enumerate(checks_b)}

    def _compact_check(c: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": c.get("name", "unnamed"),
            "layer": c.get("layer"),
            "failure_kind": c.get("failure_kind"),
            "detail": c.get("detail", ""),
            "artifact_comparison": c.get("artifact_comparison"),
        }

    only_in_a = [_compact_check(map_a[k]) for k in map_a if k not in map_b]
    only_in_b = [_compact_check(map_b[k]) for k in map_b if k not in map_a]
    shared = []
    for k in map_a:
        if k not in map_b:
            continue
        ca, cb = map_a[k], map_b[k]
        shared.append(
            {
                "name": k,
                "layer_a": ca.get("layer"),
                "layer_b": cb.get("layer"),
                "detail_a": ca.get("detail", ""),
                "detail_b": cb.get("detail", ""),
                "artifact_comparison_a": ca.get("artifact_comparison"),
                "artifact_comparison_b": cb.get("artifact_comparison"),
                "numeric_deltas": _compute_numeric_deltas(ca, cb),
            }
        )

    anchors_a = _extract_causal_anchors(bundle_a)
    anchors_b = _extract_causal_anchors(bundle_b)
    causal_anchors_diff: Dict[str, Any] = {}
    for slot in ("algorithm_or_param_definition", "deliverable_generation", "self_check"):
        da = _anchor_digest(slot, anchors_a.get(slot))
        db = _anchor_digest(slot, anchors_b.get(slot))
        slot_diff: Dict[str, Any] = {
            "a": da,
            "b": db,
            "changed": da != db,
        }
        if slot == "algorithm_or_param_definition":
            evo_a = list((da or {}).get("parameter_evolution") or [])
            evo_b = list((db or {}).get("parameter_evolution") or [])
            if evo_a or evo_b:
                slot_diff["parameter_evolution_a"] = evo_a
                slot_diff["parameter_evolution_b"] = evo_b
                slot_diff["parameter_evolution_diff"] = _diff_parameter_evolution(evo_a, evo_b)
        causal_anchors_diff[slot] = slot_diff

    return {
        "target_a": {"label": label_a, "report_dir": str(dir_a)},
        "target_b": {"label": label_b, "report_dir": str(dir_b)},
        "runtime_and_verdict": {
            "a": rv_a,
            "b": rv_b,
            "changed": rv_a != rv_b,
        },
        "primary_root_cause": {
            "a": prc_a,
            "b": prc_b,
            "confidence_delta": conf_delta,
            "changed": (
                (prc_a.get("category"), prc_a.get("subtype"), prc_a.get("code"))
                != (prc_b.get("category"), prc_b.get("subtype"), prc_b.get("code"))
            ),
        },
        "failed_checks_diff": {
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "shared": shared,
        },
        "causal_anchors_diff": causal_anchors_diff,
    }


def _render_diff_markdown(diff_data: Dict[str, Any]) -> str:
    label_a = diff_data["target_a"]["label"]
    label_b = diff_data["target_b"]["label"]
    rv = diff_data["runtime_and_verdict"]
    prc = diff_data["primary_root_cause"]
    fcd = diff_data["failed_checks_diff"]
    cad = diff_data["causal_anchors_diff"]

    lines = [
        f"# CFA Diff: `{label_a}` vs `{label_b}`",
        "",
        f"- **Target A**: `{label_a}` (`{diff_data['target_a']['report_dir']}`)",
        f"- **Target B**: `{label_b}` (`{diff_data['target_b']['report_dir']}`)",
        "",
        "## 1. Runtime & Verdict",
        "",
        f"| Field | `{label_a}` | `{label_b}` | Changed |",
        "| :--- | :--- | :--- | :---: |",
    ]
    for field in ("verdict", "reward", "execution_status", "verification_status"):
        va = rv["a"].get(field)
        vb = rv["b"].get(field)
        chg = "yes" if va != vb else "no"
        lines.append(f"| `{field}` | `{va}` | `{vb}` | {chg} |")
    lines.append("")

    lines.extend(
        [
            "## 2. Primary Root Cause",
            "",
            f"| Field | `{label_a}` | `{label_b}` |",
            "| :--- | :--- | :--- |",
            f"| `category` | `{prc['a'].get('category')}` | `{prc['b'].get('category')}` |",
            f"| `subtype` | `{prc['a'].get('subtype')}` | `{prc['b'].get('subtype')}` |",
            f"| `code` | `{prc['a'].get('code')}` | `{prc['b'].get('code')}` |",
            f"| `confidence` | `{prc['a'].get('confidence')}` | `{prc['b'].get('confidence')}` |",
        ]
    )
    if prc.get("confidence_delta") is not None:
        delta = prc["confidence_delta"]
        sign = f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}"
        lines.append(f"\n- **Confidence Delta (B - A)**: `{sign}`")
    lines.append("")

    only_a = fcd["only_in_a"]
    only_b = fcd["only_in_b"]
    shared = fcd["shared"]
    lines.append("## 3. Failed Checks Diff")
    lines.append("")
    lines.append(f"### Only Failing in A (`{label_a}`): {len(only_a)}")
    if not only_a:
        lines.append("*(none)*")
    else:
        for c in only_a:
            lines.append(
                f"- `[{c.get('layer') or '—'}]` **`{c['name']}`**: {c.get('detail') or '—'}"
            )
    lines.append("")

    lines.append(f"### Only Failing in B (`{label_b}`): {len(only_b)}")
    if not only_b:
        lines.append("*(none)*")
    else:
        for c in only_b:
            lines.append(
                f"- `[{c.get('layer') or '—'}]` **`{c['name']}`**: {c.get('detail') or '—'}"
            )
    lines.append("")

    lines.append(f"### Shared Failing Checks: {len(shared)}")
    if not shared:
        lines.append("*(none)*")
    else:
        for s in shared:
            lines.append(f"- **`{s['name']}`** (layers: `{s.get('layer_a') or '—'}` vs `{s.get('layer_b') or '—'}`)")
            if s.get("detail_a") == s.get("detail_b"):
                lines.append(f"  - Detail: {s.get('detail_a') or '—'}")
            else:
                lines.append(f"  - Detail A: {s.get('detail_a') or '—'}")
                lines.append(f"  - Detail B: {s.get('detail_b') or '—'}")
            if s.get("artifact_comparison_a") is not None:
                lines.append(
                    f"  - Artifact Comparison A: `{json.dumps(s['artifact_comparison_a'], ensure_ascii=False)}`"
                )
            if s.get("artifact_comparison_b") is not None:
                lines.append(
                    f"  - Artifact Comparison B: `{json.dumps(s['artifact_comparison_b'], ensure_ascii=False)}`"
                )
            for nd in s.get("numeric_deltas") or []:
                lines.append(
                    f"  - Numeric Delta (`{nd['metric']}`): "
                    f"delivered `{nd['a_delivered']}` -> `{nd['b_delivered']}` (delta=`{nd['delivered_delta']}`), "
                    f"abs_diff `{nd['a_abs_diff']}` -> `{nd['b_abs_diff']}` (delta=`{nd['abs_diff_delta']}`)"
                )
    lines.append("")

    lines.append("## 4. Causal Anchors Diff")
    lines.append("")
    for slot in ("algorithm_or_param_definition", "deliverable_generation", "self_check"):
        info = cad.get(slot) or {}
        lines.append(f"### `{slot}` (changed=`{info.get('changed')}`)")
        lines.append(f"- **A**: `{json.dumps(info.get('a'), ensure_ascii=False)}`")
        lines.append(f"- **B**: `{json.dumps(info.get('b'), ensure_ascii=False)}`")
        evo_diff = info.get("parameter_evolution_diff")
        if evo_diff:
            lines.append("- **Parameter Evolution**:")
            for row in evo_diff:
                lines.append(
                    f"  - `{row['param']}`: A (`{row['a_initial']}` -> `{row['a_final']}`) "
                    f"vs B (`{row['b_initial']}` -> `{row['b_final']}`)"
                )
        lines.append("")

    return "\n".join(lines).strip()


def cmd_diff(args: argparse.Namespace) -> int:
    """Side-by-side comparison of two audited tasks or trials."""
    if args.target_b is not None and args.trials is not None:
        print(
            "ERROR: cannot combine positional <target_b> with --trials.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.target_b is not None:
            dir_a, label_a = _resolve_diff_target(
                args.target_a, jobs_root=args.jobs_root, report_dir=args.report_dir
            )
            dir_b, label_b = _resolve_diff_target(
                args.target_b, jobs_root=args.jobs_root, report_dir=args.report_dir
            )
        else:
            base_dir, base_label = _resolve_base_report_dir(
                args.target_a, jobs_root=args.jobs_root, report_dir=args.report_dir
            )
            trials_root = base_dir / "trials"
            if args.trials:
                t1_name, t2_name = args.trials
                dir_a = (trials_root / t1_name).resolve()
                dir_b = (trials_root / t2_name).resolve()
                for tname, tdir in ((t1_name, dir_a), (t2_name, dir_b)):
                    if not tdir.is_dir() or not _is_audited_report_dir(tdir):
                        avail = (
                            [d.name for d in sorted(trials_root.iterdir()) if d.is_dir()]
                            if trials_root.is_dir()
                            else []
                        )
                        raise ValueError(
                            f"trial '{tname}' not found or not audited under '{trials_root}'. "
                            f"Available: {avail or 'none'}"
                        )
                label_a = t1_name
                label_b = t2_name
            else:
                dir_a, dir_b = _select_two_latest_trials(trials_root)
                label_a = dir_a.name
                label_b = dir_b.name
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    bundle_a = _load_report_bundle(dir_a, basename_hint=label_a)
    bundle_b = _load_report_bundle(dir_b, basename_hint=label_b)
    diff_payload = _build_diff_payload(label_a, dir_a, bundle_a, label_b, dir_b, bundle_b)

    if args.raw:
        output_text = json.dumps(diff_payload, indent=2, ensure_ascii=False)
    else:
        output_text = _render_diff_markdown(diff_payload)

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
        choices=[
            "verdict",
            "report",
            "errors",
            "confidence",
            "evidence",
            "anchors",
            "artifacts",
            "step",
            "data",
        ],
        default="verdict",
        help="Report section or data to inspect (default: verdict)",
    )
    inspect_parser.add_argument(
        "--step",
        default=None,
        help="Step number or event ID prefix to filter when using --section step",
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

    # diff subcommand
    diff_parser = subparsers.add_parser(
        "diff", help="Compare two audited tasks or trials side-by-side"
    )
    diff_parser.add_argument(
        "target_a", help="First task ID, trial ref, or report directory"
    )
    diff_parser.add_argument(
        "target_b",
        nargs="?",
        default=None,
        help="Optional second task ID, trial ref, or report directory",
    )
    diff_parser.add_argument(
        "--trials",
        nargs=2,
        metavar=("TRIAL_A", "TRIAL_B"),
        default=None,
        help="Compare two trial subdirectories under <report_dir>/trials/",
    )
    diff_parser.add_argument(
        "--jobs-root", type=Path, default=None, help="Benchmark checkout root"
    )
    diff_parser.add_argument(
        "--report-dir", type=Path, default=None, help="Report directory override"
    )
    diff_parser.add_argument(
        "--raw", action="store_true", help="Output structured JSON diff"
    )
    diff_parser.add_argument(
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
    elif subcommand == "diff":
        args = diff_parser.parse_args(argv[1:])
        return cmd_diff(args)
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
