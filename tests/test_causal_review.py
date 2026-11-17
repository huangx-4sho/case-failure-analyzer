"""Evidence-only causal review, provenance and conservative promotion contracts."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from causal_assessment import assess_static, validate_assessment, primary_alternative
from review_cause import apply, capability, plan, read, reviewed_analysis, validate_bundle, write
from validate_analysis import collect_valid_evidence_ids


def fixture():
    ev = {
        "case_id": "task-a",
        "artifacts": [
            {
                "artifact_id": "art:input",
                "rel_path": "input.json",
                "exists": True,
                "abs_path": str(Path(__file__).resolve()),
            },
            {
                "artifact_id": "art:output",
                "rel_path": "output.json",
                "exists": True,
                "abs_path": str(Path(__file__).resolve()),
            },
        ],
        "timeline": [
            {"event_id": "exec:1", "event_type": "tool_call", "command": "saved command"},
            {"event_id": "thought:1", "event_type": "agent_reasoning"},
        ],
        "scientific_observations": [
            {
                "sci_id": "sci:1",
                "source_ref": "verifier/log:L2",
                "matched_text": "reported 2 != recomputed 3",
                "recomputed_value": 3,
            }
        ],
        "verifier_observations": [{"obs_id": "ver:1", "type": "failed_check"}],
        "contract_observations": [{"contract_id": "rule:1"}],
        "verifier_report": {
            "verification_coverage": "reference checks only",
            "coverage_limits": ["no force validation"],
            "failed_checks": [{"name": "metric", "ok": False}],
        },
    }
    an = {
        "case_id": "task-a",
        "verdict": "failed",
        "primary_root_cause": {"category": "agent", "summary": "Metric mismatch"},
        "first_unrecovered_deviation": {"event_ref": "exec:1"},
        "skill_prescription": {"draft": "check units"},
    }
    assess_static(ev, an)
    return ev, an


def complete_review(ev, an):
    review = plan(ev, an)
    review.update(
        reviewer="Scientist",
        rationale="Saved command, requirement and outcome agree on this trial mechanism.",
    )
    d = review["decision"]
    d["kind"] = "execution"
    d["rationale"] = (
        "The saved execution used the wrong effective input according to the applicable requirement."
    )
    for key, ref in (("execution_binding", "exec:1"), ("basis", "rule:1"), ("outcome", "ver:1")):
        d[key] = {"statement": "Specific reviewed evidence", "evidence_refs": [ref]}
    d["execution_binding"].update(
        effective_input_refs=["art:input"], effective_output_refs=["art:output"]
    )
    for alternative in review["alternatives"]:
        alternative.update(
            status="ruled_out",
            reason="Reviewed saved trace for this competing explanation.",
            evidence_refs=["exec:1", "ver:1"],
        )
    primary = primary_alternative(an["primary_root_cause"]["category"], d["kind"])
    for alternative in review["alternatives"]:
        if alternative["category"] == primary:
            alternative["status"] = "supports_candidate"
    return review


def nonagent_review(kind="verifier"):
    ev, an = fixture()
    an["primary_root_cause"] = {
        "category": "unknown",
        "subtype": "insufficient_evidence",
        "code": "UNKNOWN_INSUFFICIENT_EVIDENCE",
        "confidence": 0.0,
        "summary": "Cause unresolved",
    }
    ev["artifacts"].extend(
        [
            {
                "artifact_id": "art:rule",
                "rel_path": "tests/verify.py",
                "scope": "task",
                "exists": True,
                "abs_path": str(Path(__file__).resolve()),
            },
            {
                "artifact_id": "art:report",
                "rel_path": "verifier/verification.json",
                "exists": True,
                "abs_path": str(Path(__file__).resolve()),
            },
        ]
    )
    ev["verifier_observations"][0]["source_ref"] = "verification.json"
    ev["contract_observations"][0]["source_refs"] = ["task:tests/verify.py"]
    ev["error_observations"] = [
        {"error_id": "error:terminal", "causal_candidate": True, "source_ref": "verification.json"}
    ]
    review = complete_review(ev, an)
    category, subtype, code, event = {
        "verifier": (
            "verifier",
            "regex_or_parser_defect",
            "VERIFIER_REGEX_OR_PARSER_DEFECT",
            "ver:1",
        ),
        "case": ("case", "ambiguous_contract", "CASE_AMBIGUOUS_CONTRACT", "rule:1"),
        "infrastructure": (
            "infra",
            "container_runtime",
            "INFRA_CONTAINER_RUNTIME",
            "error:terminal",
        ),
    }[kind]
    review["proposed_attribution"] = {
        "category": category,
        "subtype": subtype,
        "code": code,
        "summary_en": "Saved rule/source and rejection support this mechanism for the reported check only.",
        "summary_zh": "保存的规则、源码与拒绝记录支持此机制，范围仅限该检查。",
    }
    review["decision"].update(
        kind=kind,
        event_ref=event,
        description="Review of the reported rejection, not whole-task success",
    )
    review["decision"]["execution_binding"].update(evidence_refs=[event, "art:rule", "art:report"])
    review["decision"]["basis"]["evidence_refs"] = ["rule:1", "art:rule"]
    for alternative in review["alternatives"]:
        alternative["status"] = (
            "supports_candidate" if alternative["category"] == category else "ruled_out"
        )
    return ev, an, review


class EvidenceOnlyReviewTests(unittest.TestCase):
    def test_nonagent_reviews_can_reclassify_unknown_without_changing_score_or_verdict(self):
        for kind in ("verifier", "case", "infrastructure"):
            ev, an, review = nonagent_review(kind)
            result = reviewed_analysis(ev, an, review)
            self.assertEqual(
                result["primary_root_cause"]["category"], review["proposed_attribution"]["category"]
            )
            self.assertEqual(result["primary_root_cause"]["confidence"], 0.0)
            self.assertEqual(
                result["primary_root_cause"]["confidence_kind"], "original_rule_evidence_score"
            )
            self.assertEqual(result["causal_assessment"]["level"], "evidence_supported_cause")
            self.assertEqual(result["verdict"], an["verdict"])
            self.assertEqual(
                result["competing_hypotheses"][0]["evidence_for"], result["evidence_refs"]
            )
            self.assertEqual(an["primary_root_cause"]["category"], "unknown")

    def test_nonagent_proposals_reject_codes_kinds_and_invented_or_unbound_sources(self):
        for change, message in (
            (
                lambda r: r["proposed_attribution"].update(code="CASE_AMBIGUOUS_CONTRACT"),
                "category/subtype/code",
            ),
            (lambda r: r["proposed_attribution"].update(confidence=1.0), "unsupported fields"),
            (lambda r: r["decision"].update(kind="case"), "conflicts"),
            (lambda r: r["decision"].update(event_ref="thought:1"), "actual execution"),
            (
                lambda r: r["decision"]["basis"].update(evidence_refs=["invented-rule"]),
                "unresolved evidence",
            ),
            (
                lambda r: r["decision"]["execution_binding"].update(evidence_refs=["ver:1"]),
                "saved source artifact",
            ),
            (
                lambda r: r["decision"]["execution_binding"].update(effective_input_refs=[]),
                "input and output",
            ),
            (
                lambda r: r["decision"]["outcome"].update(evidence_refs=["art:output"]),
                "terminal rejection",
            ),
        ):
            ev, an, review = nonagent_review()
            change(review)
            with self.assertRaisesRegex(ValueError, message):
                reviewed_analysis(ev, an, review)

    def test_proposed_review_bundle_retains_original_and_rejects_stale_or_missing_original(self):
        ev, an, review = nonagent_review()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / "inputs"
            inputs.mkdir()
            for name, value in (("evidence", ev), ("analysis", an), ("review", review)):
                write(inputs / (name + ".json"), value)
            with (
                patch("render_report.render_report_markdown", return_value="report"),
                patch("validate_analysis.validate_all", return_value=(True, [])),
            ):
                result = apply(
                    inputs / "evidence.json",
                    inputs / "analysis.json",
                    inputs / "review.json",
                    root / "reviewed",
                )
            bundle = root / "reviewed"
            self.assertEqual(read(bundle / "original-analysis.json"), an)
            validate_bundle(bundle, ev, result)
            stale = copy.deepcopy(an)
            stale["primary_root_cause"]["summary"] = "tampered original"
            write(bundle / "original-analysis.json", stale)
            with self.assertRaisesRegex(ValueError, "binding|original"):
                validate_bundle(bundle, ev, result)
            (bundle / "original-analysis.json").unlink()
            with self.assertRaisesRegex(ValueError, "requires original-analysis"):
                validate_bundle(bundle, ev, result)

    def test_legacy_review_bundle_without_original_snapshot_remains_readable(self):
        ev, an = fixture()
        review = complete_review(ev, an)
        review["binding"].pop("original_analysis_digest")
        result = reviewed_analysis(ev, an, review)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, value in (("evidence", ev), ("analysis", result), ("review", review)):
                write(root / (name + ".json"), value)
            validate_bundle(root, ev, result)

    def test_trajectory_source_alias_binds_nonstandard_saved_filename(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "rollout.jsonl"
            source.write_text("changed saved trajectory")
            ev, an = fixture()
            ev["artifacts"].append(
                {
                    "artifact_id": "art:trajectory_json",
                    "rel_path": "agent/rollout.jsonl",
                    "exists": True,
                    "abs_path": str(source),
                    "sha256": "sha256:" + hashlib.sha256(b"original saved trajectory").hexdigest(),
                }
            )
            review = complete_review(ev, an)
            self.assertEqual(
                reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
            )
            ev["artifacts"][-1].pop("sha256")
            review = complete_review(ev, an)
            source.write_text("changed after planning")
            with self.assertRaisesRegex(ValueError, "source artifacts"):
                reviewed_analysis(ev, an, review)

    def test_stale_alternative_sources_prevent_promotion_and_changed_sources_are_rejected(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "alternative.log"
            source.write_text("changed since collection")
            ev, an = fixture()
            ev["artifacts"].append(
                {
                    "artifact_id": "art:alternative",
                    "rel_path": "alternative.log",
                    "exists": True,
                    "abs_path": str(source),
                    "sha256": "sha256:" + hashlib.sha256(b"original").hexdigest(),
                }
            )
            ev["verifier_observations"].append(
                {
                    "obs_id": "ver:alternative",
                    "type": "failed_check",
                    "source_ref": "alternative.log",
                }
            )
            for ref in ("art:alternative", "ver:alternative"):
                review = complete_review(ev, an)
                review["alternatives"][0]["evidence_refs"] = [ref]
                self.assertEqual(
                    reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
                    "candidate_cause",
                )
            ev["artifacts"][-1].pop("sha256")
            review = complete_review(ev, an)
            review["alternatives"][0]["evidence_refs"] = ["ver:alternative"]
            self.assertEqual(
                reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
                "evidence_supported_cause",
            )
            source.write_text("changed after review")
            with self.assertRaisesRegex(ValueError, "source artifacts"):
                reviewed_analysis(ev, an, review)

    def test_source_changed_before_plan_is_not_fresh_evidence(self):
        import hashlib

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "input.json"
            path.write_text("original input")
            original_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            ev, an = fixture()
            ev["artifacts"][0].update(abs_path=str(path), sha256=original_hash)
            path.write_text("changed input")
            review = complete_review(ev, an)
            self.assertEqual(
                review["binding"]["source_fingerprints"][0]["status"], "changed_since_collection"
            )
            result = reviewed_analysis(ev, an, review)
            self.assertEqual(result["causal_assessment"]["level"], "candidate_cause")
            self.assertTrue(result["causal_assessment"]["review_limits"])

    def test_static_fail_and_recompute_do_not_promote_or_confirm_capability(self):
        ev, an = fixture()
        a = an["causal_assessment"]
        self.assertEqual(a["level"], "candidate_cause")
        self.assertIsNone(an["skill_prescription"])
        self.assertEqual(a["capability"]["status"], "suspected")
        self.assertEqual(a["existing_verification"]["independence"], "not_established")
        self.assertEqual(
            a["existing_verification"]["numeric_records"][0]["kind"],
            "verifier_reported_recomputation",
        )
        self.assertIn("no force validation", a["existing_verification"]["coverage_limits"])
        self.assertFalse(validate_assessment(ev, an, collect_valid_evidence_ids(ev)))
        ev["scientific_observations"][0]["matched_text"] = "reported 2 differs from reference 3"
        assess_static(ev, an)
        self.assertEqual(
            an["causal_assessment"]["existing_verification"]["numeric_records"][0]["kind"],
            "reference_comparison_or_unspecified",
        )

    def test_generic_unknown_failure_remains_observed(self):
        ev, an = fixture()
        an["primary_root_cause"] = {"category": "unknown"}
        assess_static(ev, an)
        self.assertEqual(an["causal_assessment"]["level"], "observed_failure")

    def test_unresolved_review_stays_candidate(self):
        ev, an = fixture()
        review = complete_review(ev, an)
        review["alternatives"][0].update(status="unresolved", evidence_refs=[])
        result = reviewed_analysis(ev, an, review)
        self.assertEqual(result["causal_assessment"]["level"], "candidate_cause")

    def test_rationale_flags_missing_refs_and_reasoning_cannot_promote(self):
        ev, an = fixture()
        review = complete_review(ev, an)
        review["decision"]["event_ref"] = "thought:1"
        with self.assertRaisesRegex(ValueError, "actual execution"):
            reviewed_analysis(ev, an, review)
        review = complete_review(ev, an)
        review["decision"]["basis"]["evidence_refs"] = ["nonexistent"]
        with self.assertRaisesRegex(ValueError, "unresolved evidence"):
            reviewed_analysis(ev, an, review)
        review = complete_review(ev, an)
        review["rationale"] = ""
        review["confirmed"] = True
        with self.assertRaisesRegex(ValueError, "written rationale"):
            reviewed_analysis(ev, an, review)

    def test_uncited_shared_job_log_does_not_invalidate_bound_review(self):
        ev, an = fixture()
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "job.log"
            log.write_text("other trial started")
            ev["artifacts"].append(
                {"artifact_id": "art:job_log", "abs_path": str(log), "exists": True}
            )
            review = complete_review(ev, an)
            expected = reviewed_analysis(ev, an, review)
            log.write_text("other trial finished; another one started")
            self.assertEqual(reviewed_analysis(ev, an, review), expected)

    def test_stale_json_and_changed_sources_rejected(self):
        ev, an = fixture()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "saved.log"
            source.write_text("original")
            ev["artifacts"].append({"artifact_id": "art:1", "abs_path": str(source)})
            review = complete_review(ev, an)
            review["decision"]["execution_binding"]["evidence_refs"].append("art:1")
            changed = copy.deepcopy(ev)
            changed["timeline"][0]["command"] = "tampered"
            with self.assertRaisesRegex(ValueError, "binding"):
                reviewed_analysis(changed, an, review)
            source.write_text("modified")
            with self.assertRaisesRegex(ValueError, "source artifacts"):
                reviewed_analysis(ev, an, review)

    def test_apply_writes_new_bundle_without_any_execution_and_detects_tamper(self):
        ev, an = fixture()
        review = complete_review(ev, an)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "inputs"
            root.mkdir()
            for name, data in (("evidence", ev), ("analysis", an), ("review", review)):
                write(root / (name + ".json"), data)
            with (
                patch("subprocess.run", side_effect=AssertionError("No execution allowed")),
                patch("subprocess.Popen", side_effect=AssertionError("No execution allowed")),
                patch("render_report.render_report_markdown", return_value="Reviewed report"),
                patch("validate_analysis.validate_all", return_value=(True, [])),
            ):
                result = apply(
                    root / "evidence.json",
                    root / "analysis.json",
                    root / "review.json",
                    root.parent / "reviewed",
                )
            self.assertEqual(result["causal_assessment"]["level"], "evidence_supported_cause")
            self.assertEqual(read(root / "analysis.json"), an)
            validate_bundle(root.parent / "reviewed", ev, result)
            self.assertFalse(
                validate_assessment(
                    ev, result, collect_valid_evidence_ids(ev), root.parent / "reviewed"
                )
            )
            result["causal_assessment"]["decision"]["description"] = "altered"
            with self.assertRaisesRegex(ValueError, "differs"):
                validate_bundle(root.parent / "reviewed", ev, result)

    def test_effective_artifacts_and_complete_absence_coverage_required(self):
        ev, an = fixture()
        review = complete_review(ev, an)
        review["decision"]["execution_binding"]["effective_output_refs"] = []
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        review = complete_review(ev, an)
        review["decision"]["inference_kind"] = "missing_artifact"
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )

    def test_cross_case_capability_requires_explicit_review_and_independent_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundles = []
            for number in (1, 2):
                ev, an = fixture()
                ev["case_id"] = an["case_id"] = f"task-{number}"
                source = root / f"source-{number}.log"
                source.write_text(f"Saved independent task input {number}")
                ev["artifacts"][0]["abs_path"] = str(source)
                review = complete_review(ev, an)
                result = reviewed_analysis(ev, an, review)
                bundle = root / f"bundle-{number}"
                bundle.mkdir()
                for name, value in (
                    ("evidence", ev),
                    ("analysis", result),
                    ("original-analysis", an),
                    ("review", review),
                ):
                    write(bundle / (name + ".json"), value)
                bundles.append(str(bundle))
            request = {
                "schema_version": "cfa-capability-review-v1",
                "reviewed_bundles": bundles,
                "reviewer": "Scientist",
                "skill_prescription": {
                    "guidance": "Review and retain effective computational inputs"
                },
                "generality_review": {
                    key: {
                        "rationale": "Reviewed the specific shared mechanism and limits in both tasks",
                        "case_ids": ["task-1", "task-2"],
                    }
                    for key in (
                        "shared_mechanism",
                        "task_independence",
                        "reusable",
                        "nontrivial",
                        "no_verifier_workaround",
                        "no_answer_substitution",
                        "scope_limits",
                    )
                },
            }
            path = root / "request.json"
            write(path, request)
            with patch("validate_analysis.validate_all", return_value=(True, [])):
                result = capability(path, root / "capability.json")
            self.assertEqual(result["status"], "supported_in_reviewed_cases")
            request["generality_review"]["no_answer_substitution"] = {"confirmed": True}
            write(path, request)
            with (
                patch("validate_analysis.validate_all", return_value=(True, [])),
                self.assertRaisesRegex(ValueError, "rationale"),
            ):
                capability(path, root / "rejected.json")

    def test_missing_outputs_scientific_basis_and_recovered_warning_do_not_promote(self):
        ev, an = fixture()
        ev["artifacts"][1]["exists"] = False
        ev["deliverable_audit"] = {"coverage": {"scan_complete": True}}
        ev["trajectory_normalization"] = {"coverage": "complete"}
        review = complete_review(ev, an)
        review["decision"]["description"] = "交付文件缺失"
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        ev["artifacts"][1]["exists"] = True
        review = complete_review(ev, an)
        review["decision"]["kind"] = "scientific"
        next(a for a in review["alternatives"] if a["category"] == "agent_implementation")[
            "status"
        ] = "ruled_out"
        review["decision"]["basis"]["evidence_refs"] = ["ver:1"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        ev["scientific_observations"][0].update(
            recovered=True,
            error_family="verifier_recompute_divergence",
            agent_artifact_ref="art:output",
        )
        review = complete_review(ev, an)
        review["decision"]["outcome"]["evidence_refs"] = ["sci:1"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        ev["artifacts"].append(
            {"artifact_id": "art:result", "rel_path": "result.json", "exists": False}
        )
        review = complete_review(ev, an)
        review["decision"]["outcome"]["evidence_refs"] = ["art:result"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )

    def test_primary_category_can_be_supported_without_being_excluded(self):
        ev, an = fixture()
        an["primary_root_cause"]["category"] = "infra"
        review = complete_review(ev, an)
        next(a for a in review["alternatives"] if a["category"] == "infra")["status"] = (
            "supports_candidate"
        )
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
            "evidence_supported_cause",
        )
        next(a for a in review["alternatives"] if a["category"] == "case")["status"] = (
            "supports_candidate"
        )
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )

    def test_oversize_sources_are_bounded_and_prevent_promotion(self):
        ev, an = fixture()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "large.saved"
            with source.open("wb") as handle:
                handle.truncate(2 * 1024 * 1024)
            ev["artifacts"][0]["abs_path"] = str(source)
            review = complete_review(ev, an)
            self.assertEqual(review["binding"]["source_fingerprints"][0]["status"], "size_limit")
            result = reviewed_analysis(ev, an, review)
            self.assertEqual(result["causal_assessment"]["level"], "candidate_cause")
            self.assertTrue(result["causal_assessment"]["review_limits"])

    def test_apply_rejects_base_validation_and_input_overlap(self):
        ev, an = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "inputs"
            root.mkdir()
            review = complete_review(ev, an)
            for name, value in (("evidence", ev), ("analysis", an), ("review", review)):
                write(root / (name + ".json"), value)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                apply(
                    root / "evidence.json",
                    root / "analysis.json",
                    root / "review.json",
                    root / "reviewed",
                )
            with (
                patch("render_report.render_report_markdown", return_value="report"),
                self.assertRaisesRegex(ValueError, "base attribution"),
            ):
                apply(
                    root / "evidence.json",
                    root / "analysis.json",
                    root / "review.json",
                    root.parent / "reviewed",
                )
            self.assertFalse((root.parent / "reviewed").exists())

    def test_substring_requirement_and_requirement_only_cannot_establish_missing_output(self):
        ev, an = fixture()
        ev["artifacts"][1]["exists"] = False
        ev["deliverable_audit"] = {"coverage": {"scan_complete": True}}
        ev["trajectory_normalization"] = {"coverage": "complete"}
        contract = ev["contract_observations"][0]
        contract.update(
            item="file:output.json.backup",
            prompt_requirement="required",
            agent_output_status="missing_at_verify",
            details="Required output.json.backup must contain valid JSON",
        )
        review = complete_review(ev, an)
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        contract.update(item="file:output.json", agent_output_status="present")
        review = complete_review(ev, an)
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        contract["agent_output_status"] = "missing_at_verify"
        review = complete_review(ev, an)
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
            "evidence_supported_cause",
        )

    def test_excluded_primary_explanation_is_rejected_and_science_keeps_implementation_distinct(
        self,
    ):
        ev, an = fixture()
        review = complete_review(ev, an)
        primary = next(a for a in review["alternatives"] if a["category"] == "agent_implementation")
        for status in ("ruled_out", "not_applicable"):
            primary["status"] = status
            with self.assertRaisesRegex(ValueError, "contradicts"):
                reviewed_analysis(ev, an, review)
        review = complete_review(ev, an)
        review["decision"]["kind"] = "scientific"
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        next(a for a in review["alternatives"] if a["category"] == "agent_implementation")[
            "status"
        ] = "ruled_out"
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
            "evidence_supported_cause",
        )

    def test_collection_declaration_needs_public_applicability_and_cited_missing_outcome(self):
        ev, an = fixture()
        ev["artifacts"][1].update(exists=False, rel_path="artifacts/app/output.json")
        ev["artifacts"].append(
            {
                "artifact_id": "art:public",
                "rel_path": "instruction.md",
                "scope": "task",
                "exists": True,
                "abs_path": str(Path(__file__).resolve()),
            }
        )
        ev["deliverable_audit"] = {"coverage": {"scan_complete": True}}
        ev["trajectory_normalization"] = {"coverage": "complete"}
        ev["contract_observations"][0].update(
            item="file:artifacts/app/output.json",
            prompt_requirement="declared_collection_path",
            agent_output_status="missing_at_verify",
        )
        ev["verifier_observations"].append(
            {
                "obs_id": "ver:missing",
                "type": "missing_deliverable",
                "path": "artifacts/app/output.json",
            }
        )
        review = complete_review(ev, an)
        review["decision"]["outcome"]["evidence_refs"] = ["ver:missing"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        review["decision"]["basis"]["evidence_refs"].append("art:public")
        review["decision"]["basis"]["statement"] = (
            "The public instructions require the submitted result; the collection path is its saved location."
        )
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"],
            "evidence_supported_cause",
        )
        review["decision"]["outcome"]["evidence_refs"] = ["ver:1"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )
        review["decision"]["outcome"]["evidence_refs"] = ["ver:missing"]
        ev["artifacts"][-1]["rel_path"] = "task.toml"
        review["binding"] = plan(ev, an)["binding"]
        self.assertEqual(
            reviewed_analysis(ev, an, review)["causal_assessment"]["level"], "candidate_cause"
        )

    def test_single_case_cannot_confirm_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = root / "request.json"
            write(
                request,
                {
                    "schema_version": "cfa-capability-review-v1",
                    "reviewed_bundles": ["one-case"],
                    "confirmed": True,
                },
            )
            with self.assertRaisesRegex(ValueError, "at least two"):
                capability(request, root / "capability.json")


if __name__ == "__main__":
    unittest.main()
