"""Saved Harbor evidence reaches missing-output and review surfaces without replay."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_case import analyze_single_trial, collect_case_evidence
from cfa import _format_artifacts_summary
from discover_artifacts import discover_all
from deliverable_audit import extract_verified_missing_outputs
from causal_assessment import existing_verification
from review_cause import apply, plan, write
from validate_analysis import collect_positive_agent_evidence_ids, validate_all


class ExistingEvidenceIntegration(unittest.TestCase):
    def build(self, root: Path, *, present: bool = False, partial: bool = False):
        task, job = root / "tasks/task208", root / "jobs/job1"
        trial = job / "task208__saved"
        (task / "tests").mkdir(parents=True)
        (trial / "agent").mkdir(parents=True)
        (trial / "verifier").mkdir()
        (trial / "artifacts/app").mkdir(parents=True)
        (task / "task.toml").write_text('artifacts = ["/app/output.json"]\n')
        (task / "instruction.md").write_text("Required output: `/app/output.json`.\n")
        marker = root / "verifier-ran"
        (task / "tests/verify.py").write_text(
            f'from pathlib import Path\nPath({str(marker)!r}).write_text("should not execute")\n'
        )
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": "task208",
                    "trial_name": trial.name,
                    "agent_result": {"n_input_tokens": 1},
                    "verifier_result": {"rewards": {"reward": 0.0}},
                }
            )
        )
        records = [
            {"type": "session_meta", "payload": {"id": "example"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "x",
                    "name": "exec_command",
                    "arguments": '{"cmd":"python solve.py"}',
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "x",
                    "output": "Process exited with code 0\nfinished",
                },
            },
        ]
        text = "\n".join(json.dumps(r) for r in records)
        if partial:
            text += '\n{"type":'
        (trial / "agent/rollout-example.jsonl").write_text(text)
        (trial / "verifier/science.json").write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "verification_coverage": "artifact_presence",
                    "coverage_limits": ["No scientific recomputation for this check"],
                    "checks": {
                        "output_exists": {
                            "status": "FAIL",
                            "detail": "Missing required output: /app/output.json",
                        }
                    },
                }
            )
        )
        if present:
            (trial / "artifacts/app/output.json").write_text('{"value": 1}\n')
        return job, task, trial, marker

    def test_saved_missing_check_and_collection_scope_are_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            job, task, trial, marker = self.build(Path(td))
            inventory = discover_all(job, task)["trials"][0]
            with patch("subprocess.Popen", side_effect=AssertionError("audit must not execute")):
                evidence, analysis, report, _ = analyze_single_trial(inventory, job, lang="zh")
            audit = evidence["deliverable_audit"]
            self.assertEqual(audit["records"][0]["status"], "not_found_in_collected_tree")
            self.assertEqual(
                audit["verified_missing_outputs"][0]["declared_source"], "/app/output.json"
            )
            contract = next(
                c
                for c in evidence["contract_observations"]
                if c["contract_id"].startswith("contract:declared_missing:")
            )
            self.assertEqual(contract["prompt_requirement"], "declared_collection_path")
            self.assertEqual(contract["agent_output_status"], "missing_at_verify")
            self.assertEqual(evidence["trajectory_normalization"]["format"], "codex_rollout")
            self.assertEqual(evidence["trajectory_normalization"]["coverage"], "complete")
            self.assertFalse(
                analysis["causal_assessment"]["existing_verification"]["new_calculation_performed"]
            )
            self.assertNotEqual(analysis["causal_assessment"]["level"], "evidence_supported_cause")
            self.assertFalse(marker.exists())
            self.assertIn("未采集到不等于未生成", report)
            raw = json.loads(_format_artifacts_summary("task208", evidence, analysis, raw=True))
            self.assertEqual(raw["deliverable_audit"], audit)

    def test_bound_review_can_validate_execution_outside_heuristic_anchors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            job, task, trial, _ = self.build(root, present=True)
            (task / "instruction.md").write_text("Report the mean as output.json's value.")
            trajectory = trial / "agent/rollout-example.jsonl"
            trajectory.write_text(
                trajectory.read_text().replace("python solve.py", "python reduction.py")
            )
            (trial / "verifier/science.json").write_text(
                json.dumps(
                    {
                        "status": "FAIL",
                        "checks": {
                            "mean": {"status": "FAIL", "detail": "Recorded value is not the mean"}
                        },
                    }
                )
            )
            evidence, analysis, *_ = analyze_single_trial(discover_all(job, task)["trials"][0], job)
            event = next(item for item in evidence["timeline"] if item.get("tool_succeeded"))[
                "event_id"
            ]
            self.assertNotIn(event, collect_positive_agent_evidence_ids(evidence))
            review = plan(evidence, analysis)
            review.update(
                reviewer="Fixture",
                rationale="Saved execution and output show a different reduction from the required mean.",
                proposed_attribution={
                    "category": "agent",
                    "subtype": "result_validation",
                    "code": "AGENT_RESULT_VALIDATION",
                    "summary_zh": "输出采用了错误的统计量。",
                    "summary_en": "The output used the wrong statistic.",
                },
            )
            output_ref = "art:artifacts/app/output.json"
            review["decision"].update(
                kind="scientific",
                event_ref=event,
                description="Executed a reduction with the wrong formula.",
                rationale="The public mean requirement and recorded rejected value isolate the error.",
                mechanism={
                    "actual_logic": "Used the first observed value.",
                    "expected_logic": "Take the arithmetic mean.",
                    "why_unrecovered": "The execution did not verify the statistic.",
                    "minimal_fix": "Compute the mean before writing the output.",
                },
                execution_binding={
                    "evidence_refs": [event, output_ref],
                    "statement": "The recorded command completed and the artifact holds its result.",
                    "effective_input_refs": ["art:instruction_md"],
                    "effective_output_refs": [output_ref],
                },
                basis={
                    "evidence_refs": ["art:instruction_md"],
                    "statement": "The public instructions require the mean.",
                },
                outcome={
                    "evidence_refs": ["verifier_report.failed_checks[0]"],
                    "statement": "The verifier rejected the saved value.",
                },
            )
            for alternative in review["alternatives"]:
                alternative.update(
                    status="ruled_out",
                    evidence_refs=["art:instruction_md", output_ref],
                    reason="The fixture supplies a deterministic workload and a single explicit reduction requirement.",
                )
            inputs = root / "inputs"
            inputs.mkdir()
            paths = [inputs / name for name in ("evidence.json", "analysis.json", "review.json")]
            for path, data in zip(paths, (evidence, analysis, review)):
                write(path, data)
            reviewed = apply(*paths, root / "reviewed")
            self.assertEqual(reviewed["causal_assessment"]["level"], "evidence_supported_cause")
            _, errors = validate_all(evidence, reviewed)
            self.assertTrue(any("POSITIVE_AGENT_EVIDENCE" in error for error in errors))

    def test_present_capture_is_registered_even_when_old_verifier_says_missing(self):
        with tempfile.TemporaryDirectory() as td:
            job, task, trial, marker = self.build(Path(td), present=True, partial=True)
            evidence, *_ = collect_case_evidence(discover_all(job, task)["trials"][0], job)
            self.assertEqual(evidence["deliverable_audit"]["records"][0]["status"], "present")
            self.assertEqual(len(evidence["deliverable_audit"]["verified_missing_outputs"]), 1)
            self.assertTrue(
                evidence["deliverable_audit"]["records"][0]["capture_verifier_conflict"]
            )
            artifact = next(
                a
                for a in evidence["artifacts"]
                if a["artifact_id"] == "art:artifacts/app/output.json"
            )
            self.assertTrue(artifact["exists"])
            self.assertTrue(artifact["sha256"])
            self.assertTrue(
                any(d["code"] == "INCOMPLETE_TRAJECTORY" for d in evidence["diagnostics"])
            )
            self.assertFalse(marker.exists())

    def test_declared_missing_path_is_case_sensitive_and_not_a_unicode_prefix(self):
        evidence = {
            "metadata": {"task_toml": {"artifacts": ["/app/output.json"]}},
            "verifier_report": {"failed_checks": []},
        }
        for detail in ("Missing /app/Output.json", "Missing /app/output.json备份"):
            evidence["verifier_report"]["failed_checks"] = [{"detail": detail}]
            self.assertEqual(extract_verified_missing_outputs(evidence), [])
        evidence["verifier_report"]["failed_checks"] = [{"detail": "MISSING /app/output.json"}]
        self.assertEqual(len(extract_verified_missing_outputs(evidence)), 1)

    def test_delta_and_negated_recomputation_are_not_absolute_reference_values(self):
        report = existing_verification(
            {
                "scientific_observations": [
                    {
                        "reported_value": 0.1,
                        "recomputed_value": 0,
                        "is_delta_divergence": True,
                        "matched_text": "differs from independent replay by 0.1",
                    },
                    {
                        "reported_value": 1,
                        "recomputed_value": 2,
                        "matched_text": "not recomputed; stored reference 2",
                    },
                ]
            }
        )
        first, second = report["numeric_records"]
        self.assertEqual(first["reported_value_meaning"], "deviation")
        self.assertIsNone(first["recomputed_value"])
        self.assertEqual(second["kind"], "reference_comparison_or_unspecified")
        self.assertEqual(report["independence"], "not_established")
        self.assertFalse(report["new_calculation_performed"])


if __name__ == "__main__":
    unittest.main()
