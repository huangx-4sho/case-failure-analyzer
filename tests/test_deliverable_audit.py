"""Read-only deliverable capture auditing, including uncertain collection states."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deliverable_audit import audit_deliverables


class DeliverableAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trial = Path(self.tmp.name) / "trial"
        self.root = self.trial / "artifacts"
        self.root.mkdir(parents=True)
        self.evidence = {
            "trial_dir": str(self.trial),
            "metadata": {"task_toml": {"artifacts": ["/app/final"]}},
            "artifacts": [{"artifact_id": "art:task_toml"}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def audit(self, **kwargs):
        return audit_deliverables(self.evidence, **kwargs)

    def test_missing_capture_is_not_nonproduction_even_with_apparent_write(self):
        self.evidence["timeline"] = [{"command": "mkdir -p /app/final; echo done > /app/final/out"}]
        result = self.audit()
        self.assertEqual(result["records"][0]["status"], "not_found_in_collected_tree")
        self.assertIn("does not establish", result["records"][0]["uncertainty"][0])
        json.dumps(result)

    def test_collection_crash_preserves_runner_evidence(self):
        self.evidence["error_observations"] = [
            {
                "code": "INFRA_ARTIFACT_COLLECTION_CRASH",
                "missing_deliverable": "/app/final",
                "error_id": "err:1",
            }
        ]
        result = self.audit()
        self.assertEqual(result["records"][0]["status"], "collection_failed")
        self.assertIn("err:1", result["records"][0]["evidence_refs"])

    def test_missing_collection_root_is_unresolved(self):
        self.root.rmdir()
        result = self.audit()
        self.assertEqual(result["coverage"]["root_status"], "missing")
        self.assertEqual(result["records"][0]["status"], "unresolved")

    def test_present_empty_directory_does_not_invent_filenames(self):
        (self.root / "app/final").mkdir(parents=True)
        record = self.audit()["records"][0]
        self.assertEqual(record["status"], "present")
        self.assertEqual(record["captured_kind"], "directory")
        self.assertTrue(record["empty"])
        self.assertEqual(record["observed_files"], [])

    def test_service_mapping_is_not_assumed(self):
        self.evidence["metadata"]["task_toml"]["artifacts"] = [
            {"source": "/app/final", "service": "worker"}
        ]
        (self.root / "app/final").mkdir(parents=True)
        record = self.audit()["records"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(record["checked_paths"], [])

    def test_string_declaration_and_json_read_status(self):
        self.evidence["metadata"]["task_toml"]["artifacts"] = "/app/out.json"
        path = self.root / "app/out.json"
        path.parent.mkdir()
        path.write_text("not json")
        record = self.audit()["records"][0]
        self.assertEqual(record["status"], "present")
        self.assertEqual(record["observed_files"][0]["parse_status"], "invalid_json")
        self.assertEqual(
            self.audit(max_read_bytes=1)["records"][0]["observed_files"][0]["read_status"],
            "size_limit",
        )

    def test_scan_limits_keep_absence_uncertain(self):
        for i in range(5):
            (self.root / str(i)).write_text("x")
        result = self.audit(max_entries=2)
        self.assertEqual(result["coverage"]["entries_scanned"], 2)
        self.assertFalse(result["coverage"]["scan_complete"])
        self.assertEqual(result["records"][0]["status"], "unresolved")

    def test_depth_limit_keeps_absence_uncertain(self):
        (self.root / "deep/tree").mkdir(parents=True)
        result = self.audit(max_depth=0)
        self.assertFalse(result["coverage"]["scan_complete"])
        self.assertEqual(result["records"][0]["status"], "unresolved")

    def test_inaccessible_scan_keeps_absence_uncertain(self):
        with patch("deliverable_audit.os.scandir", side_effect=PermissionError("denied")):
            result = self.audit()
        self.assertEqual(result["records"][0]["status"], "unresolved")
        self.assertFalse(result["coverage"]["scan_complete"])

    def test_traversal_relative_and_symlink_are_unresolved(self):
        self.evidence["metadata"]["task_toml"]["artifacts"] = [
            "/../secret",
            "app/final",
            "/escape/final",
        ]
        (self.root / "escape").symlink_to(Path(self.tmp.name), target_is_directory=True)
        result = self.audit()
        self.assertTrue(all(r["status"] == "unresolved" for r in result["records"]))
        self.assertEqual(result["checked_paths"], [])

    def test_absent_and_malformed_declarations(self):
        self.evidence["metadata"] = {}
        result = self.audit()
        self.assertEqual(result["records"], [])
        self.assertTrue(result["uncertainty"])
        self.evidence["metadata"] = {"task_toml": {"artifacts": [None, {}, 4]}}
        self.assertTrue(all(r["status"] == "unresolved" for r in self.audit()["records"]))

    def test_present_capture_survives_unrelated_collection_failure(self):
        (self.root / "app/final").mkdir(parents=True)
        self.evidence["error_observations"] = [
            {"subtype": "artifact_collection_crash", "error_id": "err:1"}
        ]
        self.assertEqual(self.audit()["records"][0]["status"], "present")

    def test_unreadable_task_declaration_does_not_claim_absence(self):
        self.evidence["metadata"] = {
            "task_toml": {"_parse_error": "bad TOML"},
            "declared_deliverables": ["/app/final"],
        }
        result = self.audit()
        self.assertEqual(result["declaration_status"], "unreadable")
        self.assertEqual(result["declaration_validation"], "unavailable")
        self.assertEqual(result["records"], [])

    def test_empty_file_is_present_but_invalid_json(self):
        self.evidence["metadata"]["task_toml"]["artifacts"] = ["/empty.json"]
        (self.root / "empty.json").touch()
        record = self.audit()["records"][0]
        self.assertEqual(record["status"], "present")
        self.assertTrue(record["empty"])
        self.assertEqual(record["observed_files"][0]["parse_status"], "invalid_json")

    def test_symlink_capture_root_is_not_scanned(self):
        self.root.rmdir()
        self.root.symlink_to(Path(self.tmp.name), target_is_directory=True)
        result = self.audit()
        self.assertEqual(result["coverage"]["root_status"], "unsafe_symlink")
        self.assertEqual(result["coverage"]["entries_scanned"], 0)
        self.assertEqual(result["records"][0]["status"], "unresolved")

    def test_legacy_metadata_fallback_is_explicit(self):
        self.evidence["metadata"] = {"declared_deliverables": ["/app/final"]}
        self.assertEqual(self.audit()["declaration_status"], "metadata_fallback")


class VerifiedMissingOutputTests(unittest.TestCase):
    def evidence(self, detail, declarations=None):
        return {
            "metadata": {"task_toml": {"artifacts": declarations or ["/app/output.json"]}},
            "verifier_report": {
                "failed_checks": [
                    {
                        "name": "output_exists",
                        "detail": detail,
                        "source": "core/verify.json",
                        "source_pointer": "checks.output_exists",
                    }
                ]
            },
        }

    def extract(self, evidence):
        from deliverable_audit import extract_verified_missing_outputs

        return extract_verified_missing_outputs(evidence)

    def test_real_normalized_failed_check_binds_exact_declared_path(self):
        result = self.extract(self.evidence("Missing required output: /app/output.json"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["declared_source"], "/app/output.json")
        self.assertEqual(result[0]["collected_rel_path"], "artifacts/app/output.json")
        self.assertEqual(result[0]["check_ref"], "output_exists")
        self.assertEqual(result[0]["source_ref"], "core/verify.json")
        self.assertEqual(result[0]["source_pointer"], "checks.output_exists")

    def test_saved_log_observation_has_existing_reference(self):
        evidence = self.evidence("numerical mismatch")
        evidence["verifier_observations"] = [
            {
                "obs_id": "ver:fail_log",
                "type": "verifier_fail_message",
                "matched_text": "starting check\nFileNotFoundError: [Errno 2] No such file or directory: '/app/output.json'",
                "source_file": "verifier/verify.log",
                "source_pointer": "L1",
            }
        ]
        result = self.extract(evidence)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text_line"], 2)
        self.assertEqual(result[0]["evidence_refs"], ["ver:fail_log"])

    def test_generic_negative_prefix_and_child_mismatch_are_not_bound(self):
        for text in (
            "missing output.json",
            "missing /app/output.json.backup",
            "missing /app/output.json/child",
            "missing /prefix/app/output.json",
            "no output was supplied",
            "missing key: /app/output.json",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.extract(self.evidence(text)), [])
        self.assertEqual(
            self.extract(self.evidence("missing /app/results/output.json", ["/app/results"])), []
        )

    def test_negation_requirements_and_success_quotes_are_not_absence(self):
        for text in (
            "/app/output.json is not missing",
            "not missing /app/output.json",
            "No missing /app/output.json",
            "If missing /app/output.json, fail the check",
            "You must detect missing /app/output.json",
            "Example: missing /app/output.json",
            'PASS: saved expected message "missing /app/output.json"',
            '{"status": "ok", "message": "missing /app/output.json"}',
            "成功：缺失 /app/output.json",
            "示例：缺失 /app/output.json",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.extract(self.evidence(text)), [])

    def test_chinese_and_suffix_missing_statements(self):
        for text in (
            "/app/output.json: not found",
            "/app/output.json is missing",
            "缺失：/app/output.json",
            "/app/output.json不存在",
        ):
            with self.subTest(text=text):
                self.assertEqual(len(self.extract(self.evidence(text))), 1)

    def test_service_declarations_and_pass_records_do_not_bind(self):
        evidence = self.evidence(
            "missing /app/output.json", [{"source": "/app/output.json", "service": "worker"}]
        )
        self.assertEqual(self.extract(evidence), [])
        evidence = self.evidence("missing /app/output.json")
        evidence["verifier_report"]["failed_checks"][0]["status"] = "PASS"
        self.assertEqual(self.extract(evidence), [])

    def test_audit_exposes_additive_verifier_findings_without_trial(self):
        result = audit_deliverables(self.evidence("missing /app/output.json"))
        self.assertEqual(len(result["verified_missing_outputs"]), 1)
        self.assertEqual(result["records"][0]["status"], "unresolved")


if __name__ == "__main__":
    unittest.main()
