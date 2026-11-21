"""Packaged input LFS diagnoses require a specific saved scoring failure."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scoring_dependencies import collect_scoring_dependency_observations
from analyze_case import _attach_unscored_verifier_fault
from normalize_verifier_report import build_verifier_report
from scoring_dependencies import replay_runtime_error


POINTER = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 12399138\n"


class PackagedInputDependencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / "task"
        self.trial = self.root / "trial"
        (self.task / "environment/assets").mkdir(parents=True)
        (self.task / "tests/protected").mkdir(parents=True)
        (self.trial / "verifier").mkdir(parents=True)

    def asset(self, relative, contents=POINTER):
        path = self.task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def failure(self, message, runtime=None):
        (self.trial / "verifier/test-stdout.txt").write_text(message)
        return collect_scoring_dependency_observations(self.task, self.trial, runtime)

    def test_named_scientific_input_hash_failures_and_protected_archive(self):
        cases = (
            ("evaluator input SHA-256 mismatch: sample.npy", "environment/assets/sample.npy"),
            ("package asset hash mismatch: sample.npy", "environment/assets/sample.npy"),
            ("public input asset digest mismatch: sample.npy", "environment/assets/sample.npy"),
            (
                "protected asset missing or damaged: participant.zip",
                "tests/protected/participant.zip",
            ),
        )
        for message, relative in cases:
            with self.subTest(message=message):
                self.asset(relative)
                observations = self.failure(json.dumps({"status": "INFRA_ERROR", "error": message}))
                self.assertEqual(1, len(observations))
                observation = observations[0]
                self.assertEqual("VERIFIER_REFERENCE_LFS_POINTER", observation["code"])
                self.assertEqual(relative, observation["dependency_path"])
                self.assertEqual(
                    ["verifier/test-stdout.txt", relative], observation["binding_evidence"]
                )
                self.assertEqual(
                    "sha256:" + "a" * 64, observation["pointer_metadata"]["payload_oid"]
                )
                self.assertEqual(
                    12399138, observation["pointer_metadata"]["declared_payload_size_bytes"]
                )
                self.assertIn(
                    "Historical byte identity is not independently proven",
                    observation["uncertainty"],
                )

    def test_unrelated_pointer_cannot_explain_named_nonpointer_or_missing_file(self):
        self.asset("environment/assets/unrelated.npy")
        self.asset("environment/assets/sample.npy", "real input payload")
        for message in (
            "package asset hash mismatch: sample.npy",
            "package asset hash mismatch: missing.npy",
        ):
            self.assertEqual([], self.failure(message))

    def test_candidate_output_failure_is_not_packaged_input_failure(self):
        self.asset("environment/assets/sample.npy")
        self.assertEqual([], self.failure("candidate output hash mismatch: sample.npy"))
        self.assertEqual([], self.failure("a package asset hash mismatch occurred"))

    def test_actual_pointer_without_saved_failure_does_not_trigger(self):
        self.asset("tests/protected/participant.zip")
        self.assertEqual([], self.failure("unknown scorer interruption"))

    def test_successful_score_supersedes_stale_pointer_failure_log(self):
        self.asset("environment/assets/sample.npy")
        self.assertEqual(
            [], self.failure("package asset hash mismatch: sample.npy", {"score_produced": True})
        )

    def test_ambiguous_basename_abstains_even_when_one_candidate_is_pointer(self):
        self.asset("environment/assets/sample.npy")
        self.asset("tests/protected/sample.npy", "real payload")
        self.assertEqual([], self.failure("package asset hash mismatch: sample.npy"))

    def test_explicit_packaged_relative_path_disambiguates(self):
        self.asset("environment/assets/data/sample.npy")
        self.asset("tests/protected/sample.npy", "real payload")
        observations = self.failure("public input asset digest mismatch: assets/data/sample.npy")
        self.assertEqual("environment/assets/data/sample.npy", observations[0]["dependency_path"])
        self.assertEqual([], self.failure("package asset hash mismatch: ../sample.npy"))

    def test_ordinary_provenance_integrity_failure_stays_unknown(self):
        self.asset(
            "environment/assets/elastic_constants.provenance.json", '{"source":"published paper"}'
        )
        self.asset("environment/assets/unrelated.npy")
        self.assertEqual(
            [], self.failure("integrity failure: assets/elastic_constants.provenance.json")
        )

    def test_duplicate_saved_reports_deduplicate_same_asset(self):
        self.asset("tests/protected/participant.zip")
        message = "protected asset missing or damaged: participant.zip"
        (self.trial / "verifier/verification.json").write_text(
            json.dumps({"status": "ENV_INVALID", "error": message})
        )
        self.assertEqual(1, len(self.failure(message)))

    def test_bounded_tail_preserves_specific_failure(self):
        self.asset("environment/assets/sample.npy")
        observations = self.failure("x" * 1_200_000 + "\npackage asset hash mismatch: sample.npy\n")
        self.assertEqual(1, len(observations))
        self.assertEqual("package asset hash mismatch: sample.npy", observations[0]["matched_text"])

    def test_saved_preflight_rejection_survives_restored_local_payload(self):
        self.asset("environment/assets/sample.npy", "restored data")
        (self.trial / "verifier/verification.json").write_text(
            json.dumps(
                {
                    "status": "INFRA_ERROR",
                    "reward": None,
                    "static_scientific_checks": [
                        {
                            "name": "independent",
                            "status": "INFRA_ERROR",
                            "reason": ["evaluator input SHA-256 mismatch: sample.npy"],
                        }
                    ],
                }
            )
        )
        (self.trial / "verifier/native-result.json").write_text(
            json.dumps({"status": "SCORED", "reward": 0.0})
        )
        self.assertEqual([], collect_scoring_dependency_observations(self.task, self.trial))
        report = build_verifier_report(self.trial)
        self.assertFalse(report["scorable"])
        self.assertIsNone(report["reward"])
        observations = []
        _attach_unscored_verifier_fault(report, observations)
        preflight = observations[-1]
        self.assertEqual(preflight["code"], "VERIFIER_DEPENDENCY_PREFLIGHT_FAILED")
        self.assertEqual(preflight["source_file"], "verifier/verification.json")
        self.assertEqual(preflight["source_pointer"], "$.static_scientific_checks[0]")
        self.assertFalse(preflight["scoring_reached"])

    def test_saved_environment_guard_is_distinct_from_a_scored_failure(self):
        path = self.trial / "verifier/verify.json"
        message = "candidate replay requires executable /dev/shm tmpfs capped at 512 MiB or less"
        path.write_text(json.dumps({"status": "ENV_INVALID", "message": message}))
        runtime = replay_runtime_error(build_verifier_report(self.trial))
        self.assertEqual(runtime["code"], "INFRA_VERIFIER_RUNTIME_REQUIREMENT")
        self.assertEqual(runtime["source_pointer"], "$.message")
        path.write_text(json.dumps({"status": "SCORED", "reward": 0, "message": message}))
        self.assertIsNone(replay_runtime_error(build_verifier_report(self.trial)))


if __name__ == "__main__":
    unittest.main()
