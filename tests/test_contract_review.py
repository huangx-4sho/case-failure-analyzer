"""Portable source/field/failure binding tests; no verifier replay or execution."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from contract_review import audit_contract_review


class ContractReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / "task"
        self.trial = self.root / "trial"
        (self.task / "tests").mkdir(parents=True)
        (self.task / "environment/assets").mkdir(parents=True)
        (self.trial / "verifier").mkdir(parents=True)
        self.artifacts = []

    def audit(self, source, message, public="Write results.json."):
        (self.task / "instruction.md").write_text(public)
        (self.task / "tests/verify.py").write_text(source)
        (self.trial / "verifier/verification.json").write_text(
            json.dumps({"status": "FAIL", "message": message})
        )
        evidence = {
            "task_dir": str(self.task),
            "trial_dir": str(self.trial),
            "artifacts": self.artifacts,
            "runtime": {"reward": 0, "verdict": "failed"},
        }
        before = json.dumps(evidence, sort_keys=True)
        result = audit_contract_review(evidence)
        self.assertEqual(before, json.dumps(evidence, sort_keys=True))
        for item in result["review_items"]:
            self.assertFalse(item["automatic_attribution_eligible"])
            self.assertEqual("review_required", item["status"])
            self.assertTrue(item["verifier_source_evidence"])
            self.assertTrue(item["actual_submitted_representation"])
        return result

    def output(self, name, value):
        path = self.trial / "artifacts/app/outputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) if name.endswith(".json") else value)
        self.artifacts.append(
            {
                "abs_path": str(path),
                "rel_path": f"artifacts/app/outputs/{name}",
                "scope": "trial",
                "exists": True,
            }
        )
        return path

    def test_nested_group_samples_are_present_despite_direct_list_rejection(self):
        from report_style import render_contract_review

        self.output(
            "provenance.json",
            {
                "samples": {
                    "EAD|a|b": {
                        "source": "public table",
                        "errors": [{"mol": i, "err": 0.01} for i in range(1, 36)],
                    }
                }
            },
        )
        source = """
def check_items(items, where):
    if not isinstance(items, list) or not items:
        return [f"{where}: per-item samples missing"]
    return []
def verify(prov):
    samples = prov.get("samples")
    for key in ["EAD|a|b"]:
        problems = check_items(samples.get(key), f"samples[{key}]")
    return problems
"""
        public = 'provenance.json: samples keyed by group; identify the raw-data source for each group and include all 35 {"mol": number, "err": signed_error} entries.'
        result = self.audit(source, "samples[EAD|a|b]: per-item samples missing", public)
        item = next(
            i for i in result["review_items"] if i["subtype"] == "group_object_vs_direct_list"
        )
        self.assertEqual(item["expected_representation"]["type"], "list")
        actual = item["actual_submitted_representation"][0]
        self.assertEqual(actual["value_type"], "object")
        self.assertEqual(actual["nested_sample_lists"][0]["count"], 35)
        self.assertEqual(len(actual["value"]["errors"]), 35)
        self.assertTrue(item["public_requirement_evidence"])
        self.assertIn("remain unverified", item["uncertainty"])
        for lang, label in (("en", "Nested records"), ("zh", "嵌套样本数")):
            report = "\n".join(render_contract_review({"contract_review": result}, lang))
            self.assertIn(label, report)
            self.assertIn("35", report)
            self.assertIn("Public requirement" if lang == "en" else "公开要求", report)
        self.assertFalse(
            self.audit(source, "samples[other]: per-item samples missing", public)["review_items"]
        )
        self.assertFalse(
            self.audit(source, "samples[EAD|a|b]: numerical mismatch", public)["review_items"]
        )

    @staticmethod
    def alias_source(failure="not decisions or not decisions[0]"):
        return f"""
def validate(data):
    decisions = []
    for key in ("other_mapping", "other_rows"):
        if key in data:
            value = data[key]
            decisions.append(value.strip().lower() == "unresolved")
    if {failure}:
        raise ValueError("mapping must consistently be unresolved")
"""

    def test_literal_alias_bound_to_failure_preserves_prose(self):
        self.output(
            "summary.json",
            {
                "other_mapping": "other rows are not resolved from public data",
                "mapping_unresolved": True,
            },
        )
        result = self.audit(
            self.alias_source(),
            "mapping must consistently be unresolved",
            "Other row mapping may be expressed as a Boolean or text; aliases must agree.",
        )
        item = result["review_items"][0]
        self.assertEqual("literal_prose_alias", item["subtype"])
        self.assertIn("not resolved", item["actual_submitted_representation"][0]["value"])
        self.assertIn("not established", item["uncertainty"])

    def test_unrelated_failure_does_not_bind_alias(self):
        self.output(
            "summary.json", {"other_mapping": "other rows are not resolved from public data"}
        )
        result = self.audit(
            self.alias_source("bad_numbers"), "mapping must consistently be unresolved"
        )
        self.assertEqual([], result["review_items"])

    def test_failure_log_required(self):
        self.output(
            "summary.json", {"other_mapping": "other rows are not resolved from public data"}
        )
        result = self.audit(self.alias_source(), "an unrelated numeric convergence failure")
        self.assertEqual([], result["review_items"])

    def test_nested_shape_and_shared_public_contract(self):
        source = """
def validate(actual, expected):
    keys = ("radiative", "nonradiative")
    try:
        check([actual["yields"][k] for k in keys], [expected["yields"][k] for k in keys])
    except KeyError:
        raise ValueError("candidate: missing or invalid yield fields")
"""
        self.output("result.json", {"radiative": 0.4, "nonradiative": 0.6})
        result = self.audit(
            source,
            "candidate: missing or invalid yield fields",
            "Report radiative and nonradiative yields.",
        )
        self.assertEqual("undocumented_nesting", result["review_items"][0]["subtype"])
        contract = self.task / "environment/assets/output-contract.md"
        contract.write_text('```json\n{"yields": {"radiative": 0.4, "nonradiative": 0.6}}\n```')
        result = self.audit(
            source, "candidate: missing or invalid yield fields", "Follow assets/output-contract.md"
        )
        self.assertEqual([], result["review_items"])
        self.assertTrue(
            any(
                r["path"] == str(contract) and r["status"] == "read"
                for r in result["coverage"]["public_contract"]
            )
        )

    def test_enum_binds_specific_rejected_record(self):
        source = """
def validate(row):
    sid = row["id"]
    if row["availability"] == "retained":
        pass
    elif row["availability"] in {"metadata_only", "unavailable"}:
        pass
    else:
        fail(f"unknown source availability: {sid}")
"""
        self.output(
            "sources.json", {"sources": [{"id": "record-A", "availability": "retained_metadata"}]}
        )
        result = self.audit(
            source,
            "unknown source availability: record-A",
            "Each source has availability, title and URL.",
        )
        self.assertEqual("undocumented_enum", result["review_items"][0]["subtype"])
        self.assertEqual(
            "record-A",
            result["review_items"][0]["actual_submitted_representation"][0]["row_identifier"],
        )
        result = self.audit(
            source, "unknown source availability: absent-record", "Each source has availability."
        )
        self.assertEqual([], result["review_items"])
        result = self.audit(
            source,
            "unknown source availability: record-A",
            "availability must be retained, metadata_only, or unavailable.",
        )
        self.assertEqual([], result["review_items"])

    def test_numeric_norm_and_missing_deliverable_are_not_contract_findings(self):
        self.output("path.json", {"force": [0.01, 0.01, 0.01], "force_norm": 0.01})
        source = """
def validate(x):
    if math.sqrt(sum(v*v for v in x["force"])) > .015:
        fail("forward path stopped before force convergence")
    if not Path("diagnosis.md").exists():
        fail("diagnosis.md is required")
"""
        for message in (
            "forward path stopped before force convergence",
            "diagnosis.md is required",
        ):
            self.assertEqual([], self.audit(source, message)["review_items"])

    def test_per_sample_source_check_is_not_semantic_equivalence(self):
        self.output("summary.json", {"source_checked": True, "source_rows": ["first frame only"]})
        result = self.audit(
            """
def validate(rows):
    if any(row["source_force"] != row["computed_force"] for row in rows):
        fail("per-sample force source check mismatch")
""",
            "per-sample force source check mismatch",
            "Check every sample against its source force.",
        )
        self.assertEqual([], result["review_items"])

    def test_conditional_prose_parser_is_bound_but_not_proved_equivalent(self):
        self.output(
            "summary.json",
            {
                "applicability": [
                    "non-polar liquids",
                    "for a polar liquid static response would require an extension",
                ]
            },
        )
        source = r"""
def validate(data):
    applicability = data["applicability"]
    def contradicts(text):
        return bool(re.search(r"\bpolar\b", text)) and not re.search(r"\bnon\s*polar\b", text)
    contradiction = any(contradicts(x) for x in applicability)
    if contradiction:
        fail("equation or applicability structured answer inconsistent")
"""
        item = self.audit(source, "equation or applicability structured answer inconsistent")[
            "review_items"
        ][0]
        self.assertEqual("conditional_prose_token_parser", item["subtype"])
        self.assertIn("does not establish", item["uncertainty"])

    def test_full_overlap_definition_requires_review(self):
        self.output("summary.json", {"angular_overlap_category": "partial"})
        source = """
def overlap_category(width, candidate_width, paper_width):
    if abs(width - min(candidate_width, paper_width)) < 1e-6:
        return "full"
    return "partial"
def validate(got):
    expected = {"angular_overlap_category": overlap_category(12, 12, 20)}
    for key, value in expected.items():
        if got[key] != value:
            fail(f"conclusion {key} mismatch")
"""
        result = self.audit(
            source,
            "conclusion angular_overlap_category mismatch",
            "angular_overlap_category must be none, partial or full.",
        )
        self.assertEqual("undefined_overlap_full", result["review_items"][0]["subtype"])
        result = self.audit(
            source,
            "conclusion angular_overlap_category mismatch",
            "full means overlap equals the smaller window width.",
        )
        self.assertEqual([], result["review_items"])

    def test_spatial_center_definition_requires_review(self):
        self.output(
            "result.json", {"selection_profile": {"angle": {"spatial_spread_angstrom": 3.0}}}
        )
        source = """
def selection_profile(metrics, reference):
    centers = []
    for atoms in metrics:
        centers.append(tuple(sum(a[k] for a in atoms)/len(atoms) for k in (1,2,3)))
    return centers
def validate(result):
    expected = selection_profile(metrics, reference)
    if result["selection_profile"] != expected:
        return False, "selection_profile must reproduce the element/spatial sampling statistics"
"""
        result = self.audit(
            source,
            "selection_profile must reproduce the element/spatial sampling statistics",
            "spatial_spread_angstrom is the largest distance between family centers.",
        )
        self.assertEqual("undefined_spatial_center", result["review_items"][0]["subtype"])
        result = self.audit(
            source,
            "selection_profile must reproduce the element/spatial sampling statistics",
            "spatial_spread_angstrom uses each centroid of the participating atoms.",
        )
        self.assertEqual([], result["review_items"])

    def test_path_base_rejection_requires_matching_submitted_path(self):
        self.output("audit.csv", "orientation,slab_file\n001,inputs/slabs/001/a.xyz\n")
        source = """
def validate(rows):
    for row in rows:
        o = row["orientation"]
        fn = row["slab_file"]
        path = root/f"slabs/{o}/{fn}"
        if not path.is_file():
            fail(f"audit names unknown slab {o}/{fn}")
"""
        result = self.audit(
            source,
            "audit names unknown slab 001/inputs/slabs/001/a.xyz",
            "CSV columns: orientation,slab_file.",
        )
        self.assertEqual("undefined_relative_path_base", result["review_items"][0]["subtype"])
        result = self.audit(
            source,
            "audit names unknown slab 001/inputs/slabs/001/a.xyz",
            "slab_file is a basename only.",
        )
        self.assertEqual([], result["review_items"])

    def test_static_inspection_never_executes_source(self):
        marker = self.root / "executed"
        source = f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\nexec("print(123)")\n'
        result = self.audit(source, "some failure unrelated to source")
        self.assertFalse(marker.exists())
        self.assertEqual([], result["review_items"])
        self.assertEqual("static_ast_only", result["source_inspection"]["mode"])

    def test_captured_report_can_be_missing_from_replay_scope(self):
        report = self.output("diagnosis.md", "Scientific discussion exists.")
        self.output(
            "experiment.py",
            'from pathlib import Path\nout = Path(__file__).parent / "result.json"\nwith open(out, "w") as stream: stream.write("{}")\n',
        )
        source = """
def diagnosis_evidence(path):
    if not path.is_file():
        raise ValueError("diagnosis.md is required")
def validate(final):
    diagnosis_evidence(final / "diagnosis.md")
def _replay_experiment(script, app, root):
    scratch = Path(root) / "scratch"
    shutil.copy2(script, scratch / "final" / "experiment.py")
    proc = subprocess.run([sys.executable, "final/experiment.py"], cwd=scratch)
    return proc, scratch / "final"
def grade(app, final):
    validate(final)
    with tempfile.TemporaryDirectory() as td:
        proc, replay_final = _replay_experiment(script, app, td)
        validate(replay_final)
"""
        result = self.audit(
            source,
            "diagnosis.md is required",
            "Write diagnosis.md; experiment.py reproduces the analysis.",
        )
        self.assertEqual([], result["review_items"])
        finding = result["execution_scope_findings"][0]
        self.assertEqual("execution_scope", finding["category"])
        self.assertEqual(str(report), finding["captured_evidence"][0]["path"])
        self.assertFalse(finding["required_filename_seen_in_copy_literals"])
        self.assertFalse(finding["replay_entrypoints"][0]["required_filename_seen_as_write_target"])
        self.assertEqual(
            ["result.json"], finding["replay_entrypoints"][0]["static_write_targets"][0]["targets"]
        )
        self.assertIn("cannot prove", finding["uncertainty"])
        report.unlink()
        self.assertEqual(
            [], self.audit(source, "diagnosis.md is required")["execution_scope_findings"]
        )

    def test_ordinary_missing_report_has_no_replay_scope_finding(self):
        self.output("diagnosis.md", "Captured report.")
        result = self.audit(
            """
def validate(final):
    if not (final / "diagnosis.md").is_file():
        raise ValueError("diagnosis.md is required")
""",
            "diagnosis.md is required",
        )
        self.assertEqual([], result["execution_scope_findings"])

    def test_required_child_of_populated_object_is_secondary(self):
        self.output(
            "summary.json",
            {
                "method": {
                    "normalisation": "divide by formula units",
                    "curve": "PCHIP over the supplied range",
                }
            },
        )
        source = """
def validate(summary):
    if not isinstance(summary["method"], dict) or not summary["method"].get("description"):
        raise ValueError("summary.method needs a reproducible method description")
"""
        message = "summary.method needs a reproducible method description"
        result = self.audit(source, message, "summary.json must include method, units and results.")
        item = result["review_items"][0]
        self.assertEqual("undocumented_required_child", item["subtype"])
        self.assertEqual("method.description", item["field"])
        self.assertEqual("secondary_contract_review", item["causal_role"])
        self.assertIn("terminal runtime failure", item["uncertainty"])
        self.assertIn("normalisation", item["actual_submitted_representation"][0]["value"])
        for public in (
            "method must include a nonempty description child.",
            "Required field: method.description",
            '{"method": {"description": "reproducible procedure"}}',
        ):
            self.assertEqual([], self.audit(source, message, public)["review_items"])
        self.assertEqual(
            [], self.audit(source, "an unrelated scientific check failed")["review_items"]
        )

    def test_required_child_resolves_parent_variable_and_rejects_empty_parent(self):
        path = self.output("summary.json", {"procedure": {"algorithm": "bounded interpolation"}})
        source = """
def validate(summary):
    method = summary["procedure"]
    if not method.get("explanation"):
        raise ValueError("procedure needs a reproducible explanation")
"""
        message = "procedure needs a reproducible explanation"
        result = self.audit(source, message, "Report the procedure.")
        self.assertEqual("procedure.explanation", result["review_items"][0]["field"])
        for value in (
            {"procedure": {}},
            {},
            {"procedure": "text"},
            {"procedure": {"explanation": ""}},
        ):
            path.write_text(json.dumps(value))
            self.assertEqual([], self.audit(source, message)["review_items"])

    def test_public_per_sample_coverage_remains_separate_from_contract_defects(self):
        source = """
LABELS = ("A", "B", "C", "D")
def validate(summary):
    if set(summary["source_check"]) != set(LABELS):
        raise ValueError("source check must report all four sample labels")
"""
        quote = "Point-by-point comparison was not performed."
        self.output(
            "summary.json",
            {"source_check": {"status": quote, "counts": {"A": 10, "B": 10, "C": 10, "D": 10}}},
        )
        public = (
            "source_check must report results for all four sample labels. Labels are A, B, C, D."
        )
        result = self.audit(source, "source check must report all four sample labels", public)
        self.assertEqual([], result["review_items"])
        finding = result["requirement_coverage_findings"][0]
        self.assertEqual("requirement_coverage", finding["category"])
        self.assertFalse(finding["automatic_attribution_eligible"])
        self.assertEqual(["A", "B", "C", "D"], finding["required_labels"])
        actual = finding["actual_submitted_representation"][0]
        self.assertEqual(["A", "B", "C", "D"], actual["missing_direct_labels"])
        self.assertEqual(["$.source_check.counts"], actual["nested_label_occurrences"]["A"])
        self.assertEqual(quote, actual["submitted_text_quotes"][0]["quote"])
        self.assertTrue(
            all(finding["public_requirement_binding"]["required_labels_public"].values())
        )
        self.assertFalse(finding["public_requirement_binding"]["direct_key_schema_proven"])
        self.assertIn("not scientific incompleteness", finding["uncertainty"])
        for public in (
            "Include source_check.",
            "source_check may aggregate all sample labels A, B, C, D.",
        ):
            self.assertEqual(
                [],
                self.audit(source, "source check must report all four sample labels", public)[
                    "requirement_coverage_findings"
                ],
            )
        self.assertEqual(
            [],
            self.audit(source, "some unrelated numeric check failed", public)[
                "requirement_coverage_findings"
            ],
        )

    def test_coverage_with_unpublished_labels_stays_uncertain_and_complete_map_abstains(self):
        source = """
LABELS = ("rate_a", "rate_b")
def validate(summary):
    if set(summary["reference_checks"]) != set(LABELS):
        fail("reference checks must cover every condition")
"""
        path = self.output(
            "summary.json", {"reference_checks": {"aggregate": "source check overview"}}
        )
        result = self.audit(
            source,
            "reference checks must cover every condition",
            "reference_checks covers every condition.",
        )
        finding = result["requirement_coverage_findings"][0]
        self.assertFalse(
            any(finding["public_requirement_binding"]["required_labels_public"].values())
        )
        self.assertFalse(finding["automatic_attribution_eligible"])
        path.write_text(json.dumps({"reference_checks": {"rate_a": {}, "rate_b": {}}}))
        self.assertEqual(
            [],
            self.audit(
                source,
                "reference checks must cover every condition",
                "reference_checks covers every condition.",
            )["requirement_coverage_findings"],
        )

    def test_bounded_public_contract_is_reported(self):
        (self.task / "environment/assets/large.md").write_text("x" * 1_000_001)
        result = self.audit("pass", "failed")
        self.assertTrue(
            any(row["status"] == "budget_skipped" for row in result["coverage"]["public_contract"])
        )
        self.assertEqual([], result["review_items"])


if __name__ == "__main__":
    unittest.main()
