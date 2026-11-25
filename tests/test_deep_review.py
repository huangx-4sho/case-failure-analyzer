"""Default semantic investigation, evidence substitution and single-trial cache reuse."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deep_review import fingerprint, investigate, validation_error
from render_batch import render_job_summary_markdown


class DeepReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        task, trial = base / "task", base / "trial"
        (task / "tests").mkdir(parents=True)
        (trial / "artifacts").mkdir(parents=True)
        (task / "instruction.md").write_text("Supply a signed raw difference in result.json")
        (task / "tests/verify.py").write_text("assert result == signed_delta\n")
        (trial / "artifacts/result.json").write_text('{"delta": 1.25}')
        self.ev = {
            "trial_name": "unit_trial",
            "task_dir": str(task),
            "trial_dir": str(trial),
            "artifacts": [
                {
                    "artifact_id": "pub",
                    "scope": "task",
                    "rel_path": "instruction.md",
                    "exists": True,
                },
                {
                    "artifact_id": "scorer",
                    "scope": "task",
                    "rel_path": "tests/verify.py",
                    "exists": True,
                },
                {
                    "artifact_id": "out",
                    "scope": "trial",
                    "rel_path": "artifacts/result.json",
                    "exists": True,
                },
            ],
            "timeline": [
                {
                    "actor": "agent",
                    "event_id": "trajectory:step:8:tool:0",
                    "tool_name": "Write",
                    "tool_succeeded": True,
                    "result_recorded": True,
                    "source_pointer": "/steps/7",
                    "command": "/app/solve.py",
                    "arguments": {
                        "content": "delta = abs(signed_delta)\n",
                        "file_path": "/app/solve.py",
                    },
                }
            ],
        }
        self.analysis = {
            "verdict": "failed",
            "primary_root_cause": {"category": "unknown"},
            "causal_assessment": {
                "failure_detail": {
                    "scoring_event": {
                        "detail": "delta sign mismatch",
                        "source": "verifier/report.json",
                    }
                }
            },
        }
        self.answer = {
            "trial_name": "unit_trial",
            "status": "traced",
            "mechanism": "The writer removed the sign before serialization.",
            "gap": "This explains the comparison on the retained negative input.",
            "evidence": {
                "public": {"path": "instruction.md", "quote": "signed raw difference"},
                "submission": {"path": "artifacts/result.json", "quote": '"delta": 1.25'},
                "trajectory": {"event": "trajectory:step:8:tool:0", "quote": "abs(signed_delta)"},
                "scorer": {"path": "tests/verify.py", "quote": "result == signed_delta"},
            },
        }

    def test_default_reviewer_can_fetch_source_and_then_trace_and_reuse(self):
        lookup = {
            "requests": [{"role": "submission", "path": "artifacts/result.json", "needle": "delta"}]
        }
        with patch("deep_review._codex_answer", side_effect=[lookup, self.answer]) as model:
            result = investigate(self.ev, self.analysis, {}, mode="auto", lang="en")
        self.assertEqual(result["status"], "traced")
        self.assertEqual(len(result["evidence"]), 4)
        self.assertIn("1.25", model.call_args_list[1].args[0])
        self.assertIsNone(validation_error(result, self.ev, True))
        with patch(
            "deep_review._codex_answer", side_effect=AssertionError("cached decision was discarded")
        ):
            again = investigate(self.ev, self.analysis, {}, mode="auto", lang="en", cached=result)
        self.assertEqual(again["evidence"], result["evidence"])
        (Path(self.ev["trial_dir"]) / "artifacts/result.json").write_text('{"delta": -1.25}')
        self.assertIn("changed", validation_error(result, self.ev, True))

    def test_host_decision_requires_snapshot_four_quotes_and_success(self):
        key = self.ev["trial_name"], fingerprint(self.ev)
        with self.assertRaisesRegex(ValueError, "stale"):
            investigate(
                self.ev, self.analysis, {(key[0], "other"): self.answer}, mode="host", lang="en"
            )
        with self.assertRaisesRegex(ValueError, "four"):
            investigate(
                self.ev,
                self.analysis,
                {key: {**self.answer, "evidence": {}}},
                mode="host",
                lang="en",
            )
        self.ev["timeline"][0]["tool_succeeded"] = False
        with self.assertRaisesRegex(ValueError, "successful"):
            investigate(
                self.ev,
                self.analysis,
                {(key[0], fingerprint(self.ev)): self.answer},
                mode="host",
                lang="en",
            )

    def test_backend_failure_and_missing_receipts_are_never_counted_as_traced(self):
        with patch("deep_review._codex_answer", side_effect=ValueError("reviewer unavailable")):
            result = investigate(self.ev, self.analysis, {}, mode="auto", lang="en")
        self.assertEqual(result["status"], "review_incomplete")
        self.assertIn("reviewer unavailable", result["review_error"])
        old = investigate({**self.ev, "trial_dir": None}, self.analysis, {}, mode="host", lang="zh")
        self.assertEqual(old["status"], "open")
        self.assertIn("trajectory", old["gap"])
        self.assertIsNone(
            investigate(
                self.ev, {"primary_root_cause": {"category": "agent"}}, {}, mode="auto", lang="zh"
            )
        )

    def test_sibling_job_change_does_not_invalidate_trial_but_changed_trace_does(self):
        before = fingerprint(self.ev)
        self.ev["artifacts"].append(
            {"artifact_id": "art:job_result", "scope": "job", "sha256": "changed"}
        )
        self.ev["metadata"] = {"job_stats": {"n_errored_trials": 12}}
        self.assertEqual(before, fingerprint(self.ev))
        self.ev["timeline"][0]["arguments"]["content"] = "delta = signed_delta"
        self.assertNotEqual(before, fingerprint(self.ev))

    def test_open_and_traced_and_unfinished_are_reported_separately(self):
        rows = [
            {
                "trial_name": "trial" + str(n),
                "category": "unknown",
                "verdict": "failed",
                "deep_review": {
                    "status": status,
                    "evidence": {},
                    "mechanism": "delta units",
                    "gap": "missing operand",
                },
            }
            for n, status in enumerate(["open", "traced", "review_pending", "review_incomplete"])
        ]
        md = render_job_summary_markdown({"trials": rows}, "en")
        self.assertIn("Traced decisions 1, reviewed and open 1, incomplete 2", md)
