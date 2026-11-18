#!/usr/bin/env python3
"""
Gate 1a - the Agent never started and fatal infrastructure evidence exists.
Hard Rule 3 requires `infra` here (and forbids `agent`).

Gate 1b - the Agent never started and no logs or errors explain why.
Hard Rule 3 requires `unknown` here; `agent` is strictly forbidden, because
blaming an Agent that never launched is the failure mode this rule prevents.

Extracted once from the former `analyze_case._build_raw_causal_attribution`, so the
gate logic is byte-identical to the code it replaced. There is no generator in this
repository - these modules are maintained by hand from here, and
`tests/test_attribution_characterization.py` will show any behavior change as a
reviewable baseline diff.
"""

from __future__ import annotations

from confidence import attach_confidence_metadata
from extract_runtime_errors import API_LIMIT_CODE

from typing import Any, Dict, Optional

from ..context import AttributionContext


def gate1a_infra_prestartup(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    agent_started = ctx.agent_started
    case_id = ctx.case_id
    errors = ctx.errors
    evidence = ctx.evidence
    runtime = ctx.runtime
    trial_name = ctx.trial_name

    causal_infra_errors = [e for e in errors if e.get("causal_candidate")]

    if agent_started or not causal_infra_errors:
        ctx.trace.record(
            gate_id="gate1a_infra_prestartup",
            matched=False,
            reason="agent started, or no causal infrastructure error was observed",
            checks={
                "agent_started": agent_started,
                "causal_infra_errors": len(causal_infra_errors),
            },
        )
        return None

    net_errs = [e for e in causal_infra_errors if e["code"] == "INFRA_EXTERNAL_NETWORK"]
    primary_err = net_errs[0] if net_errs else causal_infra_errors[0]
    api_limit = primary_err["code"] == API_LIMIT_CODE
    local_build_failure = primary_err.get("build_failure_kind") == "missing_local_build_dependency"
    api_summary = f"The model API refused the request before any agent execution was recorded ({primary_err['matched_text']})."
    api_action = "Check the configured model provider's usage budget or request limits."
    local_build_action = "Provide the missing local build dependency recorded in the build log, then rebuild the image."
    secondary_errs = [e for e in causal_infra_errors if e["error_id"] != primary_err["error_id"]]

    contributing = []
    for se in secondary_errs:
        cf = attach_confidence_metadata(
            {
                "category": "infra",
                "subtype": se["subtype"],
                "code": se["code"],
                "summary": f"Accompanying infrastructure error: {se['matched_text'][:140]}",
            },
            direct_causal_evidence=1,
        )
        contributing.append(cf)

    ev_refs = [e["error_id"] for e in causal_infra_errors]
    if any(a["artifact_id"] == "art:trial_result" and a["exists"] for a in evidence["artifacts"]):
        ev_refs.append("art:trial_result")

    prc = attach_confidence_metadata(
        {
            "category": "infra",
            "subtype": primary_err["subtype"],
            "code": primary_err["code"],
            "summary": api_summary
            if api_limit
            else (
                f"Container environment failed during `{primary_err.get('stage')}` ({primary_err['code']}); "
                "agent never started (`runtime.agent_started == false`)."
            ),
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
    )

    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "infra",
            "subtype": primary_err["subtype"],
            "claim": api_summary
            if api_limit
            else "Container build / network failure prevented trial startup.",
            "evidence_for": ev_refs,
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": api_action
            if api_limit
            else local_build_action
            if local_build_failure
            else "Pre-pull image or configure local Docker/apt mirror and re-run.",
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "agent",
            "subtype": "premature_termination",
            "claim": "Agent failed to produce output files.",
            "evidence_for": [],
            "evidence_against": ev_refs,
            "missing_evidence": ["agent/trajectory.json"],
            "counterfactual_test": "N/A (agent never launched)",
        },
        ruled_out=True,
    )

    ctx.trace.record(
        gate_id="gate1a_infra_prestartup",
        matched=True,
        reason=f"fatal infrastructure during {primary_err.get('stage')} ({primary_err['code']}); agent never started",
        checks={
            "agent_started": agent_started,
            "causal_infra_errors": len(causal_infra_errors),
            "primary_code": primary_err["code"],
        },
    )

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed" if api_limit and runtime.get("score_produced") else "errored",
        "failure_stage": primary_err.get("stage", "environment_build"),
        "detection_stage": "runner",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": primary_err["error_id"],
            "timestamp": runtime.get("started_at"),
            "summary": api_summary
            if api_limit
            else f"Environment build/setup failed ({primary_err['code']}) before agent container could start.",
        },
        "failure_manifestation": {
            "type": "api_rate_limit"
            if api_limit
            else "dependency_install_failure"
            if primary_err["code"] == "INFRA_EXTERNAL_NETWORK"
            else "container_build_failure",
            "summary": primary_err["matched_text"][:240],
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "agent",
                "reason": "The model API refused the request before any agent execution was recorded."
                if api_limit
                else (
                    "Ruled out by Hard Attribution Gate: `runtime.agent_started == false` and "
                    "`agent/trajectory.json` was never created because container setup exited prior to agent launch."
                ),
            }
        ],
        "recommended_actions": [
            {
                "owner": "Infra",
                "action": api_action
                if api_limit
                else local_build_action
                if local_build_failure
                else "Pre-pull base Docker images (`docker pull`) and configure resilient registry/apt mirrors before launching benchmark jobs.",
            }
        ],
        "skill_prescription": None,
    }


def gate1b_unknown_no_evidence(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    agent_started = ctx.agent_started
    cand_hyps = ctx.cand_hyps
    case_id = ctx.case_id
    errors = ctx.errors
    evidence = ctx.evidence
    runtime = ctx.runtime
    trial_name = ctx.trial_name

    causal_infra_errors = [e for e in errors if e.get("causal_candidate")]

    if agent_started or causal_infra_errors:
        ctx.trace.record(
            gate_id="gate1b_unknown_no_evidence",
            matched=False,
            reason="agent started, or a causal infrastructure error explains the failure",
            checks={
                "agent_started": agent_started,
                "causal_infra_errors": len(causal_infra_errors),
            },
        )
        return None

    avail_refs = [a["artifact_id"] for a in evidence["artifacts"] if a["exists"]][:2]
    if not avail_refs and evidence["artifacts"]:
        avail_refs = [evidence["artifacts"][0]["artifact_id"]]
    prc = attach_confidence_metadata(
        {
            "category": "unknown",
            "subtype": "insufficient_evidence",
            "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
            "summary": "Insufficient log artifacts to determine why execution terminated prior to agent startup.",
        },
        direct_causal_evidence=0,
        missing_discriminating_evidence=2,
    )
    ctx.trace.record(
        gate_id="gate1b_unknown_no_evidence",
        matched=True,
        reason="agent never started and no infrastructure evidence explains why",
        checks={"agent_started": agent_started, "available_evidence_refs": len(avail_refs)},
    )

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "errored",
        "failure_stage": "unknown",
        "detection_stage": "runner",
        "first_unrecovered_deviation": {
            "status": "not_identified",
            "event_ref": None,
            "timestamp": runtime.get("started_at"),
            "summary": "Agent did not start and no exception or container build logs are available to pinpoint the failure.",
        },
        "failure_manifestation": {
            "type": "missing_execution_logs",
            "summary": "Agent did not start and no exception or build logs are available.",
        },
        "primary_root_cause": prc,
        "contributing_factors": [],
        "competing_hypotheses": cand_hyps["candidate_hypotheses"],
        "evidence_refs": avail_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H_AGENT",
                "category": "agent",
                "reason": "Agent never started (`runtime.agent_started == false`); cannot blame agent without trajectory.",
            }
        ],
        "recommended_actions": [
            {
                "owner": "Infra",
                "action": "Preserve runner `exception.txt` and `trial.log` when container setup aborts.",
            }
        ],
        "skill_prescription": None,
    }
