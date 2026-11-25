#!/usr/bin/env python3
"""Evidence-only causal assessments: saved outcomes do not establish causation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from attribution.capabilities import static_diagnosis
from attribution.failure_location import failure_detail, render_failure_detail

SCHEMA = "cfa-causal-assessment-v1"
LEVELS = {"not_applicable", "observed_failure", "candidate_cause", "evidence_supported_cause"}
ALTERNATIVES = (
    "case",
    "infra",
    "verifier",
    "agent_implementation",
    "measurement",
    "stochastic_variation",
)
DECISION_KINDS = {
    "unreviewed",
    "scientific",
    "parameter",
    "execution",
    "delivery",
    "infrastructure",
    "case",
    "verifier",
}
EXECUTION_TYPES = {"tool_call", "file_write", "shell_error", "repeated_command"}
AGENT_DECISION_KINDS = {"scientific", "parameter", "execution", "delivery"}


# Canonical reviewed classifications; a reviewer cannot invent a category/code pair.
REVIEWED_CODES = {
    "AGENT_SCIENTIFIC_METHOD_SELECTION": ("agent", "scientific_method_selection"),
    "AGENT_SCIENTIFIC_PARAMETER_SELECTION": ("agent", "scientific_parameter_selection"),
    "AGENT_TASK_UNDERSTANDING": ("agent", "task_understanding"),
    "AGENT_PLANNING": ("agent", "planning"),
    "AGENT_TOOL_USE": ("agent", "tool_use"),
    "AGENT_REPLAY_IMPLEMENTATION_ERROR": ("agent", "replay_implementation_error"),
    "AGENT_RESULT_VALIDATION": ("agent", "result_validation"),
    "AGENT_SELF_CHECK_GAP": ("agent", "self_check_gap"),
    "AGENT_PATH_OR_DEPENDENCY_DISCOVERY": ("agent", "path_or_dependency_discovery"),
    "AGENT_ERROR_DIAGNOSIS": ("agent", "error_diagnosis"),
    "AGENT_RECOVERY": ("agent", "recovery"),
    "AGENT_PREMATURE_TERMINATION": ("agent", "premature_termination"),
    "CASE_AMBIGUOUS_CONTRACT": ("case", "ambiguous_contract"),
    "CASE_MISSING_ASSET": ("case", "missing_asset"),
    "CASE_CORRUPT_ASSET": ("case", "corrupt_asset"),
    "CASE_PROMPT_REF_MISMATCH": ("case", "prompt_ref_mismatch"),
    "CASE_INSUFFICIENT_TIMEOUT": ("case", "insufficient_timeout"),
    "INFRA_CONTAINER_BUILD": ("infra", "container_build"),
    "INFRA_CONTAINER_RUNTIME": ("infra", "container_runtime"),
    "INFRA_EXTERNAL_NETWORK": ("infra", "external_network"),
    "INFRA_API_ERROR": ("infra", "api_error"),
    "INFRA_API_RATE_LIMIT": ("infra", "api_rate_limit"),
    "INFRA_OOM": ("infra", "oom"),
    "INFRA_DISK": ("infra", "disk"),
    "INFRA_AGENT_TIMEOUT": ("infra", "agent_timeout"),
    "INFRA_RUNNER_LIFECYCLE": ("infra", "runner_lifecycle"),
    "INFRA_VERIFIER_INTERPRETER_MISSING": ("infra", "verifier_interpreter_missing"),
    "INFRA_VERIFIER_RUNTIME_REQUIREMENT": ("infra", "verifier_runtime_requirement"),
    "INFRA_AGENT_AUTOCOMPACT_THRASHING": ("infra", "autocompact_thrashing"),
    "INFRA_AGENT_OUTPUT_TOKEN_LIMIT": ("infra", "output_token_limit"),
    "VERIFIER_TOLERANCE_TOO_STRICT": ("verifier", "tolerance_too_strict"),
    "VERIFIER_SCHEMA_MISMATCH": ("verifier", "schema_mismatch"),
    "VERIFIER_REGEX_OR_PARSER_DEFECT": ("verifier", "regex_or_parser_defect"),
    "VERIFIER_HIDDEN_CONTRACT": ("verifier", "hidden_contract"),
    "VERIFIER_RECOMPUTE_DEFECT": ("verifier", "recompute_defect"),
    "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH": ("verifier", "trusted_asset_hash_mismatch"),
    "VERIFIER_REFERENCE_LFS_POINTER": ("verifier", "reference_lfs_pointer"),
    "VERIFIER_DEPENDENCY_PREFLIGHT_FAILED": ("verifier", "dependency_preflight_failed"),
}


def proposed_attribution_errors(review: dict, analysis: dict) -> List[str]:
    proposed = review.get("proposed_attribution")
    if proposed is None:
        return []
    if not isinstance(proposed, dict):
        return ["proposed_attribution must be structured"]
    errors = []
    if set(proposed) - {"category", "subtype", "code", "summary_en", "summary_zh"}:
        errors.append(
            "proposed_attribution has unsupported fields; scores and verdicts cannot be proposed"
        )
    if REVIEWED_CODES.get(proposed.get("code")) != (
        proposed.get("category"),
        proposed.get("subtype"),
    ):
        errors.append("proposed_attribution category/subtype/code conflict or unknown code")
    if not all(
        isinstance(proposed.get(k), str) and proposed[k].strip()
        for k in ("summary_en", "summary_zh")
    ):
        errors.append("proposed_attribution requires authored summary_en and summary_zh")
    kind = (review.get("decision") or {}).get("kind")
    expected = {"verifier": "verifier", "case": "case", "infra": "infrastructure"}
    category = proposed.get("category")
    if category in expected and kind != expected[category]:
        errors.append("proposed_attribution category conflicts with decision.kind")
    if category == "agent" and kind not in ("scientific", "parameter", "execution", "delivery"):
        errors.append("proposed agent attribution requires an agent execution decision")
    if analysis.get("verdict") in ("passed", "pending"):
        errors.append("cannot propose a failure cause for a passed or pending trial")
    return errors


def nonagent_events(evidence: dict, kind: str) -> dict:
    """Typed saved observations, never agent thoughts, anchor non-agent reviews."""
    if kind == "verifier":
        result = {
            o.get("obs_id"): o
            for o in evidence.get("verifier_observations") or []
            if o.get("obs_id")
        }
        result.update(
            {
                f"verifier_report.failed_checks[{i}]": o
                for i, o in enumerate(
                    (evidence.get("verifier_report") or {}).get("failed_checks") or []
                )
            }
        )
        return result
    if kind == "case":
        result = {
            o.get("contract_id"): o
            for o in evidence.get("contract_observations") or []
            if o.get("contract_id")
        }
        result.update(
            {
                o.get("review_id"): o
                for o in (evidence.get("contract_review") or {}).get("review_items") or []
                if o.get("review_id")
            }
        )
        # The runner receipt anchors what budget was applied. A case-level
        # decision still needs a reviewed workload requirement and outcome.
        result.update(
            {
                o["error_id"]: o
                for o in evidence.get("error_observations") or []
                if o.get("error_id")
                and o.get("code") == "INFRA_AGENT_TIMEOUT"
                and o.get("timeout_seconds") is not None
                and o.get("causal_candidate")
            }
        )
        return result
    if kind == "infrastructure":
        return {
            o.get("error_id"): o
            for o in evidence.get("error_observations") or []
            if o.get("error_id") and o.get("causal_candidate") and not o.get("recovered")
        }
    return {}


def terminal_failure_refs(evidence: dict) -> set:
    refs = {
        o.get("obs_id")
        for o in evidence.get("verifier_observations") or []
        if o.get("type")
        in (
            "failed_check",
            "verifier_fail_message",
            "verifier_internal_crash",
            "missing_file",
            "missing_deliverable",
        )
    }
    refs |= {
        o.get("error_id")
        for o in evidence.get("error_observations") or []
        if o.get("causal_candidate") and not o.get("recovered")
    }
    refs |= {
        o.get("sci_id")
        for o in evidence.get("scientific_observations") or []
        if not o.get("recovered")
        and o.get("error_family") == "verifier_recompute_divergence"
        and str(o.get("source_ref", "")).startswith("verifier")
        and o.get("agent_artifact_ref")
    }
    report = evidence.get("verifier_report") or {}
    refs |= {
        f"verifier_report.failed_checks[{i}]"
        for i, _ in enumerate(report.get("failed_checks") or [])
    }
    if report.get("verdict") == "fail":
        paths = [report.get("primary_report")] + [
            x.get("rel_path") if isinstance(x, dict) else x
            for x in report.get("source_files") or []
        ]
        for artifact in evidence.get("artifacts") or []:
            rel = str(artifact.get("rel_path") or "")
            if artifact.get("exists") is True and any(
                path and (rel == path or rel == "verifier/" + str(path)) for path in paths
            ):
                refs.update(
                    r
                    for r in (
                        artifact.get("artifact_id"),
                        rel,
                        f"{artifact.get('scope', 'trial')}:{rel}",
                    )
                    if r
                )
    return refs - {None}


def _nonagent_event(evidence: dict, decision: dict) -> Optional[dict]:
    kind, ref = decision.get("kind"), decision.get("event_ref")
    event = nonagent_events(evidence, kind).get(ref)
    if event is None and kind == "case" and decision.get("contract_ambiguity") is not None:
        # Use the rejection as the observed outcome of the ambiguity. The
        # case-side evidence is supplied separately in the public task basis.
        rejected = nonagent_events(evidence, "verifier").get(ref)
        if rejected and (
            rejected.get("type") == "failed_check"
            or str(ref).startswith("verifier_report.failed_checks[")
        ):
            return rejected
    return event


def _public_task_artifact(artifact: dict) -> bool:
    path = str(artifact.get("rel_path") or "")
    return bool(
        artifact.get("exists") is True
        and artifact.get("scope") == "task"
        and (
            Path(path).name.startswith("instruction.")
            or path.startswith(("environment/assets/", "assets/", "inputs/"))
        )
    )


def nonagent_binding_errors(evidence: dict, decision: dict) -> List[str]:
    kind = decision.get("kind")
    if kind not in ("verifier", "case", "infrastructure"):
        return []
    event = _nonagent_event(evidence, decision)
    if event is None:
        return ["non-agent decision event must resolve to a typed saved observation"]
    binding = decision.get("execution_binding") or {}
    errors = []
    if not terminal_failure_refs(evidence).intersection(
        (decision.get("outcome") or {}).get("evidence_refs") or []
    ):
        errors.append("non-agent decision requires cited terminal rejection evidence")
    if decision.get("event_ref") not in binding.get("evidence_refs", []):
        errors.append("non-agent event must be cited in execution_binding")
    if not binding.get("effective_input_refs") or not binding.get("effective_output_refs"):
        errors.append("non-agent event requires saved effective input and output bindings")
    refs = set()
    for key in ("execution_binding", "basis", "outcome"):
        refs.update((decision.get(key) or {}).get("evidence_refs") or [])
    artifacts = {
        r: a
        for a in evidence.get("artifacts") or []
        for r in (
            a.get("artifact_id"),
            a.get("rel_path"),
            f"{a.get('scope', 'trial')}:{a.get('rel_path')}",
        )
        if r
    }
    basis = set((decision.get("basis") or {}).get("evidence_refs") or [])
    ambiguity = decision.get("contract_ambiguity")
    if kind == "case" and isinstance(ambiguity, dict):
        public = ambiguity.get("public_source_ref")
        if (
            not isinstance(public, str)
            or public not in basis
            or not _public_task_artifact(artifacts.get(public, {}))
            or decision.get("event_ref")
            not in (decision.get("outcome") or {}).get("evidence_refs", [])
            or not any(
                artifacts.get(ref, {}).get("scope") == "trial"
                and artifacts[ref].get("exists") is True
                and str(artifacts[ref].get("rel_path") or "").startswith("artifacts/")
                for ref in binding.get("effective_input_refs", [])
            )
        ):
            errors.append(
                "case ambiguity requires a cited public task artifact, captured submission, "
                "and its named terminal rejection"
            )
    rules = {o.get("contract_id") for o in evidence.get("contract_observations") or []}
    if not (
        basis.intersection(rules)
        or any(
            artifacts.get(r, {}).get("scope") == "task" and artifacts[r].get("exists") is True
            for r in basis
        )
    ):
        errors.append(
            "non-agent event requires an applicable saved source or contract rule in basis"
        )
    sources = event.get("source_refs") or [
        event.get("source_ref") or event.get("source_file") or event.get("source")
    ]
    if kind == "verifier" and not any(sources):
        report = evidence.get("verifier_report") or {}
        sources = [report.get("primary_report")]

    def matches(source, artifact):
        source = str(source or "").split(":L", 1)[0]
        rel = str(artifact.get("rel_path") or "")
        return source in (
            artifact.get("artifact_id"),
            rel,
            f"{artifact.get('scope', 'trial')}:{rel}",
        ) or (source and rel.endswith("/" + source))

    if not any(
        a.get("exists") is True and any(matches(source, a) for source in sources)
        for ref, a in artifacts.items()
        if ref in refs
    ):
        errors.append("non-agent event is unbound to its cited saved source artifact")
    return errors


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def analysis_binding(analysis: Dict[str, Any]) -> str:
    return digest(
        {k: v for k, v in analysis.items() if k not in ("causal_assessment", "skill_prescription")}
    )


def execution_refs(evidence: Dict[str, Any]) -> set:
    return {
        e["event_id"]
        for e in evidence.get("timeline") or []
        if e.get("event_type") in EXECUTION_TYPES and e.get("event_id")
    }


def saved_execution_event(evidence: dict, decision: dict) -> Optional[dict]:
    ref = decision.get("event_ref")
    return next(
        (
            item
            for item in evidence.get("timeline") or []
            if ref
            and item.get("event_id") == ref
            and item.get("event_type") in EXECUTION_TYPES
            and item.get("result_recorded") is True
        ),
        None,
    )


def agent_defect_evidence(evidence: dict, decision: dict) -> bool:
    """Check the cross-source binding of the reviewed discrepancy itself."""
    binding = decision["execution_binding"]
    ref = decision.get("event_ref")
    mechanism = decision.get("mechanism") or {}
    if not (
        decision.get("kind") in AGENT_DECISION_KINDS
        and str(decision.get("description") or "").strip()
        and str(decision.get("rationale") or "").strip()
        and mechanism.get("actual_logic", "").strip() != mechanism.get("expected_logic", "").strip()
        and ref in binding.get("evidence_refs", [])
        and saved_execution_event(evidence, decision)
    ):
        return False
    artifacts = {
        alias: artifact
        for artifact in evidence.get("artifacts") or []
        for alias in (
            artifact.get("artifact_id"),
            artifact.get("rel_path"),
            f"{artifact.get('scope', 'trial')}:{artifact.get('rel_path')}",
        )
        if alias
    }
    inputs = binding.get("effective_input_refs") or []
    outputs = binding.get("effective_output_refs") or []
    if (
        not inputs
        or not outputs
        or not all(artifacts.get(ref, {}).get("exists") is True for ref in [*inputs, *outputs])
    ):
        return False
    basis = [artifacts[ref] for ref in decision["basis"]["evidence_refs"] if ref in artifacts]
    public_basis = any(_public_task_artifact(artifact) for artifact in basis)
    recorded_output = any(
        artifacts[ref].get("scope", "trial") == "trial"
        and str(artifacts[ref].get("rel_path") or "").startswith("artifacts/")
        for ref in outputs
    )
    return (
        public_basis
        and recorded_output
        and bool(terminal_failure_refs(evidence).intersection(decision["outcome"]["evidence_refs"]))
    )


def quantitative_review_status(
    evidence: dict, decision: dict, *, verified_defect: bool, checked_verifier_files: set[str]
) -> str:
    """Distinguish a scored discrepancy from an isolated, retained comparison."""
    outcome = set(decision["outcome"]["evidence_refs"])
    checks = (evidence.get("verifier_report") or {}).get("failed_checks") or []
    cited_observations = {
        (
            item.get("name"),
            item.get("source_ref"),
            item.get("source_pointer"),
            item.get("matched_text"),
        )
        for item in evidence.get("verifier_observations") or []
        if item.get("obs_id") in outcome and item.get("type") == "failed_check"
    }
    selected = {
        f"verifier_report.failed_checks[{i}]": check
        for i, check in enumerate(checks)
        if f"verifier_report.failed_checks[{i}]" in outcome
        or (
            check.get("name"),
            check.get("source"),
            check.get("source_pointer"),
            check.get("detail"),
        )
        in cited_observations
    }
    witness = decision.get("quantitative_evidence") or {}
    if witness:
        check = selected.get(witness["check_ref"], {})
        record = (check.get("evidence") or {}).get("isolated_comparison")
        if (
            verified_defect
            and f"verifier/{check.get('source')}" in checked_verifier_files
            and isinstance(record, dict)
            and record.get("decision_event_ref") == decision["event_ref"]
            and record.get("isolated_change") is True
        ):
            try:
                numbers = [
                    record.get(key) for key in ("submitted", "reference", "corrected", "tolerance")
                ]
                if all(
                    type(number) in (int, float) and math.isfinite(number) for number in numbers
                ):
                    submitted, reference, corrected, tolerance = numbers
                    if tolerance >= 0 and abs(submitted - reference) > tolerance >= abs(
                        corrected - reference
                    ):
                        return "sufficient"
            except OverflowError:
                pass
    scientific_comparison = any(
        item.get("sci_id") in outcome
        and not item.get("recovered")
        and item.get("error_family") == "verifier_recompute_divergence"
        for item in evidence.get("scientific_observations") or []
    )
    numerical_scope = (
        witness
        or scientific_comparison
        or any(
            # Parameter labels in a missing-output diagnostic are not a scored comparison.
            not re.search(
                r"\b(?:no such file or directory|unsafe candidate output|missing required path)\b",
                str(check.get("detail") or ""),
                re.I,
            )
            and (
                check.get("failure_kind") == "numerical"
                or re.search(
                    r"\b(?:arithmetic|numerical|recomput\w*|relative error)\b",
                    f"{check.get('name', '')} {check.get('detail', '')}",
                    re.I,
                )
            )
            for check in selected.values()
        )
    )
    return "unverified_without_recompute" if numerical_scope else "not_applicable"


def existing_verification(evidence: Dict[str, Any]) -> dict:
    report = evidence.get("verifier_report") or {}
    records = []
    for item in evidence.get("scientific_observations") or []:
        if item.get("recomputed_value") is None:
            continue
        # The parser also normalizes reference comparisons into recomputed_value.
        # Preserve that ambiguity unless the saved text explicitly says recompute.
        text = str(item.get("matched_text") or "")
        saved_recompute = bool(
            re.search(r"recomputed|independent.*replay", text, re.I)
        ) and not re.search(
            r"not\s+recomput|never\s+recomput|recomput\w*\s+(?:skipped|not run)", text, re.I
        )
        is_delta = bool(item.get("is_delta_divergence"))
        records.append(
            {
                "kind": "verifier_reported_recomputation"
                if saved_recompute
                else "reference_comparison_or_unspecified",
                **{
                    k: item.get(k)
                    for k in (
                        "sci_id",
                        "source_ref",
                        "matched_text",
                        "metric",
                        "reported_value",
                        "tolerance",
                        "agent_artifact_ref",
                        "agent_ref",
                        "agent_reported_value",
                        "check_kind",
                    )
                },
                "reported_value_meaning": "deviation" if is_delta else "verifier_reported_value",
                "recomputed_value": None if is_delta else item.get("recomputed_value"),
                "is_delta_divergence": is_delta,
            }
        )
    return {
        "source_files": copy.deepcopy(report.get("source_files") or []),
        "verdict": report.get("verdict"),
        "failed_checks": copy.deepcopy(report.get("failed_checks") or []),
        "declared_coverage": report.get("verification_coverage"),
        "coverage_limits": copy.deepcopy(report.get("coverage_limits") or []),
        "numeric_records": records,
        "independence": "not_established",
        "new_calculation_performed": False,
        "limitations": [
            "Saved checks establish recorded outcomes within their coverage, not the cause or a reusable scientific capability gap.",
            "FAIL alone does not establish independent recomputation; reference comparisons remain distinguished.",
        ],
    }


def assess_static(evidence: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    previous = analysis.get("causal_assessment") or {}
    proposal = analysis.get("skill_prescription") or (previous.get("capability") or {}).get(
        "proposed_guidance"
    )
    prc = analysis.get("primary_root_cause") or {}
    ref = (analysis.get("first_unrecovered_deviation") or {}).get("event_ref")
    analysis["causal_assessment"] = {
        "schema_version": SCHEMA,
        "level": "not_applicable"
        if analysis.get("verdict") in ("passed", "pending")
        else "observed_failure"
        if prc.get("category", "unknown") == "unknown"
        else "candidate_cause",
        "scope": "this_trial_only",
        "decision": {
            "description": prc.get("summary", ""),
            "event_ref": ref if ref in execution_refs(evidence) else None,
            "kind": "unreviewed",
            "rationale": "",
            "execution_binding": {
                "statement": "",
                "evidence_refs": [],
                "effective_input_refs": [],
                "effective_output_refs": [],
            },
            "basis": {"statement": "", "evidence_refs": []},
            "outcome": {"statement": "", "evidence_refs": []},
        },
        "alternatives": [
            {
                "category": c,
                "status": "unresolved",
                "reason": "No supported review recorded",
                "evidence_refs": [],
            }
            for c in ALTERNATIVES
        ],
        "existing_verification": existing_verification(evidence),
        "deliverable_audit": copy.deepcopy(evidence.get("deliverable_audit")),
        "contract_review": copy.deepcopy(evidence.get("contract_review")),
        "review": None,
        "capability": {
            "status": "suspected"
            if prc.get("category") == "agent" and proposal
            else "not_assessed",
            "scope": "this_trial_only",
            "proposed_guidance": copy.deepcopy(proposal),
            "independent_cases": [],
        },
    }
    if prc.get("category") == "agent" and analysis.get("verdict") == "failed":
        analysis["causal_assessment"]["capability"]["diagnosis"] = static_diagnosis(
            evidence, analysis, execution_refs(evidence)
        )
    if analysis.get("verdict") not in ("passed", "pending") and "failure_locations" in (
        evidence.get("contract_review") or {}
    ):
        analysis["causal_assessment"]["failure_detail"] = failure_detail(evidence, analysis)
    analysis["skill_prescription"] = None
    return analysis


def primary_alternative(category: Optional[str], kind: Optional[str]) -> Optional[str]:
    if category == "agent":
        return "agent_implementation" if kind in ("execution", "delivery") else None
    return category if category in ("infra", "case", "verifier") else None


def review_errors(
    evidence: dict, review: dict, valid_refs: set, category: Optional[str] = None
) -> List[str]:
    """Validate the substance supplied by a reviewer; flags cannot replace rationale."""
    errors = []
    decision = review.get("decision")
    alternatives = review.get("alternatives")
    if not isinstance(decision, dict) or not isinstance(alternatives, list):
        return ["decision and alternatives must be structured"]
    if decision.get("kind") not in DECISION_KINDS:
        errors.append("invalid decision.kind")
    ref = decision.get("event_ref")
    if ref and ref not in execution_refs(evidence) and _nonagent_event(evidence, decision) is None:
        errors.append("decision event must be actual execution, not reasoning or a final answer")
    ambiguity = decision.get("contract_ambiguity")
    ambiguity_fields = {"public_source_ref", "submitted_interpretation", "scorer_interpretation"}
    if ambiguity is not None and (
        category != "case"
        or decision.get("kind") != "case"
        or not isinstance(ambiguity, dict)
        or set(ambiguity) != ambiguity_fields
        or any(not isinstance(value, str) or not value.strip() for value in ambiguity.values())
        or ambiguity["submitted_interpretation"].strip()
        == ambiguity["scorer_interpretation"].strip()
    ):
        errors.append(
            "decision.contract_ambiguity requires a public source and two distinct interpretations"
        )
    follow_up = decision.get("follow_up")
    if follow_up is not None and (
        not isinstance(follow_up, dict)
        or set(follow_up) != {"action_en", "action_zh"}
        or any(not isinstance(value, str) or not value.strip() for value in follow_up.values())
    ):
        errors.append("decision.follow_up requires nonempty action_en and action_zh")
    mechanism = decision.get("mechanism")
    if mechanism is not None:
        fields = {"actual_logic", "expected_logic", "why_unrecovered", "minimal_fix"}
        if (
            category != "agent"
            or ref not in execution_refs(evidence)
            or not isinstance(mechanism, dict)
            or set(mechanism) != fields
            or any(not isinstance(value, str) or not value.strip() for value in mechanism.values())
        ):
            errors.append(
                "decision.mechanism requires an agent execution event and four nonempty fields: "
                "actual_logic, expected_logic, why_unrecovered, minimal_fix"
            )
    quantity = decision.get("quantitative_evidence")
    if quantity is not None:
        outcome = decision.get("outcome") or {}
        if (
            not mechanism
            or not isinstance(quantity, dict)
            or set(quantity) != {"check_ref", "statement"}
            or not isinstance(quantity.get("check_ref"), str)
            or not re.fullmatch(r"verifier_report\.failed_checks\[\d+\]", quantity["check_ref"])
            or not isinstance(outcome, dict)
            or quantity["check_ref"] not in (outcome.get("evidence_refs") or [])
            or not isinstance(quantity.get("statement"), str)
            or not quantity["statement"].strip()
        ):
            errors.append(
                "decision.quantitative_evidence requires a mechanism and a cited failed check "
                "with a statement about the retained isolation comparison"
            )
    for key in ("execution_binding", "basis", "outcome"):
        item = decision.get(key)
        if not isinstance(item, dict):
            errors.append(f"decision.{key} must be structured")
            continue
        refs = item.get("evidence_refs", [])
        if not isinstance(refs, list) or any(
            not isinstance(r, str) or r not in valid_refs for r in refs
        ):
            errors.append(f"decision.{key}: unresolved evidence reference")
        if refs and not str(item.get("statement") or "").strip():
            errors.append(f"decision.{key}: evidence requires a review statement")
    errors.extend(nonagent_binding_errors(evidence, decision))
    binding = decision.get("execution_binding") or {}
    for key in ("effective_input_refs", "effective_output_refs"):
        refs = binding.get(key, [])
        artifact_refs = {
            r
            for a in evidence.get("artifacts") or []
            for r in (
                a.get("artifact_id"),
                a.get("rel_path"),
                f"{a.get('scope', 'trial')}:{a.get('rel_path')}",
            )
            if r
        }
        if not isinstance(refs, list) or any(
            not isinstance(r, str) or r not in artifact_refs for r in refs
        ):
            errors.append(f"execution_binding.{key} must resolve to saved artifacts")
    if len(alternatives) != len(ALTERNATIVES) or {
        a.get("category") for a in alternatives if isinstance(a, dict)
    } != set(ALTERNATIVES):
        errors.append("review every required alternative exactly once")
    for item in alternatives:
        if not isinstance(item, dict):
            errors.append("invalid alternative record")
            continue
        refs = item.get("evidence_refs", [])
        if not isinstance(refs, list) or any(
            not isinstance(r, str) or r not in valid_refs for r in refs
        ):
            errors.append("alternative: unresolved evidence reference")
        if item.get("status") not in (
            "unresolved",
            "ruled_out",
            "not_applicable",
            "supports_candidate",
        ):
            errors.append("invalid alternative status")
        if item.get("status") != "unresolved" and (
            not refs or not str(item.get("reason") or "").strip()
        ):
            errors.append("alternative review requires rationale and evidence")
    primary = primary_alternative(category, decision.get("kind"))
    if decision.get("kind") != "unreviewed" and primary:
        if any(
            a.get("category") == primary and a.get("status") in ("ruled_out", "not_applicable")
            for a in alternatives
            if isinstance(a, dict)
        ):
            errors.append("review contradicts its primary cause by excluding its own explanation")
    return errors


def promotion_ready(evidence: dict, review: dict, category: Optional[str] = None) -> bool:
    d = review["decision"]
    if nonagent_binding_errors(evidence, d):
        return False
    if category not in ("agent", "infra", "case", "verifier"):
        return False
    if category == "agent" and d.get("kind") not in (
        "scientific",
        "parameter",
        "execution",
        "delivery",
    ):
        return False
    if (
        d.get("kind") in (None, "unreviewed")
        or not d.get("event_ref")
        or not str(d.get("description") or "").strip()
        or not str(d.get("rationale") or "").strip()
    ):
        return False
    if any(
        not d.get(k, {}).get("evidence_refs")
        or not str(d.get(k, {}).get("statement") or "").strip()
        for k in ("execution_binding", "basis", "outcome")
    ):
        return False
    if d["event_ref"] not in d["execution_binding"]["evidence_refs"]:
        return False
    binding = d["execution_binding"]
    if not binding.get("effective_input_refs") or not binding.get("effective_output_refs"):
        return False
    artifacts = {
        r: a
        for a in evidence.get("artifacts") or []
        for r in (
            a.get("artifact_id"),
            a.get("rel_path"),
            f"{a.get('scope', 'trial')}:{a.get('rel_path')}",
        )
        if r
    }
    inputs = [artifacts.get(r, {}) for r in binding["effective_input_refs"]]
    outputs = [artifacts.get(r, {}) for r in binding["effective_output_refs"]]
    if any(a.get("exists") is not True for a in inputs):
        return False
    missing_outputs = [a for a in outputs if a.get("exists") is not True]
    missing_inference = bool(missing_outputs) or d.get("inference_kind") == "missing_artifact"
    if missing_inference:
        coverage = (evidence.get("deliverable_audit") or {}).get("coverage") or {}
        trajectory = evidence.get("trajectory_normalization") or {}
        if coverage.get("scan_complete") is not True or trajectory.get("coverage") != "complete":
            return False
        # A collected-tree absence is not proof that the agent never produced a
        # file. Require a declared path and a saved task/runner/verifier observation
        # explicitly binding that path to this missing-output outcome.
        if not missing_outputs:
            return False
        contracts = evidence.get("contract_observations") or []
        observations = evidence.get("verifier_observations") or []
        cited = set(d["basis"]["evidence_refs"] + d["outcome"]["evidence_refs"])
        for artifact in missing_outputs:
            path = artifact.get("rel_path")
            # Only exact structured path relationships count. Prose substrings
            # such as output.json.backup never establish output.json's status.
            required = any(
                o.get("contract_id") in cited
                and o.get("item") == f"file:{path}"
                and o.get("prompt_requirement") == "required"
                for o in contracts
            )
            absent_at_verify = any(
                o.get("contract_id") in cited
                and o.get("item") == f"file:{path}"
                and o.get("agent_output_status") == "missing_at_verify"
                for o in contracts
            )
            verifier_missing = any(
                o.get("obs_id") in d["outcome"]["evidence_refs"]
                and o.get("type") in ("missing_file", "missing_deliverable")
                and any(
                    o.get(key) == path
                    for key in ("path", "declared_path", "expected_path", "artifact_path")
                )
                for o in observations
            )
            collection_declared = any(
                o.get("contract_id") in cited
                and o.get("item") == f"file:{path}"
                and o.get("prompt_requirement") == "declared_collection_path"
                and o.get("agent_output_status") == "missing_at_verify"
                for o in contracts
            )
            # Collection configuration declares what Harbor saves, not what the
            # public task requires. A reviewer must additionally cite the public
            # instruction (or a separately structured required contract).
            public_instruction = any(
                a.get("scope") == "task"
                and a.get("exists") is True
                and Path(str(a.get("rel_path", ""))).name == "instruction.md"
                for r, a in artifacts.items()
                if r in d["basis"]["evidence_refs"]
            )
            collection_supported = (
                collection_declared and verifier_missing and (public_instruction or required)
            )
            strict_required = required and (absent_at_verify or verifier_missing)
            if not path or not (strict_required or collection_supported):
                return False
    if d.get("kind") in ("scientific", "parameter"):
        applicable_refs = {
            o.get("contract_id") for o in evidence.get("contract_observations") or []
        }
        applicable_refs |= {
            r for r, a in artifacts.items() if a.get("scope") == "task" and a.get("exists") is True
        }
        if not applicable_refs.intersection(d["basis"]["evidence_refs"]):
            return False
    # Saved outcomes must be terminal failures; recovered scientific warnings and
    # a merely inventoried result.json are insufficient for promotion.
    outcome_refs = terminal_failure_refs(evidence)
    if not outcome_refs.intersection(d["outcome"]["evidence_refs"]):
        return False
    primary = primary_alternative(category, d.get("kind"))
    return all(
        a.get("status") == "supports_candidate"
        if a.get("category") == primary
        else a.get("status") in ("ruled_out", "not_applicable")
        for a in review["alternatives"]
    )


def validate_assessment(
    evidence: Dict[str, Any],
    analysis: Dict[str, Any],
    valid_refs: set,
    review_root: Optional[Path] = None,
) -> List[str]:
    a = analysis.get("causal_assessment")
    if a is None:
        return []
    if not isinstance(a, dict) or a.get("schema_version") != SCHEMA:
        return ["CAUSAL_ASSESSMENT: invalid assessment schema"]
    errors = review_errors(
        evidence, a, valid_refs, (analysis.get("primary_root_cause") or {}).get("category")
    )
    if a.get("level") not in LEVELS:
        errors.append("invalid conclusion level")
    if ("defect_verification" in a or "quantitative_sufficiency" in a) and not a.get("review"):
        errors.append("defect and quantitative statuses require a replayable evidence review")
    if (analysis.get("verdict") in ("passed", "pending")) != (a.get("level") == "not_applicable"):
        errors.append("passed status and conclusion level disagree")
    if a.get("existing_verification") != existing_verification(evidence) or a.get(
        "deliverable_audit"
    ) != evidence.get("deliverable_audit"):
        errors.append("saved verification or deliverable summary differs from evidence")
    if a.get("contract_review") != evidence.get("contract_review"):
        errors.append("contract review differs from saved evidence")
    if "failure_detail" in a and a["failure_detail"] != failure_detail(evidence, analysis):
        errors.append("failure detail differs from the saved runtime or scoring record")
    if "deep_review" in a:
        from deep_review import validation_error

        error = validation_error(a["deep_review"], evidence, review_root is not None)
        if error:
            errors.append("DEEP_REVIEW: " + error)
    manifestation = analysis.get("failure_manifestation")
    if isinstance(manifestation, dict) and "runtime_triage" in manifestation:
        from attribution.runtime_triage import build_runtime_triage

        triage = manifestation["runtime_triage"]
        receipt = next(
            (
                o
                for o in evidence.get("error_observations") or []
                if isinstance(triage, dict)
                and o.get("error_id")
                and o["error_id"] == triage.get("terminal_ref")
                and o.get("causal_candidate")
            ),
            None,
        )
        if receipt is None or triage != build_runtime_triage(
            receipt,
            evidence.get("timeline") or [],
            evidence.get("behavioral_signals") or [],
            evidence.get("runtime"),
        ):
            errors.append("runtime triage differs from saved terminal and trace evidence")
    cap = a.get("capability") or {}
    if "diagnosis" in cap:
        if (analysis.get("primary_root_cause") or {}).get("category") != "agent" or analysis.get(
            "verdict"
        ) != "failed":
            errors.append("capability diagnosis requires a failed agent attribution")
        elif not a.get("review"):
            diagnosis = cap["diagnosis"]
            expected = static_diagnosis(evidence, analysis, execution_refs(evidence))
            if isinstance(diagnosis, dict) and "mechanism_tag" not in diagnosis:
                expected.pop("mechanism_tag", None)
            if diagnosis != expected:
                errors.append(
                    "unreviewed capability diagnosis differs from the saved decision evidence"
                )
    if (
        cap.get("status") not in ("not_assessed", "suspected")
        or cap.get("independent_cases")
        or analysis.get("skill_prescription") is not None
    ):
        errors.append("single-trial review cannot confirm capability or prescribe a reusable skill")
    if a.get("review") is not None or a.get("level") == "evidence_supported_cause":
        if review_root is None:
            errors.append("review bundle required for reviewed claims")
        else:
            try:
                from review_cause import validate_bundle

                validate_bundle(review_root, evidence, analysis)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(str(exc))
    return ["CAUSAL_ASSESSMENT: " + e for e in errors]


def render_assessment(analysis: Dict[str, Any], lang: str = "zh") -> str:
    from report_style import report_text, localized_field
    from attribution.runtime_triage import render_runtime_triage

    a = analysis.get("causal_assessment") or {}
    labels = {
        "not_applicable": ("通过试次，无失败归因", "Passed; no failure attribution"),
        "observed_failure": ("已观察到失败，原因未确定", "Failure observed; cause unresolved"),
        "candidate_cause": ("候选原因", "Candidate cause"),
        "evidence_supported_cause": (
            "现有证据及结构化审查支持的本次原因",
            "This-trial cause supported by existing evidence and structured review",
        ),
    }
    if (a.get("failure_detail") or {}).get("scoring_event"):
        labels["observed_failure"] = (
            "已定位拒收项，上游责任待定",
            "Rejection located, upstream cause open",
        )
    if a.get("defect_verification") == "verified_in_trial":
        labels["candidate_cause"] = (
            "已核实具体缺陷，整体归因仍为候选",
            "Specific defect verified, overall attribution provisional",
        )
    if analysis.get("verdict") == "pending":
        labels["not_applicable"] = (
            "待独立复核，暂无失败归因",
            "Pending independent review; no failure attribution",
        )
    en = lang == "en"
    if (a.get("deep_review") or {}).get("status") == "traced":
        labels["observed_failure"] = (
            "本项拒绝已追到具体决策，责任归属单列",
            "Decision traced for this rejection; responsibility assessed separately",
        )
    label = labels.get(a.get("level"), ("旧版归因，尚未审查", "Legacy attribution; unreviewed"))[
        int(en)
    ]
    result = f"> **{'Conclusion level' if en else '结论等级'}**: {label}\n"
    if a.get("deep_review"):
        from deep_review import render_review

        result += render_review(a["deep_review"], lang)
    location = render_failure_detail(a, lang)
    if location:
        result += location + "\n"
    result += render_runtime_triage(analysis, lang)
    unresolved = [
        x.get("category") for x in a.get("alternatives") or [] if x.get("status") == "unresolved"
    ]
    if not en:
        names = {
            "case": "题目",
            "infra": "运行环境",
            "verifier": "验证器",
            "agent_implementation": "执行实现",
            "measurement": "测量与后处理",
            "stochastic_variation": "随机波动",
        }
        unresolved = [names.get(x, x) for x in unresolved]
    if unresolved:
        result += (
            f"> **{'Unresolved alternatives' if en else '尚未排除'}**: {', '.join(unresolved)}\n"
        )
    if a.get("review"):
        decision = a.get("decision") or {}
        result += f"> **{'Reviewed decision' if en else '审查的具体决策'}**: {localized_field(decision, 'description', lang) or '—'}\n"
        defect_label = {
            "verified_in_trial": ("已由本试次记录证实", "Verified in this trial"),
            "unverified": ("现有记录尚不足以证实", "Unverified by existing records"),
        }.get(a.get("defect_verification"))
        if defect_label:
            result += f"> **{'Specific defect' if en else '具体缺陷'}** · {defect_label[int(en)]}\n"
        quantity_label = {
            "sufficient": (
                "已有隔离对照支持该缺陷解释所引拒绝",
                "A retained isolated comparison supports the defect's explanation of the cited rejection",
            ),
            "unverified_without_recompute": (
                "现有记录未确定该缺陷对所引偏差的全部贡献",
                "The full contribution to the cited discrepancy remains unverified",
            ),
            "not_applicable": ("复核未涉及定量偏差", "No quantitative discrepancy in review scope"),
        }.get(a.get("quantitative_sufficiency"))
        if quantity_label:
            result += f"> **{'Quantitative sufficiency' if en else '定量充分性'}** · {quantity_label[int(en)]}\n"
        mechanism = decision.get("mechanism") or {}
        for key, label_en, label_zh in (
            ("actual_logic", "Actual implementation", "实际实现"),
            ("expected_logic", "Expected logic", "应有逻辑"),
            ("why_unrecovered", "Why it persisted", "未纠正原因"),
            ("minimal_fix", "Minimal correction", "最小修正"),
        ):
            if mechanism.get(key):
                detail = localized_field(mechanism, key, lang)
                result += f"> **{label_en if en else label_zh}** · {detail}\n"
        result += "> [review.json](review.json)\n"
    items = (a.get("contract_review") or {}).get("review_items") or []
    if items:
        result += (
            "> **Contract review required**: representation/definition rejection; scientific failure is not established by this alone.\n"
            if en
            else "> **需复核评分契约**: 表示形式或定义上的拒绝，不能单独认定科学失败。\n"
        )
    saved = a.get("existing_verification") or {}
    result += f"> **{'Declared coverage' if en else '声明的验证覆盖'}**: {report_text(saved.get('declared_coverage'), lang) or ('unspecified' if en else '未声明')}\n"
    if saved.get("coverage_limits"):
        result += f"> **{'Coverage limits' if en else '覆盖限制'}**: {'; '.join(report_text(x, lang) for x in saved['coverage_limits'])}\n"
    scoring_event = (a.get("failure_detail") or {}).get("scoring_event") or {}
    if scoring_event.get("stage") == "preflight":
        result += (
            "> Scoring stopped at dependency preflight. The coverage above is the verifier's declaration.\n"
            if en
            else "> 本次评分在依赖预检处中断。上方覆盖范围为评分器声明。\n"
        )
    else:
        result += (
            "> **Scope**: existing evidence in this trial; saved recomputation supports an outcome, not causal proof or a confirmed reusable capability gap.\n"
            if en
            else "> **适用范围**: 当前试次的现有证据；保存的重算支持结果判断，不单独证明因果关系或可复用能力缺口。\n"
        )
    return result
