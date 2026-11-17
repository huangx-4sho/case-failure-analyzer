#!/usr/bin/env python3
"""Evidence-only causal assessments: saved outcomes do not establish causation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        if analysis.get("verdict") == "passed"
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
    if ref and ref not in execution_refs(evidence):
        errors.append("decision event must be actual execution, not reasoning or a final answer")
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
    outcome_refs = {
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
    outcome_refs |= {
        o.get("sci_id")
        for o in evidence.get("scientific_observations") or []
        if not o.get("recovered")
        and o.get("error_family") == "verifier_recompute_divergence"
        and str(o.get("source_ref", "")).startswith("verifier")
        and o.get("agent_artifact_ref")
    }
    outcome_refs |= {
        o.get("error_id")
        for o in evidence.get("error_observations") or []
        if o.get("causal_candidate") and not o.get("recovered")
    }
    report_paths = {
        "verifier/" + str(x.get("rel_path"))
        for x in (evidence.get("verifier_report") or {}).get("source_files") or []
    }
    if (evidence.get("verifier_report") or {}).get("verdict") == "fail":
        outcome_refs |= {
            r
            for r, a in artifacts.items()
            if a.get("exists") is True and a.get("rel_path") in report_paths
        }
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
    if (analysis.get("verdict") == "passed") != (a.get("level") == "not_applicable"):
        errors.append("passed status and conclusion level disagree")
    if a.get("existing_verification") != existing_verification(evidence) or a.get(
        "deliverable_audit"
    ) != evidence.get("deliverable_audit"):
        errors.append("saved verification or deliverable summary differs from evidence")
    cap = a.get("capability") or {}
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
    a = analysis.get("causal_assessment") or {}
    labels = {
        "not_applicable": ("通过试次，无失败归因", "Passed; no failure attribution"),
        "observed_failure": ("已观察到失败，原因未确定", "Failure observed; cause unresolved"),
        "candidate_cause": (
            "候选原因，现有证据审查尚不充分",
            "Candidate cause; existing evidence review incomplete",
        ),
        "evidence_supported_cause": (
            "现有证据及结构化审查支持的本次原因",
            "This-trial cause supported by existing evidence and structured review",
        ),
    }
    en = lang == "en"
    label = labels.get(a.get("level"), ("旧版归因，尚未审查", "Legacy attribution; unreviewed"))[
        int(en)
    ]
    result = f"> **{'Conclusion level' if en else '结论等级'}**: {label}\n"
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
        result += f"> **{'Reviewed decision' if en else '审查的具体决策'}**: {decision.get('description') or '—'}\n"
        result += "> [review.json](review.json)\n"
    saved = a.get("existing_verification") or {}
    result += f"> **{'Declared coverage' if en else '声明的验证覆盖'}**: {saved.get('declared_coverage') or ('unspecified' if en else '未声明')}\n"
    if saved.get("coverage_limits"):
        result += f"> **{'Coverage limits' if en else '覆盖限制'}**: {'; '.join(saved['coverage_limits'])}\n"
    result += (
        "> **Scope**: existing evidence in this trial; saved recomputation supports an outcome, not causal proof or a confirmed reusable capability gap.\n"
        if en
        else "> **适用范围**: 当前试次的现有证据；保存的重算支持结果判断，不单独证明因果关系或可复用能力缺口。\n"
    )
    return result
