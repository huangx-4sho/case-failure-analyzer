#!/usr/bin/env python3
"""
Gate 2 - a case/prompt contract defect (e.g. a promised asset is absent).

Extracted once from the former `analyze_case._build_raw_causal_attribution`, so the
gate logic is byte-identical to the code it replaced. There is no generator in this
repository - these modules are maintained by hand from here, and
`tests/test_attribution_characterization.py` will show any behavior change as a
reviewable baseline diff.
"""

from __future__ import annotations

from confidence import attach_confidence_metadata
from runtime_state import SCHEMA_VERSION

import re
from typing import Any, Dict, Optional

from ..context import AttributionContext


def gate2_case_definition(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    case_id = ctx.case_id
    contracts = ctx.contracts
    runtime = ctx.runtime
    trial_name = ctx.trial_name
    verifier_obs = ctx.verifier_obs
    verifier_started = ctx.verifier_started
    fail_text = ctx.fail_text

    case_defects = [c for c in contracts if c.get("alignment") == "case_defect"]

    if not case_defects:
        # Check for case / prompt ambiguous contract defect
        is_ambiguous = False
        ambig_summary = ""

        # a. Conserved drift sign convention ambiguity (equal magnitude, opposite sign)
        m_drift = re.search(
            r"cons_qty_drift\s+(-?[\d.eE+-]+)\s*!=\s*.*?drift\s+(-?[\d.eE+-]+)", fail_text
        )
        if m_drift:
            try:
                v1, v2 = float(m_drift.group(1)), float(m_drift.group(2))
                if v1 != 0 and abs(v1 + v2) < 1e-10 * abs(v1):
                    is_ambiguous = True
                    ambig_summary = (
                        "Prompt omitted sign convention for conserved quantity drift rate; "
                        "reported magnitude matches reference exactly with opposite sign."
                    )
            except ValueError:
                pass

        # b. Underspecified production phase sampling length / step count
        if not is_ambiguous and re.search(
            r"(?:n_msd_rows=\d+\s*!=\s*log production rows|thermo rows mismatch\s*\(expected 101,\s*got 51\))",
            fail_text,
            re.IGNORECASE,
        ):
            is_ambiguous = True
            ambig_summary = (
                "Prompt did not specify the total production phase sampling duration / step count, "
                "leading to output row count mismatch against verifier expectation."
            )

        # c. RDKit conformer clustering definition ambiguity (heavy atom vs all atom RMSD)
        if not is_ambiguous and re.search(
            r"reported n_clusters=\d+\s*!=\s*re-clustered \d+", fail_text
        ):
            is_ambiguous = True
            ambig_summary = (
                "Prompt and verifier differed on conformer clustering distance metric "
                "(heavy-atom vs all-atom RMSD distance matrix)."
            )

        # d. RDKit rotatable bonds definition ambiguity (strict vs non-strict definition)
        if not is_ambiguous and re.search(
            r"n_rotatable_bonds reported \d+\s*!=\s*recomputed \d+", fail_text
        ):
            is_ambiguous = True
            ambig_summary = (
                "Prompt did not specify whether rotatable bonds should follow strict or non-strict definition "
                "(CalcNumRotatableBonds strict=True vs False)."
            )

        # e. MD restart phase space Lyapunov drift from unconstrained neighbor list
        if not is_ambiguous and re.search(
            r"final_pe=[-\d.]+\s*differs from ref [-\d.]+\s*by\s*>0.001", fail_text
        ):
            is_ambiguous = True
            ambig_summary = (
                "Binary restart without fixed neigh_modify settings in prompt caused "
                "Lyapunov phase space trajectory divergence in MD simulation."
            )

        if not is_ambiguous:
            ctx.trace.record(
                gate_id="gate2_case_definition",
                matched=False,
                reason="no case-alignment contract defect",
                checks={"case_defect_contracts": len(case_defects)},
            )
            return None

        ev_refs = []
        if any(v.get("obs_id") == "ver:fail_log" for v in verifier_obs):
            ev_refs.append("ver:fail_log")
        for a in ctx.artifacts:
            if a.get("exists") and a.get("artifact_id") not in ev_refs and len(ev_refs) < 2:
                ev_refs.append(a["artifact_id"])
        if not ev_refs and ctx.artifacts:
            ev_refs.append(ctx.artifacts[0]["artifact_id"])

        prc = attach_confidence_metadata(
            {
                "category": "case",
                "subtype": "ambiguous_contract",
                "code": "CASE_AMBIGUOUS_CONTRACT",
                "summary": ambig_summary,
            },
            direct_causal_evidence=len(ev_refs),
            cross_source_corroboration=1,
            check_localization=ctx.check_localization,
            hypothesis_separation=ctx.hypothesis_separation,
        )
        h1 = attach_confidence_metadata(
            {
                "hypothesis_id": "H1",
                "category": "case",
                "subtype": "ambiguous_contract",
                "claim": ambig_summary,
                "evidence_for": ev_refs,
                "evidence_against": [],
                "missing_evidence": [],
                "counterfactual_test": "Disambiguate the prompt and rerun verification.",
            },
            direct_causal_evidence=len(ev_refs),
            cross_source_corroboration=1,
            check_localization=ctx.check_localization,
            hypothesis_separation=ctx.hypothesis_separation,
        )
        h2 = attach_confidence_metadata(
            {
                "hypothesis_id": "H2",
                "category": "agent",
                "subtype": "task_understanding",
                "claim": "Agent chose an arbitrary convention or unconstrained simulation parameter.",
                "evidence_for": [],
                "evidence_against": ev_refs,
                "missing_evidence": [],
                "counterfactual_test": "Check prompt instruction (specification was missing or ambiguous).",
            },
            direct_causal_evidence=0,
            contradicting_evidence=1,
        )
        ctx.trace.record(
            gate_id="gate2_case_definition",
            matched=True,
            reason=f"case ambiguous contract: {ambig_summary[:60]}",
            checks={
                "case_defect_contracts": 0,
                "ambiguous_contract": True,
            },
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "trial_name": trial_name,
            "verdict": "failed",
            "failure_stage": "agent_execution",
            "detection_stage": "verifier_execution" if verifier_started else "agent_execution",
            "first_unrecovered_deviation": {
                "status": "identified",
                "event_ref": ev_refs[0] if ev_refs else "ver:fail_log",
                "timestamp": runtime.get("started_at"),
                "summary": ambig_summary,
            },
            "failure_manifestation": {
                "type": "ambiguous_contract_specification",
                "summary": ambig_summary,
            },
            "primary_root_cause": prc,
            "contributing_factors": [],
            "competing_hypotheses": [h1, h2],
            "evidence_refs": ev_refs,
            "excluded_hypotheses": [
                {
                    "hypothesis_id": "H2",
                    "category": "agent",
                    "reason": "The failure was driven by an unconstrained parameter or ambiguous convention in prompt instruction.",
                }
            ],
            "recommended_actions": [
                {
                    "owner": "Case",
                    "action": f"Clarify specification in instruction.md: {ambig_summary}",
                }
            ],
            "skill_prescription": None,
        }

    cd = case_defects[0]
    ev_refs = [cd["contract_id"]]
    if any(v["obs_id"] == "ver:fail_log" for v in verifier_obs):
        ev_refs.append("ver:fail_log")

    # Check if downstream verifier defects also co-occurred so neither is silently dropped
    co_verifier_defects = [
        c
        for c in contracts
        if c.get("alignment")
        in ("verifier_defect", "verifier_hidden_requirement", "verifier_schema_mismatch")
    ]
    co_triggered_hazards = [
        v for v in verifier_obs if v.get("triggered") and v.get("failure_binding") == "direct"
    ]
    contributing = []
    rec_actions = [
        {
            "owner": "Case",
            "action": f"Add the missing file ({cd['item']}) to `environment/assets/` or update `instruction.md`.",
        }
    ]
    if co_verifier_defects or co_triggered_hazards:
        co_desc = (
            co_verifier_defects[0]["details"]
            if co_verifier_defects
            else co_triggered_hazards[0]["summary"]
        )
        contributing.append(
            attach_confidence_metadata(
                {
                    "category": "verifier",
                    "subtype": "regex_or_parser_defect",
                    "code": "VERIFIER_REGEX_OR_PARSER_DEFECT",
                    "summary": f"Co-occurring verifier defect observed during verification: {co_desc}",
                },
                direct_causal_evidence=1,
            )
        )
        rec_actions.append(
            {
                "owner": "Verifier",
                "action": f"Also fix co-occurring parser/contract issue in `tests/verify.py`: {co_desc}",
            }
        )

    prc = attach_confidence_metadata(
        {
            "category": "case",
            "subtype": "missing_asset",
            "code": "CASE_MISSING_ASSET",
            "summary": cd["details"],
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
        # A single failing check is not proof that the missing asset is the
        # blocker; no blind counterfactual bonus here.
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "case",
            "subtype": "missing_asset",
            "claim": cd["details"],
            "evidence_for": ev_refs,
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": "Add the missing pseudopotential/input asset to `environment/assets/`.",
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "agent",
            "subtype": "path_or_dependency_discovery",
            "claim": "Agent failed to locate the required asset inside the container.",
            "evidence_for": [],
            "evidence_against": [cd["contract_id"]],
            "missing_evidence": [],
            "counterfactual_test": "Verify `environment/assets/` and `Dockerfile` (file is absent).",
        },
        direct_causal_evidence=0,
        contradicting_evidence=1,
    )
    ctx.trace.record(
        gate_id="gate2_case_definition",
        matched=True,
        reason=f"case contract defect: {cd['item']}",
        checks={
            "case_defect_contracts": len(case_defects),
            "co_verifier_defects": len(co_verifier_defects),
            "co_triggered_hazards": len(co_triggered_hazards),
        },
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "agent_execution",
        "detection_stage": "verifier_execution" if verifier_started else "agent_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": cd["contract_id"],
            "timestamp": runtime.get("started_at"),
            "summary": cd["details"],
        },
        "failure_manifestation": {
            "type": "missing_task_asset",
            "summary": cd["details"],
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "agent",
                "reason": "The asset explicitly promised in `instruction.md` was absent from the task environment.",
            }
        ],
        "recommended_actions": rec_actions,
        "skill_prescription": None,
    }
