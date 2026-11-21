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
- `INFRA_API_RATE_LIMIT` - a terminal model-provider request/usage limit stopped
  the agent. Retain later verifier observations without attributing absent
  outputs to an agent decision.

Neither is an `environment_build` / `container_build` failure, and neither is a
scored agent "premature termination" verdict when the verifier never started.
This gate makes the runner-side fault the *primary* infra cause while keeping the
agent's missing deliverable as an explicit contributing factor (compound
attribution: infra primary + agent contributing).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from confidence import attach_confidence_metadata
from extract_runtime_errors import API_LIMIT_CODE
from runtime_state import SCHEMA_VERSION

from ..context import AttributionContext
from ..runtime_triage import build_runtime_triage

#: Upstream execution faults, in descending causal priority.
#: NOTE: a benign `litellm.BadRequestError: LLM Provider NOT provided` cost
#: estimation warning is NOT one of these — see extract_runtime_errors.py.
EXECUTION_INFRA_CODES = (
    "INFRA_AGENT_TIMEOUT",
    "INFRA_AGENT_AUTOCOMPACT_THRASHING",
    "INFRA_AGENT_OUTPUT_TOKEN_LIMIT",
    API_LIMIT_CODE,
    "INFRA_VERIFIER_INTERPRETER_MISSING",
    "INFRA_VERIFIER_RUNTIME_REQUIREMENT",
)


def _summary_for(primary: Dict[str, Any], verifier_started: bool) -> str:
    code = primary["code"]
    if code == "INFRA_VERIFIER_INTERPRETER_MISSING":
        return "Verifier shell could not launch its Python interpreter; the scoring environment failed before producing a score."
    if code == "INFRA_VERIFIER_RUNTIME_REQUIREMENT":
        return f"Verifier stopped at an environment requirement: {primary['matched_text']}."
    if code in ("INFRA_AGENT_AUTOCOMPACT_THRASHING", "INFRA_AGENT_OUTPUT_TOKEN_LIMIT"):
        return (
            f"Agent SDK terminated with {primary.get('subtype')}: {primary.get('matched_text', '')}"
        )
    scoring_note = (
        "the verifier never started, so this trial was not scored"
        if not verifier_started
        else "the verifier subsequently ran; any output defects require independent assessment"
    )
    if code == "INFRA_AGENT_TIMEOUT":
        return (
            "Harbor stopped agent execution at its configured timeout "
            f"(`{str(primary.get('matched_text') or '')[:120]}`); {scoring_note}."
        )
    if code == API_LIMIT_CODE:
        followup = (
            "The verifier ran afterward. Its recorded results remain separate."
            if verifier_started
            else "The verifier did not start."
        )
        return f"The model API stopped agent execution ({primary['matched_text']}). {followup}"
    return (
        "Runner aborted while collecting the agent's declared deliverables "
        f"(`{code}`, stage=runner, post-agent); {scoring_note}."
    )


def gate1c_runner_artifact_collection(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    crashes = ctx.artifact_collection_crashes
    score_produced = ctx.runtime.get("score_produced", ctx.runtime.get("reward") is not None)
    unscored_verifier_crash = not score_produced and any(
        o.get("type") == "verifier_internal_crash" for o in ctx.verifier_obs
    )
    exec_errors = [
        e
        for e in ctx.errors
        if e.get("code") in EXECUTION_INFRA_CODES
        and e.get("causal_candidate")
        and not (e["code"] == API_LIMIT_CODE and unscored_verifier_crash)
    ]
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
    api_primary = primary["code"] == API_LIMIT_CODE
    verifier_runtime_primary = primary.get("stage") == "verifier_execution"
    triage = build_runtime_triage(primary, ctx.timeline, ctx.signals, ctx.runtime)

    missing = list(ctx.evidence.get("missing_deliverables") or [])
    declared_paths = [
        str(primary.get("missing_deliverable") or ""),
        *missing,
    ]
    declared_paths = [p for p in dict.fromkeys(declared_paths) if p]
    sig_refs = [s["signal_id"] for s in ctx.premature_sigs[:2] if s.get("signal_id")]
    err_refs = [e["error_id"] for e in [primary, *crashes] if e.get("error_id")]
    if triage:
        # The trace ending at a limit explains absent files without proving
        # an independent earlier failure by the agent.
        sig_refs = []
    ev_refs = list(dict.fromkeys(err_refs + sig_refs))

    verifier_started = bool(ctx.verifier_started)
    verdict = "failed" if score_produced else "errored"
    failure_stage = primary.get("stage") or (
        "agent_execution" if is_execution_primary else "runner"
    )
    detection_stage = (
        failure_stage if api_primary else "verifier_execution" if verifier_started else "runner"
    )
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
    collection_missing = list(
        dict.fromkeys(e["missing_deliverable"] for e in crashes if e.get("missing_deliverable"))
    )
    has_missing_delivery = collection_missing if triage else declared_paths or sig_refs
    if has_missing_delivery and not (api_primary or verifier_runtime_primary):
        if triage:
            missing_summary = (
                "Runner could not collect these paths after the agent was interrupted: "
                + ", ".join(collection_missing[:4])
            )
        else:
            missing_summary = (
                "Agent did not leave the declared deliverable(s) in the container: "
                + ", ".join(declared_paths[:4])
                if declared_paths
                else "Agent did not leave a declared deliverable in the container."
            )
        contributing.append(
            attach_confidence_metadata(
                {
                    "category": "agent",
                    "subtype": "deliverable_not_written",
                    "code": "AGENT_MISSING_DECLARED_DELIVERABLE",
                    "summary": missing_summary,
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
                "Inspect saved terminal execution and scoring records to distinguish "
                "the runtime stop from independently observed output defects."
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

    scoring_record = (
        "verifier produced a score"
        if score_produced
        else "verifier produced no score"
        if verifier_started
        else "verifier never started"
    )
    ctx.trace.record(
        gate_id="gate1c_runner_artifact_collection",
        matched=True,
        reason=f"post-agent runner/execution failure ({primary['code']}); {scoring_record}",
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
            "Review the configured agent timeout and workload against the saved execution trace; "
            "assess scientific and contract errors independently of the timeout."
        )
    elif primary["code"] == "INFRA_VERIFIER_INTERPRETER_MISSING":
        infra_action = "Provide the interpreter used by tests/test.sh in the scoring environment."
    elif primary["code"] == "INFRA_VERIFIER_RUNTIME_REQUIREMENT":
        infra_action = (
            "Compare the scoring container's saved mount configuration with the replay requirement."
        )
    elif primary["code"] in ("INFRA_AGENT_AUTOCOMPACT_THRASHING", "INFRA_AGENT_OUTPUT_TOKEN_LIMIT"):
        infra_action = "Review the terminal SDK result and adjust context/output budgets and tool output chunking."
    elif api_primary:
        infra_action = "Check the configured model provider's usage budget or request limits before retrying the interrupted agent execution."
    else:
        infra_action = (
            "Make harbor's artifact collection tolerate a missing declared path "
            "(record a per-artifact error instead of aborting the trial) so the "
            "verifier still runs and produces a scored verdict."
        )
    actions = [{"owner": "Infra", "action": infra_action}]
    if not (api_primary or verifier_runtime_primary or triage):
        actions.append(
            {
                "owner": "Agent Policy",
                "action": (
                    "Verify every declared deliverable exists inside the container before "
                    "ending the trajectory: "
                    + (", ".join(declared_paths[:4]) or "declared outputs")
                ),
            }
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
            "scoring_reached": score_produced,
            "missing_deliverables": declared_paths[:4],
            **({"runtime_triage": triage} if triage else {}),
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing,
        "competing_hypotheses": [h1]
        if api_primary or verifier_runtime_primary or triage
        else [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": []
        if verifier_runtime_primary or ((api_primary or triage) and verifier_started)
        else [
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
        "recommended_actions": actions,
        "skill_prescription": None,
    }
