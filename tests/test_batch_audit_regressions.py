"""Regression boundaries exposed by the local-b saved audit batch."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_case import compute_batch_statistics, analyze_single_trial
from discover_artifacts import discover_all
from normalize_verifier_report import build_verifier_report
from report_style import render_contract_review
from render_batch import _scoring_coverage
from runtime_state import score_was_produced
from task_ref import normalize_task_ref, resolve_task_ref
from scoring_dependencies import collect_scoring_dependency_observations


class BatchAuditRegressions(unittest.TestCase):
    def test_scientific_definition_mismatch_is_not_described_as_json_schema(self):
        from tests.test_attribution_decisions import _evidence, _contract, _fail_log
        from attribution.engine import run_attribution_engine

        contract = _contract("agent_mismatch")
        contract.update(
            item="stopping_force_definition",
            details="Stopping code uses a component maximum instead of the required per-atom force norm.",
        )
        result = run_attribution_engine(
            _evidence(
                contract_observations=[contract],
                verifier_observations=[_fail_log("force convergence failed")],
            ),
            None,
        )
        self.assertEqual(result["primary_root_cause"]["summary"], contract["details"])
        self.assertEqual(result["primary_root_cause"]["code"], "AGENT_TASK_UNDERSTANDING")

    def test_prefixed_harbor_task_and_trial_use_fast_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            task = root / "tasks/136-task136"
            trial = root / "jobs/run/136-task136__abc"
            task.mkdir(parents=True)
            trial.mkdir(parents=True)
            (task / "task.toml").write_text('artifacts=["/app/final"]\n')
            (trial / "result.json").write_text("{}")
            for reference in ("task136", "136-task136", "136-task136__abc"):
                with patch(
                    "task_ref._walk_dirs", side_effect=AssertionError("unexpected broad scan")
                ):
                    resolved = resolve_task_ref(reference, root)
                self.assertEqual(resolved.task_def_dir, task)
                self.assertEqual(resolved.trial_dir, trial)
            self.assertEqual(normalize_task_ref("136-task136__abc"), "task136")
            self.assertNotEqual(normalize_task_ref("1360-task1360__abc"), "task136")

    def test_verifier_launch_does_not_count_as_scored_failure(self):
        rows = [
            {"verdict": "passed", "verifier_started": True, "score_produced": True, "reward": 1.0},
            {"verdict": "failed", "verifier_started": True, "score_produced": True, "reward": 0.0},
            {
                "verdict": "errored",
                "verifier_started": True,
                "score_produced": False,
                "reward": None,
            },
        ]
        stats = compute_batch_statistics(rows)
        self.assertEqual(stats["verifier_started_count"], 3)
        self.assertEqual(stats["scored_trial_count"], 2)
        self.assertEqual(stats["not_scored_trial_count"], 1)
        self.assertEqual(stats["scored_pass_rate"], 50.0)
        coverage = _scoring_coverage(stats, rows, len(rows))
        self.assertEqual(coverage["scored"], 2)
        self.assertFalse(score_was_produced({"verifier_started": True, "verdict": "errored"}))
        self.assertFalse(score_was_produced({"reward": float("nan")}))

    def test_started_dependency_failures_validate_as_unscored_errors(self):
        for code, message, pointer in (
            (
                "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH",
                "trusted semantic review evidence hash is damaged",
                False,
            ),
            ("VERIFIER_REFERENCE_LFS_POINTER", "tests/refs.json missing or malformed", True),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                task = root / "task"
                trial = root / "trial"
                (task / "tests").mkdir(parents=True)
                (trial / "agent").mkdir(parents=True)
                (trial / "verifier").mkdir()
                (task / "instruction.md").write_text("Produce the result.")
                if pointer:
                    (task / "tests/refs.json").write_text(
                        "version https://git-lfs.github.com/spec/v1\noid sha256:test\nsize 1234\n"
                    )
                (trial / "agent/trajectory.json").write_text(
                    json.dumps({"schema_version": "ATIF-v1.7", "steps": []})
                )
                (trial / "result.json").write_text(
                    json.dumps(
                        {
                            "agent_result": {"n_input_tokens": 1},
                            "verifier_result": None,
                            "exception_info": {
                                "exception_type": "RewardFileNotFoundError",
                                "exception_message": "reward file not found",
                            },
                        }
                    )
                )
                (trial / "verifier/test-stdout.txt").write_text(message)
                ev, an, _, _ = analyze_single_trial(discover_all(trial, task)["trials"][0], trial)
                self.assertEqual(an["primary_root_cause"]["code"], code)
                self.assertEqual(an["verdict"], "errored")
                self.assertTrue(ev["runtime"]["verifier_started"])
                self.assertFalse(ev["runtime"]["score_produced"])

    def test_nested_diagnostic_envelope_preserves_source_but_exposes_message(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "verifier").mkdir()
            detail = json.dumps(
                {
                    "status": "FAIL",
                    "reward": 0,
                    "error_type": "science_failure",
                    "message": "per-sample checks are incomplete",
                }
            )
            (root / "verifier/verification.json").write_text(
                json.dumps({"status": "FAIL", "reason": detail})
            )
            report = build_verifier_report(root)
            check = report["failed_checks"][0]
            self.assertEqual(check["detail"], "per-sample checks are incomplete")
            self.assertEqual(check["original_detail"], detail)

    def test_per_sample_report_quotes_the_actual_saved_statement(self):
        evidence = {
            "contract_review": {
                "requirement_coverage_findings": [
                    {
                        "actual_submitted_representation": [
                            {
                                "pointer": "$.source_check",
                                "missing_direct_labels": ["A"],
                                "nested_label_occurrences": {"A": ["$.nested.A"]},
                                "submitted_text_quotes": [
                                    {
                                        "pointer": "$.source_check.status",
                                        "quote": "The source cross-check was not completed.",
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        }
        rendered = "\n".join(render_contract_review(evidence, "en"))
        self.assertIn("The source cross-check was not completed.", rendered)
        self.assertIn("nested maps", rendered)
        self.assertNotIn("“—”", rendered)

    def test_scored_run_does_not_revive_old_dependency_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "verifier").mkdir()
            (root / "verifier/test-stdout.txt").write_text(
                "trusted semantic review evidence hash is damaged\nretry completed"
            )
            self.assertEqual(
                collect_scoring_dependency_observations(None, root, {"score_produced": True}), []
            )


if __name__ == "__main__":
    unittest.main()
