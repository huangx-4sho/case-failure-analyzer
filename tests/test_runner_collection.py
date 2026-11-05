#!/usr/bin/env python3
"""
Runner-stage artifact collection tests (`tests/test_runner_collection.py`)

Harbor aborts a trial after agent execution when `docker cp` cannot find a
declared deliverable (`Engine cp failed ... Could not find the file /app/final
in container ...`), or dies silently right after `Collecting main service
artifacts`. These tests pin the post-agent stage classification and the
infra-primary / agent-contributing compound attribution.
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

from attribution.engine import run_attribution_engine
from extract_runtime_errors import extract_runtime_errors
from validate_analysis import validate_all

ENGINE_CP_LOG = """\
Wrote Claude Code trajectory to jobs/job1/task 1/agent/trajectory.json
Collecting main service artifacts
artifacts compose cp failed; retrying download with engine cp: Docker compose command failed
Traceback (most recent call last):
  File "docker_unix.py", line 244, in _engine_cp
    raise RuntimeError(
RuntimeError: Engine cp failed ('abc:/app/final' -> 'jobs/job1/task 1/artifacts/app/final'): Error response from daemon: Could not find the file /app/final in container abc
"""

SILENT_LOG = """\
Wrote Claude Code trajectory to jobs/job1/task 1/agent/trajectory.json
Cannot estimate Claude cost for model x
Collecting main service artifacts
"""


def _trial(root: Path, log_text: str) -> Path:
    job = root / "jobs" / "job1"
    trial = job / "task 1"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "trajectory.json").write_text("{}", encoding="utf-8")
    (trial / "trial.log").write_text(log_text, encoding="utf-8")
    (job / "job.log").write_text(log_text, encoding="utf-8")
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "case-1",
                "trial_name": "task 1",
                "agent_result": {"n_input_tokens": 10},
                "verifier_result": None,
                "exception_info": None,
            }
        ),
        encoding="utf-8",
    )
    return trial


class RunnerCollectionExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_engine_cp_crash_is_runner_stage_and_causal(self) -> None:
        trial = _trial(self.root, ENGINE_CP_LOG)
        result = extract_runtime_errors(job_dir=trial.parent, trial_dir=trial)
        codes = [o["code"] for o in result["error_observations"]]
        self.assertIn("INFRA_ARTIFACT_COLLECTION_CRASH", codes)
        crash = next(
            o
            for o in result["error_observations"]
            if o["code"] == "INFRA_ARTIFACT_COLLECTION_CRASH"
        )
        self.assertEqual(crash["stage"], "runner")
        self.assertEqual(crash["missing_deliverable"], "/app/final")
        self.assertTrue(crash["causal_candidate"])
        self.assertFalse(crash["scoring_reached"])
        self.assertEqual(crash["runner_phase"], "post_agent_execution")

    def test_build_patterns_are_reclassified_out_of_environment_build(self) -> None:
        trial = _trial(self.root, ENGINE_CP_LOG)
        result = extract_runtime_errors(job_dir=trial.parent, trial_dir=trial)
        build_obs = [
            o for o in result["error_observations"] if o["code"] == "INFRA_CONTAINER_BUILD"
        ]
        self.assertTrue(build_obs)
        for obs in build_obs:
            self.assertEqual(obs["stage"], "runner")
            self.assertEqual(obs["stage_reclassified_from"], "environment_build")
            self.assertFalse(obs["causal_candidate"])

    def test_silent_interruption_is_detected(self) -> None:
        trial = _trial(self.root, SILENT_LOG)
        result = extract_runtime_errors(job_dir=trial.parent, trial_dir=trial)
        interrupted = [
            o for o in result["error_observations"] if o["code"] == "INFRA_RUNNER_INTERRUPTED"
        ]
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["stage"], "runner")
        self.assertTrue(interrupted[0]["causal_candidate"])


class RunnerCollectionAttributionTests(unittest.TestCase):
    def _evidence(self) -> dict:
        return {
            "case_id": "case-1",
            "trial_name": "task 1",
            "runtime": {
                "agent_started": True,
                "verifier_started": False,
                "verification_status": "not_run",
                "reward": None,
                "started_at": "t0",
                "finished_at": "t1",
            },
            "artifacts": [{"artifact_id": "art:trial_result", "exists": True}],
            "error_observations": [
                {
                    "error_id": "err:1",
                    "code": "INFRA_ARTIFACT_COLLECTION_CRASH",
                    "subtype": "artifact_collection_crash",
                    "stage": "runner",
                    "matched_text": "Engine cp failed ... Could not find the file /app/final",
                    "causal_candidate": True,
                    "missing_deliverable": "/app/final",
                }
            ],
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

    def test_compound_attribution_infra_primary_agent_contributing(self) -> None:
        analysis = run_attribution_engine(self._evidence(), {"candidate_hypotheses": []})
        self.assertEqual(analysis["primary_root_cause"]["category"], "infra")
        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_ARTIFACT_COLLECTION_CRASH")
        self.assertEqual(analysis["failure_stage"], "runner")
        self.assertEqual(analysis["detection_stage"], "runner")
        self.assertEqual(analysis["verdict"], "errored")
        contributing = analysis["contributing_factors"]
        self.assertTrue(any(c["category"] == "agent" for c in contributing))
        self.assertEqual(
            analysis["decision_trace"]["selected"]["gate_id"],
            "gate1c_runner_artifact_collection",
        )

    def test_analysis_passes_validator(self) -> None:
        from analyze_case import build_causal_attribution

        evidence = self._evidence()
        analysis = build_causal_attribution(evidence, {}, {}, {"candidate_hypotheses": []})
        ok, errors = validate_all(evidence, analysis)
        self.assertTrue(ok, errors)


if __name__ == "__main__":
    unittest.main()
