#!/usr/bin/env python3
"""Review saved evidence without running verifiers, simulations, or commands."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from causal_assessment import (
    analysis_binding,
    assess_static,
    digest,
    promotion_ready,
    proposed_attribution_errors,
    review_errors,
)
from validate_analysis import collect_valid_evidence_ids

REVIEW_SCHEMA = "cfa-evidence-review-v1"


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_fingerprints(evidence: dict) -> list:
    """Stream bounded freshness checks; do not silently bind changed sources."""
    result, budget = [], 8 * 1024 * 1024
    for artifact in evidence.get("artifacts") or []:
        raw = artifact.get("abs_path")
        entry = {
            "artifact_id": artifact.get("artifact_id"),
            "path": raw,
            "sha256": None,
            "status": "unavailable",
        }
        if raw:
            path = Path(raw)
            try:
                if path.is_file():
                    size = path.stat().st_size
                    if size > min(1024 * 1024, budget):
                        entry["status"] = "size_limit"
                    else:
                        value = hashlib.sha256()
                        with path.open("rb") as handle:
                            remaining = size + 1
                            while remaining:
                                chunk = handle.read(min(65536, remaining))
                                if not chunk:
                                    break
                                remaining -= len(chunk)
                                value.update(chunk)
                        budget -= size
                        if remaining != 1:
                            entry["status"] = "changed_during_read"
                        else:
                            current = value.hexdigest()
                            saved = str(artifact.get("sha256") or "").removeprefix("sha256:")
                            entry.update(
                                status="changed_since_collection"
                                if saved and saved != current
                                else "checked",
                                sha256=current,
                            )
            except OSError:
                entry["status"] = "unreadable"
        result.append(entry)
    return result


def reject_input_write(output: Path, evidence: dict) -> None:
    target = output.resolve()
    roots = [
        Path(evidence[k]).resolve() for k in ("job_dir", "trial_dir", "task_dir") if evidence.get(k)
    ]
    if any(target == root or root in target.parents for root in roots):
        raise ValueError("Review output overlaps original benchmark input directories")


def plan(evidence: dict, analysis: dict) -> dict:
    fresh = copy.deepcopy(analysis)
    assess_static(evidence, fresh)
    a = fresh["causal_assessment"]
    return {
        "schema_version": REVIEW_SCHEMA,
        "binding": {
            "evidence_digest": digest(evidence),
            "analysis_digest": analysis_binding(analysis),
            "original_analysis_digest": digest(analysis),
            "source_fingerprints": source_fingerprints(evidence),
        },
        "reviewer": "",
        "rationale": "",
        "decision": a["decision"],
        "alternatives": a["alternatives"],
        "limitations": [
            "Use existing evidence only; unresolved alternatives prevent promotion.",
            "Review rationale and applicability must be substantive; confirmed=true is not evidence.",
        ],
    }


def cited_source_refs(evidence: dict, review: dict) -> set:
    """Resolve every cited claim and alternative to its saved source inventory."""
    decision = review.get("decision") or {}
    refs = {
        r
        for key in ("execution_binding", "basis", "outcome")
        for r in (decision.get(key) or {}).get("evidence_refs", [])
    }
    if decision.get("event_ref"):
        refs.add(decision["event_ref"])
    refs.update((decision.get("execution_binding") or {}).get("effective_input_refs", []))
    refs.update((decision.get("execution_binding") or {}).get("effective_output_refs", []))
    refs.update(
        r for item in review.get("alternatives") or [] for r in item.get("evidence_refs", [])
    )
    observations = {}
    for collection, key in (
        ("timeline", "event_id"),
        ("verifier_observations", "obs_id"),
        ("contract_observations", "contract_id"),
        ("scientific_observations", "sci_id"),
        ("error_observations", "error_id"),
        ("behavioral_signals", "signal_id"),
    ):
        observations.update({o[key]: o for o in evidence.get(collection) or [] if o.get(key)})
    observations.update(
        {
            f"verifier_report.failed_checks[{i}]": item
            for i, item in enumerate(
                (evidence.get("verifier_report") or {}).get("failed_checks") or []
            )
        }
    )
    observations.update(
        {
            o["review_id"]: o
            for o in (evidence.get("contract_review") or {}).get("review_items") or []
            if o.get("review_id")
        }
    )
    pending = list(refs)
    while pending:
        ref = pending.pop()
        item = observations.get(ref, {})
        sources = list(item.get("source_refs") or [])
        sources.extend(
            item.get(k)
            for k in (
                "source_ref",
                "source_file",
                "source",
                "event_ref",
                "agent_artifact_ref",
                "agent_ref",
            )
        )
        if ref.startswith("verifier_report.failed_checks["):
            sources.append((evidence.get("verifier_report") or {}).get("primary_report"))
        for source in sources:
            if isinstance(source, str) and source and source not in refs:
                refs.add(source)
                pending.append(source)
    trajectory_cited = any(
        r.startswith("trajectory:")
        or r in {o.get("event_id") for o in evidence.get("timeline") or []}
        for r in refs
    )
    source_paths = {r.split(":L", 1)[0] for r in refs}
    for artifact in evidence.get("artifacts") or []:
        rel = str(artifact.get("rel_path") or "")
        if any(path and (rel == path or rel.endswith("/" + path)) for path in source_paths) or (
            trajectory_cited
            and (
                artifact.get("artifact_id") == "art:trajectory_json"
                or rel == "agent/trajectory.json"
            )
        ):
            if artifact.get("artifact_id"):
                refs.add(artifact["artifact_id"])
    return refs


def check_binding(review: dict, evidence: dict, analysis: dict) -> None:
    if review.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError("Invalid evidence review schema")
    binding = review.get("binding") or {}
    if binding.get("evidence_digest") != digest(evidence) or binding.get(
        "analysis_digest"
    ) != analysis_binding(analysis):
        raise ValueError("Stale or tampered evidence/analysis binding")
    if binding.get("original_analysis_digest") is not None and binding[
        "original_analysis_digest"
    ] != digest(analysis):
        raise ValueError("Stale or tampered original analysis snapshot")
    # Shared job logs can continue changing while an ended trial is reviewed.
    # Freshness must bind the sources actually used by this review, not unrelated
    # inventory files. The full evidence JSON still remains digest-bound above.
    refs = cited_source_refs(evidence, review)
    selected = [
        a
        for a in evidence.get("artifacts") or []
        if {
            a.get("artifact_id"),
            a.get("rel_path"),
            f"{a.get('scope', 'trial')}:{a.get('rel_path')}",
        }.intersection(refs)
    ]
    original = {
        (x.get("artifact_id"), x.get("path")): x for x in binding.get("source_fingerprints") or []
    }
    current = source_fingerprints({**evidence, "artifacts": selected})
    for artifact, now in zip(selected, current):
        previous = original.get((artifact.get("artifact_id"), artifact.get("abs_path")))
        if previous is None or (
            (previous.get("status") == "checked" or artifact.get("exists") is False)
            and previous != now
        ):
            raise ValueError("Stale or tampered saved source artifacts")


def reviewed_analysis(evidence: dict, analysis: dict, review: dict) -> dict:
    check_binding(review, evidence, analysis)
    errors = proposed_attribution_errors(review, analysis)
    proposed = review.get("proposed_attribution")
    category = (proposed or analysis.get("primary_root_cause") or {}).get("category")
    errors += review_errors(
        evidence,
        review,
        collect_valid_evidence_ids(evidence),
        category,
    )
    if errors:
        raise ValueError("; ".join(errors))
    if (
        not str(review.get("reviewer") or "").strip()
        or not str(review.get("rationale") or "").strip()
    ):
        raise ValueError("Review requires reviewer identity and written rationale")
    result = copy.deepcopy(analysis)
    if proposed:
        original_cause = copy.deepcopy(result.get("primary_root_cause") or {})
        result["primary_root_cause"] = {
            **original_cause,
            **copy.deepcopy(proposed),
            "summary": proposed["summary_en"],
            "confidence_kind": "original_rule_evidence_score",
            "confidence_provenance": {
                "source": "original-analysis.json",
                "primary_root_cause": original_cause,
            },
        }
        event_ref = review["decision"]["event_ref"]
        result["first_unrecovered_deviation"] = {
            "status": "identified",
            "event_ref": event_ref,
            "summary": proposed["summary_en"],
        }
        if proposed["category"] == "verifier":
            result["failure_stage"] = "verifier_execution"
        elif proposed["category"] == "infra":
            observed = next(
                (
                    o
                    for o in evidence.get("error_observations") or []
                    if o.get("error_id") == event_ref
                ),
                {},
            )
            if observed.get("stage"):
                result["failure_stage"] = observed["stage"]
        cited = list(
            dict.fromkeys(
                [review["decision"]["event_ref"]]
                + [
                    r
                    for key in ("execution_binding", "basis", "outcome")
                    for r in review["decision"][key].get("evidence_refs", [])
                ]
                + review["decision"]["execution_binding"].get("effective_input_refs", [])
                + review["decision"]["execution_binding"].get("effective_output_refs", [])
            )
        )
        result["evidence_refs"] = cited
        result["competing_hypotheses"] = [
            {
                "hypothesis_id": "H_REVIEW",
                "category": proposed["category"],
                "subtype": proposed["subtype"],
                "claim": proposed["summary_en"],
                "claim_en": proposed["summary_en"],
                "claim_zh": proposed["summary_zh"],
                "confidence": original_cause.get("confidence", 0.0),
                "evidence_for": cited,
                "evidence_against": [],
            }
        ] + [
            h
            for h in result.get("competing_hypotheses") or []
            if (h.get("category"), h.get("subtype")) != (proposed["category"], proposed["subtype"])
        ]
        result["excluded_hypotheses"] = [
            h
            for h in result.get("excluded_hypotheses") or []
            if h.get("category") != proposed["category"]
        ]
        result["contributing_factors"] = [
            h
            for h in result.get("contributing_factors") or []
            if h.get("category") != proposed["category"]
        ]
    assess_static(evidence, result)
    a = result["causal_assessment"]
    a["decision"] = copy.deepcopy(review["decision"])
    a["alternatives"] = copy.deepcopy(review["alternatives"])
    fingerprints = review["binding"]["source_fingerprints"]
    cited_artifacts = cited_source_refs(evidence, review)
    unchecked = []
    for artifact, fingerprint in zip(evidence.get("artifacts") or [], fingerprints):
        aliases = {
            artifact.get("artifact_id"),
            artifact.get("rel_path"),
            f"{artifact.get('scope', 'trial')}:{artifact.get('rel_path')}",
        }
        if (
            aliases.intersection(cited_artifacts)
            and artifact.get("exists") is True
            and fingerprint["status"] != "checked"
        ):
            unchecked.append(fingerprint)
    a["review_limits"] = [
        f"Saved source freshness not checked: {x['path']} ({x['status']})"
        for x in fingerprints
        if x["status"] != "checked"
    ]
    if a["level"] != "not_applicable":
        a["level"] = (
            "evidence_supported_cause"
            if not unchecked and promotion_ready(evidence, review, category)
            else "candidate_cause"
        )
    a["review"] = {
        "path": "review.json",
        "digest": digest(review),
        "reviewer": review["reviewer"],
        "rationale": review["rationale"],
    }
    return result


def validate_bundle(root: Path, evidence: dict, analysis: dict) -> None:
    review = read(root / "review.json")
    original_path = root / "original-analysis.json"
    original = read(original_path) if original_path.exists() else analysis
    if review.get("proposed_attribution") and not original_path.exists():
        raise ValueError("Proposed attribution bundle requires original-analysis.json")
    expected = reviewed_analysis(evidence, original, review)
    if expected != analysis:
        raise ValueError("Reviewed analysis differs from its bound evidence review")
    if read(root / "evidence.json") != evidence or read(root / "analysis.json") != analysis:
        raise ValueError("Review bundle JSON snapshots differ")


def apply(evidence_path: Path, analysis_path: Path, review_path: Path, output: Path) -> dict:
    evidence, analysis, review = read(evidence_path), read(analysis_path), read(review_path)
    result = reviewed_analysis(evidence, analysis, review)
    # Review reports stay outside the original task/trial trees and source dirs.
    target = output.resolve()
    input_roots = [
        Path(evidence[k]).resolve() for k in ("job_dir", "trial_dir", "task_dir") if evidence.get(k)
    ]
    input_roots += [p.resolve().parent for p in (evidence_path, analysis_path, review_path)]
    input_roots += [
        Path(a["abs_path"]).resolve().parent
        for a in evidence.get("artifacts") or []
        if a.get("abs_path")
    ]
    if any(target == root or root in target.parents for root in input_roots):
        raise ValueError("Review output overlaps original input directories")
    if output.exists():
        raise ValueError("Review output must be a fresh directory")
    from render_report import render_report_markdown
    from validate_analysis import validate_all

    reports = {
        f"report.{lang}.md": render_report_markdown(evidence, result, lang=lang)
        for lang in ("zh", "en")
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cfa-review-", dir=output.parent))
    try:
        write(staging / "evidence.json", evidence)
        write(staging / "analysis.json", result)
        write(staging / "original-analysis.json", analysis)
        write(staging / "review.json", review)
        for name, content in reports.items():
            (staging / name).write_text(content, encoding="utf-8")
        for report in reports.values():
            ok, errors = validate_all(evidence, result, report, review_root=staging)
            if not ok:
                raise ValueError("Review failed base attribution validation: " + "; ".join(errors))
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return result


def capability(review_path: Path, output: Path) -> dict:
    """A separate, scoped prescription review over already reviewed independent tasks."""
    request = read(review_path)
    if request.get("schema_version") != "cfa-capability-review-v1":
        raise ValueError("Expected cfa-capability-review-v1")
    bundles = request.get("reviewed_bundles") or []
    if len(bundles) < 2:
        raise ValueError("Capability review requires at least two independent reviewed tasks")
    identities, sources, bindings = set(), set(), []
    for raw in bundles:
        root = Path(raw)
        ev, an = read(root / "evidence.json"), read(root / "analysis.json")
        reject_input_write(output, ev)
        validate_bundle(root, ev, an)
        from validate_analysis import validate_all

        ok, errors = validate_all(ev, an, review_root=root)
        if not ok:
            raise ValueError(
                "Capability input fails base attribution validation: " + "; ".join(errors)
            )
        if (
            an["causal_assessment"]["level"] != "evidence_supported_cause"
            or (an.get("primary_root_cause") or {}).get("category") != "agent"
        ):
            raise ValueError("Capability inputs must be evidence-supported agent-cause reviews")
        # Explicit task identity, not trial count, prevents repeated trials being independence.
        identity = str(ev.get("case_id") or "")
        source_hashes = sorted(
            {a["sha256"] for a in source_fingerprints(ev) if a.get("sha256")}
            | {a["sha256"] for a in ev.get("artifacts") or [] if a.get("sha256")}
        )
        if not source_hashes:
            raise ValueError("Capability review requires saved source content identities")
        source = digest(source_hashes)
        if not identity or identity in identities or source in sources:
            raise ValueError("Capability review requires distinct task and source identities")
        identities.add(identity)
        sources.add(source)
        bindings.append(
            {
                "bundle": str(root.resolve()),
                "case_id": identity,
                "evidence_digest": digest(ev),
                "analysis_digest": digest(an),
            }
        )
    reviews = request.get("generality_review") or {}
    required = (
        "shared_mechanism",
        "task_independence",
        "reusable",
        "nontrivial",
        "no_verifier_workaround",
        "no_answer_substitution",
        "scope_limits",
    )
    for key in required:
        item = reviews.get(key)
        if (
            not isinstance(item, dict)
            or not str(item.get("rationale") or "").strip()
            or not item.get("case_ids")
            or not set(item["case_ids"]).issubset(identities)
        ):
            raise ValueError(f"Generality review {key} requires rationale and reviewed case_ids")
    if any(
        set(reviews[key]["case_ids"]) != identities
        for key in ("shared_mechanism", "task_independence")
    ):
        raise ValueError("Mechanism and independence reviews must cover every reviewed task")
    prescription = request.get("skill_prescription")
    if (
        not isinstance(prescription, dict)
        or not str(prescription.get("guidance") or "").strip()
        or not str(request.get("reviewer") or "").strip()
    ):
        raise ValueError("Capability review requires reviewer and structured prescription guidance")
    result = {
        "schema_version": "cfa-capability-assessment-v1",
        "status": "supported_in_reviewed_cases",
        "scope": "reviewed independent tasks only; no universal model deficit established",
        "bindings": bindings,
        "review": request,
        "skill_prescription": prescription,
    }
    if output.exists():
        raise ValueError("Capability output must be a fresh file")
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, result)
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="cfa review", description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    p = subs.add_parser("plan")
    for name in ("evidence", "analysis", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p = subs.add_parser("apply")
    for name in ("evidence", "analysis", "review", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p = subs.add_parser("capability")
    for name in ("review", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            if args.output.exists():
                raise ValueError("Plan output must be a fresh file")
            evidence, analysis = read(args.evidence), read(args.analysis)
            reject_input_write(args.output, evidence)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write(args.output, plan(evidence, analysis))
            print(f"REVIEW_PLAN: {args.output.resolve()}")
        elif args.action == "apply":
            result = apply(args.evidence, args.analysis, args.review, args.output)
            print(f"REVIEW_DIR: {args.output.resolve()}")
            print(f"CONCLUSION_LEVEL: {result['causal_assessment']['level']}")
        else:
            result = capability(args.review, args.output)
            print(f"CAPABILITY_REVIEW: {args.output.resolve()}")
            print(f"STATUS: {result['status']}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
