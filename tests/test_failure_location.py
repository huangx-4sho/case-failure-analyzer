"""Localize saved rejections without treating matching code as causal proof."""

import ast
import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from attribution.failure_location import locate_failures
from normalize_verifier_report import build_verifier_report
from review_cause import plan, check_binding
from tests.test_causal_review import fixture
from validate_analysis import _reviewed_self_check_event


class FailureLocationTests(unittest.TestCase):
    def test_pointer_value_and_scorer_guard_refer_to_the_same_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "verifier").mkdir()
            message = "audit.json: $.attempts[1].count: expected integer"
            (root / "verifier/verification.json").write_text(
                json.dumps(
                    {
                        "overall": "FAIL",
                        "checks": [
                            {"criterion": "value_type", "pass": False, "error": message},
                            {"criterion": "other", "pass": True},
                        ],
                        "failed": ["value_type"],
                    }
                )
            )
            report = build_verifier_report(root)
            self.assertEqual(len(report["failed_checks"]), 1)
            code = ast.parse(
                'require(isinstance(value, int), "audit.json: $.attempts[1].count: expected integer")'
            )
            evidence = {
                "runtime": {"verdict": "failed"},
                "trial_dir": str(root),
                "task_dir": str(root),
                "verifier_report": report,
            }
            sources = [(root / "artifacts/audit.json", {"attempts": [{}, {"count": None}]})]

            def get():
                return locate_failures(evidence, code, {}, root / "tests/verify.py", sources, [])

            result = get()[0]
            self.assertEqual(result["pointer"], "$.checks[0]")
            self.assertEqual(result["matching_rule"]["condition"], "isinstance(value, int)")
            self.assertIsNone(result["submitted_field"]["value"])
            sources.append((root / "artifacts/other/audit.json", {"attempts": [{}, {"count": 1}]}))
            code.body *= 2
            self.assertNotIn("matching_rule", get()[0])
            self.assertNotIn("submitted_field", get()[0])

    def test_primary_reason_and_distinct_row_errors_survive_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            v = root / "verifier"
            v.mkdir()
            for name in ("verification.json", "native-initial.json"):
                (v / name).write_text(
                    json.dumps({"diagnostics": {"status": "FAIL", "reason": "metrics invalid"}})
                )
            (v / "other.json").write_text(
                json.dumps({"problems": ["table: row A lacks x", "table: row B lacks y"]})
            )
            report = build_verifier_report(root)
            checks = report["failed_checks"]
            source = next(x for x in checks if x["detail"] == "metrics invalid")
            self.assertEqual(
                (source["source"], source["source_pointer"]),
                ("verification.json", "$.diagnostics.reason"),
            )
            self.assertEqual(
                {x["detail"] for x in checks if x["name"] == "table"},
                {"table: row A lacks x", "table: row B lacks y"},
            )

    def test_plain_log_fallback_and_completeness_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            v = root / "verifier"
            v.mkdir()
            (v / "verification.json").write_text(
                json.dumps(
                    {
                        "reward_scope": "evidence_completeness_only",
                        "completeness_rubric": [
                            {
                                "name": "manifest",
                                "passed": False,
                                "detail": "missing command references",
                            }
                        ],
                    }
                )
            )
            report = build_verifier_report(root)
            check = report["failed_checks"][0]
            self.assertEqual(check["reward_scope"], "evidence_completeness_only")
            evidence = {
                "runtime": {"verdict": "failed"},
                "trial_dir": str(root),
                "verifier_report": {},
            }
            src = root / "artifacts/app/solver.log"
            src.parent.mkdir(parents=True)
            src.write_text("field residual 4.4e-16\n")
            evidence["artifacts"] = [
                {"scope": "trial", "exists": True, "rel_path": "artifacts/app/solver.log"}
            ]
            loc = locate_failures(
                evidence,
                ast.Module(body=[], type_ignores=[]),
                {},
                None,
                [],
                [(v / "test-stdout.txt", "FAIL L2: solver.log is missing numeric residual")],
            )[0]
            self.assertEqual(loc["submitted_field"]["value"], "field residual 4.4e-16")
            self.assertEqual(loc["pointer"], "L1")

    def test_reviewed_check_requires_the_same_executed_script_public_rule_and_rejection(self):
        ev, _ = fixture()
        ev["artifacts"][0].update(scope="task", rel_path="instruction.md")
        ev["causal_anchors"] = {
            "self_check_executions": [
                {"event_id": "exec:1", "script_event_refs": ["write:1"], "result_status": "passed"}
            ]
        }
        d = {
            "event_ref": "exec:1",
            "execution_binding": {"evidence_refs": ["write:1", "exec:1"]},
            "basis": {"evidence_refs": ["art:input"]},
            "outcome": {"evidence_refs": ["ver:1"]},
        }
        a = {
            "evidence_refs": ["exec:1"],
            "causal_assessment": {"decision": d, "review": {"reviewer": "x"}},
        }
        self.assertEqual(_reviewed_self_check_event(ev, a), "exec:1")
        for container, key, replacement in (
            (d["execution_binding"], "evidence_refs", ["exec:1"]),
            (d["basis"], "evidence_refs", ["art:output"]),
            (d["outcome"], "evidence_refs", []),
            (d, "event_ref", "thought:1"),
        ):
            actual = container[key]
            container[key] = replacement
            self.assertIsNone(_reviewed_self_check_event(ev, a))
            container[key] = actual

    def test_extended_fingerprint_is_pinned_and_checked_again_on_apply(self):
        ev, an = fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trajectory"
            data = b"x" * (2**20 + 1)
            path.write_bytes(data)
            ev["artifacts"][0].update(abs_path=str(path), sha256=hashlib.sha256(data).hexdigest())
            r = plan(ev, an, ["art:input"])
            self.assertEqual(r["binding"]["source_fingerprints"][0]["status"], "checked")
            r["decision"]["execution_binding"]["effective_input_refs"] = ["art:input"]
            check_binding(r, ev, an)
            path.write_bytes(data[:-1] + b"y")
            with self.assertRaisesRegex(ValueError, "Stale"):
                check_binding(r, ev, an)
            with self.assertRaisesRegex(ValueError, "artifact ID"):
                plan(ev, an, ["art:absent"])
