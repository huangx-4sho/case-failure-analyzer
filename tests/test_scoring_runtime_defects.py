"""Regressions using small saved-artifact shapes, never live execution."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from attribution.engine import run_attribution_engine
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

    def attribute(self, verifier_observations=(), verifier_report=None):
        errors = self.errors()
        runtime = compute_runtime_state({}, self.result, self.trial)
        evidence = {
            "runtime": runtime,
            "artifacts": [],
            "error_observations": errors,
            "missing_deliverables": ["/app/final"],
            "verifier_observations": list(verifier_observations),
            "verifier_report": verifier_report,
        }
        return run_attribution_engine(evidence, {"candidate_hypotheses": []}), runtime

    def set_api_budget_exception(self):
        self.result["exception_info"] = {
            "exception_type": "ApiUsageLimitError",
            "exception_message": "API Error: Request rejected (429) · Budget has been exceeded",
        }

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

    def test_api_usage_limit_preserves_scored_rejection_without_agent_blame(self):
        self.set_api_budget_exception()
        self.result["verifier_result"] = {"rewards": {"reward": 0}}
        check = {"name": "deliverables_complete", "ok": False, "detail": "Missing result.json"}
        analysis, runtime = self.attribute(verifier_report={"failed_checks": [check]})

        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_API_RATE_LIMIT")
        self.assertEqual(analysis["verdict"], "failed")
        self.assertEqual(runtime["reward"], 0)
        self.assertFalse(any(c["category"] == "agent" for c in analysis["contributing_factors"]))
        self.assertEqual(
            analysis["causal_assessment"]["existing_verification"]["failed_checks"], [check]
        )

    def test_api_usage_limit_before_agent_execution(self):
        self.set_api_budget_exception()
        self.result.pop("agent_result")
        analysis, runtime = self.attribute()

        self.assertFalse(runtime["agent_started"])
        self.assertEqual(analysis["failure_stage"], "agent_setup")
        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_API_RATE_LIMIT")
        self.assertIn("model API", analysis["primary_root_cause"]["summary"])

    def test_terminal_sdk_budget_error_without_api_error_field(self):
        self.result["exception_info"] = {"exception_type": "NonZeroAgentExitCodeError"}
        self.write(
            "agent/claude-code.txt",
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "API Error: Request rejected (429) · Budget has been exceeded! TeamMember=private",
                }
            ),
        )
        obs = next(o for o in self.errors() if o.get("limit_kind") == "budget")
        self.assertTrue(obs["causal_candidate"])
        self.assertEqual(obs["http_status"], 429)
        self.assertEqual(obs["source_file"], "agent/claude-code.txt")
        self.assertNotIn("private", obs["matched_text"])

    def test_recovered_or_incidental_api_notices_are_not_terminal(self):
        self.result["verifier_result"] = {"rewards": {"reward": 0}}
        self.write("trial.log", "Source data download received: 429 Too Many Requests")
        error = {"type": "result", "is_error": True, "api_error": "rate_limit"}
        for continuation in ({"type": "result", "is_error": False}, {"type": "assistant"}):
            with self.subTest(continuation=continuation):
                self.write(
                    "agent/claude-code.txt", json.dumps(error) + "\n" + json.dumps(continuation)
                )
                self.assertFalse(any(o["causal_candidate"] for o in self.errors()))

    def test_terminal_api_exception_preserves_pass_and_pending_review(self):
        self.set_api_budget_exception()
        self.result["verifier_result"] = {"rewards": {"reward": 1}}
        analysis, runtime = self.attribute()
        self.assertEqual(analysis["verdict"], "passed")
        self.assertEqual(runtime["reward"], 1)

        self.result.pop("verifier_result")
        self.write("verifier/verification.json", '{"status":"REVIEW_REQUIRED"}')
        analysis, runtime = self.attribute()
        self.assertEqual(analysis["verdict"], "pending")
        self.assertFalse(runtime["score_produced"])

    def test_unscored_independent_verifier_crash_survives_agent_api_limit(self):
        self.set_api_budget_exception()
        self.write("verifier/test-stdout.txt", "Evaluation asset is unreadable")
        crash = {
            "obs_id": "ver:internal_crash",
            "type": "verifier_internal_crash",
            "summary": "Evaluation asset is unreadable",
        }
        analysis, runtime = self.attribute([crash])
        self.assertEqual(analysis["primary_root_cause"]["code"], "VERIFIER_RECOMPUTE_DEFECT")
        self.assertTrue(any(o.get("limit_kind") == "budget" for o in self.errors()))
        self.assertFalse(runtime["score_produced"])

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

    def test_prestartup_registry_diagnostic_preserves_actual_line_and_code(self):
        self.result.pop("agent_result")
        self.result["exception_info"] = {
            "exception_type": "RuntimeError",
            "exception_message": "Docker compose command failed during build",
        }
        image = "docker.io/library/compchembench-base:2.1.267"
        for kind, detail in (
            ("registry_access_denied", "pull access denied; server message: insufficient_scope"),
            ("image_not_found", "manifest unknown"),
            ("registry_token_request_failed", "failed to fetch anonymous token: EOF"),
        ):
            line = f"failed to solve: failed to resolve source metadata for {image}: {detail}"
            with self.subTest(kind=kind):
                self.write(
                    "trial.log", "Docker compose command failed during build\napt-get\n" + line
                )
                obs = [o for o in self.errors() if o["source_file"] == "trial.log"]
                self.assertEqual(
                    {o["code"] for o in obs}, {"INFRA_EXTERNAL_NETWORK", "INFRA_CONTAINER_BUILD"}
                )
                self.assertTrue(all(o["source_pointer"] == "L3" for o in obs))
                self.assertTrue(all(o["matched_text"] == line for o in obs))
                self.assertTrue(all(o["build_failure_kind"] == kind for o in obs))

    def test_prestartup_build_missing_tool_outranks_recovered_download(self):
        self.result.pop("agent_result")
        failure = "#10 13.11            error: could not find git for clone of test-fixtures"
        log = "\n".join(
            [
                "Docker compose command failed during build",
                "#3 ERROR: Failed to fetch token: temporary mirror issue",
                "#3 DONE after retry",
                failure,
                'failed to solve: process "apt-get update && compile" exited with status 1',
            ]
        )
        self.result["exception_info"] = {
            "exception_type": "RuntimeError",
            "exception_message": log,
        }
        self.write("exception.txt", "Traceback (most recent call last):\n" + log)
        obs = [o for o in self.errors() if o["causal_candidate"]]
        self.assertEqual({o["code"] for o in obs}, {"INFRA_CONTAINER_BUILD"})
        selected = next(o for o in obs if o["source_file"] == "exception.txt")
        self.assertEqual(selected["source_pointer"], "L5")
        self.assertEqual(selected["matched_text"], failure)
        self.assertEqual(selected["build_failure_kind"], "missing_local_build_dependency")
        analysis, runtime = self.attribute()
        self.assertEqual(analysis["primary_root_cause"]["code"], "INFRA_CONTAINER_BUILD")
        self.assertFalse(runtime["agent_started"])

        completed = log.replace(
            failure + "\n", failure + "\n#10 DONE after retry\n#13 ERROR: unrelated build failure\n"
        )
        self.result["exception_info"]["exception_message"] = completed
        self.write("exception.txt", "Traceback (most recent call last):\n" + completed)
        self.assertFalse(
            any(
                o.get("build_failure_kind") == "missing_local_build_dependency"
                for o in self.errors()
            )
        )

    def test_trusted_review_hash_diagnostic(self):
        self.write(
            "verifier/test-stdout.txt",
            '{"status":"ENV_INVALID","reason":"trusted semantic review evidence hash is damaged"}',
        )
        obs = collect_scoring_dependency_observations(self.task, self.trial)
        self.assertEqual(obs[0]["code"], "VERIFIER_TRUSTED_ASSET_HASH_MISMATCH")
        self.assertEqual(obs[0]["source_file"], "verifier/test-stdout.txt")
