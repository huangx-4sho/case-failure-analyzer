"""Evidence requirements for trial-scoped Agent capability diagnoses."""

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_causal_review import complete_review, fixture, nonagent_review

from causal_assessment import assess_static, validate_assessment
from review_cause import read, reviewed_analysis, validate_bundle, write
from runtime_state import SCHEMA_VERSION
from validate_analysis import collect_valid_evidence_ids

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION = {
    "dimension": "observable_definition",
    "rationale_zh": "执行时采用了错误的指标定义，导致结果未通过该项检查。",
    "rationale_en": "The applied metric definition caused the recorded check to fail.",
    "evidence_refs": ["exec:1", "rule:1", "ver:1"],
}


def diagnosis(analysis):
    return analysis["causal_assessment"]["capability"]["diagnosis"]


class ReviewedCapabilityDiagnosisTests(unittest.TestCase):
    def test_cli_applies_bound_annotation_and_writes_localized_reports(self):
        evidence, analysis = fixture()
        evidence.update(
            runtime={"agent_started": True, "verifier_started": True, "verdict": "failed"},
            error_observations=[],
            missing_artifacts=[],
        )
        evidence["contract_observations"][0]["alignment"] = "agent_mismatch"
        analysis["first_unrecovered_deviation"]["summary"] = "Applied the wrong metric."
        analysis["primary_root_cause"].update(
            subtype="instruction_misinterpretation", code="AGENT_TASK_UNDERSTANDING", confidence=0.8
        )
        analysis.update(
            schema_version=SCHEMA_VERSION,
            failure_stage="agent_execution",
            detection_stage="verifier_execution",
            failure_manifestation={"summary": "Metric mismatch"},
            contributing_factors=[],
            competing_hypotheses=[{"category": "agent", "evidence_for": ["rule:1"]}],
            evidence_refs=["rule:1"],
            excluded_hypotheses=[],
            recommended_actions=[],
        )
        review = complete_review(evidence, analysis)
        review["capability_dimension"] = copy.deepcopy(ANNOTATION)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / "inputs"
            inputs.mkdir()
            output = root / "reviewed"
            for name, data in (("evidence", evidence), ("analysis", analysis), ("review", review)):
                write(inputs / f"{name}.json", data)
            run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "cfa.py"),
                    "review",
                    "apply",
                    "--evidence",
                    str(inputs / "evidence.json"),
                    "--analysis",
                    str(inputs / "analysis.json"),
                    "--review",
                    str(inputs / "review.json"),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            result = read(output / "analysis.json")
            self.assertEqual(
                diagnosis(result),
                {**ANNOTATION, "status": "supported_in_trial", "event_ref": "exec:1"},
            )
            self.assertEqual(result["primary_root_cause"], analysis["primary_root_cause"])
            self.assertIsNone(result["skill_prescription"])
            for language, label in (
                ("zh", "科学问题与物理量定义"),
                ("en", "Scientific question and observable definition"),
            ):
                report = (output / f"report.{language}.md").read_text(encoding="utf-8")
                self.assertIn(label, report)
                self.assertIn(ANNOTATION[f"rationale_{language}"], report)
                self.assertIn("`exec:1`", report)
                self.assertIn("`rule:1`", report)
                self.assertIn("`ver:1`", report)
            validate_bundle(output, evidence, result)
            diagnosis(result)["dimension"] = "numerical_adequacy"
            write(output / "analysis.json", result)
            with self.assertRaisesRegex(ValueError, "differs"):
                validate_bundle(output, evidence, result)

    def test_annotation_requires_known_dimension_and_bound_decision_refs(self):
        for change, error in (
            ({"dimension": "general_incompetence"}, "Unknown capability dimension"),
            ({"rationale_en": " "}, "rationale"),
            ({"evidence_refs": ["rule:1", "ver:1"]}, "execution decision"),
            ({"evidence_refs": ["exec:1", "ver:1"]}, "basis and outcome"),
            ({"evidence_refs": ["exec:1", "rule:1"]}, "basis and outcome"),
            ({"evidence_refs": ["exec:1", "rule:1", "ver:1", "sci:1"]}, "same decision"),
        ):
            with self.subTest(change=change):
                evidence, analysis = fixture()
                review = complete_review(evidence, analysis)
                review["capability_dimension"] = {**ANNOTATION, **change}
                with self.assertRaisesRegex(ValueError, error):
                    reviewed_analysis(evidence, analysis, review)

    def test_nonagent_review_cannot_attach_agent_diagnosis(self):
        evidence, analysis, review = nonagent_review()
        review["capability_dimension"] = copy.deepcopy(ANNOTATION)
        with self.assertRaisesRegex(ValueError, "failed agent trial"):
            reviewed_analysis(evidence, analysis, review)

    def test_unresolved_cause_leaves_dimension_candidate(self):
        evidence, analysis = fixture()
        review = complete_review(evidence, analysis)
        review["alternatives"][0].update(status="unresolved", evidence_refs=[])
        review["capability_dimension"] = copy.deepcopy(ANNOTATION)
        result = reviewed_analysis(evidence, analysis, review)
        self.assertEqual(result["causal_assessment"]["level"], "candidate_cause")
        self.assertEqual(diagnosis(result)["status"], "candidate")

    def test_review_can_record_explicit_insufficient_evidence(self):
        evidence, analysis = fixture()
        review = complete_review(evidence, analysis)
        review["capability_dimension"] = {
            "status": "insufficient_evidence",
            "rationale_zh": "保留的证据无法区分输入定义与实现。",
            "rationale_en": "The saved evidence cannot distinguish the input definition from its implementation.",
        }
        result = diagnosis(reviewed_analysis(evidence, analysis, review))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["dimension"])
        self.assertEqual(result["rationale_en"], review["capability_dimension"]["rationale_en"])

    def test_bundle_without_optional_diagnosis_replays(self):
        evidence, analysis = fixture()
        analysis["causal_assessment"]["capability"].pop("diagnosis")
        review = complete_review(evidence, analysis)
        review.pop("capability_dimension")
        review["binding"].pop("original_analysis_digest")
        result = reviewed_analysis(evidence, analysis, review)
        self.assertNotIn("diagnosis", result["causal_assessment"]["capability"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, data in (("evidence", evidence), ("analysis", result), ("review", review)):
                write(root / f"{name}.json", data)
            validate_bundle(root, evidence, result)


class StaticCapabilityDiagnosisTests(unittest.TestCase):
    def test_workflow_candidate_requires_bound_remote_delivery_under_agent(self):
        evidence, analysis = fixture()
        analysis["primary_root_cause"]["code"] = "AGENT_PREMATURE_TERMINATION"
        analysis["evidence_refs"] = ["sig:stop"]
        signal = {
            "signal_id": "sig:stop",
            "event_ref": "exec:1",
            "signal_type": "premature_completion",
        }
        evidence["behavioral_signals"] = [signal]
        assess_static(evidence, analysis)
        self.assertIsNone(diagnosis(analysis)["dimension"])
        signal["confirmed_unfinished_remote_delivery"] = True
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["dimension"], "workflow_state")
        analysis["primary_root_cause"]["category"] = "infra"
        assess_static(evidence, analysis)
        self.assertNotIn("diagnosis", analysis["causal_assessment"]["capability"])

    def test_force_definition_requires_public_bound_evidence_and_failed_check(self):
        evidence, analysis = fixture()
        analysis["primary_root_cause"]["code"] = "AGENT_TASK_UNDERSTANDING"
        analysis["evidence_refs"] = ["rule:1"]
        evidence["contract_observations"][0].update(
            item="stopping_force_definition", source_refs=["exec:1"]
        )
        finding = {
            "code": "AGENT_FORCE_STOP_METRIC_MISMATCH",
            "observed_code": {"event_ref": "exec:1"},
            "public_requirement": [{"source_ref": "art:input"}],
            "saved_failure": {"source_ref": "ver:1"},
            "application_binding": "bound",
        }
        evidence["scientific_contract_review"] = {"findings": [finding]}
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["dimension"], "observable_definition")
        self.assertEqual(diagnosis(analysis)["status"], "candidate")
        self.assertEqual(diagnosis(analysis)["evidence_refs"], ["exec:1", "rule:1"])
        diagnosis(analysis)["status"] = "supported_in_trial"
        errors = validate_assessment(evidence, analysis, collect_valid_evidence_ids(evidence))
        self.assertTrue(
            any("differs from the saved decision evidence" in error for error in errors)
        )
        for key, missing in (
            ("application_binding", "unbound"),
            ("public_requirement", []),
            ("saved_failure", None),
            ("observed_code", {"event_ref": "thought:1"}),
        ):
            with self.subTest(missing=key):
                evidence["scientific_contract_review"]["findings"] = [{**finding, key: missing}]
                assess_static(evidence, analysis)
                self.assertEqual(diagnosis(analysis)["status"], "needs_review")
                self.assertIsNone(diagnosis(analysis)["dimension"])

    def test_retry_requires_same_unrecovered_failure_in_the_recorded_observation(self):
        evidence, analysis = fixture()
        analysis["primary_root_cause"]["code"] = "AGENT_ERROR_DIAGNOSIS"
        analysis["evidence_refs"] = ["sig:retry", "sci:1"]
        error = "SCF convergence failed"
        first = {"event_id": "exec:1", "event_type": "tool_call", "command": "pw.x -in scf.in"}
        retry = {**first, "event_id": "exec:2", "observation": error}
        evidence["timeline"] = [{**first, "observation": error}, retry]
        evidence["behavioral_signals"] = [
            {
                "signal_id": "sig:retry",
                "event_ref": "exec:2",
                "signal_type": "repeated_failed_action",
            }
        ]
        observation = {
            "sci_id": "sci:1",
            "source_ref": "exec:1",
            "recovered": False,
            "matched_text": error,
        }
        evidence["scientific_observations"] = [observation]
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["status"], "candidate")
        self.assertEqual(diagnosis(analysis)["dimension"], "execution_recovery")
        self.assertEqual(diagnosis(analysis)["event_ref"], "exec:2")

        evidence["timeline"][0]["command"] = "cp2k input.inp"
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["status"], "needs_review")
        evidence["timeline"][0]["command"] = first["command"]

        evidence["scientific_observations"] = [{**observation, "recovered": True}]
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["status"], "needs_review")

        evidence["scientific_observations"] = [observation]
        retry["observation"] = "A later tool result obscured this retry's error"
        assess_static(evidence, analysis)
        self.assertEqual(diagnosis(analysis)["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
