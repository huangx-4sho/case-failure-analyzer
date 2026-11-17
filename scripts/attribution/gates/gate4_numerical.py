#!/usr/bin/env python3
"""
Gate 4 - structured numerical / stochastic trajectory drift.
Hard Rule 1: a bare keyword is never enough; both the divergence and the
ensemble-agreement structure must be present.

Two paths live in this gate, evaluated in this order:

1. Deterministic verifier-recompute divergence (added 2026-09): the verifier
   itself independently recomputed a metric the agent delivered and the values
   disagree. Corroboration requires either the agent's own *execution output*
   recording the same absolute value, or — for delta divergences — the
   delivered value being read from a structured artifact record named by the
   verifier. The metric must also bind to the task contract. This attributes
   `agent` and never fires on `count_mismatch`, uncorroborated divergences, or
   unbindable metrics.
2. The original ensemble-statistics path for Lyapunov-sensitive trajectory
   divergence, unchanged below.

Extracted once from the former `analyze_case._build_raw_causal_attribution`, so the
gate logic is byte-identical to the code it replaced. There is no generator in this
repository - these modules are maintained by hand from here, and
`tests/test_attribution_characterization.py` will show any behavior change as a
reviewable baseline diff.
"""

from __future__ import annotations

from confidence import attach_confidence_metadata
import re
from typing import Any, Dict, List, Optional

from audit_contract import is_causally_bound_numerical_failure
from ..context import AttributionContext

#: Deterministic path: check kinds where the verifier independently recomputed
#: the agent's delivered value or compared it against a reference, and the
#: comparison failed. `count_mismatch` is deliberately excluded — a row/count
#: discrepancy is not evidence that the agent's delivered value is wrong.
_AGENT_BLAME_CHECK_KINDS = ("recompute_divergence", "reference_mismatch")


#: Metric-name stems counted as physical result values (energy / force /
#: distance / counts class). Any other metric falls to the parameter-selection
#: sub-judgment.
def _resolve_contract_binding(ctx: AttributionContext, metric: str) -> Optional[str]:
    """
    Deterministically bind the diverging metric to the task contract.

    Binding ladder (first hit wins):
      1. `contract_observations` - a contract item names the metric.
      2. `instruction_trace`     - the metric appears in the prompt contract's
         `json_keys` (extracted from instruction.md).
      3. `verifier_selfcheck`    - no instruction text is available to audit
         (`prompt_contract` absent or `instruction_exists` falsy); the verifier
         having executed its own recompute check is the binding evidence.
    Returns `None` when the metric cannot be bound (instruction text exists but
    never mentions the metric and no contract names it) — the caller abstains.
    """
    metric_lower = (metric or "").strip().lower()
    if not metric_lower:
        return None
    for c in ctx.contracts:
        if metric_lower in str(c.get("item", "")).lower():
            return "contract_observations"
    prompt_contract = ctx.evidence.get("prompt_contract") or {}
    json_keys = [str(k).lower() for k in (prompt_contract.get("json_keys") or [])]
    if metric_lower in json_keys:
        return "instruction_trace"
    if not prompt_contract.get("instruction_exists"):
        return "verifier_selfcheck"
    return None


def _causal_anchor_fud_ref(ctx: AttributionContext) -> Optional[str]:
    """First execution anchor that can carry the first unrecovered deviation."""
    anchors = ctx.evidence.get("causal_anchors") or {}
    for key in ("deliverable_generation", "algorithm_or_param_definition"):
        anchor = anchors.get(key)
        if isinstance(anchor, dict) and anchor.get("event_id"):
            return str(anchor["event_id"])
    return None


def _recompute_divergence_agent_attribution(
    ctx: AttributionContext,
    chosen: Dict[str, Any],
    metric: str,
) -> Dict[str, Any]:
    """Build the 15-key agent attribution for a corroborated recompute divergence."""
    case_id = ctx.case_id
    runtime = ctx.runtime
    trial_name = ctx.trial_name
    fail_text = ctx.fail_text

    reported = chosen.get("reported_value")
    recomputed = chosen.get("recomputed_value")
    tolerance = chosen.get("tolerance")
    agent_reported = chosen.get("agent_reported_value")
    artifact_ref = chosen.get("agent_artifact_ref")
    is_delta = bool(chosen.get("is_delta_divergence"))
    artifact_clause = f" (read from `{artifact_ref}`)" if artifact_ref else ""

    code = "AGENT_RESULT_VALIDATION"
    subtype = "result_validation"
    if is_delta:
        delivered_clause = (
            f"Agent delivered '{metric}' = {agent_reported}{artifact_clause}"
            if agent_reported is not None
            else f"Agent's delivered '{metric}' value"
        )
        summary = (
            f"{delivered_clause}; the saved verifier comparison reports deviation {reported} "
            f"beyond bound {tolerance}. The responsible decision and validity of the reference remain to be reviewed."
        )
    else:
        summary = (
            f"Agent's delivered '{metric}' ({agent_reported if agent_reported is not None else reported}) "
            f"differs from the saved verifier value ({recomputed}, tol {tolerance}). "
            f"This is a recorded discrepancy; it does not identify a scientific-method or parameter error by itself."
        )

    # Required citations: the divergence observation, the verifier fail log (or
    # the observation's own source ref), the execution step that produced the
    # value, and the structured artifact the delivered value was read from.
    ev_refs: List[str] = []
    if chosen.get("sci_id"):
        ev_refs.append(str(chosen["sci_id"]))
    if ctx.fail_log_obs:
        ev_refs.append("ver:fail_log")
    elif chosen.get("source_ref"):
        ev_refs.append(str(chosen["source_ref"]))
    if chosen.get("agent_ref"):
        ev_refs.append(str(chosen["agent_ref"]))
    if artifact_ref:
        ev_refs.append(str(artifact_ref))
    ev_refs = list(dict.fromkeys(ref for ref in ev_refs if ref))

    # Check localization is observational evidence. Replacing a value is not
    # an executed causal intervention and earns no counterfactual bonus.
    counterfactual_supported = False

    # A delta divergence binds the delivered value through a structured
    # artifact but has no absolute verifier value to equality-match, so it earns
    # one fewer direct evidence point than an exact absolute match.
    direct_points = 2 if is_delta else 3

    prc = attach_confidence_metadata(
        {
            "category": "agent",
            "subtype": subtype,
            "code": code,
            "summary": summary,
        },
        direct_causal_evidence=direct_points,
        cross_source_corroboration=1,
        counterfactual_supported=counterfactual_supported,
        single_keyword_only=False,
        missing_discriminating_evidence=0,
        contradicting_evidence=0,
        margin_ratio=ctx.margin_ratio,
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "agent",
            "subtype": subtype,
            "claim": summary,
            "evidence_for": ev_refs,
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": (
                f"Trace the saved input and execution that generated '{metric}', and review the "
                f"recorded verifier calculation and its applicable requirements."
                if is_delta
                else (
                    f"Compare the saved '{metric}' calculation, delivered record, and verifier "
                    f"recomputation, preserving unresolved reference or implementation explanations."
                )
            ),
        },
        direct_causal_evidence=direct_points,
        cross_source_corroboration=1,
        counterfactual_supported=counterfactual_supported,
        single_keyword_only=False,
        missing_discriminating_evidence=0,
        contradicting_evidence=0,
        margin_ratio=ctx.margin_ratio,
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "verifier",
            "subtype": "recompute_defect",
            "claim": "The verifier's recomputation or reference value itself is faulty.",
            "evidence_for": [],
            "evidence_against": ev_refs,
            "missing_evidence": [],
            "counterfactual_test": (
                "Audit the verifier's recomputation formula/reference against the "
                "task specification."
            ),
        },
        direct_causal_evidence=0,
        contradicting_evidence=1,
    )

    if is_delta:
        fud_ref = _causal_anchor_fud_ref(ctx) or chosen.get("agent_ref") or ev_refs[0]
        fud_summary = (
            f"Agent delivered '{metric}' = {agent_reported}{artifact_clause}; the verifier's "
            f"independent replay diverged by {reported} beyond its tolerance bound {tolerance}."
            if agent_reported is not None
            else (
                f"Verifier's independent replay of '{metric}' diverged by {reported} beyond "
                f"its tolerance bound {tolerance}; the delivered value could not be bound to "
                f"a structured artifact."
            )
        )
        action = (
            f"Inspect the saved '{metric}' record{artifact_clause}, its generating inputs, "
            f"and the verifier's saved replay and coverage; identify the source of the discrepancy."
        )
    else:
        fud_ref = chosen.get("agent_ref") or ev_refs[0]
        fud_summary = (
            f"Agent's execution output recorded '{metric}' = {agent_reported}, which the "
            f"verifier's independent recomputation contradicted (recomputed {recomputed}, "
            f"tol {tolerance})."
            if agent_reported is not None
            else (
                f"Verifier recomputation of '{metric}' diverged from the delivered value "
                f"(reported {reported} != recomputed {recomputed}, tol {tolerance})."
            )
        )
        action = (
            f"Compare the saved '{metric}' execution and output with the verifier's "
            f"recorded calculation and reference conditions before assigning a cause."
        )

    contributing_factors = []
    recommended_actions = [
        {
            "owner": "Agent",
            "action": action,
        }
    ]
    causal_anchors = ctx.evidence.get("causal_anchors") or {}
    algo_anchor = causal_anchors.get("algorithm_or_param_definition")
    if isinstance(algo_anchor, dict):
        thought = str(algo_anchor.get("thought") or "")
        has_tradeoff = bool(algo_anchor.get("parameter_tradeoff_detected")) or bool(
            re.search(r"(?:η|cutoff|alpha|tradeoff|tolerance|convergence)", thought, re.IGNORECASE)
        )
        if has_tradeoff:
            cf = attach_confidence_metadata(
                {
                    "category": "agent",
                    "subtype": "scientific_parameter_selection",
                    "code": "AGENT_SCIENTIFIC_PARAMETER_SELECTION",
                    "summary": (
                        "Causal anchor thought records an explicit cutoff/convergence "
                        "parameter tradeoff prior to generating the diverging metric."
                    ),
                },
                direct_causal_evidence=1,
                cross_source_corroboration=1,
            )
            contributing_factors.append(cf)

    scale_hint = chosen.get("scale_or_sign_hint")
    if isinstance(scale_hint, dict) and scale_hint:
        hint_kind = str(scale_hint.get("kind") or "unit_conversion")
        hint_label = str(scale_hint.get("label") or "")
        hint_factor = scale_hint.get("factor")
        unit_corroborated = bool(scale_hint.get("unit_token_corroborated"))
        if hint_kind == "sign_flip":
            cf_code = "AGENT_TASK_UNDERSTANDING"
            cf_subtype = "task_understanding"
            cf_summary = (
                f"Candidate sign-flip lead: delivered '{metric}' matches a sign inversion "
                f"({hint_label}, ratio factor={hint_factor}) relative to the verifier's recomputed value."
            )
            hint_action = (
                f"Check the required sign convention for '{metric}' ({hint_label}); "
                "the delivered value is approximately the negation of the verifier's recomputed value."
            )
        else:
            cf_code = "AGENT_RESULT_VALIDATION"
            cf_subtype = "result_validation"
            corrob_note = (
                "with matching unit tokens in metric/verifier text"
                if unit_corroborated
                else "from numeric ratio alone (no unit metadata checked)"
            )
            cf_summary = (
                f"Candidate {hint_kind.replace('_', ' ')} lead ({corrob_note}): delivered '{metric}' "
                f"ratio matches {hint_label} (factor={hint_factor}) relative to the verifier's recomputed value."
            )
            hint_action = (
                f"Check units and scaling for '{metric}' against the ratio lead "
                f"({hint_label}, factor={hint_factor}, {corrob_note}) before writing the final deliverable."
            )
        contributing_factors.append(
            attach_confidence_metadata(
                {
                    "category": "agent",
                    "subtype": cf_subtype,
                    "code": cf_code,
                    "summary": cf_summary,
                },
                direct_causal_evidence=1,
                cross_source_corroboration=1,
            )
        )
        recommended_actions.append({"owner": "Agent", "action": hint_action})

    if chosen.get("stale_deliverable_sync"):
        sync_ref = chosen.get("recomputed_match_in_trajectory_ref")
        ref_clause = f" at `{sync_ref}`" if sync_ref else ""
        contributing_factors.append(
            attach_confidence_metadata(
                {
                    "category": "agent",
                    "subtype": "planning",
                    "code": "AGENT_PLANNING",
                    "summary": (
                        f"Execution output{ref_clause} printed a value matching the verifier's "
                        f"recomputed '{metric}' ({recomputed}), while the collected artifact on disk "
                        f"retained {reported}; check whether the final output file was overwritten or left unsynced."
                    ),
                },
                direct_causal_evidence=1,
                cross_source_corroboration=1,
            )
        )
        recommended_actions.append(
            {
                "owner": "Agent",
                "action": (
                    f"Verify execution and file-write ordering for '{metric}' around{ref_clause or ' the run'}, "
                    f"and determine whether the saved output is stale; do not replace it with a reference value as evidence of a fix."
                ),
            }
        )

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "agent_execution",
        "detection_stage": "verifier_execution" if ctx.verifier_started else "agent_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": fud_ref,
            "timestamp": runtime.get("finished_at"),
            "summary": fud_summary,
        },
        "failure_manifestation": {
            "type": "verifier_recompute_divergence",
            "summary": fail_text[:240] if fail_text else summary[:240],
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing_factors,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [],
        "recommended_actions": recommended_actions,
        "skill_prescription": None,
    }


def gate4_numerical_divergence(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    case_id = ctx.case_id
    fail_log_obs = ctx.fail_log_obs
    fail_text = ctx.fail_text
    runtime = ctx.runtime
    trial_name = ctx.trial_name

    has_ensemble_match = bool(
        re.search(
            r"(?:ensemble average matches|ensemble.*within tolerance|conserved.*matches)",
            fail_text,
            re.IGNORECASE,
        )
    )
    has_trajectory_metric = bool(
        re.search(
            r"(?:trajectory_rmsd\s*=\s*[\d.]+|instantaneous_position.*?>\s*[\d.]+|coord(?:inate)?_rmsd\s*=\s*[\d.]+)",
            fail_text,
            re.IGNORECASE,
        )
    )
    has_structured_numerical = has_ensemble_match and has_trajectory_metric

    # ── deterministic path: verifier-recompute divergence with agent corroboration ──
    # Runs before the ensemble-statistics path. Matched or abstaining, it records
    # the divergence counts into whatever gate4 trace entry is emitted, and it
    # never changes the gate order or the abstain-return-None semantics.
    divergences = ctx.recompute_divergences
    has_recompute_divergence = ctx.has_recompute_divergence
    corroborated = [
        o
        for o in divergences
        if o.get("discriminating")
        and (
            o.get("agent_reported_matches")
            or (o.get("is_delta_divergence") and o.get("agent_reported_value") is not None)
        )
    ]
    agent_eligible = [o for o in corroborated if o.get("check_kind") in _AGENT_BLAME_CHECK_KINDS]
    divergence_checks: Dict[str, Any] = {
        "has_recompute_divergence": has_recompute_divergence,
        "recompute_divergence_count": len(divergences),
        "agent_corroborated_count": len(corroborated),
        "contract_binding": None,
    }

    if agent_eligible:
        chosen = agent_eligible[0]
        metric = str(chosen.get("metric") or "").strip()
        binding = _resolve_contract_binding(ctx, metric)
        if binding is not None:
            ctx.trace.record(
                gate_id="gate4_numerical_divergence",
                matched=True,
                reason=(
                    "verifier's independent replay/calculation diverges beyond tolerance "
                    "from the agent's delivered value located in a structured deliverable"
                    if chosen.get("is_delta_divergence")
                    else (
                        "verifier recomputed the agent's delivered metric and it diverges "
                        "beyond tolerance while the agent's execution output records the "
                        "same value"
                    )
                ),
                checks={
                    "has_recompute_divergence": True,
                    "recompute_divergence_count": len(divergences),
                    "agent_corroborated_count": len(corroborated),
                    "contract_binding": binding,
                    "check_kind": chosen.get("check_kind"),
                    "has_structured_numerical": has_structured_numerical,
                    "agent_artifact_ref": chosen.get("agent_artifact_ref"),
                },
            )
            return _recompute_divergence_agent_attribution(ctx, chosen, metric)
        # The metric cannot be bound to the task contract: conservatively abstain
        # and mark the binding failure on the fall-through trace entry.
        divergence_checks["contract_binding"] = "unbound"

    if not has_structured_numerical:
        ctx.trace.record(
            gate_id="gate4_numerical_divergence",
            matched=False,
            reason="verifier log lacks the structured divergence + ensemble-agreement pair",
            checks={
                "has_structured_numerical": False,
                "has_ensemble_match": has_ensemble_match,
                "has_trajectory_metric": has_trajectory_metric,
                **divergence_checks,
            },
        )
        return None

    # Direct causal binding: verify that the failing assertion in the verifier log
    # is specifically evaluating the instantaneous trajectory/position comparison.
    bound_to_failure = is_causally_bound_numerical_failure(fail_text)

    if not bound_to_failure:
        ctx.trace.record(
            gate_id="gate4_numerical_divergence",
            matched=False,
            reason="numerical metrics present in log but not causally bound to the failing verifier rule",
            checks={
                "has_structured_numerical": True,
                "bound_to_failure": False,
                **divergence_checks,
            },
        )
        return None

    # Three-way contract closed-loop verification:
    # 1. Reject if prompt explicitly mandated exact pointwise trajectory reproduction
    prompt_pointwise = False
    for c in ctx.contracts:
        if c.get("prompt_requirement") == "pointwise_exact":
            prompt_pointwise = True
            break
    prompt_contract = ctx.evidence.get("prompt_contract") or {}
    if prompt_contract.get("instruction_exists") is False:
        ctx.trace.record(
            gate_id="gate4_numerical_divergence",
            matched=False,
            reason="missing instruction.md: cannot verify prompt contract; abstaining from numerical verifier defect",
            checks={
                "has_structured_numerical": True,
                "bound_to_failure": True,
                "instruction_exists": False,
                **divergence_checks,
            },
        )
        return None

    if prompt_contract.get("specifies_pointwise_trajectory"):
        prompt_pointwise = True

    if prompt_pointwise:
        ctx.trace.record(
            gate_id="gate4_numerical_divergence",
            matched=False,
            reason="prompt explicitly mandated exact pointwise trajectory reproduction; verifier tolerance is not at fault",
            checks={
                "has_structured_numerical": True,
                "bound_to_failure": True,
                "prompt_pointwise": True,
                **divergence_checks,
            },
        )
        return None

    # 2. Require verified contract proof from tests/verify.py and audit_contract
    contract_defects = [
        c
        for c in ctx.contracts
        if c.get("item") == "trajectory:instantaneous_rmsd"
        and c.get("alignment") == "verifier_defect"
    ]
    hazard_defects = [
        v
        for v in ctx.verifier_obs
        if v.get("matched_text") == "instantaneous_trajectory_rmsd"
        and v.get("triggered")
        and v.get("failure_binding") == "direct"
    ]
    has_contract_proof = bool(contract_defects or hazard_defects)

    if not has_contract_proof:
        ctx.trace.record(
            gate_id="gate4_numerical_divergence",
            matched=False,
            reason="unproven numerical tolerance defect: tests/verify.py or instruction.md contract closed loop not established",
            checks={
                "has_structured_numerical": True,
                "bound_to_failure": True,
                "has_contract_proof": False,
                **divergence_checks,
            },
        )
        return None

    ev_refs = ["ver:fail_log"] if fail_log_obs else ["art:trial_result"]
    if contract_defects:
        ev_refs.append(contract_defects[0]["contract_id"])
    elif hazard_defects:
        ev_refs.append(hazard_defects[0]["obs_id"])
    prc = attach_confidence_metadata(
        {
            "category": "verifier",
            "subtype": "tolerance_too_strict",
            "code": "VERIFIER_TOLERANCE_TOO_STRICT",
            "summary": "Verifier compared instantaneous late-step coordinates/energies rather than ensemble averages despite physical/numerical chaos.",
        },
        direct_causal_evidence=2,
        cross_source_corroboration=1,
    )
    cf_case = attach_confidence_metadata(
        {
            "category": "case",
            "subtype": "ambiguous_contract",
            "code": "CASE_AMBIGUOUS_CONTRACT",
            "summary": "Benchmark case did not fix RNG seeds or specify ensemble statistical verification bounds.",
        },
        direct_causal_evidence=1,
    )
    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "verifier",
            "subtype": "tolerance_too_strict",
            "claim": "Verifier evaluated instantaneous trajectory/position divergence rather than ensemble averages, causing failure despite valid ensemble statistics.",
            "evidence_for": ev_refs,
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": "Compare ensemble averages or pin MPI/OMP thread counts.",
        },
        direct_causal_evidence=2,
        cross_source_corroboration=1,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "agent",
            "subtype": "scientific_parameter_selection",
            "claim": "Agent configured wrong ensemble or thermodynamic state.",
            "evidence_for": [],
            "evidence_against": ev_refs,
            "missing_evidence": [],
            "counterfactual_test": "Check ensemble average match reported in `verify.log`.",
        },
        direct_causal_evidence=0,
        contradicting_evidence=1,
    )
    ctx.trace.record(
        gate_id="gate4_numerical_divergence",
        matched=True,
        reason="verifier log shows divergence with matching ensemble statistics and verified prompt/verifier contract",
        checks={
            "has_structured_numerical": has_structured_numerical,
            "bound_to_failure": bound_to_failure,
            "has_contract_proof": True,
            "prompt_pointwise": False,
        },
    )

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "verifier_execution",
        "detection_stage": "verifier_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": ev_refs[0],
            "timestamp": runtime.get("finished_at"),
            "summary": "Lyapunov-sensitive MD trajectory divergence while ensemble statistics remained consistent.",
        },
        "failure_manifestation": {
            "type": "numerical_trajectory_divergence",
            "summary": fail_text[:240],
        },
        "primary_root_cause": prc,
        "contributing_factors": [cf_case],
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "agent",
                "reason": "Agent used the requested parameters and ensemble averages matched reference tolerances; divergence reflects finite-precision floating-point accumulation sensitivity.",
            }
        ],
        "recommended_actions": [
            {
                "owner": "Verifier",
                "action": "Verify ensemble averages or conserved-quantity drift rather than late-step instantaneous values, or pin MPI/OMP thread counts.",
            }
        ],
        "skill_prescription": None,
    }
