"""Inspect packed and multi-file scoring sources through the saved audit path."""

import base64
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_contract import audit_contract
from contract_review import audit_contract_review
from verifier_source import collect_verifier_sources


def write_bundle(task, members, *, checksum=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as stream:
        for relative, contents in members.items():
            data = contents.encode()
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    packed = buffer.getvalue()
    (task / "tests/refs.json").write_text(
        json.dumps(
            {
                "encoding": "tar+gzip;base64",
                "sha256": checksum or hashlib.sha256(packed).hexdigest(),
                "chunks": [base64.b64encode(packed).decode()],
            }
        )
    )


class VerifierSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.task = self.root / "task"
        self.trial = self.root / "job/trial"
        (self.task / "tests").mkdir(parents=True)
        (self.trial / "verifier").mkdir(parents=True)
        (self.trial / "artifacts/app").mkdir(parents=True)
        self.entry = self.task / "tests/verify.py"

    def test_packed_scorer_replacement_and_key_set_binding(self):
        self.entry.write_text(
            "encoding = 'tar+gzip;base64'; archive = 'refs.json'\nopen('public')\n"
        )
        write_bundle(
            self.task,
            {
                "tests/_support/check.py": (
                    "def validate(submission):\n"
                    "    actual = json.loads((submission / 'report.json').read_text())\n"
                    "    require(set(actual) == {'metrics'}, 'report top-level contract')\n"
                )
            },
        )
        submitted = self.trial / "artifacts/app/report.json"
        submitted.write_text('{"metrics": {}, "uncatalogued": []}')
        public = self.task / "environment/assets/output-contract.json"
        public.parent.mkdir(parents=True)
        public.write_text(
            json.dumps(
                {
                    "report_schema": {
                        "type": "object",
                        "properties": {
                            "metrics": {"type": "object"},
                            "uncatalogued": {"type": "array"},
                        },
                        "required": ["metrics"],
                        "additionalProperties": False,
                    }
                }
            )
        )
        evidence = {
            "task_dir": str(self.task),
            "trial_dir": str(self.trial),
            "runtime": {"verdict": "failed"},
            "artifacts": [
                {
                    "scope": "trial",
                    "abs_path": str(submitted),
                    "rel_path": "artifacts/app/report.json",
                }
            ],
            "verifier_report": {
                "failed_checks": [
                    {
                        "name": "structure",
                        "detail": "report top-level contract",
                        "source": "verify.json",
                    }
                ]
            },
        }
        snapshot = collect_verifier_sources(self.task)
        with patch("subprocess.Popen", side_effect=AssertionError("must stay read-only")):
            result = audit_contract_review(evidence, verifier_sources=snapshot)
        self.assertTrue(result["source_inspection"]["encoded_source_decoded"])
        (rule,) = result["failure_locations"]
        self.assertEqual(rule["matching_rule"]["archive_member"], "tests/_support/check.py")
        self.assertEqual(rule["matching_rule"]["line"], 3)
        self.assertEqual(rule["key_set_difference"]["unexpected_keys"], ["uncatalogued"])
        self.assertEqual(rule["public_schema_comparison"][0]["unexpected_public_keys"], [])
        item = next(i for i in result["review_items"] if i.get("structured_comparison"))
        self.assertEqual(item["structured_comparison"]["scorer"]["expected_keys"], ["metrics"])
        self.assertEqual(
            item["structured_comparison"]["submission"]["unexpected_keys"], ["uncatalogued"]
        )
        self.assertEqual(item["structured_comparison"]["public"]["status"], "observed_declaration")
        self.assertFalse((self.task / "tests/_support").exists())

    def test_inline_module_and_single_call_bind_nested_keys(self):
        code = (
            "def compare(doc):\n"
            "    params = doc.get('parameters')\n"
            "    if set(params) != {'basis'}:\n"
            "        raise ValueError('unsupported parameter representation')\n"
            "def score(root):\n"
            "    result = strict_json(root / 'report.json')\n"
            "    compare(result)\n"
        )
        safe = "SCHEMA = 'ccw-tests-refs/v1'\nMODULE_SOURCE = "
        self.entry.write_text(safe + repr({"verify.py": code}))
        captured = self.trial / "artifacts/app/report.json"
        captured.write_text(json.dumps({"parameters": {"basis": "x", "extra": "x" * 1800}}))
        evidence = {
            "task_dir": str(self.task),
            "trial_dir": str(self.trial),
            "runtime": {"verdict": "failed"},
            "artifacts": [
                {
                    "scope": "trial",
                    "rel_path": "artifacts/app/report.json",
                    "abs_path": str(captured),
                }
            ],
            "verifier_report": {
                "failed_checks": [
                    {
                        "name": "representation",
                        "source": "verify.json",
                        "detail": "unsupported parameter representation",
                    }
                ]
            },
        }
        review = audit_contract_review(evidence)
        record = review["failure_locations"][0]
        self.assertEqual(record["matching_rule"]["source_ref"], "art:verify_py")
        self.assertEqual(record["key_set_difference"]["pointer"], "$.parameters")
        self.assertEqual(record["key_set_difference"]["unexpected_keys"], ["extra"])
        self.assertFalse(
            review["review_items"][0]["structured_comparison"]["public"]["absence_is_proven"]
        )
        self.entry.write_text(safe + "dict(verify=hostile_call())")
        rejected = collect_verifier_sources(self.task)
        self.assertEqual(rejected.sources, [])
        self.assertTrue(rejected.diagnostics)

    def test_archive_metadata_no_fallback_to_wrapper_after_rejection(self):
        self.entry.write_text("encoding = 'tar+gzip;base64'; archive = 'refs.json'\n")
        for kwargs, members in (
            ({}, {"../outside.py": "pass", "tests/verify.py": "pass"}),
            ({"checksum": "0" * 64}, {"tests/verify.py": "pass"}),
        ):
            write_bundle(self.task, members, **kwargs)
            result = collect_verifier_sources(self.task)
            self.assertEqual(result.sources, [])
            self.assertTrue(result.encoded_source_detected)
            self.assertFalse(result.encoded_source_decoded)
            self.assertEqual(result.diagnostics[0]["code"], "VERIFIER_BUNDLE_UNAVAILABLE")

    def test_sealed_replay_shape_abstains_from_tampered_output(self):
        self.entry.write_text(
            "def score(doc):\n"
            "    try:\n"
            "        expected = {'met': True, 'note': ''}\n"
            "        compare_optional_record(doc['report'], expected, {'note'}, '$.report')\n"
            "    except ValueError as exc:\n"
            "        raise ValueError('candidate output schema or values are invalid: ' + str(exc))\n"
        )
        result_file = self.trial / "verifier/candidate-01.123abc.result.json"
        raw = b'{"report":{"met":true,"unlisted":5}}'
        result_file.write_bytes(raw)
        (self.trial / "verifier/candidate-01.result.json.record").write_text(
            json.dumps(
                {
                    "snapshot": result_file.name,
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        )
        evidence = {
            "task_dir": str(self.task),
            "trial_dir": str(self.trial),
            "runtime": {"verdict": "failed"},
            "verifier_report": {
                "failed_checks": [
                    {
                        "name": "schema",
                        "source": "verify.json",
                        "detail": "candidate output schema or values are invalid: $.report: object keys mismatch",
                    }
                ]
            },
        }
        [item] = audit_contract_review(evidence)["review_items"]
        comparison = item["structured_comparison"]
        self.assertEqual(comparison["scorer"]["expected_keys"], ["met", "note"])
        self.assertEqual(comparison["submission"]["unexpected_keys"], ["unlisted"])
        self.assertEqual(comparison["submission"]["scope"], "verifier_retained_replay")
        result_file.write_bytes(raw.replace(b":5", b":6"))
        [item] = audit_contract_review(evidence)["review_items"]
        self.assertEqual(item["structured_comparison"]["submission"], {"status": "unavailable"})

    def test_plain_helper_files_are_available(self):
        self.entry.write_text("from _support.rules import validate\n")
        helper = self.task / "tests/_support/rules.py"
        helper.parent.mkdir()
        helper.write_text("def validate(x):\n    assert x, 'expected records'\n")
        records = collect_verifier_sources(self.task)
        self.assertEqual(
            [s.path for s in records.sources], ["tests/verify.py", "tests/_support/rules.py"]
        )

    def test_pytest_printed_source_does_not_indicate_an_internal_crash_or_missing_output(self):
        (self.task / "instruction.md").write_text("Write /app/final.json.")
        self.entry.write_text("open('/app/final.json')\n")
        (self.trial / "artifacts/app/final.json").write_text("{}\n")
        log = self.trial / "verifier/test-stdout.txt"
        log.write_text(
            "test session starts\n"
            ">       assert address.exists(), 'Missing /app/final.json'\n"
            "    except (OSError, KeyError):\n"
            "E       assert 2928 <= 502\n"
            "FAILED ../tests/verify.py::test_targets - assert 2928 <= 502\n"
        )
        result = audit_contract(self.task, self.trial, runtime={"score_produced": True})
        self.assertFalse(
            any(v["type"] == "verifier_internal_crash" for v in result["verifier_observations"])
        )
        self.assertFalse(
            any(
                v["agent_output_status"] == "missing_at_verify"
                for v in result["contract_observations"]
            )
        )
        log.write_text("FAIL: Missing /app/final.json\n")
        result = audit_contract(self.task, self.trial)
        self.assertEqual(
            result["contract_observations"][0]["agent_output_status"], "present_in_capture"
        )
