#!/usr/bin/env python3
"""
Gate 3 - verifier-side defect: an internal crash, a contract-alignment defect,
or a parser hazard causally bound to the failure (`failure_binding == "direct"`).
Hard Rule 5: a hazard with no direct binding must NOT reach this gate.

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


def gate3_verifier_defect(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    case_id = ctx.case_id
    runtime = ctx.runtime
    trial_name = ctx.trial_name
    verifier_defect_contracts = ctx.verifier_defect_contracts
    verifier_obs = ctx.verifier_obs
    fail_text = ctx.fail_text

    verifier_crashes = [v for v in verifier_obs if v.get("type") == "verifier_internal_crash"]
    verifier_defects = [
        c for c in verifier_defect_contracts if c.get("item") != "trajectory:instantaneous_rmsd"
    ]
    triggered_hazards = [
        v
        for v in verifier_obs
        if v.get("triggered")
        and v.get("failure_binding") == "direct"
        and v.get("type") != "verifier_internal_crash"
        and v.get("matched_text") != "instantaneous_trajectory_rmsd"
    ]

    is_tol_strict = False
    tol_summary = ""
    m_tol = re.search(r"differs from ref .*? by >\s*([0-9.eE+-]+)", fail_text)
    if m_tol:
        try:
            tol_val = float(m_tol.group(1))
            if tol_val <= 1.00e-05:
                is_tol_strict = True
                tol_summary = (
                    f"Verifier evaluated with ultra-strict tolerance ({tol_val:g}) "
                    "rejecting a physically valid/converged result within normal platform numerical noise."
                )
        except ValueError:
            pass

    m_schema1 = re.search(r"must be ([^,]+),\s*got\s*\1\b", fail_text)
    m_schema2 = re.search(
        r"\b([a-zA-Z0-9_]+)=([0-9.]+)\s*!=\s*(?:replayed|ref|recomputed)?\s*\2\b", fail_text
    )
    is_schema_fail = bool(
        m_schema1
        or m_schema2
        or re.search(
            r"(?:type mismatch|isinstance|expected int, got str)", fail_text, re.IGNORECASE
        )
    )

    m_parser1 = re.search(r"Expected (\d+) thermo rows .*? got (?:\1\+1|32)", fail_text)
    m_parser2 = re.search(r"outdir mismatch: scf .*? vs bands", fail_text)
    is_parser_fail = bool(m_parser1 or m_parser2)

    if not (
        verifier_crashes
        or verifier_defects
        or triggered_hazards
        or is_tol_strict
        or is_schema_fail
        or is_parser_fail
    ):
        ctx.trace.record(
            gate_id="gate3_verifier_defect",
            matched=False,
            reason="no verifier crash, verifier-alignment defect, or direct-bound parser hazard",
            checks={
                "verifier_crashes": len(verifier_crashes),
                "verifier_defect_contracts": len(verifier_defects),
                "triggered_direct_hazards": len(triggered_hazards),
                "is_tol_strict": is_tol_strict,
                "is_schema_fail": is_schema_fail,
                "is_parser_fail": is_parser_fail,
            },
        )
        return None

    vc = verifier_crashes[0] if verifier_crashes else None
    vd = verifier_defects[0] if verifier_defects else None
    th = triggered_hazards[0] if triggered_hazards else None

    if vc:
        code = vc.get("code", "VERIFIER_RECOMPUTE_DEFECT")
        subtype = vc.get("subtype", "recompute_defect")
        summary = vc["summary"]
    elif is_tol_strict:
        code = "VERIFIER_TOLERANCE_TOO_STRICT"
        subtype = "tolerance_too_strict"
        summary = tol_summary
    elif is_schema_fail or (vd and vd.get("alignment") == "verifier_schema_mismatch"):
        code = "VERIFIER_SCHEMA_MISMATCH"
        subtype = "schema_mismatch"
        summary = (
            "Verifier performed rigid type comparison (e.g., isinstance(..., int) vs string integer) "
            "on numerically identical values."
            if not vd
            else vd["details"]
        )
    elif (
        is_parser_fail
        or (th and th.get("type") == "parser_hazard")
        or (vd and "namelist" in vd.get("item", ""))
    ):
        code = "VERIFIER_REGEX_OR_PARSER_DEFECT"
        subtype = "regex_or_parser_defect"
        summary = (
            "Verifier text/log parser defect (duplicated thermo step 0 header or outdir parsing mismatch) "
            "rejected valid simulation output."
            if not th
            else th["summary"]
        )
    else:
        code = "VERIFIER_HIDDEN_CONTRACT"
        subtype = "hidden_contract"
        summary = vd["details"] if vd else "Verifier hidden contract rejected output."

    ev_refs = []
    if vc:
        ev_refs.append(vc["obs_id"])
    if vd:
        ev_refs.append(vd["contract_id"])
    if th:
        ev_refs.append(th["obs_id"])
    if any(v.get("obs_id") == "ver:fail_log" for v in verifier_obs):
        ev_refs.append("ver:fail_log")
    for a in ctx.artifacts:
        if a.get("exists") and a.get("artifact_id") not in ev_refs and len(ev_refs) < 2:
            ev_refs.append(a["artifact_id"])
    if not ev_refs and ctx.artifacts:
        ev_refs.append(ctx.artifacts[0]["artifact_id"])

    fail_summary = next(
        (v["summary"] for v in verifier_obs if v["obs_id"] == "ver:fail_log"),
        summary,
    )

    contributing = []
    if not vc:
        contributing.append(
            attach_confidence_metadata(
                {
                    "category": "case",
                    "subtype": "ambiguous_contract",
                    "code": "CASE_AMBIGUOUS_CONTRACT",
                    "summary": "Prompt did not explicitly constrain input/log syntax formatting assumed by `verify.py`.",
                },
                direct_causal_evidence=1,
            )
        )

    # No blind counterfactual bonus: a single failing check does not prove the
    # verifier defect is what flips it. Only Gate 4 binds that claim to the
    # failing check's metric text before awarding it.
    margin_ratio = 4.0 if is_schema_fail else None

    prc = attach_confidence_metadata(
        {
            "category": "verifier",
            "subtype": subtype,
            "code": code,
            "summary": summary,
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
        margin_ratio=margin_ratio,
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h1 = attach_confidence_metadata(
        {
            "hypothesis_id": "H1",
            "category": "verifier",
            "subtype": subtype,
            "claim": summary,
            "evidence_for": ev_refs,
            "evidence_against": [],
            "missing_evidence": [],
            "counterfactual_test": "Fix `tests/verify.py` internal file/parser logic and re-verify existing trial artifacts.",
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=1,
        margin_ratio=margin_ratio,
        check_localization=ctx.check_localization,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "agent",
            "subtype": "result_validation",
            "claim": "Agent produced invalid or missing simulation results.",
            "evidence_for": [],
            "evidence_against": ev_refs,
            "missing_evidence": [],
            "counterfactual_test": "Inspect agent's workspace outputs against `instruction.md` requirements.",
        },
        direct_causal_evidence=0,
        contradicting_evidence=1,
    )

    ctx.trace.record(
        gate_id="gate3_verifier_defect",
        matched=True,
        reason="verifier crash, contract defect, or direct-bound parser hazard",
        checks={
            "verifier_crashes": len(verifier_crashes),
            "verifier_defect_contracts": len(verifier_defects),
            "triggered_direct_hazards": len(triggered_hazards),
        },
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "trial_name": trial_name,
        "verdict": "failed",
        "failure_stage": "verifier_execution",
        "detection_stage": "verifier_execution",
        "first_unrecovered_deviation": {
            "status": "identified",
            "event_ref": ev_refs[0] if ev_refs else "ver:fail_log",
            "timestamp": (runtime.get("stages", {}).get("verifier") or {}).get("started_at"),
            "summary": summary,
        },
        "failure_manifestation": {
            "type": "verifier_internal_crash" if vc else "verifier_false_rejection",
            "summary": fail_summary,
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "agent",
                "reason": (
                    "Verifier crashed internally on its own reference/temp file before validating agent output."
                    if vc
                    else "Agent's simulation outputs satisfied the prompt contract before hitting the causally bound verifier parser/header defect."
                ),
            }
        ],
        "recommended_actions": [
            {
                "owner": "Verifier",
                "action": (
                    f"Fix `tests/verify.py` ({code}): ensure internal reference paths exist and use robust namelist/float/header parsing."
                ),
            }
        ],
        "skill_prescription": None,
    }
