#!/usr/bin/env python3
"""
Normalized Verifier Report (`scripts/normalize_verifier_report.py`)

Harbor-style benchmark tasks do **not** share one verifier output schema. In the
CompChemWorld job set the same "did the science pass?" information is spread
across at least five shapes:

- layered per-check lists (`layers: {"preflight": [...], "L1": [...], "L3": [...]}`)
  in `verifier/core/verify.json` (33-wpd-loop) and
  `verifier/scientific/verify.json` (35-binding-pose-metad);
- a `criteria` list in `verifier/raw-verifier.json` (32-qeq-electrostatics);
- a flat `checks` mapping in `verifier/native/verify.json` (37-ml-reaction-space)
  and `verifier/science.json` (38-co2-double-layer);
- a scoring *envelope* in `verifier/verify.json` / `verification.json`
  (`status`, `scientific_status`, `reward`, `failure_origin`, `core_failures`,
  `scientific_report`, ...);
- embedded copies of a layered report under an envelope key
  (`scientific_report` in the ccw35 envelope).

The old evidence collector only read `verifier/verify.log`, so for all of these
trials the failed check names, per-check deviation values, `core_failures` and
`ACADEMIC_INTEGRITY_VETO`-style qualitative verdicts were invisible. This module
walks every JSON file under the trial's `verifier/` directory, flattens the
check-like structures into one list, and returns a stable, schema-agnostic dict
that evidence, gates, reports and the batch board can all consume.

Design constraints:

- Zero dependencies, stdlib only (matches the rest of `scripts/`).
- Never raises on malformed / hostile input: an unreadable or oversized JSON file
  is skipped and recorded in `skipped_files`.
- Read-only, deterministic ordering (path-sorted), bounded file size/count.
- No absolute paths are emitted — only paths relative to `<trial>/verifier`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

#: Hard cap on any single verifier JSON file and the number of files walked.
MAX_REPORT_BYTES = 4_000_000
MAX_REPORT_FILES = 64

#: Verdict words. `SCORED` is deliberately NOT a pass word: harbor envelopes
#: commonly say `status=SCORED, scientific_status=FAIL, reward=0`.
PASS_WORDS = frozenset(
    {
        "PASS",
        "PASSED",
        "OK",
        "TRUE",
        "VALID",
        "COMPLETE",
        "SCIENTIFIC_PASS",
        "ACCEPTED",
        "SUCCESS",
        "YES",
    }
)
FAIL_WORDS = frozenset(
    {
        "FAIL",
        "FAILED",
        "SCIENTIFIC_FAIL",
        "ERROR",
        "ERRORED",
        "INVALID",
        "ACADEMIC_INTEGRITY_VETO",
        "VETO",
        "NOT_VERIFIED",
        "NOT_SCORED",
        "NOT_ATTACHED",
        "INCOMPLETE",
        "REJECTED",
        "ABORTED",
    }
)
#: Statuses that describe the *scoring process*, not the science outcome.
NEUTRAL_WORDS = frozenset({"SCORED", "SCORING", "PENDING", "UNKNOWN", "UNSCORED", "N/A"})

STATUS_KEYS = (
    "verdict",
    "overall",
    "result",
    "scientific_status",
    "scientific_verifier_status",
    "native_overall",
    "agent_result",
    "structured_result",
    "numerical_status",
    "status",
    "preflight",
)
MESSAGE_KEYS = ("message", "reason", "detail", "scope", "submission_criteria_status")
NAME_KEYS = ("name", "criterion", "check", "id", "key")
DETAIL_KEYS = ("detail", "summary", "actual", "message", "description", "reason")

_ENV_RUNTIME_RE = re.compile(
    r"\b(?:ENV_INVALID|FileNotFoundError|Traceback|Exception|Crash)\b|missing/damaged",
    re.IGNORECASE,
)
_TRACE_PROV_RE = re.compile(
    r"\b(?:trace|provenance|receipt|log|execution_or_analysis_trace)\b",
    re.IGNORECASE,
)
_SCHEMA_ID_RE = re.compile(
    r"unknown\s+reference\s+ids?|\b(?:schema|missing\s+field|missing\s+key|missing\s+column|shape\s+mismatch|dtype|type\s+mismatch|parse|invalid\s+format)\b",
    re.IGNORECASE,
)
_NUMERICAL_KEYWORDS_RE = re.compile(
    r"\b(?:differs\s+from|recomputed|tolerance|bound|mae|rmse|ev|hartree|kcal|deviation)\b|!=|(?:\d+(?:\.\d+)?\s*(?:[A-Za-z/]+)?\s*(?:deviation|error))",
    re.IGNORECASE,
)


def _bounded_json_copy(obj: Any, max_depth: int = 5, max_items: int = 100) -> Any:
    """Return a bounded, JSON-safe copy of evidence data."""
    if max_depth <= 0:
        return None
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if isinstance(obj, list):
        return [_bounded_json_copy(x, max_depth - 1, max_items) for x in obj[:max_items]]
    if isinstance(obj, dict):
        res = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= max_items:
                break
            res[str(k)] = _bounded_json_copy(v, max_depth - 1, max_items)
        return res
    return str(obj)


def _has_numeric_values(val: Any) -> bool:
    """Return True if val contains any numeric scalar."""
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, (list, tuple, set)):
        return any(_has_numeric_values(v) for v in val)
    if isinstance(val, dict):
        return any(_has_numeric_values(v) for v in val.values())
    if isinstance(val, str):
        return bool(re.search(r"\d", val))
    return False


def _classify_failure_kind(
    name: str,
    detail: str,
    evidence: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify the failure category of a failed check."""
    name_str = name or ""
    detail_str = detail or ""
    combined = f"{name_str} {detail_str}"

    if _ENV_RUNTIME_RE.search(combined):
        return "env_or_runtime"
    if _TRACE_PROV_RE.search(combined):
        return "trace_or_provenance"
    if _SCHEMA_ID_RE.search(combined):
        return "schema_or_id"

    if _NUMERICAL_KEYWORDS_RE.search(combined):
        return "numerical"
    if isinstance(evidence, dict) and evidence:
        if any(
            k in evidence
            for k in (
                "recomputed_eV",
                "recomputed",
                "expected",
                "reference",
                "tolerance",
                "bound",
                "diff",
            )
        ):
            return "numerical"
        if _has_numeric_values(evidence):
            return "numerical"
    if re.search(r"\d+\.\d+", detail_str):
        return "numerical"

    return "general"


#: Root-level scoring envelopes, in *authority* order (NOT alphabetical):
#: `verify.json` is the final graded verdict; `science.json` /
#: `raw-verifier.json` are native reports that may predate regrading.
PRIMARY_FILENAMES = (
    "verify.json",
    "verification.json",
    "scientific-result.json",
    "raw-core-diagnostic.json",
    "science.json",
    "raw-verifier.json",
    "native-result.json",
    "verifier-result.json",
)


def _as_bool(value: Any) -> Optional[bool]:
    """Map a check's `ok` / `status` value to True/False/None (undecided)."""
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (int, float)):
        return None
    if not isinstance(value, str):
        return None
    word = value.strip().upper().replace("-", "_").replace(" ", "_")
    if word in PASS_WORDS:
        return True
    if word in FAIL_WORDS:
        return False
    return None


def _status_verdict(value: Any) -> Optional[str]:
    """Map a status string to `pass` / `fail` / None (neutral)."""
    ok = _as_bool(value)
    if ok is True:
        return "pass"
    if ok is False:
        return "fail"
    return None


def _first_value(data: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _text(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    return " ".join(str(value).split())[:limit]


def _normalize_check(
    item: Dict[str, Any],
    *,
    layer: str,
    source: str,
    name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    ok = _as_bool(
        _first_value(
            item, ("ok", "passed", "status", "result", "state", "numeric_answer", "verdict")
        )
    )
    if name is None:
        name = str(_first_value(item, NAME_KEYS) or "")
    detail = _text(_first_value(item, DETAIL_KEYS))
    core_raw = item.get("core")
    rec: Dict[str, Any] = {
        "layer": layer or "unspecified",
        "name": name or "unnamed_check",
        "ok": ok,
        "detail": detail,
        "core": core_raw is True or str(core_raw).lower() == "true",
        "source": source,
    }
    raw_ev = item.get("evidence")
    safe_ev = None
    if isinstance(raw_ev, dict) and raw_ev:
        safe_ev = _bounded_json_copy(raw_ev)
        rec["evidence"] = safe_ev

    if ok is False:
        rec["failure_kind"] = _classify_failure_kind(rec["name"], detail, safe_ev)

    return rec


def _collect_from_mapping(data: Dict[str, Any], rel: str, checks: List[Dict[str, Any]]) -> None:
    """Flatten one JSON object's check-like structures into `checks`."""
    initial_len = len(checks)
    layers = data.get("layers")
    if isinstance(layers, dict):
        for layer, items in layers.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    rec = _normalize_check(item, layer=str(layer), source=rel)
                    if rec:
                        checks.append(rec)

    criteria = data.get("criteria")
    if isinstance(criteria, list):
        for item in criteria:
            if not isinstance(item, dict):
                continue
            rec = _normalize_check(item, layer=str(item.get("level") or "criteria"), source=rel)
            if rec:
                checks.append(rec)

    flat = data.get("checks")
    if isinstance(flat, dict):
        for key, value in flat.items():
            if isinstance(value, str) and _status_verdict(value) is not None:
                checks.append(
                    {
                        "layer": "checks",
                        "name": str(key),
                        "ok": _as_bool(value),
                        "detail": "",
                        "core": False,
                        "source": rel,
                    }
                )
            elif isinstance(value, dict):
                rec = _normalize_check(value, layer="checks", source=rel, name=str(key))
                if rec:
                    checks.append(rec)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        rec = _normalize_check(item, layer="checks", source=rel, name=str(key))
                        if rec:
                            checks.append(rec)
                    elif isinstance(item, str) and key.lower().startswith("error"):
                        checks.append(
                            {
                                "layer": "checks",
                                "name": str(key),
                                "ok": False,
                                "detail": _text(item),
                                "core": False,
                                "source": rel,
                            }
                        )
    elif isinstance(flat, list):
        for item in flat:
            if isinstance(item, dict):
                rec = _normalize_check(item, layer=str(item.get("level") or "checks"), source=rel)
                if rec:
                    checks.append(rec)

    # Some graders expose a bare `failed_checks` / `errors` list of names.
    for key in ("failed_checks", "failures", "errors"):
        values = data.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str) and item.strip():
                checks.append(
                    {
                        "layer": "reported_failures",
                        "name": _text(item, 160),
                        "ok": False,
                        "detail": "",
                        "core": False,
                        "source": rel,
                    }
                )
            elif isinstance(item, dict):
                rec = _normalize_check(item, layer="reported_failures", source=rel)
                if rec and rec["ok"] is not False:
                    rec["ok"] = False
                if rec:
                    checks.append(rec)

    # Flat single-result reports like native.json: {"overall": "FAIL", "detail": "atom_to_site uses unknown reference IDs"}
    if len(checks) == initial_len:
        status_val = _first_value(
            data,
            ("overall", "status", "result", "scientific_status", "state", "verdict"),
        )
        if _status_verdict(status_val) == "fail":
            detail = _text(_first_value(data, ("detail", "reason", "message", "error")) or "")
            if detail:
                name_match = re.match(r"^([A-Za-z0-9_.-]+)", detail)
                check_name = name_match.group(1) if name_match else "diagnostic_check"
                layer = rel.split(".")[0].split("/")[-1] or "diagnostic"
                checks.append(
                    {
                        "layer": layer,
                        "name": check_name,
                        "ok": False,
                        "detail": detail,
                        "core": True,
                        "source": rel,
                    }
                )


def _walk_checks(data: Any, rel: str, checks: List[Dict[str, Any]], depth: int = 0) -> None:
    """Recurse into embedded reports (e.g. `scientific_report`), bounded."""
    if depth > 4:
        return
    if isinstance(data, dict):
        _collect_from_mapping(data, rel, checks)
        for key in (
            "scientific_report",
            "native_report",
            "core_report",
            "report",
            "verifier_report",
        ):
            nested = data.get(key)
            if isinstance(nested, dict):
                _walk_checks(nested, rel, checks, depth + 1)


def _dedupe_checks(checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the richest observation per (layer, name) — embedded copies repeat."""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    order: List[Tuple[str, str]] = []
    for rec in checks:
        key = (rec["layer"], rec["name"])
        if key not in best:
            best[key] = rec
            order.append(key)
            continue
        prev = best[key]
        score = (
            len(rec.get("detail", "")),
            int(rec.get("core", False)),
            int(rec.get("ok") is False),
        )
        prev_score = (
            len(prev.get("detail", "")),
            int(prev.get("core", False)),
            int(prev.get("ok") is False),
        )
        if score > prev_score:
            if "evidence" in prev and "evidence" not in rec:
                rec["evidence"] = prev["evidence"]
            if "failure_kind" in prev and "failure_kind" not in rec:
                rec["failure_kind"] = prev["failure_kind"]
            best[key] = rec
        else:
            if "evidence" in rec and "evidence" not in prev:
                prev["evidence"] = rec["evidence"]
            if "failure_kind" in rec and "failure_kind" not in prev:
                prev["failure_kind"] = rec["failure_kind"]
    return [best[k] for k in order]


def _envelope_fields(
    docs_in_priority: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge envelope scalars across reports, most authoritative first.

    Some trials keep `failure_origin` in `verify.json` but `core_failures` only
    in `scientific/verify.json`; a primary-only read would silently drop them.
    """

    def first_number(key: str, alt: Optional[str] = None) -> Optional[float]:
        for doc in docs_in_priority:
            for k in (key, alt) if alt else (key,):
                value = doc.get(k)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return value
        return None

    def first_text(key: str, *alts: str, limit: int = 80) -> Optional[str]:
        for doc in docs_in_priority:
            value = _first_value(doc, (key, *alts))
            if value not in (None, ""):
                return _text(value, limit) or None
        return None

    origin: Optional[Dict[str, Any]] = None
    for doc in docs_in_priority:
        value = doc.get("failure_origin")
        if isinstance(value, str) and value.strip() and value.strip().lower() != "none":
            origin = {"reason_code": _text(value, 120)}
            break
        if isinstance(value, dict) and value:
            origin = value
            break

    core_failures: List[str] = []
    for doc in docs_in_priority:
        raw = doc.get("core_failures")
        if not isinstance(raw, list):
            continue
        for value in raw:
            name = _text(value, 160)
            if name and name not in core_failures:
                core_failures.append(name)

    messages: List[str] = []
    for doc in docs_in_priority:
        for key in MESSAGE_KEYS:
            value = doc.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            text = _text(value, 300)
            if text and text not in messages:
                messages.append(text)
        if len(messages) >= 3:
            break

    return {
        "status": first_text("status", "verdict"),
        "scientific_status": first_text("scientific_status", "scientific_verifier_status"),
        "reward": first_number("reward"),
        "score": first_number("score", "core_weighted_score"),
        "schema_version": first_text("schema_version"),
        "failure_origin": origin,
        "core_failures": core_failures,
        "messages": messages[:3],
    }


def _scorable(primary: Dict[str, Any], all_docs: List[Dict[str, Any]]) -> bool:
    for doc in [primary, *all_docs]:
        for key in ("scoreable", "scoring_eligible", "eligible_for_success_ranking"):
            if doc.get(key) is True:
                return True
        if isinstance(doc.get("reward"), (int, float)) and not isinstance(doc["reward"], bool):
            return True
        if _text(doc.get("status"), 40).upper() in {"SCORED", "PASS", "FAIL", "SCIENTIFIC_FAIL"}:
            return True
    return False


def _assess_diagnosability(
    failed: List[Dict[str, Any]], messages: List[str]
) -> Tuple[str, List[str]]:
    """Flag reports whose failure text cannot tell you *what* to fix.

    Real example (task 208): four criteria all fail with the same
    `invalid numeric setting at primary_01` — no check-specific value, no
    deviation, no discriminating detail.
    """
    reasons: List[str] = []
    details = [c["detail"] for c in failed if c.get("detail")]
    if not failed:
        if not messages:
            reasons.append("verifier reported a non-pass outcome without naming a failed check")
        elif all(len(m) < 80 and not re.search(r"\d", m) for m in messages):
            reasons.append("failure reason is a terse generic message with no named check or value")
    else:
        if not details:
            # If all failed checks have numeric evidence, they are diagnosable
            if not any(
                isinstance(c.get("evidence"), dict) and _has_numeric_values(c["evidence"])
                for c in failed
            ):
                reasons.append("failed checks carry no detail text")
        elif len(failed) >= 2 and len(set(details)) <= 1 and len(details[0]) < 100:
            reasons.append("multiple failed checks share one terse message")

        # Do NOT flag "failed check details contain no numerical deviation" when:
        # (a) any failed check has numeric values inside check.get("evidence"), OR
        # (b) all failed checks have failure_kind in ("schema_or_id", "trace_or_provenance", "env_or_runtime")
        has_numeric_in_evidence = any(
            isinstance(c.get("evidence"), dict) and _has_numeric_values(c["evidence"])
            for c in failed
        )
        all_non_numerical = bool(failed) and all(
            c.get("failure_kind") in ("schema_or_id", "trace_or_provenance", "env_or_runtime")
            for c in failed
        )

        if details and not any(re.search(r"\d", d) for d in details):
            if not has_numeric_in_evidence and not all_non_numerical:
                reasons.append("failed check details contain no numerical deviation")

    return ("low" if reasons else "normal"), reasons


def _summarize(failed: List[Dict[str, Any]], core_failures: List[str], messages: List[str]) -> str:
    parts: List[str] = []
    for check in failed[:3]:
        label = f"[{check['layer']}] {check['name']}"
        if check.get("detail"):
            label += f": {check['detail'][:240]}"
        parts.append(label)
    if len(failed) > 3:
        parts.append(f"(+{len(failed) - 3} more failed checks)")
    if not parts and core_failures:
        parts.append("core_failures: " + ", ".join(core_failures[:3]))
    if not parts:
        parts.extend(messages[:1])
    return "; ".join(parts)[:900]


def build_verifier_report(
    trial_dir: Optional[Path],
    *,
    max_report_bytes: int = MAX_REPORT_BYTES,
) -> Optional[Dict[str, Any]]:
    """
    Normalize all JSON verifier reports for one trial.

    Returns `None` when the trial has no `verifier/` directory or no readable
    JSON report — that absence is itself the P2 "not scored" signal.
    """
    if trial_dir is None:
        return None
    verifier_dir = Path(trial_dir) / "verifier"
    if not verifier_dir.is_dir():
        return None

    paths: List[Path] = []
    try:
        for path in verifier_dir.rglob("*.json"):
            if not path.is_file():
                continue
            paths.append(path)
            if len(paths) >= MAX_REPORT_FILES:
                break
    except OSError:
        return None
    paths.sort(key=lambda p: str(p.relative_to(verifier_dir)))

    docs: List[Tuple[str, Dict[str, Any]]] = []
    skipped: List[str] = []
    for path in paths:
        rel = str(path.relative_to(verifier_dir))
        try:
            if path.stat().st_size > max_report_bytes:
                skipped.append(f"{rel} (oversized)")
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            skipped.append(f"{rel} (unreadable)")
            continue
        if isinstance(data, dict):
            docs.append((rel, data))

    if not docs:
        return None

    def role(rel: str) -> str:
        if rel.startswith("original" + "/") or rel.startswith("backup" + "/"):
            return "historical"
        parts = rel.split("/")
        if len(parts) == 1:
            return "primary" if parts[0] in PRIMARY_FILENAMES else "diagnostic"
        return "nested"

    # The root envelope is authoritative for the trial's scoring verdict; among
    # root files `verify.json` outranks native/diagnostic reports.
    root_docs = [(rel, doc) for rel, doc in docs if role(rel) == "primary"]
    root_docs.sort(key=lambda pair: PRIMARY_FILENAMES.index(pair[0].split("/")[-1]))
    primary: Dict[str, Any] = {}
    primary_rel = ""
    if root_docs:
        primary_rel, primary = root_docs[0]
    else:
        nested = [(rel, doc) for rel, doc in docs if role(rel) == "nested"]
        if nested:
            primary_rel, primary = nested[0]
        else:
            primary_rel, primary = docs[0]

    # Everything except preserved `original/` copies contributes observations.
    ordered_docs = [primary] + [
        doc for rel, doc in docs if role(rel) != "historical" and doc is not primary
    ]

    checks: List[Dict[str, Any]] = []
    for rel, doc in docs:
        if role(rel) == "historical":
            continue  # a preserved pre-regrade copy, not the current verdict
        _walk_checks(doc, rel, checks)
    checks = _dedupe_checks(checks)

    # `core_failures` names belong in the failed-check set even when the layered
    # report omits the corresponding node (fail-fast graders).
    known_names = {c["name"] for c in checks}
    fields = _envelope_fields(ordered_docs)
    for name in fields["core_failures"]:
        if name not in known_names:
            checks.append(
                {
                    "layer": "core_failures",
                    "name": name,
                    "ok": False,
                    "detail": "",
                    "core": True,
                    "source": primary_rel,
                }
            )

    # Ensure all failed checks carry failure_kind
    for c in checks:
        if c.get("ok") is False and "failure_kind" not in c:
            c["failure_kind"] = _classify_failure_kind(
                c.get("name", ""), c.get("detail", ""), c.get("evidence")
            )

    failed = [c for c in checks if c["ok"] is False]
    passed = [c for c in checks if c["ok"] is True]

    # Extract top-level verification coverage and limits from any non-historical doc
    verification_coverage: Optional[str] = None
    coverage_limits: List[str] = []
    for _, doc in docs:
        if role(_) == "historical":
            continue
        if verification_coverage is None:
            cov = doc.get("verification_coverage") or doc.get("verification_implementation")
            if isinstance(cov, str) and cov.strip():
                verification_coverage = _text(cov.strip(), 240)
        if not coverage_limits and "coverage_limits" in doc:
            raw_limits = doc.get("coverage_limits")
            if isinstance(raw_limits, list):
                for item in raw_limits:
                    if isinstance(item, str) and item.strip():
                        coverage_limits.append(_text(item.strip(), 240))
                    elif isinstance(item, dict):
                        limit_text = (
                            item.get("description")
                            or item.get("limit")
                            or item.get("name")
                            or _text(item, 240)
                        )
                        if limit_text:
                            coverage_limits.append(_text(limit_text, 240))

    verdict: Optional[str] = None
    for doc in ordered_docs:
        for key in STATUS_KEYS:
            verdict = _status_verdict(doc.get(key))
            if verdict:
                break
        if verdict:
            break
    if failed or fields["core_failures"]:
        verdict = "fail"
    if verdict is None and fields["reward"] is not None:
        verdict = "pass" if fields["reward"] > 0 else "fail"
    if verdict is None:
        verdict = "unknown"

    if verdict == "pass":
        diag, diag_reasons = "normal", []
    else:
        diag, diag_reasons = _assess_diagnosability(failed, fields["messages"])
    families: Set[str] = set()
    for _, doc in docs:
        if isinstance(doc.get("layers"), dict):
            families.add("layered")
        if isinstance(doc.get("criteria"), list):
            families.add("criteria")
        if isinstance(doc.get("checks"), dict):
            families.add("checks")
        if any(k in doc for k in ("scientific_status", "failure_origin", "core_failures")):
            families.add("envelope")

    return {
        "schema_family": "+".join(sorted(families)) or "unknown",
        "schema_version": fields["schema_version"],
        "verdict": verdict,
        "scorable": _scorable(primary, [doc for _, doc in docs]),
        "status": fields["status"],
        "scientific_status": fields["scientific_status"],
        "reward": fields["reward"],
        "score": fields["score"],
        "failure_origin": fields["failure_origin"],
        "core_failures": fields["core_failures"],
        "messages": fields["messages"],
        "source_files": [{"rel_path": rel, "role": role(rel)} for rel, _ in docs],
        "primary_report": primary_rel or None,
        "skipped_files": skipped,
        "checks": checks,
        "failed_checks": failed,
        "passed_check_count": len(passed),
        "failed_check_count": len(failed),
        "failed_check_summaries": [
            f"[{c['layer']}] {c['name']}" + (f": {c['detail'][:200]}" if c["detail"] else "")
            for c in failed
        ],
        "verification_coverage": verification_coverage,
        "coverage_limits": coverage_limits,
        "diagnosability": diag,
        "diagnosability_reasons": diag_reasons,
        "summary": _summarize(failed, fields["core_failures"], fields["messages"]),
    }


def build_check_stats_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bridge a normalized report into the `verifier_check_stats` shape produced by
    `verify_check_stats.build_check_stats`.

    Harbor trials frequently ship a structured verifier report but no
    `verifier/verify.log`; without this bridge the attribution context would see
    zero named failed checks and fall back to generic completion heuristics.
    """
    passed = int(
        report.get("passed_check_count")
        if report.get("passed_check_count") is not None
        else len([c for c in report.get("checks", []) if isinstance(c, dict) and c.get("ok")])
    )
    failed_checks = report.get("failed_checks") or []
    failed = len(failed_checks)
    if not failed and report.get("failed_check_count"):
        failed = int(report["failed_check_count"])
        passed = max(0, passed)
    total = passed + failed
    summaries = list(report.get("failed_check_summaries") or [])
    if not summaries and failed_checks:
        for c in failed_checks:
            if isinstance(c, dict):
                layer = c.get("layer")
                prefix = f"[{layer}] " if layer else ""
                name = c.get("name") or "check"
                detail = f": {c['detail'][:200]}" if c.get("detail") else ""
                summaries.append(f"{prefix}{name}{detail}")
    failed_check_names = [
        c.get("name") for c in failed_checks if isinstance(c, dict) and c.get("name")
    ]
    return {
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "failed_check_summaries": summaries,
        "failed_check_names": failed_check_names,
        "single_failure": failed == 1 and total >= 2,
        "widespread_failure": failed >= 3 and failed >= passed,
        "counterfactual_candidate": failed == 1,
        "source": "verifier_report",
    }


__all__ = [
    "build_verifier_report",
    "build_check_stats_from_report",
    "MAX_REPORT_BYTES",
    "MAX_REPORT_FILES",
]
