#!/usr/bin/env python3
"""
Phase 5: Scientific Software Error Knowledge Layer (`scripts/extract_scientific_errors.py`)

Loads the single-source-of-truth error registry from `references/error-families.json`
and runs deterministic error-family adapters across all 11 scientific computing and MLIP suites
(60 error families across CP2K, Quantum ESPRESSO, VASP/ABACUS, ORCA/Gaussian/PySCF, LAMMPS,
GROMACS/AMBER/OpenMM, MLIP, ASE, xTB, RDKit, and Generic Scientific) over trajectory observations and verifier logs.
Outputs structured `scientific_observations` with `candidate_causes`, `aliases`, and
`required_discriminating_evidence` without jumping directly to agent blame.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "references" / "error-families.json"

AdapterEntry = Tuple[str, str, List[str], re.Pattern[str], List[str], List[str]]

# Verifier logs scanned by the knowledge-base family adapters (section 3) and by
# the deterministic recompute-divergence parser (section 4). Order matters: the
# canonical `<trial>/verifier/verify.log` is scanned first.
_VERIFIER_LOG_REL_PATHS = (
    "verifier/verify.log",
    "verifier/test-stdout.txt",
    "verifier/pytest.log",
    "verify.log",
)

# Raw ATIF trajectory.json files larger than this are skipped for agent-side
# corroboration (corroboration is best-effort; never load unbounded files).
MAX_TRAJECTORY_JSON_BYTES = 4_000_000


def load_error_family_registry(registry_path: Path = REGISTRY_PATH) -> Dict[str, Dict[str, Any]]:
    if not registry_path.is_file():
        raise FileNotFoundError(f"Scientific error family registry not found: {registry_path}")
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"Scientific error family registry must be a non-empty JSON object: {registry_path}"
        )
    return data


def build_scientific_adapters(
    registry_path: Path = REGISTRY_PATH,
) -> List[AdapterEntry]:
    reg = load_error_family_registry(registry_path)
    adapters: List[AdapterEntry] = []
    for software, families in reg.items():
        if not isinstance(families, dict):
            raise ValueError(f"Invalid family mapping for software {software!r} in {registry_path}")
        for family_name, spec in families.items():
            if not isinstance(spec, dict):
                raise ValueError(
                    f"Invalid family spec for {software}.{family_name} in {registry_path}"
                )
            status = spec.get("status")
            if status not in ("implemented", "planned"):
                raise ValueError(
                    f"Invalid status {status!r} for {software}.{family_name} in {registry_path}"
                )
            if status != "implemented":
                continue
            patterns = spec.get("patterns") or []
            if not patterns:
                raise ValueError(
                    f"Implemented error family {software}.{family_name} has no regex patterns in {registry_path}"
                )
            combined = "(?:" + "|".join(patterns) + ")"
            compiled = re.compile(combined, re.IGNORECASE)
            aliases = spec.get("aliases") or []
            causes = spec.get("candidate_causes") or []
            req_ev = spec.get("required_discriminating_evidence") or []
            adapters.append((software, family_name, aliases, compiled, causes, req_ev))
    if not adapters:
        raise RuntimeError(
            f"Zero implemented scientific error adapters loaded from {registry_path}"
        )
    return adapters


SCIENTIFIC_ADAPTERS: List[AdapterEntry] = build_scientific_adapters()


def extract_scientific_errors(
    trial_dir: Optional[Path],
    normalized_traj: Optional[Dict[str, Any]] = None,
    verifier_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    observations: List[Dict[str, Any]] = []
    seen = set()
    sci_counter = 1

    # 1. Scan trajectory events and track whether later simulation runs recovered
    if normalized_traj:
        traj_events = normalized_traj.get("events") or []
        sim_bins = (
            "cp2k",
            "pw.x",
            "bands.x",
            "lmp",
            "lammps",
            "xtb",
            "vasp",
            "abacus",
            "orca",
            "g16",
            "g09",
            "pyscf",
            "psi4",
            "nwchem",
            "gmx",
            "mdrun",
            "pmemd",
            "sander",
            "openmm",
            "mace",
            "nequip",
            "deepmd",
            "dp ",
            "chgnet",
            "sevennet",
            "openfoam",
            "fenics",
        )
        for ev_idx, ev in enumerate(traj_events):
            text = f"{ev.get('command') or ''}\n{ev.get('observation') or ''}"
            ev_id = ev.get("event_id", "trajectory:unknown")
            for sw, family, aliases, pattern, causes, req_ev in SCIENTIFIC_ADAPTERS:
                m = pattern.search(text)
                if m:
                    dedup_key = (sw, family, m.group(0)[:80])
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    # Check if a subsequent trajectory step ran a simulation and succeeded without this error
                    recovery_ref: Optional[str] = None
                    for later_ev in traj_events[ev_idx + 1 :]:
                        later_cmd = (later_ev.get("command") or "").strip()
                        later_obs = later_ev.get("observation") or ""
                        later_exit = later_ev.get("exit_code")
                        later_text = f"{later_cmd}\n{later_obs}"
                        is_sim_cmd = any(b in later_cmd for b in sim_bins)
                        if (
                            is_sim_cmd
                            and later_exit == 0
                            and later_ev.get("event_type") != "shell_error"
                            and not pattern.search(later_text)
                            and not any(
                                p[3].search(later_text) for p in SCIENTIFIC_ADAPTERS if p[0] == sw
                            )
                        ):
                            recovery_ref = later_ev.get("event_id")
                            break

                    observations.append(
                        {
                            "sci_id": f"sci:{sci_counter}",
                            "software": sw,
                            "error_family": family,
                            "aliases": aliases,
                            "reference_anchor": f"references/software/{sw}.md#{family}",
                            "matched_text": m.group(0)[:240],
                            "source_ref": ev_id,
                            "recovered": recovery_ref is not None,
                            "recovery_event_ref": recovery_ref,
                            "candidate_causes": causes,
                            "required_discriminating_evidence": req_ev,
                        }
                    )
                    sci_counter += 1

    # 2. Fallback scan of agent logs when trajectory.json has no events
    if not (normalized_traj and normalized_traj.get("events")) and trial_dir:
        for rel_log in (
            "agent/claude-code.txt",
            "agent/codex.txt",
            "agent/agent.log",
            "agent/stdout.txt",
            "agent/output.log",
        ):
            cc_path = trial_dir / rel_log
            if cc_path.is_file():
                cc_text = _read_bounded_text(cc_path)
                for sw, family, aliases, pattern, causes, req_ev in SCIENTIFIC_ADAPTERS:
                    m = pattern.search(cc_text)
                    if m:
                        dedup_key = (sw, family, m.group(0)[:80])
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        observations.append(
                            {
                                "sci_id": f"sci:{sci_counter}",
                                "software": sw,
                                "error_family": family,
                                "aliases": aliases,
                                "reference_anchor": f"references/software/{sw}.md#{family}",
                                "matched_text": _extract_match_line(cc_text, m)[:240],
                                "source_ref": "art:claude_code_txt"
                                if rel_log == "agent/claude-code.txt"
                                else rel_log,
                                "candidate_causes": causes,
                                "required_discriminating_evidence": req_ev,
                            }
                        )
                        sci_counter += 1

    # 3. Scan verifier logs (verify.log, test-stdout.txt, pytest.log)
    if trial_dir:
        for rel_vlog in _VERIFIER_LOG_REL_PATHS:
            vpath = trial_dir / rel_vlog
            if vpath.is_file():
                vlog = _read_bounded_text(vpath)
                for sw, family, aliases, pattern, causes, req_ev in SCIENTIFIC_ADAPTERS:
                    m = pattern.search(vlog)
                    if m:
                        dedup_key = (sw, family, m.group(0)[:80])
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        observations.append(
                            {
                                "sci_id": f"sci:{sci_counter}",
                                "software": sw,
                                "error_family": family,
                                "aliases": aliases,
                                "reference_anchor": f"references/software/{sw}.md#{family}",
                                "matched_text": _extract_match_line(vlog, m)[:240],
                                "source_ref": "verifier/verify.log"
                                if rel_vlog.startswith("verifier/")
                                else rel_vlog,
                                "candidate_causes": causes,
                                "required_discriminating_evidence": req_ev,
                            }
                        )
                        sci_counter += 1

    # 4. Deterministic verifier recompute-divergence parsing over numeric
    #    FAIL/exception lines in verifier logs. Unlike the knowledge-base
    #    adapters above, this parses the verifier's own recomputed numbers, so
    #    `scientific_observations` gains structured numeric evidence even when
    #    no software-specific family pattern matches. Observations are appended
    #    after the family observations, continuing the `sci:N` ordinal sequence
    #    as `sci:recompute_divergence:<n>`.
    if trial_dir:
        trajectory_steps = _load_trajectory_steps(trial_dir)
        for rel_vlog in _VERIFIER_LOG_REL_PATHS:
            vpath = trial_dir / rel_vlog
            if not vpath.is_file():
                continue
            source_label = (
                f"verifier:{vpath.name}" if rel_vlog.startswith("verifier/") else vpath.name
            )
            for divergence in extract_verifier_divergences(
                _read_numbered_lines(vpath),
                trajectory_steps=trajectory_steps,
                source_label=source_label,
            ):
                dedup_key = divergence["matched_text"]
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                divergence["sci_id"] = f"sci:recompute_divergence:{sci_counter}"
                observations.append(divergence)
                sci_counter += 1

    # 5. Structured verifier report scan (raw-verifier.json, verify.json, pytest summary).
    #    Captures named check divergences and software errors directly from structured failed checks
    #    and messages, ensuring that failures not written to plain verify.log are surfaced.
    if verifier_report and isinstance(verifier_report, dict):
        trajectory_steps = _load_trajectory_steps(trial_dir) if trial_dir else []
        failed_checks = verifier_report.get("failed_checks") or []
        for fc in failed_checks:
            if not isinstance(fc, dict):
                continue
            detail = (fc.get("detail") or "").strip()
            name = fc.get("name") or "check"
            src = fc.get("source") or "verifier_report"
            source_ref = f"verifier:{src}:{name}" if name else f"verifier:{src}"

            if detail:
                for sw, family, aliases, pattern, causes, req_ev in SCIENTIFIC_ADAPTERS:
                    m = pattern.search(detail)
                    if m:
                        dedup_key = (sw, family, m.group(0)[:80])
                        if dedup_key not in seen:
                            seen.add(dedup_key)
                            observations.append(
                                {
                                    "sci_id": f"sci:{sci_counter}",
                                    "software": sw,
                                    "error_family": family,
                                    "aliases": aliases,
                                    "reference_anchor": f"references/software/{sw}.md#{family}",
                                    "matched_text": detail[:240],
                                    "source_ref": source_ref,
                                    "candidate_causes": causes,
                                    "required_discriminating_evidence": req_ev,
                                }
                            )
                            sci_counter += 1

                hit = _match_divergence_line(detail)
                if hit is not None:
                    dedup_key = detail
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        agent_matches, agent_value, agent_ref = _corroborate_agent_reported_value(
                            hit["metric"],
                            hit["reported_raw"],
                            hit["reported_value"],
                            trajectory_steps,
                        )
                        observations.append(
                            {
                                "sci_id": f"sci:recompute_divergence:{sci_counter}",
                                "software": VERIFIER_SOFTWARE_LABEL,
                                "error_family": VERIFIER_DIVERGENCE_FAMILY,
                                "aliases": list(VERIFIER_DIVERGENCE_ALIASES),
                                "reference_anchor": None,
                                "matched_text": detail,
                                "source_ref": source_ref,
                                "metric": hit["metric"],
                                "reported_value": hit["reported_value"],
                                "recomputed_value": hit["recomputed_value"],
                                "tolerance": hit["tolerance"],
                                "check_kind": hit["check_kind"],
                                "agent_reported_value": agent_value,
                                "agent_ref": agent_ref,
                                "agent_reported_matches": agent_matches,
                                "discriminating": (
                                    hit["reported_value"] is not None and hit["recomputed_value"] is not None
                                ),
                                "candidate_causes": [],
                                "required_discriminating_evidence": list(VERIFIER_DIVERGENCE_EVIDENCE),
                            }
                        )
                        sci_counter += 1

        for msg in verifier_report.get("messages") or []:
            if not isinstance(msg, str):
                continue
            msg_str = msg.strip()
            if not msg_str or msg_str in seen:
                continue
            hit = _match_divergence_line(msg_str)
            if hit is not None:
                seen.add(msg_str)
                agent_matches, agent_value, agent_ref = _corroborate_agent_reported_value(
                    hit["metric"],
                    hit["reported_raw"],
                    hit["reported_value"],
                    trajectory_steps,
                )
                observations.append(
                    {
                        "sci_id": f"sci:recompute_divergence:{sci_counter}",
                        "software": VERIFIER_SOFTWARE_LABEL,
                        "error_family": VERIFIER_DIVERGENCE_FAMILY,
                        "aliases": list(VERIFIER_DIVERGENCE_ALIASES),
                        "reference_anchor": None,
                        "matched_text": msg_str,
                        "source_ref": "verifier:report:messages",
                        "metric": hit["metric"],
                        "reported_value": hit["reported_value"],
                        "recomputed_value": hit["recomputed_value"],
                        "tolerance": hit["tolerance"],
                        "check_kind": hit["check_kind"],
                        "agent_reported_value": agent_value,
                        "agent_ref": agent_ref,
                        "agent_reported_matches": agent_matches,
                        "discriminating": (
                            hit["reported_value"] is not None and hit["recomputed_value"] is not None
                        ),
                        "candidate_causes": [],
                        "required_discriminating_evidence": list(VERIFIER_DIVERGENCE_EVIDENCE),
                    }
                )
                sci_counter += 1

    return {"scientific_observations": observations}


def _read_bounded_text(path: Path, max_bytes: int = 200000) -> str:
    try:
        file_size = path.stat().st_size
        if file_size <= max_bytes:
            return path.read_bytes().decode("utf-8", errors="replace")
        half = max_bytes // 2
        with path.open("rb") as f:
            head = f.read(half)
            f.seek(max(0, file_size - half))
            tail = f.read(half)
        return (
            head.decode("utf-8", errors="replace")
            + "\n... [TRUNCATED] ...\n"
            + tail.decode("utf-8", errors="replace")
        )
    except OSError:
        return ""


def _extract_match_line(text: str, match: re.Match[str]) -> str:
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    return line or match.group(0)


# ---------------------------------------------------------------------------
# Verifier recompute-divergence parsing (verifier logs -> scientific_observations)
#
# Textbook verifier failures carry deterministic recomputed numbers, e.g.
#   FAIL: reported max_force 0.02863 != recomputed 0.03666 (tol 0.001)
#   FAIL: final energy -0.02125869 eV differs from ref -0.02125870
#   FAIL: results.json n_msd_rows must be 100, got 51
#   FAIL: expected 10 irreducible k-points, got 11
# The patterns below are matched in priority order (P1..P4); each qualifying
# line yields at most one observation (first hit wins).
# ---------------------------------------------------------------------------

VERIFIER_SOFTWARE_LABEL = "verifier"
VERIFIER_DIVERGENCE_FAMILY = "verifier_recompute_divergence"
VERIFIER_DIVERGENCE_ALIASES = ("numerical_divergence",)
# Schema-compat guidance strings: the attribution gates index
# `required_discriminating_evidence` / `candidate_causes` on the first
# unrecovered scientific observation, so verifier-sourced observations must
# carry both keys to keep the existing observation schema compatible.
VERIFIER_DIVERGENCE_EVIDENCE = (
    "reported_value",
    "recomputed_value",
    "tolerance",
    "agent_reported_value",
)

_NUMBER = r"[-+0-9.eE]+"
_INT = r"[-+0-9]+"

# P1: `reported [<metric>] <rep> != recomputed [<metric>] <rec> (tol <tol>)`
# with optional tolerance. The metric label between `reported`/`recomputed`
# and the number is optional so both `reported max_force 0.02863 != ...` (the
# real corpus form) and `reported 0.02863 != ...` parse.
_P1_METRIC_LABEL = r"(?:[A-Za-z_][A-Za-z0-9_.]*\s+)?"
_P1_RECOMPUTE_TOLERANCE = re.compile(
    rf"reported {_P1_METRIC_LABEL}(?P<rep>{_NUMBER}) != recomputed {_P1_METRIC_LABEL}"
    rf"(?P<rec>{_NUMBER})(?: \(tol (?P<tol>{_NUMBER})\))?"
)
# P2: `<metric> <rep>[ <unit>] differs from [the] <ref|reference|recomputed|
# expected|pinned> <rec>`. The optional unit token (e.g. `eV`, `eV/A`) is a
# conservative superset of the reference pattern so textbook lines such as
# `final energy -0.02125869 eV differs from ref ...` still parse.
_P2_REF_KEYWORDS = r"(?:ref|reference|recomputed|expected|pinned)"
_P2_REFERENCE_MISMATCH = re.compile(
    rf"(?P<metric>[A-Za-z_][A-Za-z0-9_]*) (?P<rep>{_NUMBER})"
    rf"(?:\s+[^\s,;]+)?\s+differs from (?:the )?{_P2_REF_KEYWORDS} (?P<rec>{_NUMBER})"
)
# P2B: `<target> <metric> differs from <ref> by <delta>[ <unit>] beyond [its ]bound <tol>`
_P2B_DELTA_MISMATCH = re.compile(
    rf"(?P<metric>[A-Za-z_][A-Za-z0-9_]*)\s+differs from (?:the )?[^,;:]+?\s+by\s+"
    rf"(?P<delta>{_NUMBER})(?:\s+[^\s,;]+)?\s+beyond (?:its )?(?:[a-z_ ]+ )?bound\s+(?P<tol>{_NUMBER})"
)
# P3: integer count mismatches (`must be N, got M` / `expected N ..., got M`).
_P3A_MUST_BE_COUNT = re.compile(rf"must be (?P<rec>{_INT}), got (?P<rep>{_INT})")
_P3B_EXPECTED_COUNT = re.compile(rf"expected (?P<rec>{_INT})[^,;]*, got (?P<rep>{_INT})")
# P4: fallback for FAIL lines with a bare numeric inequality.
_P4_FAIL_INEQUALITY = re.compile(rf"(?P<rep>{_NUMBER})\s*!=\s*(?P<rec>{_NUMBER})")

_DIVERGENCE_PATTERNS: List[Tuple[re.Pattern[str], str, bool]] = [
    (_P1_RECOMPUTE_TOLERANCE, "recompute_divergence", False),
    (_P2_REFERENCE_MISMATCH, "reference_mismatch", False),
    (_P2B_DELTA_MISMATCH, "reference_mismatch", False),
    (_P3A_MUST_BE_COUNT, "count_mismatch", False),
    (_P3B_EXPECTED_COUNT, "count_mismatch", False),
    (_P4_FAIL_INEQUALITY, "recompute_divergence", True),
]

# Only numeric FAIL/exception lines are considered divergence candidates.
_DIVERGENCE_LINE_MARKER = re.compile(r"FAIL|\bTraceback\b|\w*Error\b|\bException\b|\bassert\b")
_HAS_DIGIT = re.compile(r"\d")

# Best-effort metric extraction: prefer known metric keywords, else the first
# identifier-like token that is not verifier boilerplate.
_METRIC_KEYWORD_RE = re.compile(
    r"max_force|final_energy|energy|n_[a-z_]+|n_clusters|n_rotatable_bonds|cons_qty\w*|\bD\b"
)
_METRIC_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_METRIC_STOPWORDS = frozenset(
    {
        "fail",
        "failed",
        "failure",
        "reported",
        "recomputed",
        "expected",
        "got",
        "must",
        "be",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "to",
        "of",
        "in",
        "on",
        "with",
        "for",
        "and",
        "or",
        "not",
        "no",
        "value",
        "values",
        "ref",
        "reference",
        "differs",
        "from",
        "tol",
        "tolerance",
        "pinned",
        "check",
        "checked",
        "verify",
        "verifier",
        "verification",
        "line",
        "actual",
        "result",
        "results",
        "json",
        "log",
        "product",
        "but",
        "it",
        "its",
        "this",
        "that",
        "number",
        "count",
        "mismatch",
        "error",
        "exception",
        "traceback",
        "assert",
        "assertion",
        "nan",
        "inf",
        "vs",
        "by",
        "at",
        "as",
        "than",
        "then",
    }
)


def _parse_number(raw: Optional[str], as_int: bool = False) -> Optional[Union[int, float]]:
    """Parse a regex-captured numeric token; return None when unparseable."""
    if raw is None:
        return None
    try:
        return int(raw) if as_int else float(raw)
    except (TypeError, ValueError):
        return None


def _extract_metric_from_line(line: str) -> Optional[str]:
    """Best-effort metric name extraction from a verifier log line."""
    keyword = _METRIC_KEYWORD_RE.search(line)
    if keyword:
        return keyword.group(0)
    for m in _METRIC_IDENTIFIER_RE.finditer(line):
        if m.group(0).lower() not in _METRIC_STOPWORDS:
            return m.group(0)
    return None


def _match_divergence_line(line: str) -> Optional[Dict[str, Any]]:
    """Match one verifier line against P1..P4 in order (first hit wins)."""
    for pattern, check_kind, requires_fail in _DIVERGENCE_PATTERNS:
        if requires_fail and "FAIL" not in line:
            continue
        m = pattern.search(line)
        if not m:
            continue
        gd = m.groupdict()
        if check_kind == "count_mismatch":
            reported = _parse_number(gd.get("rep"), as_int=True)
            recomputed = _parse_number(gd.get("rec"), as_int=True)
            tolerance = None
            reported_raw = gd.get("rep")
        elif "delta" in gd:
            delta = _parse_number(gd.get("delta"))
            tolerance = _parse_number(gd.get("tol"))
            reported = delta
            recomputed = 0.0
            reported_raw = gd.get("delta")
        else:
            reported = _parse_number(gd.get("rep"))
            recomputed = _parse_number(gd.get("rec"))
            tolerance = _parse_number(gd.get("tol"))
            reported_raw = gd.get("rep")
        metric = gd.get("metric") or _extract_metric_from_line(line)
        res = {
            "check_kind": check_kind,
            "metric": metric,
            "reported_raw": reported_raw,
            "reported_value": reported,
            "recomputed_value": recomputed,
            "tolerance": tolerance,
        }
        if "delta" in gd:
            res["delta_value"] = delta
        return res
    return None


def _value_string_candidates(raw: Optional[str], parsed: Any) -> List[str]:
    """String forms of the reported value to search for in trajectory text."""
    candidates: List[str] = []
    if raw:
        candidates.append(raw)
        if "-" in raw:
            # ATIF trajectory prose often typesets U+2212 MINUS SIGN where the
            # verifier log prints an ASCII hyphen-minus.
            candidates.append(raw.replace("-", "\u2212"))
    if isinstance(parsed, float):
        candidates.append(repr(parsed))
    elif isinstance(parsed, int):
        candidates.append(str(parsed))
    unique: List[str] = []
    for cand in candidates:
        if cand and cand not in unique:
            unique.append(cand)
    return unique


def _corroborate_agent_reported_value(
    metric: Optional[str],
    reported_raw: Optional[str],
    reported_value: Any,
    trajectory_steps: Optional[List[Tuple[Any, str]]],
) -> Tuple[bool, Any, Optional[str]]:
    """Search serialized trajectory steps for the metric + reported value pair.

    A step corroborates when both the metric name and one string form of the
    reported value occur within the same step's serialized text. Unparseable
    reported values are never corroborated.
    """
    if reported_value is None or not trajectory_steps:
        return False, None, None
    value_candidates = _value_string_candidates(reported_raw, reported_value)
    for idx, (step_id, step_text) in enumerate(trajectory_steps):
        if not step_text:
            continue
        metric_present = metric is None or metric in step_text
        if metric_present and any(cand in step_text for cand in value_candidates):
            ref_id = step_id if step_id is not None else idx + 1
            return True, reported_value, f"trajectory:step:{ref_id}"
    return False, None, None


def _load_trajectory_steps(trial_dir: Optional[Path]) -> List[Tuple[Any, str]]:
    """Load (step_id, serialized step text) pairs from the trial's raw trajectory.

    The raw ATIF `agent/trajectory.json` is used (not the normalized events) so
    corroboration sees the agent's own verbatim numbers. Any read/parse failure
    yields an empty list: corroboration is best-effort and must never fabricate.
    """
    if trial_dir is None:
        return []
    traj_path = trial_dir / "agent" / "trajectory.json"
    try:
        if not traj_path.is_file() or traj_path.stat().st_size > MAX_TRAJECTORY_JSON_BYTES:
            return []
        data = json.loads(traj_path.read_bytes().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    serialized: List[Tuple[Any, str]] = []
    for step in steps:
        step_id = step.get("step_id") if isinstance(step, dict) else None
        try:
            text = json.dumps(step, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(step)
        serialized.append((step_id, text))
    return serialized


def _read_numbered_lines(path: Path, max_bytes: int = 2_000_000) -> List[Tuple[int, str]]:
    """Read a verifier log as (1-based line number, line text) pairs.

    Files up to `max_bytes` are read whole so line numbers are exact. Larger
    files keep the head and tail halves; the skipped middle is only counted for
    newlines so tail line numbers stay true (the split point may bisect one
    line, which is acceptable for a best-effort parse of oversized logs).
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) <= max_bytes:
        text = data.decode("utf-8", errors="replace")
        return [(i + 1, ln) for i, ln in enumerate(text.splitlines())]
    half = max_bytes // 2
    head = data[:half]
    tail = data[len(data) - half :]
    middle_newlines = data[half : len(data) - half].count(b"\n")
    tail_start = head.count(b"\n") + middle_newlines + 1
    head_lines = head.decode("utf-8", errors="replace").splitlines()
    numbered = [(i + 1, ln) for i, ln in enumerate(head_lines)]
    tail_lines = tail.decode("utf-8", errors="replace").splitlines()
    numbered.extend((tail_start + i, ln) for i, ln in enumerate(tail_lines))
    return numbered


def extract_verifier_divergences(
    numbered_lines: List[Tuple[int, str]],
    trajectory_steps: Optional[List[Tuple[Any, str]]] = None,
    source_label: str = "verifier:verify.log",
) -> List[Dict[str, Any]]:
    """Parse numbered verifier-log lines into recompute-divergence observations.

    `numbered_lines` holds (1-based line number, line text) pairs. Only lines
    that contain a digit and a FAIL/exception marker are considered; each line
    is matched against P1..P4 in order and yields at most one observation.
    `trajectory_steps` (from `_load_trajectory_steps`) enables best-effort
    agent-side corroboration of the reported value.
    """
    results: List[Dict[str, Any]] = []
    for lineno, raw_line in numbered_lines:
        line = raw_line.strip()
        if not line or not _HAS_DIGIT.search(line):
            continue
        if not _DIVERGENCE_LINE_MARKER.search(line):
            continue
        hit = _match_divergence_line(line)
        if hit is None:
            continue
        agent_matches, agent_value, agent_ref = _corroborate_agent_reported_value(
            hit["metric"],
            hit["reported_raw"],
            hit["reported_value"],
            trajectory_steps,
        )
        results.append(
            {
                "software": VERIFIER_SOFTWARE_LABEL,
                "error_family": VERIFIER_DIVERGENCE_FAMILY,
                "aliases": list(VERIFIER_DIVERGENCE_ALIASES),
                "reference_anchor": None,
                "matched_text": line,
                "source_ref": f"{source_label}:L{lineno}",
                "metric": hit["metric"],
                "reported_value": hit["reported_value"],
                "recomputed_value": hit["recomputed_value"],
                "tolerance": hit["tolerance"],
                "check_kind": hit["check_kind"],
                "agent_reported_value": agent_value,
                "agent_ref": agent_ref,
                "agent_reported_matches": agent_matches,
                "discriminating": (
                    hit["reported_value"] is not None and hit["recomputed_value"] is not None
                ),
                "candidate_causes": [],
                "required_discriminating_evidence": list(VERIFIER_DIVERGENCE_EVIDENCE),
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract scientific software error observations.")
    parser.add_argument(
        "--trial-dir", required=False, type=Path, default=None, help="Path to trial directory"
    )
    parser.add_argument(
        "--trajectory-norm",
        required=False,
        type=Path,
        default=None,
        help="Normalized trajectory JSON",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    norm_traj = None
    if args.trajectory_norm and args.trajectory_norm.is_file():
        norm_traj = json.loads(args.trajectory_norm.read_text(encoding="utf-8", errors="replace"))

    res = extract_scientific_errors(trial_dir=args.trial_dir, normalized_traj=norm_traj)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
