#!/usr/bin/env python3
"""
Gate 6 - positive Agent evidence exists. Selects subcase 5a/5b/5c/5c-ext/5d/else
and emits the attribution. This gate always matches, so it terminates the chain and
the engine can never fall off the end.

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
from report_style import clean_prose, extract_verifier_message
from runtime_state import SCHEMA_VERSION

import re
from typing import Any, Dict, List, Optional

from ..context import AttributionContext


def _readable_failure_text(
    fail_text: str,
    evidence: Optional[Dict[str, Any]] = None,
    limit: int = 220,
) -> str:
    """Never leak a raw (often truncated) verifier JSON envelope into prose."""
    message = extract_verifier_message(fail_text, limit=limit)
    if message:
        return message
    cleaned = clean_prose(fail_text, limit=limit)
    if cleaned and cleaned != "(raw verifier payload omitted)" and not cleaned.startswith("{"):
        return cleaned

    # Harbor `verify.log` sometimes dumps a truncated JSON envelope with no
    # parseable `message`; the normalized report still carries the named checks.
    stats = (evidence or {}).get("verifier_check_stats") or {}
    report = (evidence or {}).get("verifier_report") or {}
    failed_checks = [c for c in (report.get("failed_checks") or []) if isinstance(c, dict)]
    named = []
    for check in failed_checks:
        detail = str(check.get("detail") or check.get("summary") or "").strip()
        name = str(check.get("name") or "").strip()
        named.append(f"{name}: {detail}" if detail else name)
    if not named:
        named = [str(item) for item in stats.get("failed_check_summaries") or [] if item]
    if named:
        return clean_prose("; ".join(named), limit=limit)
    names = [str(name) for name in stats.get("failed_check_names") or [] if name]
    if names:
        return clean_prose("verifier named failed checks: " + ", ".join(names), limit=limit)
    return cleaned


def gate6_agent_primary(ctx: AttributionContext) -> Optional[Dict[str, Any]]:
    agent_mismatch_contracts = ctx.agent_mismatch_contracts
    agent_timeline_events = ctx.agent_timeline_events
    case_id = ctx.case_id
    dep_search_sigs = ctx.dep_search_sigs
    fail_log_obs = ctx.fail_log_obs
    fail_text = ctx.fail_text
    failure_excerpt = _readable_failure_text(fail_text, evidence=ctx.evidence, limit=220)
    premature_sigs = ctx.premature_sigs
    repeated_fail_sigs = ctx.repeated_fail_sigs
    runtime = ctx.runtime
    sci_obs = ctx.sci_obs
    signals = ctx.signals
    timeline = ctx.timeline
    trial_name = ctx.trial_name
    unrecovered_sci_obs = ctx.unrecovered_sci_obs
    unverified_output_sigs = ctx.unverified_output_sigs
    verifier_started = ctx.verifier_started

    # Positive Agent Evidence Attribution
    contributing_factors: List[Dict[str, Any]] = []
    ev_refs = []
    for s in unrecovered_sci_obs:
        if str(s.get("source_ref", "")).startswith("trajectory:"):
            ev_refs.append(s["sci_id"])
    # Dependency-discovery signals are exploration, not causal evidence for a
    # scored numerical failure; never let them occupy one of the three slots.
    added_signals = 0
    for sig in signals:
        if sig.get("signal_type") == "dependency_search_attempted":
            continue
        if sig.get("signal_id"):
            ev_refs.append(sig["signal_id"])
            added_signals += 1
        if added_signals >= 3:
            break
    for c in agent_mismatch_contracts:
        ev_refs.append(c["contract_id"])
    if fail_log_obs:
        ev_refs.append("ver:fail_log")
    ev_refs = list(dict.fromkeys(ref for ref in ev_refs if ref))

    causal_anchors = ctx.evidence.get("causal_anchors") or {}
    algo_anchor = causal_anchors.get("algorithm_or_param_definition")
    deliv_anchor = causal_anchors.get("deliverable_generation")
    self_check_anchor = causal_anchors.get("self_check") or {}
    for ref_key in ("event_id", "script_event_ref"):
        ref = self_check_anchor.get(ref_key)
        if ref and ref not in ev_refs:
            ev_refs.append(str(ref))
    for a in (deliv_anchor, algo_anchor):
        if isinstance(a, dict) and a.get("event_id"):
            ref = str(a["event_id"])
            if ref not in ev_refs:
                ev_refs.append(ref)

    # Collect failed check names for anchor matching
    stats = ctx.evidence.get("verifier_check_stats") or {}
    report = ctx.evidence.get("verifier_report") or {}
    failed_check_names = set(stats.get("failed_check_names") or [])
    for c in report.get("failed_checks") or []:
        if isinstance(c, dict) and c.get("name"):
            failed_check_names.add(str(c["name"]).strip())
        elif isinstance(c, str):
            failed_check_names.add(c.strip())

    deliv_matched = bool(
        isinstance(deliv_anchor, dict) and deliv_anchor.get("matched_failed_checks")
    )
    algo_matched = bool(isinstance(algo_anchor, dict) and algo_anchor.get("matched_failed_checks"))

    deliv_text = (
        f"{deliv_anchor.get('command', '')} {deliv_anchor.get('target_path', '')}"
        if isinstance(deliv_anchor, dict)
        else ""
    )
    algo_text = (
        f"{algo_anchor.get('command', '')} {algo_anchor.get('target_path', '')}"
        if isinstance(algo_anchor, dict)
        else ""
    )

    deliv_has_check_name = False
    algo_has_check_name = False
    if failed_check_names:
        for name in failed_check_names:
            if not name:
                continue
            name_lower = name.lower()
            if name_lower in deliv_text.lower():
                deliv_has_check_name = True
            if name_lower in algo_text.lower():
                algo_has_check_name = True

    prefer_deliv = (
        isinstance(deliv_anchor, dict)
        and bool(deliv_anchor.get("event_id"))
        and (
            (deliv_matched and not algo_matched)
            or (deliv_has_check_name and not algo_has_check_name)
        )
    )

    if prefer_deliv:
        anchor_fud_ref = deliv_anchor.get("event_id")
    else:
        anchor_fud_ref = (
            algo_anchor.get("event_id")
            if algo_anchor
            else (deliv_anchor.get("event_id") if deliv_anchor else None)
        )

    debugging_fail_sigs = [
        s for s in signals if s.get("signal_type") == "iterative_debugging_failure"
    ]

    # Subcase 5a: Repeated failed action after unrecovered scientific software error (e.g. SCF nonconvergence)
    if unrecovered_sci_obs and (repeated_fail_sigs or debugging_fail_sigs):
        subcase_id = "5a"
        first_sci = unrecovered_sci_obs[0]
        rep_sig = repeated_fail_sigs[0] if repeated_fail_sigs else debugging_fail_sigs[0]
        subtype = "error_diagnosis"
        code = "AGENT_ERROR_DIAGNOSIS"
        summary = (
            f"Software emitted `{first_sci['error_family']}` ({first_sci['software']}), "
            f"but agent failed to diagnose the physical/input cause and repeatedly re-executed the failing setup (`{rep_sig['signal_id']}`)."
        )
        fud_ref = anchor_fud_ref or rep_sig["event_ref"]
        skill_prescription = {
            "recommended_skill": {
                "name": f"{first_sci['software']}-{first_sci['error_family'].replace('_', '-')}-diagnosis",
                "trigger": [
                    f"{first_sci['software']} outputs `{first_sci['matched_text'][:80]}`",
                    f"Error family `{first_sci['error_family']}` encountered during simulation",
                ],
                "capability_gap": [
                    f"Agent failed to diagnose `{first_sci['error_family']}` and repeated identical failing runs."
                ],
                "required_guidance": [
                    f"Inspect {', '.join(first_sci['required_discriminating_evidence'][:2])} before rerunning.",
                    f"Address candidate physical causes: {', '.join(first_sci['candidate_causes'][:3])}.",
                ],
                "anti_patterns": [
                    "Do not rerun the exact same command/input after a deterministic convergence or stability error."
                ],
                "evidence_cases": [f"{case_id}:{fud_ref}"],
            }
        }
    # Subcase 5b: Missing file/dependency/checkpoint discovery when file exists in container (`/opt/...` or `/workspace/assets/`)
    elif (
        any(_matches_family(s, DEPENDENCY_DISCOVERY_FAMILIES) for s in unrecovered_sci_obs)
        and not dep_search_sigs
    ):
        subcase_id = "5b"
        first_sci = next(
            s for s in unrecovered_sci_obs if _matches_family(s, DEPENDENCY_DISCOVERY_FAMILIES)
        )
        subtype = "path_or_dependency_discovery"
        code = "AGENT_PATH_OR_DEPENDENCY_DISCOVERY"
        summary = (
            f"Simulation failed with `{first_sci['error_family']}` ({first_sci['software']}) because agent did not search "
            "container directories (`/opt/` or `/workspace/assets/`) or resolve model/potential/force-field paths."
        )
        fud_ref = (
            first_sci["source_ref"]
            if str(first_sci.get("source_ref", "")).startswith(("trajectory:", "art:"))
            else first_sci["sci_id"]
        )
        skill_prescription = None
    # Subcase 5c: Continuation / state-preservation parameter mismatch
    elif any(_matches_family(s, CONTINUATION_FAMILIES) for s in unrecovered_sci_obs):
        subcase_id = "5c"
        first_sci = next(
            s for s in unrecovered_sci_obs if _matches_family(s, CONTINUATION_FAMILIES)
        )
        subtype = "scientific_parameter_selection"
        code = "AGENT_SCIENTIFIC_PARAMETER_SELECTION"
        summary = (
            f"Simulation continuation failed `{first_sci['error_family']}` ({first_sci['software']}) "
            f"due to altered state/parameter continuation settings ({', '.join(first_sci['candidate_causes'][:2])})."
        )
        fud_ref = timeline[-1]["event_id"] if timeline else first_sci["sci_id"]
        skill_prescription = {
            "recommended_skill": {
                "name": f"{first_sci['software']}-{first_sci['error_family'].replace('_', '-')}-protocol",
                "trigger": [
                    f"{first_sci['software']} task requires exact state/restart continuation (`{first_sci['error_family']}`)",
                    f"Observed diagnostic: `{first_sci['matched_text'][:80]}`",
                ],
                "capability_gap": [
                    f"Agent violated state-continuation invariants across {first_sci['software']} workflow steps."
                ],
                "required_guidance": [
                    f"Verify {', '.join(first_sci['required_discriminating_evidence'][:2])}.",
                    f"Prevent candidate causes: {', '.join(first_sci['candidate_causes'][:3])}.",
                ],
                "anti_patterns": [
                    "Do not reset state counters or re-initialize ensembles when deterministic continuation is required."
                ],
                "evidence_cases": [f"{case_id}:{fud_ref}"],
            }
        }
    # Subcase 5d: Premature completion / missing output files.
    # A named failing verifier check is more specific evidence about what the
    # task rejected than a generic completion heuristic, so it must not be
    # preempted by one: when the verifier named a failing check, fall through to
    # subcase 5e instead. A contract misalignment still binds directly and keeps
    # priority.
    elif agent_mismatch_contracts or (
        premature_sigs and not ctx.named_check_failure and verifier_started
    ):
        subcase_id = "5d"
        subtype = "task_understanding" if not premature_sigs else "premature_termination"
        code = "AGENT_TASK_UNDERSTANDING" if not premature_sigs else "AGENT_PREMATURE_TERMINATION"
        summary = f"Agent failed to satisfy explicit output file or JSON schema requirements (`{_readable_failure_text(fail_text, evidence=ctx.evidence, limit=160) or 'missing required output'}`)."
        fud_ref = (
            premature_sigs[0]["event_ref"]
            if premature_sigs
            else (
                agent_mismatch_contracts[0]["contract_id"]
                if agent_mismatch_contracts
                else (timeline[-1]["event_id"] if timeline else "ver:fail_log")
            )
        )
        skill_prescription = None
    # Subcase 5e: Inaccurate output / calculation error backed by behavioral signals
    elif (unverified_output_sigs or signals) and (fail_text or failure_excerpt):
        subcase_id = "5e"
        combined_fail_text = f"{fail_text}\n{failure_excerpt}"
        report_failed_checks = [
            c
            for c in ((ctx.evidence.get("verifier_report") or {}).get("failed_checks") or [])
            if isinstance(c, dict)
        ]
        all_schema_or_id = bool(report_failed_checks) and all(
            c.get("failure_kind") == "schema_or_id" for c in report_failed_checks
        )
        fud_ref = (
            anchor_fud_ref
            or (unverified_output_sigs[0]["event_ref"] if unverified_output_sigs else None)
            or (signals[0]["event_ref"] if signals else None)
            or (timeline[-1]["event_id"] if timeline else "ver:fail_log")
        )
        skill_prescription = None
        contributing_factors = []

        if any(s.get("signal_type") == "self_verification_executed" for s in ctx.signals):
            subtype = "self_check_gap"
            code = "AGENT_SELF_CHECK_GAP"
            has_anchor = bool(self_check_anchor.get("event_id"))
            uncovered = list(self_check_anchor.get("uncovered_failed_checks") or [])
            covered = list(self_check_anchor.get("checked_failed_checks") or [])
            script = self_check_anchor.get("script_path") or "the self-check script"
            check_event = self_check_anchor.get("event_id") or "self-check execution"
            passed = bool(self_check_anchor.get("self_check_passed"))
            if has_anchor:
                prefix = (
                    f"Agent ran self-verification at `{check_event}` and reported success"
                    if passed
                    else f"Agent ran self-verification at `{check_event}` without a pass marker"
                )
                if uncovered:
                    gap_sentence = (
                        f"its script `{script}` omits verifier-required checks: "
                        f"{', '.join(uncovered)}"
                        + (f" (covered: {', '.join(covered)})" if covered else "")
                    )
                else:
                    gap_sentence = (
                        f"its script `{script}` did not reproduce the verifier's "
                        f"independent recomputation criteria"
                    )
            else:
                prefix = "Agent ran self-verification"
                gap_sentence = (
                    "no self-check script source was captured, so its criteria cannot be "
                    "confirmed against the verifier's named failed checks"
                )
            summary = (
                f"{prefix}, but {gap_sentence}; "
                f"verifier rejected submitted outputs: {failure_excerpt}"
            )
            guidance = [
                "Include all task specification criteria and independent ground-truth checks in pre-submission validation."
            ]
            if uncovered:
                guidance.append(
                    "Explicitly reproduce the verifier's named checks before submission: "
                    f"{', '.join(uncovered)}."
                )
            skill_prescription = {
                "recommended_skill": {
                    "name": "verification-criteria-alignment",
                    "trigger": ["Agent self-verification passes but benchmark verifier fails"],
                    "capability_gap": [
                        "Agent self-check omitted benchmark-mandated validation criteria or independent recomputation."
                    ],
                    "required_guidance": guidance,
                    "anti_patterns": [
                        "Do not conclude verification based only on internal consistency without checking task acceptance criteria."
                    ],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }

            # Check if fail_text ALSO matches a specific scientific domain issue
            # Add to contributing_factors so both the self-check gap and scientific discrepancy are surfaced
            cf_subtype = None
            cf_code = None
            cf_summary = None
            if re.search(
                r"(?:spin(?:[-_]polarized|_selection)?|UKS|LSD|theory|functional)",
                combined_fail_text,
                re.IGNORECASE,
            ):
                cf_subtype = "scientific_method_selection"
                cf_code = "AGENT_SCIENTIFIC_METHOD_SELECTION"
                cf_summary = f"Underlying method selection discrepancy: {failure_excerpt}"
            elif re.search(
                r"(?:timestep.*?too large|stability limit|expected \d+ irreducible k-points|max_force.*?!=|differential step|energy_parameters|cutoff)",
                combined_fail_text,
                re.IGNORECASE,
            ):
                cf_subtype = "scientific_parameter_selection"
                cf_code = "AGENT_SCIENTIFIC_PARAMETER_SELECTION"
                cf_summary = f"Underlying simulation parameter discrepancy: {failure_excerpt}"
            elif all_schema_or_id or re.search(
                r"(?:unknown reference id|uses unknown .*? id|schema mismatch|missing (?:required )?(?:field|column|key)s?|shape mismatch|dtype mismatch)",
                combined_fail_text,
                re.IGNORECASE,
            ):
                cf_subtype = "schema_mismatch"
                cf_code = "AGENT_SCHEMA_MISMATCH"
                cf_summary = f"Underlying deliverable schema/reference mismatch: {failure_excerpt}"

            if cf_code and cf_subtype and cf_summary:
                cf = attach_confidence_metadata(
                    {
                        "category": "agent",
                        "subtype": cf_subtype,
                        "code": cf_code,
                        "summary": cf_summary,
                    },
                    direct_causal_evidence=len(ev_refs),
                    cross_source_corroboration=(
                        1 if (sci_obs or signals or ctx.agent_timeline_events) else 0
                    ),
                )
                contributing_factors.append(cf)
        elif re.search(
            r"(?:spin(?:[-_]polarized|_selection)?|UKS|LSD|theory|functional)",
            fail_text,
            re.IGNORECASE,
        ):
            subtype = "scientific_method_selection"
            code = "AGENT_SCIENTIFIC_METHOD_SELECTION"
            summary = (
                f"Agent failed to select appropriate scientific theory/method "
                f"(e.g. spin-polarization for open-shell systems): {failure_excerpt}"
            )
            skill_prescription = {
                "recommended_skill": {
                    "name": "open-shell-radical-scf-protocol",
                    "trigger": ["Open-shell radical or spin-polarized calculation required"],
                    "capability_gap": [
                        "Agent failed to identify open-shell radical state and enable spin-polarization (UKS/LSD)."
                    ],
                    "required_guidance": [
                        "Inspect system valence electron count; enable spin polarization (UKS/LSD) for doublet or triplet states."
                    ],
                    "anti_patterns": [
                        "Do not attempt closed-shell RKS/RHF calculations on systems with unpaired electrons."
                    ],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }
        elif re.search(
            r"(?:timestep.*?too large|stability limit|expected \d+ irreducible k-points|max_force.*?!=|differential step)",
            fail_text,
            re.IGNORECASE,
        ):
            subtype = "scientific_parameter_selection"
            code = "AGENT_SCIENTIFIC_PARAMETER_SELECTION"
            summary = (
                f"Agent selected simulation parameters violating numerical stability, cutoff convergence, "
                f"or grid sampling limits: {failure_excerpt}"
            )
            skill_prescription = {
                "recommended_skill": {
                    "name": "scientific-parameter-selection-protocol",
                    "trigger": [
                        "Simulation parameter selection for numerical stability or convergence"
                    ],
                    "capability_gap": [
                        "Agent chose parameters (timestep, k-points, cutoff) outside physical stability boundaries."
                    ],
                    "required_guidance": [
                        "Verify numerical stability limits (e.g. LJ dt <= 0.006) and Monkhorst-Pack grid shifts."
                    ],
                    "anti_patterns": [
                        "Do not select integration timesteps exceeding the numerical stability threshold."
                    ],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }
        elif re.search(
            r"(?:pinned asset geometry|outdir mismatch|prefix mismatch)", fail_text, re.IGNORECASE
        ):
            subtype = "planning"
            code = "AGENT_PLANNING"
            summary = (
                f"Agent failed in workflow orchestration or context state preservation "
                f"(e.g. modified pinned assets or mismatched outdir): {failure_excerpt}"
            )
            skill_prescription = {
                "recommended_skill": {
                    "name": "workflow-state-preservation-protocol",
                    "trigger": [
                        "Multi-stage workflow with pinned input assets or interdependent stages"
                    ],
                    "capability_gap": [
                        "Agent modified immutable input assets or failed to propagate directory context across steps."
                    ],
                    "required_guidance": [
                        "Ensure referenced assets remain unmodified and output directories (outdir) match across stages."
                    ],
                    "anti_patterns": ["Do not overwrite or re-optimize pinned reference assets."],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }
        elif re.search(r"(?:missing celldm|celldm\(1\)|syntax error)", fail_text, re.IGNORECASE):
            subtype = "tool_use"
            code = "AGENT_TOOL_USE"
            summary = (
                f"Agent input-generation script or tool call omitted required simulation parameters "
                f"or contained syntax errors: {failure_excerpt}"
            )
            skill_prescription = {
                "recommended_skill": {
                    "name": "scientific-input-templating-sanitizer",
                    "trigger": ["Automated generation of simulation input files"],
                    "capability_gap": ["Script generation omitted essential namelist parameters."],
                    "required_guidance": [
                        "Validate all mandatory namelist keys before running simulation."
                    ],
                    "anti_patterns": [
                        "Do not generate simulation inputs without checking required cell and system parameters."
                    ],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }
        elif re.search(r"(?:window.*?yields|colleague|audit)", fail_text, re.IGNORECASE):
            subtype = "task_understanding"
            code = "AGENT_TASK_UNDERSTANDING"
            summary = (
                f"Agent misunderstood task specification or physical conventions "
                f"(e.g. data fitting window): {failure_excerpt}"
            )
            skill_prescription = {
                "recommended_skill": {
                    "name": "scientific-task-specification-audit",
                    "trigger": ["Scientific audit or fitting window selection"],
                    "capability_gap": [
                        "Agent failed to align with task-specified fitting windows or norm conventions."
                    ],
                    "required_guidance": [
                        "Follow exact specification ranges rather than baseline or colleague defaults."
                    ],
                    "anti_patterns": [
                        "Do not use unverified legacy windows when prompt requests a specific range."
                    ],
                    "evidence_cases": [f"{case_id}:{fud_ref}"],
                }
            }
        elif all_schema_or_id or re.search(
            r"(?:unknown reference id|uses unknown .*? id|schema mismatch|missing (?:required )?(?:field|column|key)s?|shape mismatch|dtype mismatch)",
            combined_fail_text,
            re.IGNORECASE,
        ):
            subtype = "schema_mismatch"
            code = "AGENT_SCHEMA_MISMATCH"
            if isinstance(deliv_anchor, dict) and deliv_anchor.get("event_id") and deliv_matched:
                fud_ref = str(deliv_anchor["event_id"])
            declared = (ctx.evidence.get("metadata") or {}).get("declared_deliverables") or []
            target = f"`{declared[0]}`" if declared else "`results.json`"
            summary = (
                f"Agent delivered {target} with a structural, representation, "
                f"or reference-ID mismatch against the verifier contract: {failure_excerpt}"
            )
            skill_prescription = None
        else:
            subtype = "result_validation"
            code = "AGENT_RESULT_VALIDATION"
            declared = (ctx.evidence.get("metadata") or {}).get("declared_deliverables") or []
            target = f"`{declared[0]}`" if declared else "`results.json`"
            summary = (
                f"Agent wrote {target} without sanity validation, "
                f"producing inaccurate physical/numerical values: {failure_excerpt}"
            )
            skill_prescription = None
    else:
        ctx.trace.record(
            gate_id="gate6_agent_primary",
            matched=False,
            reason="no positive agent causal evidence matches subcases 5a-5e",
        )
        return None

    # Check if multiple failed checks share identical summary/detail (coupled calculation)
    loc = ctx.check_localization
    if loc == "widespread":
        stats = ctx.evidence.get("verifier_check_stats") or {}
        report = ctx.evidence.get("verifier_report") or {}
        failed_checks = [c for c in (report.get("failed_checks") or []) if isinstance(c, dict)]
        details = [str(c.get("detail") or c.get("summary") or "").strip() for c in failed_checks]
        details = [d for d in details if d]
        summaries = [
            str(s).strip() for s in (stats.get("failed_check_summaries") or []) if str(s).strip()
        ]
        if (len(details) > 1 and len(set(details)) == 1) or (
            len(summaries) > 1 and len(set(summaries)) == 1
        ):
            loc = None

    # A single failing check does not prove the agent decision is the sole
    # blocker; no blind counterfactual bonus here.
    cross_source = 1 if (sci_obs or signals or ctx.agent_timeline_events) else 0

    prc = attach_confidence_metadata(
        {
            "category": "agent",
            "subtype": subtype,
            "code": code,
            "summary": summary,
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=cross_source,
        check_localization=loc,
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
            "counterfactual_test": "Correct the agent's input script / workflow decision and run `verify.py`.",
        },
        direct_causal_evidence=len(ev_refs),
        cross_source_corroboration=cross_source,
        check_localization=loc,
        hypothesis_separation=ctx.hypothesis_separation,
    )
    h2 = attach_confidence_metadata(
        {
            "hypothesis_id": "H2",
            "category": "infra",
            "subtype": "container_runtime",
            "claim": "Infrastructure prevented simulation execution.",
            "evidence_for": [],
            "evidence_against": ["art:trajectory_json"],
            "missing_evidence": [],
            "counterfactual_test": (
                "Check `runtime.agent_started` and `runtime.verifier_started` "
                + (
                    "(both true)."
                    if verifier_started
                    else "(agent started; verifier never ran, so the trial died before scoring)."
                )
            ),
        },
        direct_causal_evidence=0,
        contradicting_evidence=2,
    )

    ctx.trace.record(
        gate_id="gate6_agent_primary",
        matched=True,
        reason=f"positive agent evidence ({code})",
        checks={
            "unrecovered_sci": len(unrecovered_sci_obs),
            "repeated_failed_action": len(repeated_fail_sigs),
            "dependency_search_attempted": len(dep_search_sigs),
            "premature_completion": len(premature_sigs),
            "agent_mismatch_contracts": len(agent_mismatch_contracts),
            "agent_timeline_events": len(agent_timeline_events),
        },
        subcase_id=subcase_id,
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
            "event_ref": fud_ref,
            "timestamp": runtime.get("finished_at"),
            "summary": summary,
        },
        "failure_manifestation": {
            "type": "verifier_check_failed" if verifier_started else "agent_execution_failed",
            "summary": failure_excerpt if fail_text else summary,
        },
        "primary_root_cause": prc,
        "contributing_factors": contributing_factors,
        "competing_hypotheses": [h1, h2],
        "evidence_refs": ev_refs,
        "excluded_hypotheses": [
            {
                "hypothesis_id": "H2",
                "category": "infra",
                "reason": (
                    "Container built cleanly, agent executed commands (`runtime.agent_started == true`), and verifier ran to completion."
                    if verifier_started
                    else "Container built cleanly and agent executed commands (`runtime.agent_started == true`)."
                ),
            }
        ],
        "recommended_actions": [
            {
                "owner": "Agent Policy",
                "action": summary,
            }
        ],
        "skill_prescription": skill_prescription,
    }
