"""Small synthetic saved records for definition review; no force calculations."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scientific_contract_review import audit_scientific_contracts


class ScientificContractReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = self.root / "trial"
        self.task = self.root / "task"
        for path in (
            self.trial / "agent",
            self.trial / "verifier",
            self.task / "tests",
            self.trial / "artifacts/app/final",
        ):
            path.mkdir(parents=True)
        self.evidence = {"trial_dir": str(self.trial), "task_dir": str(self.task)}
        (self.trial / "verifier/test-stdout.txt").write_text(
            "forward path stopped before force convergence or the accepted-step cap"
        )
        (self.task / "tests/verify.py").write_text(
            'Fmax.append(float(np.max(np.linalg.norm(res.forces_eV_A,axis=1))))\nif Fmax[-1] <= thresholds["force_threshold"]: expected_stop="force_threshold"'
        )
        (self.trial / "artifacts/app/final/execution_trace.csv").write_text(
            "scope,accepted_steps,stop_status\nforward,12,force_threshold\n"
        )
        self.code = 'fmax_try = float(np.abs(f_try).max())\nif fmax_try <= FORCE_TOL:\n    stop_status = "force_threshold"; break\n'

    def review(self, code=None, public=True):
        steps = []
        if public:
            steps.append(
                {
                    "step_id": 1,
                    "tool_calls": [
                        {
                            "tool_call_id": "read",
                            "arguments": {"command": "cat /app/assets/path_protocol.json"},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "read",
                                "content": '{"stopping":{"max_atomic_force_eV_A":0.037}}',
                            }
                        ]
                    },
                }
            )
        steps.append(
            {
                "step_id": 2,
                "tool_calls": [{"arguments": {"command": self.code if code is None else code}}],
            }
        )
        (self.trial / "agent/trajectory.json").write_text(json.dumps({"steps": steps}))
        return audit_scientific_contracts(self.evidence)

    def test_applied_stop_definition_and_saved_failure(self):
        result = self.review()
        finding = result["findings"][0]
        self.assertEqual(finding["status"], "candidate")
        self.assertEqual(finding["scope"], "public_requirement_observed")
        self.assertIn("FORCE_TOL", finding["stopping_condition"])
        self.assertEqual(finding["retained_output"][0]["row"]["accepted_steps"], "12")
        self.assertTrue(finding["saved_failure"])

    def test_incidental_absolute_value_print_is_not_applied_stop(self):
        self.assertFalse(
            self.review("print(np.abs(f_try).max())\nif norm <= FORCE_TOL: break")["findings"]
        )

    def test_assignment_used_only_for_logging_does_not_bind(self):
        self.assertFalse(
            self.review(
                "debug = float(np.abs(f_try).max())\nprint(debug)\nif norm <= FORCE_TOL: break"
            )["findings"]
        )

    def test_correct_metric_reassignment_suppresses_candidate(self):
        self.assertFalse(
            self.review(
                self.code.replace(
                    "if fmax_try", "fmax_try = np.linalg.norm(f_try, axis=1).max()\nif fmax_try"
                )
            )["findings"]
        )

    def test_missing_public_requirement_retains_scope_uncertainty(self):
        finding = self.review(public=False)["findings"][0]
        self.assertEqual(finding["scope"], "public_requirement_uncertain")
        self.assertTrue(any("scope" in text for text in finding["uncertainty"]))

    def test_missing_matching_failed_check_suppresses_candidate(self):
        (self.trial / "verifier/test-stdout.txt").write_text("missing report")
        self.assertFalse(self.review()["findings"])

    def test_matching_public_text_in_own_report_is_not_requirement(self):
        finding = self.review(public=False)["findings"][0]
        self.assertFalse(finding["public_requirement"])

    def bound_steps(self, run="final.py", corrected=False):
        self.review()
        path = self.trial / "agent/trajectory.json"
        data = json.loads(path.read_text())
        data["steps"][-1]["tool_calls"][0] = {
            "tool_call_id": "write",
            "arguments": {"command": "cat > /app/work/final.py <<'EOF'\n" + self.code + "EOF"},
        }
        if corrected:
            data["steps"].append(
                {
                    "step_id": 3,
                    "tool_calls": [
                        {
                            "tool_call_id": "fix",
                            "arguments": {
                                "command": "cat > /app/work/final.py <<'EOF'\nfmax_try = np.linalg.norm(f_try,axis=1).max()\nif fmax_try <= FORCE_TOL:\n    stop_status='force_threshold'; break\nEOF"
                            },
                        }
                    ],
                }
            )
        data["steps"].append(
            {
                "step_id": 4,
                "tool_calls": [{"tool_call_id": "run", "arguments": {"command": "python3 " + run}}],
                "observation": {
                    "results": [
                        {"source_call_id": "run", "content": "forward force_threshold fmax=0.03"}
                    ]
                },
            }
        )
        path.write_text(json.dumps(data))
        return audit_scientific_contracts(self.evidence)

    def test_application_bound_to_same_script_and_full_event_ref(self):
        finding = self.bound_steps()["findings"][0]
        self.assertEqual(finding["application_binding"], "bound")
        self.assertEqual(finding["observed_code"]["event_ref"], "trajectory:step:2:tool:0")
        self.assertEqual(finding["observed_code"]["script_target"], "/app/work/final.py")

    def test_unrelated_execution_cannot_bind_source(self):
        finding = self.bound_steps("probe.py")["findings"][0]
        self.assertEqual(finding["application_binding"], "unresolved")
        self.assertFalse(finding["saved_execution"])

    def test_corrected_later_script_replacement_suppresses_stale_source(self):
        self.assertFalse(self.bound_steps(corrected=True)["findings"])

    def test_missing_script_target_is_unresolved(self):
        self.assertEqual(self.review()["findings"][0]["application_binding"], "unresolved")

    def test_jsonl_recorded_calls_and_results_support_same_binding(self):
        self.bound_steps()
        path = self.trial / "agent/trajectory.json"
        data = json.loads(path.read_text())
        records = []
        for step in data["steps"]:
            for call in step.get("tool_calls", []):
                records.append(
                    {
                        "type": "function_call",
                        "call_id": call["tool_call_id"],
                        "name": "Bash",
                        "arguments": json.dumps(call["arguments"]),
                    }
                )
            for obs in (step.get("observation") or {}).get("results", []):
                records.append(
                    {
                        "type": "function_call_output",
                        "call_id": obs["source_call_id"],
                        "output": obs["content"],
                    }
                )
        path.unlink()
        (self.trial / "agent/trajectory.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records)
        )
        result = audit_scientific_contracts(self.evidence)
        self.assertEqual(result["findings"][0]["application_binding"], "bound")
