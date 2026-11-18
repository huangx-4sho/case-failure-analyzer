"""Explicit unscored verifier review requests are pending, not failures."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime_state import compute_runtime_state
from attribution.engine import run_attribution_engine
from analyze_case import compute_batch_statistics
from render_index import render_index_markdown
from validate_analysis import validate_report_sections


class PendingReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = Path(self.tmp.name)
        (self.trial / "verifier").mkdir()
        (self.trial / "verifier/verification.json").write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED",
                    "reason": "independent scientific review remains unresolved",
                }
            )
        )
        self.result = {
            "agent_result": {},
            "verifier_result": {"rewards": {"review_required": 1}},
            "exception_info": {"exception_type": "RewardFileNotFoundError"},
        }

    def test_missing_reward_exception_is_pending_not_failed_or_errored(self):
        state = compute_runtime_state({}, self.result, self.trial)
        self.assertEqual(state["verdict"], "pending")
        self.assertEqual(state["verification_status"], "pending")
        self.assertFalse(state["score_produced"])
        self.assertIsNone(state["reward"])
        self.assertTrue(state["has_trial_exception"])
        analysis = run_attribution_engine({"runtime": state, "case_id": "synthetic"}, {})
        self.assertEqual(analysis["primary_root_cause"]["code"], "REVIEW_REQUIRED")
        self.assertEqual(analysis["causal_assessment"]["level"], "not_applicable")
        self.assertFalse(analysis["skill_prescription"])

    def test_genuine_numeric_reward_contradiction_is_preserved(self):
        for reward in (0, 1):
            self.result["verifier_result"] = {"rewards": {"reward": reward}}
            state = compute_runtime_state({}, self.result, self.trial)
            self.assertEqual(state["verdict"], "passed" if reward else "failed")
            self.assertTrue(state["score_produced"])
            self.assertTrue(state["state_conflicts"])

    def test_nested_review_requirement_does_not_override_failure(self):
        (self.trial / "verifier/verification.json").write_text(
            json.dumps({"status": "FAIL", "review": {"status": "REVIEW_REQUIRED"}})
        )
        state = compute_runtime_state({}, self.result, self.trial)
        self.assertNotEqual(state["verdict"], "pending")

    def test_plain_stdout_review_request(self):
        (self.trial / "verifier/verification.json").unlink()
        (self.trial / "verifier/test-stdout.txt").write_text(
            "REVIEW_REQUIRED: independent scientific review remains unresolved"
        )
        self.assertEqual(compute_runtime_state({}, self.result, self.trial)["verdict"], "pending")

    def test_pending_counts_are_separate_from_failed_and_error_categories(self):
        rows = [
            {
                "task_id": "one",
                "report_path": "one/report.md",
                "verdict": "pending",
                "score_produced": False,
                "category": "none",
            },
            {
                "task_id": "two",
                "report_path": "two/report.md",
                "verdict": "passed",
                "score_produced": True,
                "category": "none",
            },
        ]
        stats = compute_batch_statistics(rows)
        self.assertEqual(stats["pending_review_trial_count"], 1)
        self.assertEqual(stats["scored_trial_count"], 1)
        self.assertEqual(stats["scored_pass_rate"], 100)
        index = render_index_markdown(rows)
        self.assertIn("Pending Review**: `1`", index)
        self.assertIn("Errored & Unscored**: `0`", index)

    def test_pending_report_has_two_nonfailure_sections(self):
        self.assertEqual(
            validate_report_sections(
                "## 1. Review status\nPending review.\n## 2. Saved evidence\nSaved verifier receipt.\n",
                verdict="pending",
            ),
            [],
        )

    def test_canonical_final_status_beats_stale_stdout_review_request(self):
        (self.trial / "verifier/test-stdout.txt").write_text("REVIEW_REQUIRED: stale request")
        for status in ("PASS", "FAIL"):
            (self.trial / "verifier/verification.json").write_text(json.dumps({"status": status}))
            state = compute_runtime_state({}, self.result, self.trial)
            self.assertNotEqual(state["verdict"], "pending")
            self.assertFalse(state["review_required"])
