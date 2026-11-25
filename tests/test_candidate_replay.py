"""Distinguish the candidate subprocess from a verifier error in the same log."""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_contract import _internal_failure
from replay_failure import bound_replay_contract, candidate_tracebacks
from normalize_verifier_report import build_verifier_report


class CandidateReplayTests(unittest.TestCase):
    log = (
        "FAIL: candidate exit 1: Traceback (most recent call last):\n"
        '  File "/replay/analysis.py", line 1, in main\n'
        '    delta = manifest["public"]\n'
        "KeyError: 'public'\n"
    )

    def test_subprocess_binding_requires_captured_code_and_successful_write(self):
        frames = candidate_tracebacks(self.log)
        self.assertIsNone(_internal_failure(self.log, [], False, frames))
        with tempfile.TemporaryDirectory() as temp:
            trial = Path(temp)
            path = trial / "artifacts/app/analysis.py"
            path.parent.mkdir(parents=True)
            path.write_text('delta = manifest["public"]\n')
            event = {
                "event_id": "trajectory:step:2",
                "tool_name": "Write",
                "tool_succeeded": True,
                "arguments": {"file_path": "/app/analysis.py", "content": path.read_text()},
            }
            args = (
                frames[4],
                [event],
                trial,
                "Run /app/analysis.py",
                "verifier/test-stdout.txt",
                "L4",
            )
            self.assertEqual(bound_replay_contract(*args)["event_ref"], event["event_id"])
            event["tool_succeeded"] = False
            self.assertIsNone(bound_replay_contract(*args))
            event["tool_succeeded"] = True
            path.write_text('delta = manifest["other"]\n')
            self.assertIsNone(bound_replay_contract(*args))

    def test_independent_verifier_crash_after_candidate_exception_survives(self):
        log = (
            self.log
            + 'Traceback (most recent call last):\n  File "/tests/grade.py", line 9, in main\nKeyError: missing_ref\n'
        )
        self.assertEqual(
            _internal_failure(log, [], False, candidate_tracebacks(log)),
            (7, "KeyError: missing_ref"),
        )

    def test_json_only_replay_failure_retains_terminal_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            trial = Path(temp)
            (trial / "verifier").mkdir()
            report = trial / "verifier/verification.json"
            report.write_text(json.dumps({"status": "FAIL", "message": self.log}))
            receipt = build_verifier_report(trial)["candidate_replay_exception"]
            self.assertEqual(
                receipt["frame"][1:], [1, 'delta = manifest["public"]', "KeyError: 'public'"]
            )
            self.assertEqual(receipt["pointer"], "$.message")
            report.write_text(json.dumps({"status": "PASS", "message": self.log}))
            self.assertNotIn("candidate_replay_exception", build_verifier_report(trial))
