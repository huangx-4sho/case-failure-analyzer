#!/usr/bin/env python3
"""
Schema & Attribution Constraint Validator (`scripts/validate_analysis.py`)

Validates `evidence.json`, `analysis.json`, and optional `report.md` against
the `failure-analysis-v1` schema and enforces hard causal attribution rules:
  1. No false-agent-blame when `runtime.agent_started == False`.
  2. Positive agent evidence required when `primary_root_cause.category == "agent"`.
  3. Non-empty `competing_hypotheses` and structured `first_unrecovered_deviation` required
     for all `failed` / `errored` analyses.
  4. Every `evidence_ref` in `analysis.json` (and inside `competing_hypotheses` /
     `first_unrecovered_deviation`) must resolve to a valid entry in `evidence.json`.
  5. Non-agent root causes cannot generate a `skill_prescription`.
  6. `report.md` must include all 4 mandatory `## <N>. <Title>` sections with non-empty body text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_state import SCHEMA_VERSION


REQUIRED_EVIDENCE_KEYS = {
    "case_id",
    "artifacts",
    "runtime",
    "timeline",
    "error_observations",
    "contract_observations",
    "verifier_observations",
    "scientific_observations",
    "missing_artifacts",
}

REQUIRED_ANALYSIS_KEYS = {
    "schema_version",
    "case_id",
    "verdict",
    "failure_stage",
    "detection_stage",
    "first_unrecovered_deviation",
    "failure_manifestation",
    "primary_root_cause",
    "contributing_factors",
    "competing_hypotheses",
    "evidence_refs",
    "excluded_hypotheses",
    "recommended_actions",
    "skill_prescription",
}

VALID_CATEGORIES = {"infra", "case", "agent", "verifier", "unknown", "none"}
VALID_VERDICTS = {"passed", "failed", "errored", "unknown"}

from report_style import accepted_titles

# Legacy aliases remain importable for callers that reference them directly.
REQUIRED_REPORT_SECTIONS = [
    (1, "归因结论", "Root Cause Verdict"),
    (2, "失败证据链", "Failure Evidence Chain"),
    (3, "假设检验与排除", "Hypothesis Audit"),
    (4, "改进建议", "Recommended Actions"),
]

REQUIRED_PASSED_REPORT_SECTIONS = [
    (1, "通过结论", "Verdict & Pass Summary"),
    (2, "验证证据", "Verification Evidence"),
]


def collect_valid_evidence_ids(evidence: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    for a in evidence.get("artifacts") or []:
        if a.get("artifact_id"):
            ids.add(a["artifact_id"])
        if a.get("rel_path"):
            ids.add(a["rel_path"])
            ids.add(f"{a.get('scope', 'trial')}:{a['rel_path']}")
    for ev in evidence.get("timeline") or []:
        if ev.get("event_id"):
            ids.add(ev["event_id"])
    for sig in evidence.get("behavioral_signals") or []:
        if sig.get("signal_id"):
            ids.add(sig["signal_id"])
    for err in evidence.get("error_observations") or []:
        if err.get("error_id"):
            ids.add(err["error_id"])
    for c in evidence.get("contract_observations") or []:
        if c.get("contract_id"):
            ids.add(c["contract_id"])
    for v in evidence.get("verifier_observations") or []:
        if v.get("obs_id"):
            ids.add(v["obs_id"])
    for s in evidence.get("scientific_observations") or []:
        if s.get("sci_id"):
            ids.add(s["sci_id"])
        if s.get("source_ref"):
            ids.add(str(s["source_ref"]))
        if s.get("agent_ref"):
            ids.add(str(s["agent_ref"]))
        if s.get("agent_artifact_ref"):
            ids.add(str(s["agent_artifact_ref"]))
    return ids


def collect_positive_agent_evidence_ids(evidence: Dict[str, Any]) -> Set[str]:
    pos_ids: Set[str] = set()
    for sig in evidence.get("behavioral_signals") or []:
        if sig.get("signal_id"):
            pos_ids.add(sig["signal_id"])
        if sig.get("event_ref"):
            pos_ids.add(sig["event_ref"])
    for c in evidence.get("contract_observations") or []:
        if c.get("alignment") == "agent_mismatch" and c.get("contract_id"):
            pos_ids.add(c["contract_id"])
    for anchor in (evidence.get("causal_anchors") or {}).values():
        if isinstance(anchor, dict) and anchor.get("event_id"):
            pos_ids.add(str(anchor["event_id"]))
    for s in evidence.get("scientific_observations") or []:
        # Scientific observation is only positive agent evidence if unrecovered in agent trajectory
        if not s.get("recovered", False) and str(s.get("source_ref", "")).startswith("trajectory:"):
            if s.get("sci_id"):
                pos_ids.add(s["sci_id"])
            if s.get("source_ref"):
                pos_ids.add(str(s["source_ref"]))
        # A verifier recompute divergence corroborated against the agent's own
        # delivered value is positive agent evidence even though the observation
        # originates in the verifier log: the observation binds the divergence to
        # the agent's delivered metric. Absolute divergences corroborate by value
        # equality; delta divergences corroborate by a structured artifact value.
        artifact_bound_delta = (
            s.get("is_delta_divergence")
            and s.get("agent_reported_value") is not None
            and bool(s.get("agent_artifact_ref") or s.get("agent_ref"))
        )
        if not s.get("recovered", False) and (
            s.get("agent_reported_matches") or artifact_bound_delta
        ):
            if s.get("sci_id"):
                pos_ids.add(s["sci_id"])
            if s.get("source_ref"):
                pos_ids.add(str(s["source_ref"]))
            if s.get("agent_ref"):
                pos_ids.add(str(s["agent_ref"]))
            if s.get("agent_artifact_ref"):
                pos_ids.add(str(s["agent_artifact_ref"]))
    return pos_ids


def validate_report_sections(report_text: str, verdict: Optional[str] = None) -> List[str]:
    errors: List[str] = []
    passed_hint = "|".join(re.escape(t) for t in accepted_titles(1, passed=True))
    is_passed = (verdict == "passed") or bool(
        re.search(rf"(?m)^##\s+1\.\s+(?:{passed_hint})", report_text)
    )
    section_count = 2 if is_passed else 4

    for num in range(1, section_count + 1):
        titles = accepted_titles(num, passed=is_passed)
        alternation = "|".join(re.escape(t) for t in titles)
        header_pat = re.compile(rf"(?m)^##\s+{num}\.\s+(?:{alternation})(?:\s.*)?$")
        m = header_pat.search(report_text)
        canonical = titles[0]
        if not m:
            errors.append(
                f"report.md missing required section header: '## {num}. {canonical}' "
                f"(accepted: {', '.join(titles[:4])})"
            )
            continue

        # Extract section body up to the next `## ` level-2 header or EOF
        after_header = report_text[m.end() :]
        next_h2 = re.search(r"(?m)^##\s+", after_header)
        body = after_header[: next_h2.start()] if next_h2 else after_header
        if not body.strip():
            errors.append(f"report.md section '## {num}. {canonical}' has empty body content.")
    return errors


def validate_all(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    report_text: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    # 1. Check evidence.json keys
    missing_ev = REQUIRED_EVIDENCE_KEYS - set(evidence.keys())
    if missing_ev:
        errors.append(f"evidence.json missing required keys: {sorted(missing_ev)}")

    # 2. Check analysis.json keys & schema version
    missing_an = REQUIRED_ANALYSIS_KEYS - set(analysis.keys())
    if missing_an:
        errors.append(f"analysis.json missing required keys: {sorted(missing_an)}")

    if analysis.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"Invalid schema_version: {analysis.get('schema_version')!r}, expected {SCHEMA_VERSION!r}"
        )

    verdict = analysis.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"Invalid verdict: {verdict!r}; must be one of {sorted(VALID_VERDICTS)}")

    prc = analysis.get("primary_root_cause") or {}
    cat = prc.get("category")
    if cat not in VALID_CATEGORIES:
        errors.append(f"Invalid primary_root_cause.category: {cat!r}")
    elif verdict in ("failed", "errored") and cat == "none":
        errors.append("Failed/errored analysis cannot have primary_root_cause.category='none'")
    elif verdict == "passed" and cat != "none":
        errors.append(f"Passed analysis must have primary_root_cause.category='none', got {cat!r}")

    conf = prc.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        errors.append(f"Invalid primary_root_cause.confidence: {conf!r}; must be in [0.0, 1.0]")

    # 3. Hard Rule: Pre-startup constraints when agent did not start (Hard Rule 3)
    agent_started = bool((evidence.get("runtime") or {}).get("agent_started", False))
    if not agent_started:
        if cat == "agent":
            errors.append(
                "FALSE_AGENT_BLAME violation: primary_root_cause.category is 'agent' "
                "when runtime.agent_started is False."
            )
        elif verdict in ("failed", "errored"):
            causal_infra_errors = [
                e for e in (evidence.get("error_observations") or []) if e.get("causal_candidate")
            ]
            if causal_infra_errors and cat != "infra":
                errors.append(
                    f"PRESTARTUP_INFRA_VIOLATION: runtime.agent_started is False and fatal "
                    f"infrastructure evidence exists, so category must be 'infra', got {cat!r}."
                )
            elif not causal_infra_errors and cat != "unknown":
                errors.append(
                    f"PRESTARTUP_UNKNOWN_VIOLATION: runtime.agent_started is False and no fatal "
                    f"infrastructure evidence exists, so category must be 'unknown', got {cat!r}."
                )

    # Hard Rule 5: Verifier internal crash priority (cannot blame agent or be unknown when isolated)
    verifier_crashes = [
        v
        for v in (evidence.get("verifier_observations") or [])
        if v.get("type") == "verifier_internal_crash"
    ]
    if verifier_crashes:
        if cat == "agent":
            errors.append(
                "VERIFIER_CRASH_BLAME violation: verifier internal crash detected, "
                "primary_root_cause.category cannot be 'agent'."
            )
        case_defects = [
            c
            for c in (evidence.get("contract_observations") or [])
            if c.get("alignment") == "case_defect"
        ]
        causal_infra_errors = [
            e for e in (evidence.get("error_observations") or []) if e.get("causal_candidate")
        ]
        if not causal_infra_errors and not case_defects:
            if cat != "verifier":
                errors.append(
                    f"VERIFIER_CRASH_PRIORITY violation: verifier internal crash detected with no earlier "
                    f"infra or case defect, primary_root_cause.category must be 'verifier', got {cat!r}."
                )
            elif analysis.get("primary_root_cause", {}).get("code") != "VERIFIER_RECOMPUTE_DEFECT":
                errors.append(
                    f"VERIFIER_CRASH_PRIORITY violation: verifier internal crash detected, "
                    f"primary_root_cause.code must be 'VERIFIER_RECOMPUTE_DEFECT', got {analysis.get('primary_root_cause', {}).get('code')!r}."
                )

    valid_ids = collect_valid_evidence_ids(evidence)

    # 4. Hard Rule: Non-empty evidence_refs and all refs must resolve in evidence.json
    ev_refs = analysis.get("evidence_refs") or []
    if verdict in ("failed", "errored") and not ev_refs:
        errors.append("Failed/errored analysis must cite at least one evidence_ref.")

    for ref in ev_refs:
        if ref not in valid_ids:
            errors.append(f"Unresolved evidence_ref {ref!r} not found in evidence.json")

    # 5. Hard Rule: first_unrecovered_deviation and competing_hypotheses for failed/errored cases
    if verdict in ("failed", "errored"):
        fud = analysis.get("first_unrecovered_deviation")
        if not isinstance(fud, dict):
            errors.append(
                "Failed/errored analysis must provide a structured `first_unrecovered_deviation` object."
            )
        else:
            fud_status = fud.get(
                "status", "identified" if fud.get("event_ref") else "not_identified"
            )
            fud_ref = fud.get("event_ref")
            if fud_status == "identified":
                if not fud_ref:
                    errors.append(
                        "`first_unrecovered_deviation` with status='identified' must specify `event_ref`."
                    )
                elif fud_ref not in valid_ids:
                    errors.append(
                        f"Unresolved first_unrecovered_deviation.event_ref {fud_ref!r} not found in evidence.json"
                    )
            elif fud_status != "not_identified":
                errors.append(f"Invalid first_unrecovered_deviation.status: {fud_status!r}")
            if not (fud.get("summary") or "").strip():
                errors.append("`first_unrecovered_deviation.summary` must be non-empty.")

        comp_hyps = analysis.get("competing_hypotheses")
        if not isinstance(comp_hyps, list) or len(comp_hyps) < 1:
            errors.append(
                "Failed/errored analysis must include at least one hypothesis in `competing_hypotheses`."
            )
        else:
            matched_primary = False
            for hyp in comp_hyps:
                if not isinstance(hyp, dict):
                    continue
                if hyp.get("category") == cat:
                    if not matched_primary:
                        matched_primary = True
                        if cat != "unknown" and not (hyp.get("evidence_for") or []):
                            errors.append(
                                f"Primary hypothesis {hyp.get('hypothesis_id')!r} ({cat}) must list non-empty `evidence_for`."
                            )
                for h_ref in (hyp.get("evidence_for") or []) + (hyp.get("evidence_against") or []):
                    if h_ref not in valid_ids:
                        errors.append(
                            f"Unresolved hypothesis evidence ref {h_ref!r} in {hyp.get('hypothesis_id')!r}"
                        )
            if not matched_primary and cat != "unknown":
                errors.append(
                    f"primary_root_cause.category={cat!r} does not match any hypothesis in `competing_hypotheses`."
                )

    # 6. Hard Rule: Positive agent evidence required when primary_root_cause.category == 'agent'
    if cat == "agent":
        pos_agent_ids = collect_positive_agent_evidence_ids(evidence)
        if not any(r in pos_agent_ids for r in ev_refs):
            errors.append(
                "POSITIVE_AGENT_EVIDENCE violation: primary_root_cause.category is 'agent', "
                "but `evidence_refs` does not cite any positive agent evidence "
                "(behavioral_signal, agent_mismatch contract, agent timeline event, or scientific_observation)."
            )
        runtime = evidence.get("runtime") or {}
        verifier_started = bool(runtime.get("verifier_started", False))
        runtime_verdict = runtime.get("verdict")
        if not verifier_started or runtime_verdict in ("unknown", "not_run"):
            premature_sig_ids = {
                s.get("signal_id")
                for s in (evidence.get("behavioral_signals") or [])
                if s.get("signal_type") == "premature_completion"
            }
            cited_agent_refs = [r for r in ev_refs if r in pos_agent_ids]
            if not cited_agent_refs or all(r in premature_sig_ids for r in cited_agent_refs):
                errors.append(
                    "VERIFIER_NOT_RUN_PREMATURE_BLAME violation: verifier did not run "
                    "(`runtime.verifier_started is False` or `runtime.verdict in ('unknown', 'not_run')`), "
                    "so primary_root_cause.category cannot be 'agent' based solely on trajectory completion heuristics."
                )

        # Hard Rule: a structured verifier report naming failed checks proves the
        # trial reached scoring. `AGENT_PREMATURE_TERMINATION` would hide the
        # actual failed check and its deviation, so it is forbidden here.
        verifier_report = evidence.get("verifier_report")
        if (
            isinstance(verifier_report, dict)
            and verifier_report.get("failed_checks")
            and prc.get("code") == "AGENT_PREMATURE_TERMINATION"
        ):
            errors.append(
                "NAMED_CHECK_PREMATURE_BLAME violation: verifier_report names failing "
                "checks, so primary_root_cause.code cannot be 'AGENT_PREMATURE_TERMINATION'."
            )
    elif cat == "verifier":
        unbound_hazards = {
            v.get("obs_id")
            for v in (evidence.get("verifier_observations") or [])
            if v.get("hazard_detected")
            and not v.get("triggered")
            and v.get("failure_binding") != "direct"
        }
        for ref in ev_refs:
            if ref in unbound_hazards:
                errors.append(
                    f"UNBOUND_VERIFIER_HAZARD violation: static verifier hazard {ref!r} cited "
                    "in evidence_refs without direct failure binding."
                )

    # 7. Hard Rule: Skill prescription only allowed when primary_root_cause is 'agent'
    if analysis.get("skill_prescription") is not None and cat != "agent":
        errors.append(
            f"SKILL_PRESCRIPTION_POLICY violation: skill_prescription generated for category={cat!r} (only allowed for 'agent')."
        )

    # 8. Optional `decision_trace` shape check.
    #    Deliberately NOT in REQUIRED_ANALYSIS_KEYS: the trace is additive, and a
    #    model-authored or hand-edited analysis.json need not carry one. It is only
    #    checked for shape when present, so a malformed trace cannot pass silently.
    decision_trace = analysis.get("decision_trace")
    if decision_trace is not None:
        if not isinstance(decision_trace, dict):
            errors.append(
                f"decision_trace must be an object when present, got {type(decision_trace).__name__}."
            )
        else:
            evaluated = decision_trace.get("evaluated")
            if not isinstance(evaluated, list):
                errors.append(
                    "decision_trace.evaluated must be a list when decision_trace is present."
                )
            else:
                for idx, entry in enumerate(evaluated):
                    if not isinstance(entry, dict) or not entry.get("gate_id"):
                        errors.append(
                            f"decision_trace.evaluated[{idx}] must be an object carrying a `gate_id`."
                        )
                        break

    # 9. Optional report.md section & non-empty body validation
    if report_text is not None:
        errors.extend(validate_report_sections(report_text, verdict=analysis.get("verdict")))

    # 10. Readable prose: an analysis sentence must not ship a raw verifier
    # JSON envelope (Harbor `verify.log` often dumps the whole report object).
    fud = analysis.get("first_unrecovered_deviation") or {}
    for field, value in (
        ("first_unrecovered_deviation.summary", fud.get("summary")),
        ("primary_root_cause.summary", prc.get("summary")),
    ):
        if (
            isinstance(value, str)
            and value.lstrip().startswith("{")
            and re.search(r'"(?:schema_version|message|status|scientific_status)"\s*:', value)
        ):
            errors.append(
                f"RAW_VERIFIER_PAYLOAD violation: {field} begins with a raw JSON envelope; "
                "use the extracted human-readable message."
            )

    # 11. `AGENT_SELF_CHECK_GAP` must be backed by an actual self-check
    # execution (a trajectory signal or a `self_check` causal anchor).
    if prc.get("code") == "AGENT_SELF_CHECK_GAP":
        anchors = evidence.get("causal_anchors") or {}
        self_check_anchor = anchors.get("self_check") if isinstance(anchors, dict) else None
        has_signal = any(
            isinstance(sig, dict) and sig.get("signal_type") == "self_verification_executed"
            for sig in (evidence.get("behavioral_signals") or [])
        )
        has_anchor = isinstance(self_check_anchor, dict) and bool(self_check_anchor.get("event_id"))
        if not (has_signal or has_anchor):
            errors.append(
                "SELF_CHECK_GAP_EVIDENCE violation: AGENT_SELF_CHECK_GAP requires a "
                "`self_verification_executed` signal or a `causal_anchors.self_check` execution anchor."
            )

    # 12. Optional `confidence_breakdown` validation when present on primary_root_cause
    breakdown = prc.get("confidence_breakdown")
    if breakdown is not None:
        if not isinstance(breakdown, dict):
            errors.append(
                f"confidence_breakdown must be an object when present, got {type(breakdown).__name__}."
            )
        else:
            for req_key in ("points", "raw_points", "denominator", "factors", "missing_to_raise"):
                if req_key not in breakdown:
                    errors.append(f"confidence_breakdown missing required key: {req_key!r}")
            if not isinstance(breakdown.get("factors"), list):
                errors.append("confidence_breakdown.factors must be a list when present.")
            if not isinstance(breakdown.get("missing_to_raise"), list):
                errors.append("confidence_breakdown.missing_to_raise must be a list when present.")

    return len(errors) == 0, errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate evidence.json, analysis.json, and report.md."
    )
    parser.add_argument("--evidence", required=True, type=Path, help="Path to evidence.json")
    parser.add_argument("--analysis", required=True, type=Path, help="Path to analysis.json")
    parser.add_argument(
        "--report", required=False, type=Path, default=None, help="Optional path to report.md"
    )
    args = parser.parse_args()

    if not args.evidence.is_file():
        print(f"ERROR: --evidence file does not exist: {args.evidence}", file=sys.stderr)
        sys.exit(2)
    if not args.analysis.is_file():
        print(f"ERROR: --analysis file does not exist: {args.analysis}", file=sys.stderr)
        sys.exit(2)
    if args.report is not None and not args.report.is_file():
        print(f"ERROR: --report file does not exist: {args.report}", file=sys.stderr)
        sys.exit(2)

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    report_text = args.report.read_text(encoding="utf-8") if args.report is not None else None

    ok, errors = validate_all(evidence, analysis, report_text)
    if not ok:
        for err in errors:
            print(f"VALIDATION ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    print("Validation passed successfully.")


if __name__ == "__main__":
    main()
