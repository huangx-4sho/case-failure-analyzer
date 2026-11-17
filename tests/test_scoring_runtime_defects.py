"""Regressions using small saved-artifact shapes, never live execution."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_runtime_errors import extract_runtime_errors
from runtime_state import compute_runtime_state
from scoring_dependencies import collect_scoring_dependency_observations


class ScoringRuntimeDefects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        self.trial = self.job / "trial"
        (self.trial / "agent").mkdir(parents=True)
        (self.trial / "verifier").mkdir()
        self.task = self.job / "task"
        (self.task / "tests").mkdir(parents=True)
        self.result = {
            "agent_result": {},
            "exception_info": {
                "exception_type": "RewardFileNotFoundError",
                "exception_message": "No reward file",
            },
        }

    def write(self, path, content):
        (self.trial / path).write_text(content)

    def errors(self):
        self.write("result.json", json.dumps(self.result))
        return extract_runtime_errors(self.job, self.trial)["error_observations"]

    def test_interpreter_failure_is_scoring_environment(self):
        self.write("verifier/test-stdout.txt", "/tests/test.sh: 6: exec: python: not found\n")
        obs = self.errors()[0]
        self.assertEqual(obs["stage"], "verifier_execution")
        self.assertTrue(obs["causal_candidate"])
        state = compute_runtime_state({}, self.result, self.trial)
        self.assertTrue(state["verifier_started"])
        self.assertFalse(state["score_produced"])
        self.assertEqual(state["verification_status"], "errored")

    def test_pass_supersedes_prior_exception_and_interpreter_notice(self):
        self.result["verifier_result"] = {"rewards": {"reward": 1}}
        self.write("verifier/test-stdout.txt", "python: not found")
        self.assertFalse(self.errors()[0]["causal_candidate"])
        self.assertEqual(compute_runtime_state({}, self.result, self.trial)["verdict"], "passed")

    def test_terminal_sdk_error_overrides_nonfatal_model_notice(self):
        self.write(
            "agent/claude-code.txt",
            "[claude-code:unrecognized_model] {}\n"
            + json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "api_error": "autocompact_thrashing",
                    "result": "Autocompact is thrashing",
                }
            ),
        )
        codes = [o["code"] for o in self.errors() if o["causal_candidate"]]
        self.assertEqual(codes, ["INFRA_AGENT_AUTOCOMPACT_THRASHING"])

    def test_recovered_sdk_result_is_not_terminal_failure(self):
        self.write(
            "agent/claude-code.txt",
            "\n".join(
                json.dumps(e)
                for e in [
                    {"type": "result", "is_error": True, "api_error": "autocompact_thrashing"},
                    {"type": "result", "is_error": False},
                ]
            ),
        )
        self.assertEqual(self.errors(), [])

    def test_timeout_with_score_and_recovered_api_notice(self):
        self.result.update(
            exception_info={
                "exception_type": "AgentTimeoutError",
                "exception_message": "Agent execution timed out after 900 seconds",
            },
            verifier_result={"rewards": {"reward": 0}},
        )
        self.write("trial.log", "APIConnectionError recovered\nAgent execution timed out")
        obs = self.errors()
        self.assertTrue(
            any(o["code"] == "INFRA_AGENT_TIMEOUT" and o["causal_candidate"] for o in obs)
        )
        self.assertTrue(
            all(not o["causal_candidate"] for o in obs if o["code"] == "INFRA_API_ERROR")
        )
        state = compute_runtime_state({}, self.result, self.trial)
        self.assertTrue(state["score_produced"])
        self.assertEqual(state["verification_status"], "failed")

    def test_reference_lfs_requires_actual_pointer_and_saved_failure(self):
        refs = self.task / "tests/refs.json"
        refs.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 200\n")
        self.assertEqual(collect_scoring_dependency_observations(self.task, self.trial), [])
        self.write(
            "verifier/scorer.stdout",
            "ENV_ERROR: tests/refs.json missing or malformed (Expecting value)",
        )
        obs = collect_scoring_dependency_observations(self.task, self.trial)
        self.assertEqual(obs[0]["code"], "VERIFIER_REFERENCE_LFS_POINTER")
        refs.write_text("{}")
        self.assertEqual(collect_scoring_dependency_observations(self.task, self.trial), [])

    def test_other_trial_job_build_failure_cannot_override_local_cause(self):
        (self.job / "job.log").write_text(
            "Docker compose command failed build: apt-get Failed to fetch"
        )
        self.write("verifier/test-stdout.txt", "python: not found")
        obs = self.errors()
        self.assertEqual(
            [o["code"] for o in obs if o["causal_candidate"]],
            ["INFRA_VERIFIER_INTERPRETER_MISSING"],
        )

    def test_trusted_review_hash_diagnostic(self):
        self.write(
            "verifier/test-stdout.txt",
            '{"status":"ENV_INVALID","reason":"trusted semantic review evidence hash is damaged"}',
        )
        obs = collect_scoring_dependency_observations(self.task, self.trial)
        self.assertEqual(obs[0]["code"], "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH")
        self.assertEqual(obs[0]["source_file"], "verifier/test-stdout.txt")
