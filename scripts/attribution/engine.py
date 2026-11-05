#!/usr/bin/env python3
"""
Attribution engine (`scripts/attribution/engine.py`)

Runs the gates in fixed order and returns the first complete attribution. The
order is load-bearing — regression tests pin the boundaries between gates (a bare
`chaotic` keyword must not reach Gate 4, an unbound `D+03` hazard must not reach
Gate 3, a recovered SCF error must not reach Gate 6) — so it is declared here
once, as data, rather than implied by the control flow of a long function.

Gate 6 always matches, so the loop always returns and the trailing `raise` is
unreachable; it exists so that a future edit which makes Gate 6 conditional fails
loudly instead of returning `None` into the caller.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from .context import AttributionContext, build_context
from .gates import (
    gate0_passed as _gate0,
    gate1_prestartup as _gate1,
    gate2_case as _gate2,
    gate3_verifier as _gate3,
    gate4_numerical as _gate4,
    gate5_insufficient as _gate5,
    gate6_agent_primary as _gate6,
)
from .gates.gate1c_runner_collection import gate1c_runner_artifact_collection as _gate1c
from .schema import make_attribution
from .trace import DecisionTrace

#: Evaluation order. Gate ids are stable identifiers used by the decision trace.
GATE_ORDER: Tuple[str, ...] = (
    "gate0_passed",
    "gate1a_infra_prestartup",
    "gate1b_unknown_no_evidence",
    "gate1c_runner_artifact_collection",
    "gate2_case_definition",
    "gate3_verifier_defect",
    "gate4_numerical_divergence",
    "gate5_insufficient_positive_evidence",
    "gate6_agent_primary",
)

GATES: Tuple[Callable[[AttributionContext], Optional[Dict[str, Any]]], ...] = (
    _gate0.gate0_passed,
    _gate1.gate1a_infra_prestartup,
    _gate1.gate1b_unknown_no_evidence,
    _gate1c,
    _gate2.gate2_case_definition,
    _gate3.gate3_verifier_defect,
    _gate4.gate4_numerical_divergence,
    _gate5.gate5_insufficient_positive_evidence,
    _gate6.gate6_agent_primary,
)


def run_attribution_engine(
    evidence: Dict[str, Any],
    cand_hyps: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Decide the primary root cause for one trial.

    `evidence` is read-only: the gates never mutate it. The returned dict carries
    exactly `OUTCOME_KEYS` in canonical order — `make_attribution` enforces that,
    so a gate that forgets a key raises here rather than producing an artifact the
    validator later rejects.
    """
    trace = DecisionTrace(gate_order=GATE_ORDER)
    ctx = build_context(evidence, cand_hyps, trace)

    for gate in GATES:
        result = gate(ctx)
        if result is None:
            continue

        analysis = make_attribution(**result)
        # The gate that matched is whichever one recorded last, so the engine does
        # not keep a parallel registry of gate ids that could drift out of sync.
        last = ctx.trace.evaluated[-1]
        prc = analysis.get("primary_root_cause") or {}
        ctx.trace.select(
            gate_id=last["gate_id"],
            subcase_id=last.get("subcase_id"),
            category=prc.get("category"),
            code=prc.get("code"),
            verdict=analysis.get("verdict"),
            failure_stage=analysis.get("failure_stage"),
        )
        # Appended last so the first 15 keys keep the canonical contract order.
        analysis["decision_trace"] = ctx.trace.to_dict()
        return analysis

    raise RuntimeError(
        "attribution engine exhausted every gate without a match; gate6 must "
        f"always match (evaluated={[e['gate_id'] for e in trace.evaluated]!r})"
    )
