#!/usr/bin/env python3
"""
Harbor deliverable / runner-stage regression tests
(`tests/test_harbor_deliverables.py`)

Pins the harbor-specific behaviors added to the skill:

- `task.toml` `artifacts` is the authoritative declared-deliverable set;
  `sig:premature_completion` fires only for a genuinely missing declared path.
- shell commands that merely *read* a declared path (`ls ... 2>/dev/null`)
  must not be counted as producing it.
- collected `<trial>/artifacts/app/<path>` evidence overrides a trajectory
  matching miss.
- causal `INFRA_AGENT_TIMEOUT` outranks a downstream collection crash, and a
  benign litellm cost-estimation warning is never an infra error.
- batch dashboards expose three-state scoring coverage and the scored pass rate.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_case import (
    _bind_unfinished_remote_delivery,
    _confirm_delivered_on_disk,
    _reconcile_delivered_deliverables,
    compute_batch_statistics,
)
from attribution.engine import run_attribution_engine
from discover_artifacts import extract_declared_deliverables
from extract_runtime_errors import extract_runtime_errors
from normalize_trajectory import _command_writes_path, normalize_trajectory
from render_batch import render_job_summary_markdown
from validate_analysis import validate_all


def _trajectory(root: Path, commands: list[str]) -> Path:
    """Write a minimal ATIF-v1.7 trajectory containing the given bash commands."""
    path = root / "agent" / "trajectory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tool_calls = []
    results = []
    for i, cmd in enumerate(commands):
        tool_calls.append(
            {
                "tool_call_id": f"c{i}",
                "function_name": "Bash",
                "arguments": {"command": cmd},
            }
        )
        results.append({"source_call_id": f"c{i}", "content": ""})
    steps = [
        {
            "step_id": 1,
            "source": "agent",
            "message": "",
            "tool_calls": tool_calls,
            "observation": {"results": results},
        }
    ]
    path.write_text(json.dumps({"schema_version": "ATIF-v1.7", "steps": steps}), encoding="utf-8")
    return path


def _signals(norm: dict, kind: str) -> list[dict]:
    return [s for s in norm["behavioral_signals"] if s.get("signal_type") == kind]


class DeclaredDeliverableParsingTests(unittest.TestCase):
    def test_artifacts_list_and_string_are_supported(self) -> None:
        self.assertEqual(
            extract_declared_deliverables({"artifacts": ["/app/final", "/app/out.json"]}),
            ["/app/final", "/app/out.json"],
        )
        self.assertEqual(extract_declared_deliverables({"artifacts": "/app/final"}), ["/app/final"])

    def test_missing_or_invalid_declaration_yields_empty_list(self) -> None:
        for value in (None, {}, {"artifacts": None}, {"artifacts": 3}, "not a dict"):
            self.assertEqual(extract_declared_deliverables(value), [])
        self.assertEqual(
            extract_declared_deliverables({"artifacts": ["", "  ", "/app/final", "/app/final"]}),
            ["/app/final"],
        )


class CommandWriteMatcherTests(unittest.TestCase):
    def test_read_only_commands_do_not_count(self) -> None:
        cases = [
            "ls -la /app; ls -la /app/final 2>/dev/null",
            "ls -la /app/final 2>&1; echo done",
            "cat /app/final/x.json",
            "cp /app/final/x.json /tmp/backup/",
            "sed -n 1,5p /app/final/x.json",
        ]
        for cmd in cases:
            self.assertFalse(_command_writes_path(cmd, "/app/final"), cmd)

    def test_write_commands_count(self) -> None:
        cases = [
            "echo x > /app/final/out.json",
            "echo x >>/app/final/out.json",
            "cp /tmp/x.json /app/final/",
            "mv /app/startup/x /app/final/",
            "mkdir -p /app/final",
            "python3 -c \"import json;json.dump(d,open('/app/final/x.json','w'))\"",
            "cat > /app/final/report.md <<EOF",
            "tar czf /app/final/out.tgz dir/",
        ]
        for cmd in cases:
            self.assertTrue(_command_writes_path(cmd, "/app/final"), cmd)


class DeliverableAwareTrajectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_declared_deliverable_written_emits_no_premature_signal(self) -> None:
        p = _trajectory(self.root, ["mkdir -p /app/final && echo ok > /app/final/out.json"])
        norm = normalize_trajectory(p, expected_deliverables=["/app/final"])
        self.assertEqual(norm["missing_deliverables"], [])
        self.assertEqual(_signals(norm, "premature_completion"), [])

    def test_declared_deliverable_absent_emits_premature_with_missing_list(self) -> None:
        p = _trajectory(self.root, ["ls -la /app/final 2>/dev/null"])
        norm = normalize_trajectory(p, expected_deliverables=["/app/final"])
        self.assertEqual(norm["missing_deliverables"], ["/app/final"])
        sigs = _signals(norm, "premature_completion")
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["missing_deliverables"], ["/app/final"])
        self.assertIn("task.toml", sigs[0]["description"])

    def test_partial_deliverables_report_only_the_missing_ones(self) -> None:
        p = _trajectory(self.root, ["echo ok > /app/out.json"])
        norm = normalize_trajectory(p, expected_deliverables=["/app/final", "/app/out.json"])
        self.assertEqual(norm["missing_deliverables"], ["/app/final"])

    def test_legacy_results_json_heuristic_without_declaration(self) -> None:
        p = _trajectory(self.root, ["echo working"])
        norm = normalize_trajectory(p)
        self.assertEqual(len(_signals(norm, "premature_completion")), 1)

        p2 = _trajectory(self.root / "b", ["echo '{}' > results.json"])
        norm2 = normalize_trajectory(p2)
        self.assertEqual(_signals(norm2, "premature_completion"), [])
        self.assertEqual(len(_signals(norm2, "unverified_output")), 1)


class CollectedArtifactReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_collected_path_overrides_trajectory_matching_miss(self) -> None:
        p = _trajectory(self.root, ["ls -la /app/final 2>/dev/null"])
        norm = normalize_trajectory(p, expected_deliverables=["/app/final"])
        self.assertEqual(norm["missing_deliverables"], ["/app/final"])

        trial_dir = self.root / "trial"
        collected = trial_dir / "artifacts" / "app" / "final"
        collected.parent.mkdir(parents=True)
        collected.write_text("{}", encoding="utf-8")
        self.assertEqual(_confirm_delivered_on_disk(trial_dir, ["/app/final"]), {"/app/final"})

        reconciled = _reconcile_delivered_deliverables(norm, trial_dir, ["/app/final"])
        self.assertEqual(reconciled, [])
        self.assertEqual(norm["missing_deliverables"], [])
        self.assertEqual(_signals(norm, "premature_completion"), [])

    def test_missing_path_remains_when_nothing_was_collected(self) -> None:
        p = _trajectory(self.root, ["ls -la /app/final 2>/dev/null"])
        norm = normalize_trajectory(p, expected_deliverables=["/app/final"])
        trial_dir = self.root / "trial"
        trial_dir.mkdir()
        self.assertEqual(
            _reconcile_delivered_deliverables(norm, trial_dir, ["/app/final"]),
            ["/app/final"],
        )
        self.assertEqual(len(_signals(norm, "premature_completion")), 1)


class RunnerExecutionInfraTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _evidence(self, errors: list[dict], verifier_started: bool) -> dict:
        return {
            "case_id": "harbor-case",
            "trial_name": "trial-1",
            "runtime": {
                "agent_started": True,
                "verifier_started": verifier_started,
                "verification_status": "failed" if verifier_started else "not_run",
                "reward": 0.0 if verifier_started else None,
                "verdict": "failed" if verifier_started else "unknown",
                "started_at": "t0",
                "finished_at": "t1",
            },
            "artifacts": [{"artifact_id": "art:trial_result", "exists": True}],
            "error_observations": errors,
            "contract_observations": [],
            "verifier_observations": [],
            "scientific_observations": [],
            "behavioral_signals": [
                {
                    "signal_id": "sig:premature_completion",
                    "signal_type": "premature_completion",
                    "event_ref": "trajectory:step:1",
                }
            ],
            "timeline": [],
            "missing_artifacts": [],
            "missing_deliverables": ["/app/final"],
        }

    def _timeout(self) -> dict:
        return {
            "error_id": "err:1",
            "code": "INFRA_AGENT_TIMEOUT",
            "subtype": "agent_timeout",
            "stage": "agent_execution",
            "matched_text": "AgentTimeoutError: Agent execution timed out after 120.0 seconds",
            "causal_candidate": True,
        }

    def _crash(self) -> dict:
        return {
            "error_id": "err:2",
            "code": "INFRA_ARTIFACT_COLLECTION_CRASH",
            "subtype": "artifact_collection_crash",
            "stage": "runner",
            "matched_text": "Engine cp failed ... Could not find the file /app/final in container",
            "causal_candidate": True,
            "missing_deliverable": "/app/final",
        }

    def test_timeout_outranks_downstream_collection_crash(self) -> None:
        analysis = run_attribution_engine(
            self._evidence([self._timeout(), self._crash()], verifier_started=False),
            {"candidate_hypotheses": []},
        )
        self.assertEqual(analysis["primary_root_cause"]["category"], "infra")
        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_AGENT_TIMEOUT")
        self.assertEqual(analysis["verdict"], "errored")
        self.assertEqual(analysis["failure_stage"], "agent_execution")
        codes = [c["code"] for c in analysis["contributing_factors"]]
        self.assertIn("INFRA_ARTIFACT_COLLECTION_CRASH", codes)
        self.assertIn("AGENT_MISSING_DECLARED_DELIVERABLE", codes)
        ok, errors = validate_all(
            self._evidence([self._timeout(), self._crash()], verifier_started=False), analysis
        )
        self.assertTrue(ok, errors)

    def test_timeout_with_scored_verdict_is_infra_not_agent(self) -> None:
        # A 120s infra timeout that still reached the verifier must not be
        # blamed on AGENT_PREMATURE_TERMINATION.
        evidence = self._evidence([self._timeout()], verifier_started=True)
        analysis = run_attribution_engine(evidence, {"candidate_hypotheses": []})
        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_AGENT_TIMEOUT")
        self.assertEqual(analysis["verdict"], "failed")
        self.assertEqual(analysis["detection_stage"], "verifier_execution")
        ok, errors = validate_all(evidence, analysis)
        self.assertTrue(ok, errors)

    def test_benign_litellm_cost_warning_is_not_an_infra_error(self) -> None:
        job = self.root / "jobs" / "job1"
        trial = job / "task 1"
        (trial / "agent").mkdir(parents=True)
        (trial / "agent" / "trajectory.json").write_text("{}", encoding="utf-8")
        log = (
            "Cannot estimate Claude cost for model 'deepseek-v4.1-flash': "
            "litellm.BadRequestError: LLM Provider NOT provided. "
            "Pass in the LLM provider you are trying to call.\n"
            "Wrote Claude Code trajectory to agent/trajectory.json\n"
            "Collecting main service artifacts\n"
        )
        (trial / "trial.log").write_text(log, encoding="utf-8")
        (job / "job.log").write_text(log, encoding="utf-8")
        (trial / "result.json").write_text(
            json.dumps({"agent_result": {"n_input_tokens": 1}, "verifier_result": None}),
            encoding="utf-8",
        )
        out = extract_runtime_errors(job_dir=job, trial_dir=trial)
        codes = [o["code"] for o in out["error_observations"]]
        self.assertEqual(codes, ["INFRA_RUNNER_INTERRUPTED"])
        self.assertNotIn("INFRA_LLM_PROVIDER_ERROR", codes)


class ThreeStateBatchBoardTests(unittest.TestCase):
    def test_statistics_expose_scored_and_not_scored_counts(self) -> None:
        rows = [
            {"verdict": "passed", "verifier_started": True, "verifier_diagnosability": "normal"},
            {"verdict": "failed", "verifier_started": True, "verifier_diagnosability": "low"},
            {"verdict": "errored", "verifier_started": False},
        ]
        stats = compute_batch_statistics(rows)
        self.assertEqual(stats["total_trials"], 3)
        self.assertEqual(stats["scored_trial_count"], 2)
        self.assertEqual(stats["not_scored_trial_count"], 1)
        self.assertEqual(stats["scored_pass_rate"], 50.0)
        self.assertEqual(stats["low_diagnosability_trial_count"], 1)

    def test_renderer_shows_three_state_block_in_all_languages(self) -> None:
        for lang in ("zh", "en", "bilingual"):
            md = render_job_summary_markdown(
                {
                    "job_dir": "/tmp/jobs/batch1",
                    "batch_statistics": {
                        "total_trials": 3,
                        "verifier_started_count": 2,
                        "scored_trial_count": 2,
                        "not_scored_trial_count": 1,
                        "scored_pass_rate": 50.0,
                        "low_diagnosability_trial_count": 1,
                        "verdict_counts": {"passed": 1, "failed": 1, "errored": 1},
                    },
                    "trials": [
                        {"trial_name": "a", "verdict": "passed", "verifier_started": True},
                        {"trial_name": "b", "verdict": "failed", "verifier_started": True},
                        {"trial_name": "c", "verdict": "errored", "verifier_started": False},
                    ],
                },
                lang=lang,
            )
            self.assertIn("### 1.1", md, lang)
            self.assertIn("50.0%", md, lang)
            self.assertIn("33.3%", md, lang)  # legacy all-trial rate retained
            self.assertIn("| 3 | 1 | 1 | 1 | **33.3%** |", md, lang)
            diag_word = "可诊断性" if lang == "zh" else "Diagnosability"
            self.assertIn(diag_word, md, lang)



class RemoteDeliveryEvidenceTests(unittest.TestCase):
    def test_only_bind_confirmed_unfinished_remote_delivery(self):
        scenarios = ("confirmed", "completed", "failed_poll", "manifest_exists",
                     "missing_notification", "runtime_exception", "other_verifier_failure",
                     "path_prefix_only", "malformed_trajectory")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "artifacts/app/final"
                output.mkdir(parents=True)
                required = "/app/final/execution_manifest.json"
                instruction = required + (".bak" if scenario == "path_prefix_only" else "")
                (root / "instruction.md").write_text(f"Required output: `{instruction}`.")
                trajectory = root / "trajectory.json"
                steps = [{"source": "agent", "extra": {"stop_reason": "end_turn"}}]
                trajectory.write_text(json.dumps({
                    "steps": [None] if scenario == "malformed_trajectory" else steps
                }))
                if scenario == "manifest_exists":
                    (output / "execution_manifest.json").write_text("{}")
                evidence = {
                    "runtime": {
                        "verifier_started": True,
                        "reward": 0.0,
                        "has_trial_exception": scenario == "runtime_exception",
                    },
                    "verifier_report": {"failed_checks": [{"detail": "missing execution_manifest.json"}]},
                    "metadata": {"declared_deliverables": ["/app/final"]},
                    "timeline": [
                        {"tool_name": "Bash", "observation": (
                            "" if scenario == "missing_notification"
                            else "You will be notified when it completes."
                        )},
                        {
                            "event_id": "trajectory:step:2:tool:0",
                            "tool_name": "Bash",
                            "command": "python3 submit.py status --job production",
                            "observation": (
                                "production => completed 0" if scenario == "completed"
                                else "production => running None"
                            ),
                            "exit_code": int(scenario == "failed_poll"),
                        },
                    ],
                    "missing_deliverables": [],
                    "behavioral_signals": [],
                }
                if scenario == "other_verifier_failure":
                    evidence["verifier_report"]["failed_checks"].append({"detail": "numeric mismatch"})
                _bind_unfinished_remote_delivery(evidence, trajectory, root, root)
                self.assertEqual(bool(evidence["behavioral_signals"]), scenario == "confirmed")
                self.assertEqual(evidence["missing_deliverables"], [required] if scenario == "confirmed" else [])


if __name__ == "__main__":
    unittest.main()
