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
import csv
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

from normalize_trajectory import _parse_trajectory_payload, extract_exit_code_and_error
from runtime_state import resolve_trajectory_path


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

# Deliverable files probed for the agent's actually delivered metric value.
# Only structured CSV/TSV/JSON records are mined, because only those can bind a
# value to the named record in the verifier message (`R02-M01/site000003`).
# Free prose is never searched for numbers: that is how a verifier's own delta
# used to be misread as the agent's delivered value.
_DELIVERABLE_DELIMITERS = {".csv": ",", ".tsv": "\t"}
MAX_DELIVERABLE_BYTES = 8_000_000
_MAX_DELIVERABLE_FILES = 200
_MAX_DELIVERABLE_ROWS = 200_000
_MAX_JSON_NODES = 20_000

# Unit suffixes stripped when matching a metric name (`charge`) to a column or
# JSON key (`charge_e`, `charge_eV`); only exact base-token equality counts.
_UNIT_TOKENS = frozenset(
    {
        "e",
        "ev",
        "eh",
        "ha",
        "hartree",
        "ry",
        "ryd",
        "kcal",
        "kj",
        "mol",
        "a",
        "ang",
        "angstrom",
        "bohr",
        "nm",
        "ps",
        "fs",
        "k",
        "bar",
        "gpa",
        "mpa",
    }
)

# Record identifiers such as `R02-M01` / `site000003`: contain both a letter
# and a digit, so units (`eV`), ranks (`L3`), and bare numbers are excluded.
_SELECTOR_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?=[A-Za-z0-9_.-]{3,})"
    r"(?=[A-Za-z0-9_.-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_.-]*\d)"
    r"[A-Za-z][A-Za-z0-9_.-]*"
    r"(?![A-Za-z0-9_.-])"
)
_SELECTOR_STOP_RE = re.compile(r"^L\d+$", re.IGNORECASE)


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
    seen_divergence_keys: Set[Tuple[Any, Any, Any, Any]] = set()
    if trial_dir:
        trajectory_steps = _load_trajectory_steps(trial_dir)
        for rel_vlog in _VERIFIER_LOG_REL_PATHS:
            vpath = trial_dir / rel_vlog
            if not vpath.is_file():
                continue
            source_label = (
                f"verifier:{vpath.name}" if rel_vlog.startswith("verifier/") else vpath.name
            )
            numbered_lines = _read_numbered_lines(vpath)
            # If the log is a single raw JSON report dump, let Section 5's structured parser handle it
            if len(numbered_lines) <= 5 and any(
                ln.strip().startswith("{") and ln.strip().endswith("}") for _, ln in numbered_lines
            ):
                continue
            for divergence in extract_verifier_divergences(
                numbered_lines,
                trajectory_steps=trajectory_steps,
                source_label=source_label,
                trial_dir=trial_dir,
            ):
                div_key = (
                    divergence.get("metric"),
                    divergence.get("reported_value"),
                    divergence.get("recomputed_value"),
                    divergence.get("tolerance"),
                )
                if div_key in seen_divergence_keys:
                    continue
                seen_divergence_keys.add(div_key)
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
                    div_key = (
                        hit.get("metric"),
                        hit.get("reported_value"),
                        hit.get("recomputed_value"),
                        hit.get("tolerance"),
                    )
                    if div_key not in seen_divergence_keys:
                        seen_divergence_keys.add(div_key)
                        dedup_key = detail
                        if dedup_key not in seen:
                            seen.add(dedup_key)
                            observations.append(
                                _build_divergence_observation(
                                    hit=hit,
                                    matched_text=detail,
                                    source_ref=source_ref,
                                    trajectory_steps=trajectory_steps,
                                    trial_dir=trial_dir,
                                    sci_id=f"sci:recompute_divergence:{sci_counter}",
                                )
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
                div_key = (
                    hit.get("metric"),
                    hit.get("reported_value"),
                    hit.get("recomputed_value"),
                    hit.get("tolerance"),
                )
                if div_key not in seen_divergence_keys:
                    seen_divergence_keys.add(div_key)
                    seen.add(msg_str)
                    observations.append(
                        _build_divergence_observation(
                            hit=hit,
                            matched_text=msg_str,
                            source_ref="verifier:report:messages",
                            trajectory_steps=trajectory_steps,
                            trial_dir=trial_dir,
                            sci_id=f"sci:recompute_divergence:{sci_counter}",
                        )
                    )
                    sci_counter += 1

    # Attach artifact comparisons onto failed checks if verifier report and trial artifacts exist
    if verifier_report and trial_dir:
        pair_verifier_evidence_with_artifacts(verifier_report, trial_dir)

    return {"scientific_observations": observations}


def _collect_artifact_numbers(trial_dir: Path) -> List[Tuple[str, float, str]]:
    """Scan JSON and CSV files under <trial>/artifacts and extract numeric records.

    Returns a list of (metric_key, delivered_value, artifact_ref).
    """
    root = trial_dir / "artifacts"
    if not root.is_dir():
        return []

    records: List[Tuple[str, float, str]] = []
    scanned = 0

    for path in sorted(root.rglob("*")):
        if scanned >= 100:
            break
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in (".json", ".csv", ".tsv"):
            continue
        try:
            if path.stat().st_size > MAX_DELIVERABLE_BYTES:
                continue
        except OSError:
            continue
        scanned += 1

        try:
            rel_path = path.relative_to(trial_dir).as_posix()
        except ValueError:
            rel_path = path.name

        art_ref = f"art:{rel_path}"

        if suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue

            def _walk_json(node: Any, prefix: str = "", target_ref: str = art_ref) -> None:
                if len(records) >= 2000:
                    return
                if isinstance(node, dict):
                    for k, v in node.items():
                        full_key = f"{prefix}.{k}" if prefix else str(k)
                        _walk_json(v, full_key, target_ref)
                elif isinstance(node, list):
                    for idx, v in enumerate(node):
                        full_key = f"{prefix}[{idx}]"
                        _walk_json(v, full_key, target_ref)
                elif isinstance(node, (int, float)) and not isinstance(node, bool):
                    if math.isfinite(node):
                        leaf = prefix.split(".")[-1].split("[")[0]
                        records.append((leaf, float(node), target_ref))

            _walk_json(data)

        elif suffix in (".csv", ".tsv"):
            delimiter = "," if suffix == ".csv" else "\t"
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
                for row_idx, row in enumerate(reader):
                    if row_idx >= 200:
                        break
                    for col_name, val_str in row.items():
                        if not val_str or not col_name:
                            continue
                        try:
                            val = float(val_str.strip())
                            if math.isfinite(val):
                                records.append((col_name.strip(), val, art_ref))
                        except ValueError:
                            continue
            except Exception:
                continue

    return records


_UNIT_CONVERSION_FACTORS: Tuple[Tuple[float, str, Tuple[str, ...]], ...] = (
    (27.211386, "Hartree/eV", ("hartree", "ha", "eh", "ev")),
    (627.5095, "Hartree/kcal/mol", ("hartree", "ha", "eh", "kcal")),
    (23.06055, "eV/kcal/mol", ("ev", "kcal")),
    (96.4853, "eV/kJ/mol", ("ev", "kj")),
    (4.184, "kcal/kJ", ("kcal", "kj")),
    (0.529177, "Bohr/Angstrom", ("bohr", "ang", "angstrom")),
)
_DECADIC_FACTORS: Tuple[float, ...] = (10.0, 100.0, 1000.0)


def _has_unit_token_corroboration(
    unit_tokens: Tuple[str, ...],
    metric: Optional[str],
    context_text: str,
) -> bool:
    """True when any expected unit token appears in `metric` or `context_text`."""
    haystack = f"{metric or ''} {context_text or ''}".lower()
    if not haystack.strip():
        return False
    tokens = {tok for tok in re.split(r"[^a-z0-9]+", haystack) if tok}
    return any(u in tokens for u in unit_tokens)


def detect_scale_or_sign_factor(
    delivered: Optional[float],
    recomputed: Optional[float],
    metric: Optional[str] = None,
    context_text: str = "",
) -> Optional[Dict[str, Any]]:
    """Detect common physical unit conversions, decadic scaling, or sign convention flips.

    Returns ``{"kind": ..., "factor": ..., "label": ..., "unit_token_corroborated": ...}``
    when ``delivered`` and ``recomputed`` are non-zero and match within 1.5% relative error
    for unit/decadic factors (in either direction) or within 2% magnitude error for a sign flip.
    """
    if delivered is None or recomputed is None:
        return None
    if isinstance(delivered, bool) or isinstance(recomputed, bool):
        return None
    try:
        d = float(delivered)
        r = float(recomputed)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(d) and math.isfinite(r)) or d == 0.0 or r == 0.0:
        return None

    # 1. Sign convention flip: delivered ≈ -recomputed within 2% relative magnitude error
    if (d > 0.0 and r < 0.0) or (d < 0.0 and r > 0.0):
        rel_mag_err = abs(d + r) / abs(r)
        if rel_mag_err <= 0.02:
            return {
                "kind": "sign_flip",
                "factor": -1.0,
                "label": "sign convention flip (delivered ≈ -recomputed)",
                "unit_token_corroborated": False,
            }
        return None

    # 2. Same-sign physical unit conversion & decadic scaling within 1.5% relative error
    d_over_r = d / r
    r_over_d = r / d
    best_match: Optional[Tuple[float, Dict[str, Any]]] = None

    for factor, name, unit_tokens in _UNIT_CONVERSION_FACTORS:
        corroborated = _has_unit_token_corroboration(unit_tokens, metric, context_text)
        err_fwd = abs(d_over_r - factor) / factor
        if err_fwd <= 0.015:
            cand = {
                "kind": "unit_conversion",
                "factor": factor,
                "label": f"{name} (×{factor})",
                "unit_token_corroborated": corroborated,
            }
            if best_match is None or err_fwd < best_match[0]:
                best_match = (err_fwd, cand)
        err_rev = abs(r_over_d - factor) / factor
        if err_rev <= 0.015:
            cand = {
                "kind": "unit_conversion",
                "factor": round(1.0 / factor, 6),
                "label": f"{name} (÷{factor})",
                "unit_token_corroborated": corroborated,
            }
            if best_match is None or err_rev < best_match[0]:
                best_match = (err_rev, cand)

    if best_match is not None:
        return best_match[1]

    for factor in _DECADIC_FACTORS:
        err_fwd = abs(d_over_r - factor) / factor
        if err_fwd <= 0.015:
            cand = {
                "kind": "decadic_scale",
                "factor": factor,
                "label": f"decadic scale (×{int(factor)})",
                "unit_token_corroborated": False,
            }
            if best_match is None or err_fwd < best_match[0]:
                best_match = (err_fwd, cand)
        err_rev = abs(r_over_d - factor) / factor
        if err_rev <= 0.015:
            cand = {
                "kind": "decadic_scale",
                "factor": round(1.0 / factor, 6),
                "label": f"decadic scale (÷{int(factor)})",
                "unit_token_corroborated": False,
            }
            if best_match is None or err_rev < best_match[0]:
                best_match = (err_rev, cand)

    return best_match[1] if best_match is not None else None


def pair_verifier_evidence_with_artifacts(verifier_report: Dict[str, Any], trial_dir: Path) -> None:
    """Pair structured verifier evidence arrays/dicts with delivered artifacts.

    Attaches 'artifact_comparison' and 'evidence_comparison' onto matching failed
    checks without emitting false verifier_recompute_divergence observations.
    """
    if not isinstance(verifier_report, dict) or not trial_dir:
        return
    failed_checks = verifier_report.get("failed_checks") or []
    if not failed_checks:
        return

    artifact_numbers = _collect_artifact_numbers(trial_dir)
    if not artifact_numbers:
        return

    used_candidates: Set[Tuple[str, float, str]] = set()
    _skip_hint_keys = frozenset(
        {"tolerance", "tol", "bound", "threshold", "diff", "abs_diff", "rel_diff"}
    )

    for fc in failed_checks:
        if not isinstance(fc, dict):
            continue
        ev = fc.get("evidence")
        if not isinstance(ev, dict) or not ev:
            continue

        targets: List[Tuple[Optional[str], float]] = []

        # 1. Array of recomputed values (e.g. recomputed_eV: [IP0, IP1, chi, J])
        recomp_ev = ev.get("recomputed_eV")
        if isinstance(recomp_ev, list):
            for x in recomp_ev:
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    targets.append((None, float(x)))
        elif isinstance(recomp_ev, (int, float)) and not isinstance(recomp_ev, bool):
            targets.append((None, float(recomp_ev)))

        # 2. Scalar numeric expected / recomputed / reference keys
        for key in ("recomputed", "expected", "reference", "ref"):
            if key in ev and isinstance(ev[key], (int, float)) and not isinstance(ev[key], bool):
                m_hint = ev.get("metric") or fc.get("name")
                targets.append((str(m_hint) if m_hint else None, float(ev[key])))

        # 3. Other scalar float/int metrics in evidence (e.g. IP0_eV: 7.48...)
        for k, v in ev.items():
            if k in (
                "recomputed_eV",
                "recomputed",
                "expected",
                "reference",
                "ref",
                "selected",
                "metric",
            ):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                targets.append((str(k), float(v)))

        comparisons: List[Dict[str, Any]] = []

        for metric_hint, rec_val in targets:
            best_cand: Optional[Tuple[str, float, str]] = None
            best_rank: Tuple[int, float] = (999, float("inf"))

            for cand_key, delivered_val, art_ref in artifact_numbers:
                diff = abs(delivered_val - rec_val)
                rel_diff = diff / (abs(rec_val) + 1e-12)

                if metric_hint:
                    m_low = metric_hint.lower()
                    c_low = cand_key.lower()
                    if c_low == m_low:
                        tier = 0
                    elif _metric_key_matches(metric_hint, cand_key):
                        tier = 1
                    else:
                        m_stem = _normalized_metric_stem(metric_hint)
                        c_stem = _normalized_metric_stem(cand_key)
                        if m_stem and c_stem and m_stem == c_stem:
                            tier = 1
                        elif (
                            len(m_low) >= 3
                            and len(c_low) >= 3
                            and m_low not in _skip_hint_keys
                            and (m_low in c_low or c_low in m_low)
                        ):
                            tier = 2
                        else:
                            continue
                    # Named metric match binds without rejecting large deviations
                    rank = (tier, diff)
                    if rank < best_rank:
                        best_rank = rank
                        best_cand = (cand_key, delivered_val, art_ref)
                else:
                    # Numerical proximity match (applied only when metric_hint is None)
                    cand_ident = (cand_key, delivered_val, art_ref)
                    if cand_ident in used_candidates:
                        continue
                    if (rel_diff < 0.05 or diff < 0.05) and (0, diff) < best_rank:
                        best_rank = (0, diff)
                        best_cand = (cand_key, delivered_val, art_ref)

            if best_cand is not None:
                used_candidates.add(best_cand)
                cand_key, delivered_val, art_ref = best_cand
                comp: Dict[str, Any] = {
                    "metric": cand_key,
                    "delivered": delivered_val,
                    "recomputed": rec_val,
                    "abs_diff": abs(delivered_val - rec_val),
                    "artifact_ref": art_ref,
                }
                hint = detect_scale_or_sign_factor(
                    delivered_val,
                    rec_val,
                    metric=cand_key,
                    context_text=f"{fc.get('detail') or ''} {metric_hint or ''}",
                )
                if hint is not None:
                    comp["scale_or_sign_hint"] = hint
                comparisons.append(comp)

        if comparisons:
            fc["artifact_comparison"] = comparisons
            fc["evidence_comparison"] = comparisons


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
    "agent_artifact_ref",
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

# Only numeric FAIL/exception/mismatch lines are considered divergence candidates.
_DIVERGENCE_LINE_MARKER = re.compile(
    r"FAIL|\bTraceback\b|\w*Error\b|\bException\b|\bassert\b|differs from|mismatch",
    re.IGNORECASE,
)
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
            res["is_delta_divergence"] = True
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


def _metric_tokens(value: Any) -> List[str]:
    """Lower-case base tokens of a metric/key, with unit suffixes removed."""
    return [
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if token and token not in _UNIT_TOKENS
    ]


def _metric_key_matches(metric: Optional[str], key: Any) -> bool:
    """True when `key` names the same quantity as `metric` (`charge`/`charge_e`)."""
    metric_tokens = _metric_tokens(metric)
    key_tokens = _metric_tokens(key)
    return bool(metric_tokens) and metric_tokens == key_tokens


def _normalized_metric_stem(value: Any) -> str:
    """Return unit-stripped alphanumeric stem for fuzzy metric key matching."""
    tokens = _metric_tokens(value)
    if tokens:
        return "".join(tokens)
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _selector_tokens(text: str, metric: Optional[str]) -> List[str]:
    """Record identifiers in a verifier message (`R02-M01`, `site000003`)."""
    metric_norm = (metric or "").strip().lower()
    tokens: List[str] = []
    for match in _SELECTOR_TOKEN_RE.finditer(text or ""):
        token = match.group(0)
        if _SELECTOR_STOP_RE.match(token) or token.lower() == metric_norm:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _artifact_container_path(artifact_ref: Optional[str]) -> Optional[str]:
    """Map `art:app/outputs/charges.csv:L628` to the container path."""
    if not artifact_ref or not artifact_ref.startswith("art:"):
        return None
    body = re.sub(r":L\d+$", "", artifact_ref[4:])
    parts = body.split("/")
    if parts and parts[0] == "artifacts":
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] == "app":
        return "/" + "/".join(parts)
    return None


def _parse_csv_delivered_value(
    path: Path,
    metric: Optional[str],
    selectors: List[str],
    trial_dir: Path,
) -> Optional[Tuple[float, str]]:
    """Read a metric value bound to every named record from a CSV/TSV file."""
    delimiter = _DELIVERABLE_DELIMITERS.get(path.suffix.lower())
    if delimiter is None or not selectors:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            header = [name for name in (reader.fieldnames or []) if name]
            metric_columns = [name for name in header if _metric_key_matches(metric, name)]
            if not metric_columns:
                return None
            token_columns: Dict[str, Set[str]] = {token: set() for token in selectors}
            rows: List[Tuple[int, Dict[str, str]]] = []
            for row in reader:
                if reader.line_num > _MAX_DELIVERABLE_ROWS:
                    break
                values = {
                    name: ("" if value is None else str(value).strip())
                    for name, value in row.items()
                    if name
                }
                hit = False
                for token in selectors:
                    for column in header:
                        if values.get(column) == token:
                            token_columns[token].add(column)
                            hit = True
                if hit:
                    rows.append((reader.line_num, values))
    except (OSError, csv.Error):
        return None

    # Every identifier in the verifier message must bind to exactly one column.
    # A token that appears in no column (or in several) means we cannot prove the
    # row is the same record, so abstain rather than match on a subset.
    if any(len(columns) != 1 for columns in token_columns.values()):
        return None
    bound = {token: next(iter(columns)) for token, columns in token_columns.items()}
    selected = [
        (line, values)
        for line, values in rows
        if all(values.get(column) == token for token, column in bound.items())
    ]
    if not selected:
        return None

    numeric: List[Tuple[float, int]] = []
    for line, values in selected:
        for column in metric_columns:
            try:
                value = float(values.get(column, ""))
            except ValueError:
                continue
            if math.isfinite(value):
                numeric.append((value, line))
                break
    if not numeric:
        return None
    # Conflicting values for the same record cannot be resolved truthfully.
    if len({round(value, 15) for value, _ in numeric}) != 1:
        return None
    value, line = numeric[0]
    try:
        rel_path = path.relative_to(trial_dir).as_posix()
    except ValueError:
        rel_path = path.name
    return value, f"art:{rel_path}:L{line}"


def _iter_json_metric_records(
    node: Any,
    metric: Optional[str],
    selectors: List[str],
    path: str = "$",
) -> Iterator[Tuple[float, str]]:
    """Yield metric values from JSON records that carry every selector."""
    stack: List[Tuple[Any, str]] = [(node, path)]
    visited = 0
    while stack:
        current, current_path = stack.pop()
        visited += 1
        if visited > _MAX_JSON_NODES:
            return
        if isinstance(current, dict):
            metric_keys = [key for key in current if _metric_key_matches(metric, key)]
            if metric_keys and selectors:
                scalars = {
                    str(value).strip()
                    for value in current.values()
                    if isinstance(value, (str, int, float)) and not isinstance(value, bool)
                }
                if all(token in scalars for token in selectors):
                    for key in metric_keys:
                        value = current[key]
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            yield float(value), f"{current_path}.{key}"
            for key, value in current.items():
                stack.append((value, f"{current_path}.{key}"))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                stack.append((value, f"{current_path}[{index}]"))


def _find_delivered_metric_value(
    trial_dir: Optional[Path],
    metric: Optional[str],
    match_text: str,
) -> Optional[Tuple[float, str]]:
    """Locate the agent's actually delivered value for `metric`.

    Searches structured deliverable files under `<trial>/artifacts` for a record
    that carries every identifier in the verifier message and returns that
    record's metric value. Returns `None` when no unique bound value exists;
    the caller must then abstain rather than reuse the verifier's delta.
    """
    if trial_dir is None or not metric:
        return None
    root = trial_dir / "artifacts"
    if not root.is_dir():
        return None
    selectors = _selector_tokens(match_text, metric)
    if not selectors:
        return None

    candidates: List[Tuple[float, str]] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if scanned >= _MAX_DELIVERABLE_FILES:
            break
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in _DELIVERABLE_DELIMITERS and suffix != ".json":
            continue
        try:
            if path.stat().st_size > MAX_DELIVERABLE_BYTES:
                continue
        except OSError:
            continue
        scanned += 1
        if suffix == ".json":
            try:
                data = json.loads(path.read_bytes().decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            try:
                rel_path = path.relative_to(trial_dir).as_posix()
            except ValueError:
                rel_path = path.name
            for value, json_path in _iter_json_metric_records(data, metric, selectors):
                if math.isfinite(value):
                    candidates.append((value, f"art:{rel_path}:{json_path}"))
        else:
            hit = _parse_csv_delivered_value(path, metric, selectors, trial_dir)
            if hit is not None:
                candidates.append(hit)

    if not candidates:
        return None
    if len({round(value, 15) for value, _ in candidates}) != 1:
        return None
    return candidates[0]


def _find_metric_execution_ref(
    trajectory_steps: Optional[List[Tuple[Any, str]]],
    metric: Optional[str],
    artifact_ref: Optional[str],
) -> Optional[str]:
    """Reference the earliest execution step that produced the bound artifact."""
    artifact_path = _artifact_container_path(artifact_ref)
    artifact_name = PurePosixPath(artifact_path).name.lower() if artifact_path else ""
    artifact_dir = str(PurePosixPath(artifact_path).parent).lower() if artifact_path else ""
    metric_lower = (metric or "").strip().lower()

    producers: List[str] = []
    mentions: List[str] = []
    for step_id, serialized in trajectory_steps or []:
        if not serialized:
            continue
        tool_text = serialized
        observation = ""
        try:
            payload = json.loads(serialized)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            tool_text = json.dumps(payload.get("tool_calls") or [], ensure_ascii=False)
            observation = str(payload.get("observation") or "")
        tool_low = tool_text.lower()
        ref = f"trajectory:step:{step_id}"
        if metric_lower and metric_lower in (tool_low + "\n" + observation.lower()):
            mentions.append(ref)

        writes_artifact = bool(artifact_name and artifact_name in tool_low)
        writes_output_dir = bool(
            artifact_dir
            and artifact_dir in tool_low
            and (
                "--output" in tool_low
                or " -o " in tool_low
                or re.search(r"(?<!\d)>(?!>)", tool_low)
                or " cp " in tool_low
                or "tee " in tool_low
            )
        )
        if writes_artifact or writes_output_dir:
            producers.append(ref)
    if producers:
        return producers[0]
    return mentions[0] if mentions else None


def _corroborate_agent_reported_value(
    metric: Optional[str],
    reported_raw: Optional[str],
    reported_value: Any,
    trajectory_steps: Optional[List[Tuple[Any, str]]],
    is_delta_divergence: bool = False,
    trial_dir: Optional[Path] = None,
    match_text: str = "",
) -> Tuple[bool, Any, Optional[str], Optional[str]]:
    """Bind a divergence to the agent's own delivered value.

    Returns `(matches, agent_value, agent_ref, agent_artifact_ref)`.

    - For absolute divergences, `matches` is true only when the metric and one
      string form of the verifier-reported value occur in the same execution
      step's command/tool output.
    - For delta divergences the verifier reports a deviation, not an absolute
      agent value. The agent value is read from a structured deliverable record
      that carries every identifier in the verifier message; `matches` stays
      false because there is no absolute value to compare. When no such record
      exists, all fields are `None` — the verifier's delta is NEVER returned as
      the agent's value.
    """
    if is_delta_divergence:
        delivered = _find_delivered_metric_value(trial_dir, metric, match_text)
        if delivered is None:
            return False, None, None, None
        value, artifact_ref = delivered
        agent_ref = _find_metric_execution_ref(trajectory_steps, metric, artifact_ref)
        return False, value, agent_ref, artifact_ref

    if reported_value is None or not trajectory_steps:
        return False, None, None, None

    value_candidates = _value_string_candidates(reported_raw, reported_value)
    for idx, (step_id, step_text) in enumerate(trajectory_steps):
        if not step_text:
            continue
        metric_present = metric is None or metric in step_text
        if metric_present and any(cand in step_text for cand in value_candidates):
            ref_id = step_id if step_id is not None else idx + 1
            return True, reported_value, f"trajectory:step:{ref_id}", None
    return False, None, None, None


_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?(?![A-Za-z0-9_.])"
)


def _extract_observation_text(step_text: str) -> str:
    """Extract only execution observation output from a serialized step (never tool_calls/command)."""
    if not step_text:
        return ""
    try:
        payload = json.loads(step_text)
    except (TypeError, ValueError):
        return step_text
    if not isinstance(payload, dict):
        return step_text
    if "observation" not in payload:
        return ""
    obs = payload.get("observation")
    if obs is None:
        return ""
    if isinstance(obs, str):
        stripped = obs.strip()
        if stripped.startswith("{") and '"results"' in stripped:
            try:
                obs_obj = json.loads(stripped)
                if isinstance(obs_obj, dict) and isinstance(obs_obj.get("results"), list):
                    parts = [
                        str(r.get("content") or "")
                        for r in obs_obj["results"]
                        if isinstance(r, dict) and r.get("content")
                    ]
                    return "\n".join(parts)
            except ValueError:
                pass
        return obs
    if isinstance(obs, dict) and isinstance(obs.get("results"), list):
        parts = [
            str(r.get("content") or "")
            for r in obs.get("results")
            if isinstance(r, dict) and r.get("content")
        ]
        return "\n".join(parts)
    return json.dumps(obs, ensure_ascii=False, default=str)


def _step_contains_recomputed_value(
    step_text: str,
    metric: Optional[str],
    rec_val: float,
    rep_val: Optional[float],
) -> bool:
    """True when error-free execution observation text contains `metric` and `rec_val` (>=4 sig figs)."""
    obs_text = _extract_observation_text(step_text)
    if not obs_text.strip():
        return False
    _, has_err = extract_exit_code_and_error(obs_text)
    if has_err:
        return False
    if metric is not None and metric not in obs_text and metric.lower() not in obs_text.lower():
        return False
    normalized = obs_text.replace("\u2212", "-")
    for match in _NUMERIC_TOKEN_RE.finditer(normalized):
        tok = match.group(0)
        mantissa = tok.lstrip("+-").split("e", 1)[0].split("E", 1)[0].replace(".", "")
        sig_digits = len(mantissa.lstrip("0"))
        if sig_digits < 4:
            continue
        try:
            tok_val = float(tok)
        except ValueError:
            continue
        if not math.isfinite(tok_val):
            continue
        if abs(tok_val - rec_val) <= max(1e-9, abs(rec_val) * 5e-4):
            if rep_val is not None:
                if abs(tok_val - rep_val) <= abs(tok_val - rec_val):
                    continue
                if abs(tok_val - rec_val) >= abs(rep_val - rec_val) * 0.1:
                    continue
            return True
    return False


def _detect_stale_deliverable_sync(
    metric: Optional[str],
    reported_value: Any,
    recomputed_value: Any,
    trajectory_steps: Optional[List[Tuple[Any, str]]],
    trial_dir: Optional[Path] = None,
    is_delta_divergence: bool = False,
    artifact_numbers: Optional[List[Tuple[str, float, str]]] = None,
) -> Optional[str]:
    """Return `trajectory:step:<id>` when an on-disk deliverable holds `reported_value` while an error-free observation printed `recomputed_value`."""
    if (
        trial_dir is None
        or is_delta_divergence
        or recomputed_value is None
        or isinstance(recomputed_value, bool)
        or not trajectory_steps
    ):
        return None
    try:
        rec_val = float(recomputed_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rec_val):
        return None

    rep_val: Optional[float] = None
    if reported_value is not None and not isinstance(reported_value, bool):
        try:
            parsed_rep = float(reported_value)
            if math.isfinite(parsed_rep):
                rep_val = parsed_rep
        except (TypeError, ValueError):
            rep_val = None
    if rep_val is not None and rep_val == rec_val:
        return None

    if artifact_numbers is None:
        artifact_numbers = _collect_artifact_numbers(trial_dir)
    matching_art_vals = [
        val
        for key, val, _ in artifact_numbers
        if metric is None
        or _metric_key_matches(metric, key)
        or _normalized_metric_stem(metric) == _normalized_metric_stem(key)
    ]
    if not matching_art_vals:
        return None
    for art_val in matching_art_vals:
        if abs(art_val - rec_val) <= max(1e-9, abs(rec_val) * 1e-6):
            return None
    if rep_val is not None and not any(
        abs(art_val - rep_val) <= max(1e-9, abs(rep_val) * 1e-4) for art_val in matching_art_vals
    ):
        return None

    for idx, (step_id, step_text) in enumerate(trajectory_steps):
        if _step_contains_recomputed_value(step_text, metric, rec_val, rep_val):
            ref_id = step_id if step_id is not None else idx + 1
            return f"trajectory:step:{ref_id}"
    return None


def _build_divergence_observation(
    hit: Dict[str, Any],
    matched_text: str,
    source_ref: str,
    trajectory_steps: Optional[List[Tuple[Any, str]]],
    trial_dir: Optional[Path],
    sci_id: Optional[str] = None,
    artifact_numbers: Optional[List[Tuple[str, float, str]]] = None,
) -> Dict[str, Any]:
    """Construct a structured `verifier_recompute_divergence` observation."""
    is_delta = bool(hit.get("is_delta_divergence", False))
    agent_matches, agent_value, agent_ref, agent_artifact_ref = _corroborate_agent_reported_value(
        hit["metric"],
        hit["reported_raw"],
        hit["reported_value"],
        trajectory_steps,
        is_delta_divergence=is_delta,
        trial_dir=trial_dir,
        match_text=matched_text,
    )
    scale_hint: Optional[Dict[str, Any]] = None
    if not is_delta and hit.get("check_kind") != "count_mismatch":
        scale_hint = detect_scale_or_sign_factor(
            hit.get("reported_value"),
            hit.get("recomputed_value"),
            metric=hit.get("metric"),
            context_text=matched_text,
        )
    stale_sync_ref = _detect_stale_deliverable_sync(
        hit.get("metric"),
        hit.get("reported_value"),
        hit.get("recomputed_value"),
        trajectory_steps,
        trial_dir=trial_dir,
        is_delta_divergence=is_delta,
        artifact_numbers=artifact_numbers,
    )

    obs: Dict[str, Any] = {}
    if sci_id is not None:
        obs["sci_id"] = sci_id
    obs.update(
        {
            "software": VERIFIER_SOFTWARE_LABEL,
            "error_family": VERIFIER_DIVERGENCE_FAMILY,
            "aliases": list(VERIFIER_DIVERGENCE_ALIASES),
            "reference_anchor": None,
            "matched_text": matched_text,
            "source_ref": source_ref,
            "metric": hit["metric"],
            "reported_value": hit["reported_value"],
            "recomputed_value": hit["recomputed_value"],
            "tolerance": hit["tolerance"],
            "check_kind": hit["check_kind"],
            **({"is_delta_divergence": True} if is_delta else {}),
            "agent_reported_value": agent_value,
            "agent_ref": agent_ref,
            "agent_artifact_ref": agent_artifact_ref,
            "agent_reported_matches": agent_matches,
            "discriminating": (
                hit["reported_value"] is not None and hit["recomputed_value"] is not None
            ),
            **({"scale_or_sign_hint": scale_hint} if scale_hint is not None else {}),
            **(
                {
                    "stale_deliverable_sync": True,
                    "recomputed_match_in_trajectory_ref": stale_sync_ref,
                }
                if stale_sync_ref is not None
                else {}
            ),
            "candidate_causes": [],
            "required_discriminating_evidence": list(VERIFIER_DIVERGENCE_EVIDENCE),
        }
    )
    return obs


def _load_trajectory_steps(trial_dir: Optional[Path]) -> List[Tuple[Any, str]]:
    """Load (step_id, execution text) pairs from the trial's raw trajectory.

    Only execution payloads are serialized: tool calls and their observations.
    Assistant messages and reasoning/thinking text are deliberately excluded —
    a final answer that mentions a number is not evidence that the agent
    produced it, and must never be bound as the delivered value.

    Supports ATIF JSON, JSON message arrays, and JSONL streams via
    `resolve_trajectory_path` and `_parse_trajectory_payload`.
    Any read/parse failure yields an empty list: corroboration is best-effort
    and must never fabricate.
    """
    if trial_dir is None:
        return []
    traj_path = resolve_trajectory_path(trial_dir)
    if traj_path is None:
        return []
    try:
        if not traj_path.is_file() or traj_path.stat().st_size > MAX_TRAJECTORY_JSON_BYTES:
            return []
        raw_text = traj_path.read_bytes().decode("utf-8", errors="replace")
        data = _parse_trajectory_payload(raw_text)
    except (OSError, ValueError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    serialized: List[Tuple[Any, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        observation = step.get("observation")
        if isinstance(observation, (dict, list)):
            observation = json.dumps(observation, ensure_ascii=False, default=str)
        observation = str(observation or "")
        tool_calls = step.get("tool_calls") or []
        command = step.get("command") or ""
        if not tool_calls and not observation and not command:
            continue
        payload = {
            "tool_calls": tool_calls,
            "observation": observation,
        }
        if command:
            payload["command"] = command
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(payload)
        serialized.append((step.get("step_id"), text))
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
    trial_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Parse numbered verifier-log lines into recompute-divergence observations.

    `numbered_lines` holds (1-based line number, line text) pairs. Only lines
    that contain a digit and a FAIL/exception marker are considered; each line
    is matched against P1..P4 in order and yields at most one observation.
    `trajectory_steps` (from `_load_trajectory_steps`) enables best-effort
    agent-side corroboration; `trial_dir` lets delta divergences bind to the
    agent's actually delivered value in `artifacts/`.
    """
    results: List[Dict[str, Any]] = []
    artifact_numbers = _collect_artifact_numbers(trial_dir) if trial_dir is not None else None
    for lineno, raw_line in numbered_lines:
        line = raw_line.strip()
        if not line or not _HAS_DIGIT.search(line):
            continue
        if not _DIVERGENCE_LINE_MARKER.search(line):
            continue
        hit = _match_divergence_line(line)
        if hit is None:
            continue
        results.append(
            _build_divergence_observation(
                hit=hit,
                matched_text=line,
                source_ref=f"{source_label}:L{lineno}",
                trajectory_steps=trajectory_steps,
                trial_dir=trial_dir,
                artifact_numbers=artifact_numbers,
            )
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
