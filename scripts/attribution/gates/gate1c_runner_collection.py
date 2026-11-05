#!/usr/bin/env python3
"""
Gate 1c - Pre-verification execution / runner infrastructure failure
(`scripts/attribution/gates/gate1c_runner_collection.py`)

Harbor collects the agent's declared deliverables with `docker cp` *after* agent
execution and *before* the verifier. When a declared path is missing, the runner
raises `Engine cp failed (... Could not find the file /app/final in container ...)`
and aborts the trial; some trials instead just stop logging at
`Collecting main service artifacts`.

One *upstream* runner fault is also handled here, because it occurs earlier in
the same pre-verification window and outranks the collection crash as primary
cause:

- `INFRA_AGENT_TIMEOUT` - harbor killed the agent at its configured timeout
  before the declared deliverables were complete. A short timeout is an infra /
  job-configuration fault, not an agent decision; blaming `AGENT_PREMATURE_
  TERMINATION` here is a false agent blame.

Neither is an `environment_build` / `container_build` failure, and neither is a
scored agent "premature termination" verdict when the verifier never started.
This gate makes the runner-side fault the *primary* infra cause while keeping the
agent's missing deliverable as an explicit contributing factor (compound
attribution: infra primary + agent contributing).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from confidence import attach_confidence_metadata
from runtime_state import SCHEMA_VERSION

from ..context import AttributionContext

#: Upstream execution faults, in descending causal priority.
#: NOTE: a benign `litellm.BadRequestError: LLM Provider NOT provided` cost
#: estimation warning is NOT one of these — see extract_runtime_errors.py.
EXECUTION_INFRA_CODES = ("INFRA_AGENT_TIMEOUT",)


def _summary_for(primary: Dict[str, Any], verifier_started: bool) -> str:
    code = primary["code"]
    scoring_note = (
        "the verifier never started, so this trial was not scored"
        if not verifier_started
        else "the verifier still ran and scored the incomplete outputs"
    )
    if code == "INFRA_AGENT_TIMEOUT":
        return (
            "Harbor killed the agent at its configured execution timeout before "
            "the declared deliverables were complete "
            f"(`{str(primary.get('matched_text') or '')[:120]}`); {scoring_note}."
        )
    return (
        "Runner aborted while collecting the agent's declared deliverables "
        f"(`{code}`, stage=runner, post-agent); {scoring_note}."
    )


def gate1c_runner_artifact_collection(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    crashes = ctx.artifact_collection_crashes
    exec_errors = ctx.execution_infra_errors
    if (not crashes and not exec_errors) or not ctx.agent_started:
        ctx.trace.record(
            gate_id="gate1c_runner_artifact_collection",
            matched=False,
            reason="no causal post-agent artifact collection or execution failure",
            checks={
                "agent_started": ctx.agent_started,
                "runner_collection_crashes": len(crashes),
                "execution_infra_errors": len(exec_errors),
            },
        )
        return None

    # An upstream execution fault (model gateway, agent timeout) outranks the
    # downstream collection crash: the agent was stopped before it could write
    # the declared paths, so the cp failure is a symptom.
    primary: Optional[Dict[str, Any]] = None
    for code in EXECUTION_INFRA_CODES:
        primary = next((e for e in exec_errors if e.get("code") == code), None)
        if primary is not None:
            break
    if primary is None:
        primary = crashes[0]
    is_execution_primary = primary["code"] in EXECUTION_INFRA_CODES

    missing = list(ctx.evidence.get("missing_deliverables") or [])
    declared_paths = [
        str(primary.get("missing_deliverable") or ""),
        *missing,
    ]
    declared_paths = [p for p in dict.fromkeys(declared_paths) if p]
    sig_refs = [s["signal_id"] for s in ctx.premature_sigs[:2] if s.get("signal_id")]
    err_refs = [e["error_id"] for e in [primary, *crashes] if e.get("error_id")]
    ev_refs = list(dict.fromkeys(err_refs + sig_refs))

    verifier_started = bool(ctx.verifier_started)
    verdict = "failed" if verifier_started else "errored"
    failure_stage = "agent_execution" if is_execution_primary else "runner"
    detection_stage = "verifier_execution" if verifier_started else "runner"
    summary = _summary_for(primary, verifier_started)
    manifestation = str(primary.get("matched_text") or summary)[:300]

    prc = attach_confidence_metadata(
        {
            "category": "infra",
            "subtype": primary.get("subtype") or "artifact_collection_crash",
            "code": primary["code"],
            "summary": summary,
        },
        direct_causal_evidence=len(err_refs),
        cross_source_corroboration=1,
    )

    contributing: List[Dict[str, Any]] = []
    if declared_paths or sig_refs:
        contributing.append(
            attach_confidence_metadata(
                {
                    "category": "agent",
                    "subtype": "deliverable_not_written",
                    "code": "AGENT_MISSING_DECLARED_DELIVERABLE",
                    "summary": (
                        "Agent did not leave the declared deliverable(s) in the container: "
                        + ", ".join(declared_paths[:4])
                        if declared_paths
                        else "Agent did not leave a declared deliverable in the container."
                    ),
                },
                direct_causal_evidence=len(sig_refs) or 1,
            )
        )
    if is_execution_primary and crashes:
        contributing.append(
            attach_confidence_metadata(
                {
                    "category": "infra",
                    "subtype": crashes[0].get("subtype") or "artifact_collection_crash",
                    "code": crashes[0]["code"],
                    "summary": (
                        "Downstream runner artifact collection also failed: "
                        f"{str(crashes[0].get('matched_text') or '')[:160]}"
                    ),
                },
                direct_causal_evidence=1,
            )
        )

    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "infra",
            "subtype": primary.get("subtype") or "artifact_collection_crash",
            "claim": summary,
            "evidence_for": list(ev_refs),
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": (
                "Fix the runner-side fault (model gateway / agent timeout / "
                "artifact collection tolerance) and re-run: the trial should "
                "reach the verifier with all declared deliverables present."
            ),
        },
        direct_causal_evidence=len(err_refs),
        cross_source_corroboration=1,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "agent",
            "subtype": "deliverable_not_written",
            "claim": "Agent never wrote the declared deliverable inside the container.",
            "evidence_for": list(sig_refs),
            "evidence_against": [],
            "missing_evidence": ["agent/trajectory.json final tool results"],
            "counterfactual_test": "Inspect the last trajectory turn for a write to the declared path.",
        },
        direct_causal_evidence=len(sig_refs),
    )

    ctx.trace.record(
        gate_id="gate1c_runner_artifact_collection",
        matched=True,
        reason=f"post-agent runner/execution failure ({primary['code']}); "
        + ("verifier scored incomplete outputs" if verifier_started else "verifier never started"),
        checks={
            "agent_started": ctx.agent_started,
            "verifier_started": verifier_started,
            "runner_collection_crashes": len(crashes),
            "execution_infra_errors": len(exec_errors),
            "missing_deliverables": len(declared_paths),
        },
        subcase_id="1c-runner",
    )

    if primary["code"] == "INFRA_AGENT_TIMEOUT":
        infra_action = (
            "Raise `agent.timeout_sec` / the job's agent timeout multiplier to a "
            "budget that matches the task's scientific workload before re-running."
        )
    else:
        infra_action = (
            "Make harbor's artifact collection tolerate a missing declared path "
            "(record a per-artifact error instead of aborting the trial) so the "
            "verifier still runs and produces a scored verdict."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": ctx.case_id,
        "trial_name": ctx.trial_name,
        "verdict": verdict,
        "failure_stage": failure_stage,
        "detection_stage": detection_stage,
        "first_unrecovered_deviation": {
            "status": "identified" if err_refs else "not_identified",
            "event_ref": err_refs[0] if err_refs else None,
            "timestamp": ctx.runtime.get("finished_at"),
            "summary": summary,
        },
        "failure_manifestation": {
            "type": primary.get("subtype") or "artifact_collection_crash",
            "summary": manifestation,
            "scoring_reached": verifier_started,
            "missing_deliverables": declared_paths[:4],
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H_VERIFIER",
                "category": "verifier",
                "reason": (
                    "Verifier did not start (`runtime.verifier_started == false`): the "
                    "runner aborted during agent execution / artifact collection before "
                    "scoring."
                    if not verifier_started
                    else "The verifier ran, but the upstream runner fault prevented the "
                    "agent from producing complete declared deliverables."
                ),
            }
        ],
        "recommended_actions": [
            {"owner": "Infra", "action": infra_action},
            {
                "owner": "Agent Policy",
                "action": (
                    "Verify every declared deliverable exists inside the container before "
                    "ending the trajectory: "
                    + (", ".join(declared_paths[:4]) or "declared outputs")
                ),
            },
        ],
        "skill_prescription": None,
    }
