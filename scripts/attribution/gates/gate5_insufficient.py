#!/usr/bin/env python3
"""
Gate 5 - the trial failed, but no positive evidence implicates Agent behaviour.
Hard Rule 4: `reward == 0` or a `FAIL` line alone is NOT positive Agent
evidence; the calibrated answer is `unknown`.

Extracted once from the former `analyze_case._build_raw_causal_attribution`, so the
gate logic is byte-identical to the code it replaced. There is no generator in this
repository - these modules are maintained by hand from here, and
`tests/test_attribution_characterization.py` will show any behavior change as a
reviewable baseline diff.
"""

from __future__ import annotations

from ..families import (
    CONTINUATION_FAMILIES,
    DEPENDENCY_DISCOVERY_FAMILIES,
    matches_family as _matches_family,
)
from confidence import attach_confidence_metadata
import re

from typing import Any, Dict, List, Optional

from ..context import AttributionContext


def _anchor_text(anchor: Dict[str, Any]) -> str:
    """Searchable execution text of a causal anchor."""
    return " ".join(
        str(anchor.get(key) or "")
        for key in ("target_path", "command", "observation_snippet", "script_path")
    )


def _failed_check_names(evidence: Dict[str, Any]) -> List[str]:
    """Named failing checks from normalized stats or the structured report."""
    names: List[str] = []
    stats = evidence.get("verifier_check_stats")
    if isinstance(stats, dict):
        names.extend(str(name) for name in stats.get("failed_check_names") or [] if name)
    report = evidence.get("verifier_report")
    if isinstance(report, dict):
        for check in report.get("failed_checks") or []:
            if isinstance(check, dict) and check.get("name"):
                names.append(str(check["name"]))
    return list(dict.fromkeys(names))


def _anchor_links_failure(anchor: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
    """True when the anchor writes a declared deliverable or names a failed check.

    A bare "some deliverable anchor exists" is not enough: the anchor must
    literally reference the task's declared output path or a named failing
    verifier check, otherwise an unrelated script write would route every
    failed trial to agent blame.
    """
    text = _anchor_text(anchor)
    if not text.strip():
        return False
    declared = (evidence.get("metadata") or {}).get("declared_deliverables") or []
    for item in declared:
        token = str(item or "").strip().rstrip("/")
        if token and token in text:
            return True
    return any(name.lower() in text.lower() for name in _failed_check_names(evidence))


def gate5_insufficient_positive_evidence(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    agent_mismatch_contracts = ctx.agent_mismatch_contracts
    agent_timeline_events = ctx.agent_timeline_events
    cand_hyps = ctx.cand_hyps
    case_id = ctx.case_id
    dep_search_sigs = ctx.dep_search_sigs
    evidence = ctx.evidence
    fail_log_obs = ctx.fail_log_obs
    fail_text = ctx.fail_text
    premature_sigs = ctx.premature_sigs
    recovered_sci_obs = ctx.recovered_sci_obs
    repeated_fail_sigs = ctx.repeated_fail_sigs
    runtime = ctx.runtime
    trial_name = ctx.trial_name
    unrecovered_sci_obs = ctx.unrecovered_sci_obs
    unverified_output_sigs = ctx.unverified_output_sigs
    verifier_started = ctx.verifier_started

    has_self_check_gap = bool(
        any(s.get("signal_type") == "self_verification_executed" for s in ctx.signals)
        and (ctx.named_check_failure or unrecovered_sci_obs)
    )
    has_debugging_failure = bool(
        any(s.get("signal_type") == "iterative_debugging_failure" for s in ctx.signals)
        and unrecovered_sci_obs
    )
    deliverable_anchor = (evidence.get("causal_anchors") or {}).get("deliverable_generation")
    has_deliverable_anchor = bool(
        isinstance(deliverable_anchor, dict)
        and (ctx.named_check_failure or unrecovered_sci_obs)
        and _anchor_links_failure(deliverable_anchor, evidence)
    )
    has_divergence_with_named_failure = bool(
        unrecovered_sci_obs and (ctx.named_check_failure or ctx.sole_blocker_candidate)
    )

    has_positive_agent_evidence = bool(
        (unrecovered_sci_obs and repeated_fail_sigs)
        or has_self_check_gap
        or has_debugging_failure
        or has_deliverable_anchor
        or has_divergence_with_named_failure
        or (
            any(_matches_family(s, DEPENDENCY_DISCOVERY_FAMILIES) for s in unrecovered_sci_obs)
            and not dep_search_sigs
        )
        or any(_matches_family(s, CONTINUATION_FAMILIES) for s in unrecovered_sci_obs)
        or (premature_sigs and verifier_started)
        or agent_mismatch_contracts
        or (
            (
                unverified_output_sigs
                or any(
                    s.get("signal_type") in ("asset_modified", "scientific_parameter_changed")
                    for s in ctx.signals
                )
            )
            and fail_text
            and re.search(
                r"(?:got\s+[-+\d.eEdD]+|mismatch|wrong value|!=|differs from|does not match|expected\s+.*?found|too large|too small|exceeds|missing|not specified|does not enable|requested.*?window|colleague)",
                fail_text,
                re.IGNORECASE,
            )
        )
    )

    if has_positive_agent_evidence:
        ctx.trace.record(
            gate_id="gate5_insufficient_positive_evidence",
            matched=False,
            reason="positive agent evidence exists; attribution continues to gate 6",
            checks={
                "unrecovered_sci": len(unrecovered_sci_obs),
                "repeated_failed_action": len(repeated_fail_sigs),
                "dependency_search_attempted": len(dep_search_sigs),
                "premature_completion": len(premature_sigs),
                "agent_mismatch_contracts": len(agent_mismatch_contracts),
                "agent_timeline_events": len(agent_timeline_events),
                "self_check_gap": has_self_check_gap,
                "debugging_failure": has_debugging_failure,
                "deliverable_anchor_linked": has_deliverable_anchor,
            },
        )
        return None

    avail_refs = []
    if fail_log_obs:
        avail_refs.append("ver:fail_log")
    for s in recovered_sci_obs[:1]:
        avail_refs.append(s["sci_id"])
    for a in evidence["artifacts"]:
        if a["exists"] and len(avail_refs) < 2:
            avail_refs.append(a["artifact_id"])
    if not avail_refs and evidence["artifacts"]:
        avail_refs = [evidence["artifacts"][0]["artifact_id"]]

    excluded_hyps = []
    if recovered_sci_obs:
        s0 = recovered_sci_obs[0]
        excluded_hyps.append(
            {
                "hypothesis_id": "H_SCI_RECOVERED",
                "category": "agent",
                "reason": (
                    f"Earlier `{s0['error_family']}` ({s0['software']}) at `{s0['source_ref']}` was recovered by "
                    f"subsequent successful simulation execution (`{s0.get('recovery_event_ref')}`); not the cause of final failure."
                ),
            }
        )

    prc = attach_confidence_metadata(
        {
            "category": "unknown",
            "subtype": "insufficient_evidence",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "summary": (
                "Available logs and trajectory events do not provide positive unrecovered causal evidence "
                "to attribute the failure to Agent decisions, Verifier defects, or Infrastructure."
            ),
        },
        direct_causal_evidence=0,
        missing_discriminating_evidence=2,
    )
    ctx.trace.record(
        gate_id="gate5_insufficient_positive_evidence",
        matched=True,
        reason="failed with no positive evidence implicating the agent",
        checks={
            "unrecovered_sci": len(unrecovered_sci_obs),
            "repeated_failed_action": len(repeated_fail_sigs),
            "dependency_search_attempted": len(dep_search_sigs),
            "premature_completion": len(premature_sigs),
            "agent_mismatch_contracts": len(agent_mismatch_contracts),
            "agent_timeline_events": len(agent_timeline_events),
            "self_check_gap": has_self_check_gap,
            "debugging_failure": has_debugging_failure,
            "deliverable_anchor_linked": has_deliverable_anchor,
        },
    )

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "unknown",
        "detection_stage": "verifier_execution" if verifier_started else "runner",
        "first_unrecovered_deviation": {
            "status": "not_identified",
            "event_ref": None,
            "timestamp": runtime.get("finished_at"),
            "summary": "No unrecovered agent decision or deterministic infrastructure/verifier defect could be isolated from available artifacts.",
        },
        "failure_manifestation": {
            "type": "unattributed_verification_failure" if fail_text else "unknown_failure",
            "summary": fail_text[:240]
            if fail_text
            else "Trial failed without positive causal evidence.",
        },
        "primary_root_cause": prc,
        "contributing_factors": [],
        "competing_hypotheses": cand_hyps["candidate_hypotheses"],
        "evidence_refs": avail_refs,
        "excluded_hypotheses": excluded_hyps,
        "recommended_actions": [
            {
                "owner": "Infra",
                "action": (
                    "Collect complete `agent/trajectory.json` and detailed `verifier/verify.log` tracebacks to disambiguate competing hypotheses."
                    if not any(
                        a.get("artifact_id") == "art:trajectory_json" and a.get("exists")
                        for a in evidence.get("artifacts") or []
                    )
                    else "Inspect detailed verifier diagnostic logs or review task specification to disambiguate competing hypotheses."
                ),
            }
        ],
        "skill_prescription": None,
    }
