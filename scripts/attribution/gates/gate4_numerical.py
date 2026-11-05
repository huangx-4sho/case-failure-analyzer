#!/usr/bin/env python3
"""
Gate 4 - structured numerical / stochastic trajectory drift.
Hard Rule 1: a bare keyword is never enough; both the divergence and the
ensemble-agreement structure must be present.

Two paths live in this gate, evaluated in this order:

1. Deterministic verifier-recompute divergence (added 2026-09): the verifier
   itself independently recomputed a metric the agent delivered and the values
   disagree, the agent's own trajectory self-report matches the reported value
   (so the delivered result — not the verifier's arithmetic — is wrong), and the
   metric binds to the task contract. This attributes `agent` and never fires on
   `count_mismatch`, uncorroborated divergences, or unbindable metrics.
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
_AGENT_RESULT_METRIC_RE = re.compile(
    r"(?:energy|enthalpy|force|stress|virial|pressure|temperature"
    r"|dist(?:ance)?|length|angle|dihedral|volume|density"
    r"|count|number|n_[a-z0-9_]+)",
    re.IGNORECASE,
)

#: Keys probed (in order) when extracting a summary text from a stats payload.
_SUMMARY_TEXT_KEYS = (
    "summary",
    "matched_text",
    "text",
    "description",
    "name",
    "check",
    "item",
)


def _summary_text_of(payload: Any) -> str:
    """Best-effort summary text from a string or dict-shaped stats payload."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _SUMMARY_TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _single_failure_summary_text(stats: Any) -> str:
    """
    Summary text of the single failed verifier check from the evidence's
    `verifier_check_stats` (produced by `verify_check_stats.build_check_stats`).
    Tolerates both dict-shaped and string-shaped `single_failure` payloads and
    falls back to a lone `failed_check_summaries` entry; returns "" when the
    failure is not uniquely localized or the shapes are unrecognized.
    """
    if not isinstance(stats, dict):
        return ""
    text = _summary_text_of(stats.get("single_failure"))
    if text:
        return text
    summaries = stats.get("failed_check_summaries")
    if isinstance(summaries, list) and len(summaries) == 1:
        return _summary_text_of(summaries[0])
    return ""


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


def _recompute_divergence_agent_attribution(
    ctx: AttributionContext,
    chosen: Dict[str, Any],
    metric: str,
    binding: str,
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

    corroboration = (
        f"the agent's own trajectory self-report agrees with the reported value ({agent_reported})"
    )
    if _AGENT_RESULT_METRIC_RE.search(metric):
        code = "AGENT_RESULT_VALIDATION"
        subtype = "result_validation"
        summary = (
            f"Agent delivered a '{metric}' result value that fails the verifier's "
            f"independent recomputation (reported {reported} != recomputed {recomputed}, "
            f"tol {tolerance}); {corroboration}, so the delivered result itself is wrong."
        )
    else:
        code = "AGENT_SCIENTIFIC_PARAMETER_SELECTION"
        subtype = "scientific_method_selection"
        summary = (
            f"Agent's selected '{metric}' fails the verifier's independent "
            f"recomputation/reference comparison (reported {reported} != recomputed "
            f"{recomputed}, tol {tolerance}); {corroboration}, so the agent's "
            f"parameter/scientific-method selection is at fault."
        )

    # Required citations: the divergence observation, the verifier fail log (or
    # the observation's own source ref), and the agent's trajectory self-report.
    ev_refs: List[str] = []
    if chosen.get("sci_id"):
        ev_refs.append(str(chosen["sci_id"]))
    if ctx.fail_log_obs:
        ev_refs.append("ver:fail_log")
    elif chosen.get("source_ref"):
        ev_refs.append(str(chosen["source_ref"]))
    if chosen.get("agent_ref"):
        ev_refs.append(str(chosen["agent_ref"]))
    ev_refs = list(dict.fromkeys(ref for ref in ev_refs if ref))

    # True-counterfactual measurement: the verifier's check localization is a
    # single failure AND that unique failing check is the diverging metric
    # itself — i.e. replacing the agent's delivered value with the recomputed
    # one would flip the only failing check to passing.
    single_failure_text = _single_failure_summary_text(
        ctx.evidence.get("verifier_check_stats")
    ).lower()
    counterfactual_supported = (
        ctx.check_localization == "single"
        and bool(metric)
        and str(metric).strip().lower() in single_failure_text
    )

    prc = attach_confidence_metadata(
        {
            "category": "agent",
            "subtype": subtype,
            "code": code,
            "summary": summary,
        },
        direct_causal_evidence=3,
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
                f"Recompute '{metric}' independently and reconcile the agent's "
                f"delivered value with the verifier's recomputed value."
            ),
        },
        direct_causal_evidence=3,
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

    return {
        "schema_version": "failure-analysis-v1",
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "agent_execution",
        "detection_stage": "verifier_execution" if ctx.verifier_started else "agent_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": ev_refs[0],
            "timestamp": runtime.get("finished_at"),
            "summary": (
                f"Verifier recomputation of '{metric}' diverged from the agent's "
                f"delivered value while the agent self-reported the same value."
            ),
        },
        "failure_manifestation": {
            "type": "verifier_recompute_divergence",
            "summary": fail_text[:240] if fail_text else summary[:240],
        },
        "primary_root_cause": prc,
        "contributing_factors": [],
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "verifier",
                "reason": (
                    "The agent's trajectory self-report matches the reported value, so "
                    "the delivered result - not the verifier's recomputation - is "
                    "inconsistent with the reference."
                ),
            }
        ],
        "recommended_actions": [
            {
                "owner": "Agent",
                "action": (
                    f"Re-derive and sanity-check the reported '{metric}' against an "
                    f"independent calculation before writing results; reconcile it with "
                    f"the verifier's recomputed value."
                ),
            }
        ],
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
        o for o in divergences if o.get("discriminating") and o.get("agent_reported_matches")
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
                    "verifier recomputed the agent's delivered metric and it diverges "
                    "beyond tolerance while the agent's trajectory self-report matches "
                    "the reported value"
                ),
                checks={
                    "has_recompute_divergence": True,
                    "recompute_divergence_count": len(divergences),
                    "agent_corroborated_count": len(corroborated),
                    "contract_binding": binding,
                    "check_kind": chosen.get("check_kind"),
                    "has_structured_numerical": has_structured_numerical,
                },
            )
            return _recompute_divergence_agent_attribution(ctx, chosen, metric, binding)
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
